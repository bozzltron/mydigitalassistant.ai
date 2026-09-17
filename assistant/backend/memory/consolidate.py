"""Memory consolidation (Phase 9B): merge duplicate frames, redirect the graph.

The offline counterpart to per-turn extraction. Repeated exact-name-only
writes fragment the graph ("The Mountain & the Wolf" vs "the mountain and
the wolf"); this pass clusters near-duplicate frames, unions their slots onto
a single survivor, redirects associations, records alias mappings so future
extraction resolves merged names correctly, and tombstones the losers.

Safety properties:
- Nothing hard-deletes: losers get deleted_at set; slots/history stay intact.
- Owner isolation: frames are only merged within the same owner scope.
- Type gate: only compatible types merge (entity acts as a wildcard).
- dry_run computes the plan without writing anything.

Run weekly via the scheduler or on-demand: `assistant db consolidate`.
"""

import logging
from dataclasses import dataclass, field

from assistant.backend.config import settings
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.pipeline.extractor import normalize_frame_name

logger = logging.getLogger(__name__)

# Types fuzzy/normalized merges may unify besides exact type equality.
_TYPE_WILDCARD = "entity"

MAX_MERGES_PER_RUN = 50


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 2.0
    return 1 - dot / (na * nb)


class _UnionFind:
    def __init__(self, ids: list[int]) -> None:
        self.parent = {i: i for i in ids}

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class PlannedMerge:
    survivor_id: int
    survivor_name: str
    loser_id: int
    loser_name: str


@dataclass
class ConsolidationReport:
    scanned_frames: int = 0
    planned_merges: list[PlannedMerge] = field(default_factory=list)
    applied_merges: int = 0
    slots_moved: int = 0
    edges_redirected: int = 0
    aliases_recorded: int = 0
    associations_strengthened: int = 0
    capped: bool = False
    clusters_dropped: int = 0
    errors: int = 0

    def summary(self) -> str:
        mode = "planned" if not self.applied_merges else "applied"
        parts = [
            f"{self.scanned_frames} frames scanned",
            f"{len(self.planned_merges)} merges {mode}",
            f"{self.slots_moved} slots moved",
            f"{self.edges_redirected} edges redirected",
            f"{self.aliases_recorded} aliases recorded",
            f"{self.associations_strengthened} associations strengthened",
            f"{self.errors} errors",
        ]
        if self.clusters_dropped:
            parts.append(f"{self.clusters_dropped} clusters dropped (rerun)")
        return ", ".join(parts)


def _types_compatible(a: str, b: str) -> bool:
    return a == b or a == _TYPE_WILDCARD or b == _TYPE_WILDCARD


def _prefix_tokens(name: str, width: int = 4) -> set[str]:
    """Token prefixes — cheap stemming so physics≈physicists, indie≈indie."""
    return {
        t[:width]
        for t in normalize_frame_name(name).split()
        if len(t) >= width
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Names that describe a type rather than identify an entity. When choosing a
# merge survivor, a specific name always beats one of these (audit finding F3:
# extractor used to mint frames literally called "song", "movie", "entity").
_GENERIC_NAMES = {
    "song", "song_title", "song_titles", "album_title", "movie", "book",
    "entity", "concept", "event", "household", "thing", "object", "artist",
    "link", "role", "phrase", "feeling", "identity_role", "user_commentary",
}

# Tokens that turn a noun into a *different* entity (an aspect, role, or
# sub-part) rather than a duplicate mention of the same one.
_ASPECT_WORDS = {"structure", "engineer", "session", "news"}

# Minimum token-set Jaccard for any lexical (non-exact) merge evidence.
# Calibrated against the live corpus: true variants sit >= 2/3; different
# entities sharing one head noun ("south_texas" / "south_texas_grass_farms")
# sit at exactly 1/2.
_OVERLAP_MIN = 0.6


def _pick_survivor(cluster: list[dict]) -> dict:
    """Highest confidence; then a specific name over a generic one; then age."""
    return sorted(
        cluster,
        key=lambda f: (
            -f["confidence"],
            f["norm"] in _GENERIC_NAMES,
            f["created_at"],
            f["id"],
        ),
    )[0]


async def strengthen_from_episodes(db_path: str) -> int:
    """Strengthen existing associations whose frames co-occur in episodes.

    Makes the AGENTS.md promise ("association confidence increases with
    co-occurrence") real. Ground rules:

    - Only EXISTING edges gain weight — co-occurrence never mints new
      relations, so this pass has no hallucination surface.
    - Exactly-once per episode: a metadata cursor records the highest
      episode id already processed, so repeated runs never re-bump old
      evidence into uniform saturation.
    - Bounded by bump_confidence (MAX_CONFIDENCE) from confidence.py, and
      priority rises slightly so meaningful edges resist GC decay.

    Returns the number of associations strengthened.
    """
    import json as _json

    from assistant.backend.memory.confidence import bump_confidence

    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        cursor_row = await db.execute_fetchall(
            "SELECT value FROM metadata WHERE key = 'last_strengthened_episode_id'"
        )
    last_id = int(cursor_row[0][0]) if cursor_row and cursor_row[0][0] else 0

    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        rows = await db.execute_fetchall(
            "SELECT id, frame_ids FROM episodes WHERE id > ? "
            "AND frame_ids IS NOT NULL ORDER BY id",
            (last_id,),
        )

    pair_counts: dict[tuple[int, int], int] = {}
    max_seen = last_id
    for ep_id, raw in rows:
        max_seen = max(max_seen, ep_id)
        try:
            ids = _json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(ids, list):
            continue
        uniq = sorted({i for i in ids if isinstance(i, int)})
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                pair_counts[(a, b)] = pair_counts.get((a, b), 0) + 1

    strengthened = 0
    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        await db.execute("BEGIN")
        for (a, b), _count in pair_counts.items():
            cur = await db.execute(
                "SELECT id, confidence, priority FROM associations "
                "WHERE (from_frame_id=? AND to_frame_id=?) "
                "   OR (from_frame_id=? AND to_frame_id=?)",
                (a, b, b, a),
            )
            for assoc_id, conf, _priority in await cur.fetchall():
                new_conf = bump_confidence(conf)
                if new_conf > conf:
                    await db.execute(
                        "UPDATE associations SET confidence=?, "
                        "priority=MIN(1.0, priority + 0.05) WHERE id=?",
                        (new_conf, assoc_id),
                    )
                    strengthened += 1
        await db.execute(
            "INSERT INTO metadata (key, value) VALUES "
            "('last_strengthened_episode_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(max_seen),),
        )
        await db.commit()
    return strengthened


async def run_consolidation(
    db_path: str,
    dry_run: bool = False,
    embed_fn=None,
    max_merges: int | None = None,
) -> ConsolidationReport:
    """Merge duplicate frames and strengthen episode-backed associations.

    Args:
        db_path: Path to the SQLite database.
        dry_run: If True, compute the plan but do not write.
        embed_fn: Optional async callable text -> vector. When provided (and
            Ollama is reachable), near-duplicate names are also clustered by
            embedding similarity within settings.consolidation_name_distance —
            this catches variants normalized names cannot ("mountain_in_the_wolf"
            vs "the mountain and the wolf").
        max_merges: Circuit breaker for unattended runs. When the planned
            merge count exceeds this, nothing is written (dry-run semantics)
            so a clustering bug can never mass-merge the brain overnight.

    Returns:
        ConsolidationReport describing what would happen / happened.
    """
    from assistant.backend.memory.store import MemoryStore

    report = ConsolidationReport()
    store = MemoryStore(db_path)

    if not dry_run:
        report.associations_strengthened = await strengthen_from_episodes(db_path)

    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        rows = await db.execute_fetchall(
            "SELECT id, name, type, confidence, owner_user_id, created_at "
            "FROM frames WHERE deleted_at IS NULL ORDER BY id"
        )
        frames = [
            {
                "id": r[0],
                "name": r[1],
                "type": r[2],
                "confidence": r[3],
                "owner": r[4],
                "created_at": r[5],
                "norm": normalize_frame_name(r[1]),
            }
            for r in rows
        ]
        report.scanned_frames = len(frames)

    uf = _UnionFind([f["id"] for f in frames])
    by_id = {f["id"]: f for f in frames}

    # Pass 1: exact-normalized name equality.
    by_norm: dict[tuple, int] = {}
    for frame in frames:
        if not frame["norm"]:
            continue
        key = (frame["norm"], frame["owner"])
        rep = by_norm.get(key)
        if rep is None or _types_compatible(by_id[rep]["type"], frame["type"]):
            if rep is not None:
                uf.union(rep, frame["id"])
            by_norm[key] = frame["id"]

    # Pass 2: shared identifying slot values (e.g. two frames both holding
    # title="The Mountain & The Wolf" are the same album however the extractor
    # named them). Strongest duplicate evidence available.
    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        ident_rows = await db.execute_fetchall(
            """
            SELECT s1.frame_id, s2.frame_id FROM slots s1
            JOIN slots s2 ON
                lower(trim(s1.key)) = lower(trim(s2.key))
                AND lower(trim(s1.value)) = lower(trim(s2.value))
                AND trim(s1.value) != ''
                AND s1.frame_id < s2.frame_id
            JOIN frames f1 ON f1.id = s1.frame_id
            JOIN frames f2 ON f2.id = s2.frame_id
            WHERE lower(s1.key) IN ('title', 'name', 'full_name', 'url')
              AND f1.deleted_at IS NULL AND f2.deleted_at IS NULL
              AND f1.owner_user_id IS f2.owner_user_id
            """
        )
    for id_a, id_b in ident_rows:
        fa, fb = by_id[id_a], by_id[id_b]
        if _types_compatible(fa["type"], fb["type"]):
            uf.union(id_a, id_b)

    # Pass 3: tight embedding similarity between cluster representatives.
    # Requires heavy full-token overlap OR a generic type-word name on one
    # side, so unrelated proper nouns that merely embed close (sam_altman vs
    # openai, lake_austin vs lake_travis, south_texas vs south_texas_grass_
    # farms) never union on vibes alone. Generic names (audit finding F3:
    # frames literally called "movie", "entity") always absorb specifics.
    if embed_fn is not None:
        threshold = settings.consolidation_name_distance
        embeddings = dict(
            await store.get_all_frame_embeddings(settings.embedding_model)
        )
        # Fill in any missing vectors from the live frames themselves so
        # recently created or re-named frames still participate.
        missing_frames = [f for f in frames if f["id"] not in embeddings]
        if missing_frames:
            texts = [f["norm"] or f["name"] for f in missing_frames]
            try:
                new_embeddings = await embed_fn(texts)
                for frame, emb in zip(missing_frames, new_embeddings, strict=True):
                    embeddings[frame["id"]] = emb
            except Exception as exc:
                logger.warning("Batch embedding failed for %d frames: %s", len(missing_frames), exc)
        reps = {uf.find(f["id"]): f for f in frames}
        pairs: list[tuple[float, int, int]] = []
        rep_ids = list(reps)
        for i, ra in enumerate(rep_ids):
            fa, ea = reps[ra], embeddings.get(ra)
            if ea is None:
                continue
            ta = set(normalize_frame_name(fa["name"]).split())
            for rb in rep_ids[i + 1:]:
                fb, eb = reps[rb], embeddings.get(rb)
                if eb is None:
                    continue
                if fa["owner"] != fb["owner"]:
                    continue  # owner isolation (NULL == NULL compares equal)
                if not _types_compatible(fa["type"], fb["type"]):
                    continue
                tb = set(normalize_frame_name(fb["name"]).split())
                generic_pair = (
                    fa["norm"] in _GENERIC_NAMES or fb["norm"] in _GENERIC_NAMES
                )
                if not generic_pair and _jaccard(ta, tb) < _OVERLAP_MIN:
                    continue
                pairs.append((_cosine_distance(ea, eb), ra, rb))
        pairs.sort()
        for distance, ra, rb in pairs:
            if distance >= threshold:
                break
            uf.union(ra, rb)

        # Pass 4: heavy name overlap with looser embedding gate. Catches
        # cosmetic variants the extractor minted under different spellings
        # ("mountain_in_the_wolf" vs "the mountain and the wolf") whose bare
        # names embed farther apart than true duplicates should. Aspect-word
        # guard keeps "X" apart from "X_structure"/"X_engineer" — related but
        # distinct entities.
        reps = {uf.find(f["id"]): f for f in frames}
        rep_ids = list(reps)
        for i, ra in enumerate(rep_ids):
            fa = reps[ra]
            ea = embeddings.get(ra)
            if ea is None:
                continue
            ta = set(normalize_frame_name(fa["name"]).split())
            for rb in rep_ids[i + 1:]:
                fb = reps[rb]
                if fa["owner"] != fb["owner"]:
                    continue
                if not _types_compatible(fa["type"], fb["type"]):
                    continue
                tb = set(normalize_frame_name(fb["name"]).split())
                if _jaccard(ta, tb) < _OVERLAP_MIN:
                    continue
                if (ta ^ tb) & _ASPECT_WORDS:
                    continue
                eb = embeddings.get(rb)
                if eb is None:
                    continue
                if _cosine_distance(ea, eb) < settings.consolidation_name_distance * 2.5:
                    uf.union(ra, rb)

    # Materialize clusters and plan survivor picks.
    groups: dict[int, list[dict]] = {}
    for frame in frames:
        groups.setdefault(uf.find(frame["id"]), []).append(frame)
    mergeable = [g for g in groups.values() if len(g) > 1]
    if len(mergeable) > MAX_MERGES_PER_RUN:
        report.clusters_dropped = len(mergeable) - MAX_MERGES_PER_RUN
        logger.warning(
            "Consolidation found %d merge clusters; planning first %d "
            "(%d dropped — rerun to continue)",
            len(mergeable), MAX_MERGES_PER_RUN, report.clusters_dropped,
        )
    clusters = mergeable[:MAX_MERGES_PER_RUN]
    for cluster in clusters:
        survivor = _pick_survivor(cluster)
        for member in cluster:
            if member["id"] == survivor["id"]:
                continue
            report.planned_merges.append(
                PlannedMerge(
                    survivor_id=survivor["id"],
                    survivor_name=survivor["name"],
                    loser_id=member["id"],
                    loser_name=member["name"],
                )
            )

    if dry_run:
        return report

    # Circuit breaker: unattended runs must never mass-merge the brain.
    if max_merges is not None and len(report.planned_merges) > max_merges:
        logger.warning(
            "Consolidation capped: %d merges planned exceeds max_merges=%d; "
            "writing nothing (associations already strengthened). Run "
            "`assistant db consolidate` to review the plan manually.",
            len(report.planned_merges),
            max_merges,
        )
        report.planned_merges = []
        report.capped = True
        return report

    for merge in report.planned_merges:
        try:
            counts = await _apply_merge(store, db_path, merge, embed_fn=embed_fn)
            report.applied_merges += 1
            report.slots_moved += counts["slots_moved"]
            report.edges_redirected += counts["edges_redirected"]
            report.aliases_recorded += counts["aliases_recorded"]
        except Exception as exc:
            report.errors += 1
            logger.error(
                "Consolidation merge %d -> %d failed: %s",
                merge.loser_id, merge.survivor_id, exc,
            )

    logger.info("Consolidation complete: %s", report.summary())
    return report


async def _apply_merge(
    store, db_path: str, merge: PlannedMerge, embed_fn=None
) -> None:
    """Move slots/edges from loser to survivor, record alias, tombstone loser."""
    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        # 1. Slots: copy each loser slot onto the survivor through upsert_slot
        #    so confidence bumping, belief revision, conflicts and slot_history
        #    all behave exactly like a normal re-statement of the fact.
        slot_rows = await db.execute_fetchall(
            "SELECT key, value, source_type, source_url, source_reliability, "
            "source_episode_id FROM slots WHERE frame_id = ?",
            (merge.loser_id,),
        )
        moved_slots = 0
        for key, value, source_type, source_url, source_reliability, episode_id in slot_rows:
            await store.upsert_slot(
                frame_id=merge.survivor_id,
                key=key,
                value=value,
                source_episode_id=episode_id,
                source_type=source_type,
                source_url=source_url,
                source_reliability=source_reliability,
            )
            moved_slots += 1

        # 2. Associations touching the loser: recreate against the survivor,
        #    keeping provenance and never letting a merge lower confidence.
        edge_rows = await db.execute_fetchall(
            "SELECT from_frame_id, to_frame_id, relation_type, confidence, "
            "source_type, source_url, source_reliability FROM associations "
            "WHERE from_frame_id = ? OR to_frame_id = ?",
            (merge.loser_id, merge.loser_id),
        )
        redirected_edges = 0
        for (from_id, to_id, relation_type, confidence, e_source_type,
             e_source_url, e_reliability) in edge_rows:
            new_from = merge.survivor_id if from_id == merge.loser_id else from_id
            new_to = merge.survivor_id if to_id == merge.loser_id else to_id
            if new_from == new_to:
                continue  # self-loop introduced by the redirect — drop it
            seeded = max(confidence, 0.5)
            existing = await db.execute_fetchall(
                "SELECT id, confidence FROM associations "
                "WHERE from_frame_id = ? AND to_frame_id = ? AND relation_type = ?",
                (new_from, new_to, relation_type),
            )
            if existing:
                await db.execute(
                    "UPDATE associations SET confidence = ?, "
                    "source_type = COALESCE(source_type, ?), "
                    "source_url = COALESCE(source_url, ?), "
                    "source_reliability = COALESCE(source_reliability, ?) "
                    "WHERE id = ?",
                    (
                        max(seeded, existing[0][1]),
                        e_source_type, e_source_url, e_reliability, existing[0][0],
                    ),
                )
            else:
                await db.execute(
                    "INSERT INTO associations "
                    "(from_frame_id, to_frame_id, relation_type, confidence, "
                    "essential, priority, source_type, source_url, "
                    "source_reliability) VALUES (?, ?, ?, ?, 0, 0.5, ?, ?, ?)",
                    (
                        new_from, new_to, relation_type, seeded,
                        e_source_type, e_source_url, e_reliability,
                    ),
                )
            redirected_edges += 1

        # 3. Regenerable vectors: hard-delete the loser's embeddings.
        await db.execute(
            "DELETE FROM frame_embeddings WHERE frame_id = ?",
            (merge.loser_id,),
        )
        await db.commit()

    # 4. Alias bookkeeping + tombstone (store owns these small writes).
    aliases = 0
    loser_norm = normalize_frame_name(merge.loser_name)
    if loser_norm:
        await store.rewrite_aliases_target(merge.loser_id, merge.survivor_id)
        await store.record_frame_alias(loser_norm, merge.survivor_id)
        aliases += 1

    async with aiosqlite_connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout = 15000")
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?",
            (merge.loser_id,),
        )
        await db.commit()

    # 5. Refresh the survivor's embedding: its name+slot summary just changed
    # materially, and a stale vector makes brain search miss the merged frame.
    if embed_fn is not None:
        try:
            await store.embed_frames([merge.survivor_id], embed_fn)
        except Exception as exc:
            logger.warning(
                "Embedding refresh failed for survivor %d: %s",
                merge.survivor_id, exc,
            )

    return {
        "slots_moved": moved_slots,
        "edges_redirected": redirected_edges,
        "aliases_recorded": aliases,
    }
