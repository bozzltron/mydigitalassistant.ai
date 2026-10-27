"""Consolidation precision: no name-subset merges, and the cap applies.

Regression for the 2026-10-26 memory audit (R1). The live dry-run planned 74
merges, ~50+ of them distinct entities — a city into a sports team, a topic into a
publisher, a person into a podcast, a person into a band.

Two attempts were measured:

1. *Shared slot evidence* (city has `state`, team has `league`) removed the named
   pairs but admitted others: `uk`/`us` private-AI metrics share `year`,
   `jurassic_park`/`jurassic_park_1` share `title`, `pope_leo_xiv`/`..._surgery`
   share `location` — while the *true* pair `treaty_oak`/`treaty_oak_austin` also
   shares `location`. No structural key separates true from false.
2. Therefore a lexical merge now requires **identical token sets** (word order,
   avoidance of stop words) — exact-normalized names (Pass 1) and shared
   `title`/`url` (Pass 2) already carry identity. A subset is a guess.

The scheduler also applies the capped subset instead of treating `capped` as
failure (`74 merges > cap 10` skips were deadlocking every run).
"""

from assistant.backend.memory.consolidate import _pick_survivor, run_consolidation
from assistant.backend.scheduler.runner import _run_consolidation


async def _embed(texts):
    # Identical vectors: the embedding gate admits every pair, so these tests
    # exercise the name rules rather than cosine distance.
    return [[1.0, 0.0, 0.0] for _ in texts]


async def _brain(tmp_path):
    from assistant.backend.db.schema import init_db
    from assistant.backend.memory.store import MemoryStore

    db_path = str(tmp_path / "brain.db")
    await init_db(db_path)
    return db_path, MemoryStore(db_path)


async def _frame(store, name, slots: dict[str, str] | None = None):
    frame = await store.create_frame(name, "entity")
    for key, value in (slots or {}).items():
        await store.upsert_slot(frame.id, key, value)
    return frame


def _names(report):
    return {(m.survivor_name, m.loser_name) for m in report.planned_merges}


async def test_city_does_not_merge_into_sports_team(tmp_path):
    """`{los, angeles}` is a prefix of `{los, angeles, rams}` — Jaccard 2/3."""
    db_path, store = await _brain(tmp_path)
    await _frame(store, "los_angeles", {"state": "california"})
    await _frame(store, "los_angeles_rams", {"league": "nfl"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)

    assert report.planned_merges == [], _names(report)


async def test_person_does_not_merge_into_podcast(tmp_path):
    db_path, store = await _brain(tmp_path)
    await _frame(store, "jason_lee")
    await _frame(store, "the_jason_lee_podcast", {"type": "podcast"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert report.planned_merges == [], _names(report)


async def test_topic_does_not_absorb_publisher(tmp_path):
    db_path, store = await _brain(tmp_path)
    await _frame(store, "world_news", {"type": "topic"})
    await _frame(store, "bbc_news_world", {"type": "news agency"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert report.planned_merges == [], _names(report)


async def test_metric_pairs_sharing_a_year_do_not_merge(tmp_path):
    """The slot-evidence attempt admitted this pair through `year`."""
    db_path, store = await _brain(tmp_path)
    await _frame(store, "uk_private_ai_investment", {"year": "2026"})
    await _frame(store, "us_private_ai_investment", {"year": "2026"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert report.planned_merges == [], _names(report)


async def test_word_order_variants_still_merge(tmp_path):
    db_path, store = await _brain(tmp_path)
    await _frame(store, "wolf mountain")
    await _frame(store, "mountain wolf")

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert len(report.planned_merges) == 1, _names(report)


async def test_exact_normalized_names_still_merge(tmp_path):
    db_path, store = await _brain(tmp_path)
    await _frame(store, "The Mountain & The Wolf")
    await _frame(store, "the mountain and the wolf")

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert len(report.planned_merges) == 1, _names(report)


async def test_shared_url_still_merges(tmp_path):
    """Pass 2 identity evidence: a URL addresses one document."""
    db_path, store = await _brain(tmp_path)
    await _frame(store, "some_press_release", {"url": "https://example.test/a"})
    await _frame(store, "band_announces_tour", {"url": "https://example.test/a"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert len(report.planned_merges) == 1, _names(report)


async def test_shared_title_does_not_merge(tmp_path):
    """A song and its album, a film and its sequel, share a title."""
    db_path, store = await _brain(tmp_path)
    await _frame(store, "why_not", {"title": "Why Not"})
    await _frame(store, "single_release", {"title": "Why Not"})

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert report.planned_merges == [], _names(report)


async def test_version_tokens_do_not_merge(tmp_path):
    """`llama 3` and `llama 3 3` share a set but not a multiset."""
    db_path, store = await _brain(tmp_path)
    await _frame(store, "llama_3")
    await _frame(store, "llama_3.3")

    report = await run_consolidation(db_path, dry_run=True, embed_fn=_embed)
    assert report.planned_merges == [], _names(report)


def test_survivor_is_the_richer_frame():
    hollow = {
        "id": 1, "name": "world_news", "norm": "world news", "type": "entity",
        "confidence": 0.9, "created_at": "2026-01-01", "slots": {},
    }
    rich = {
        "id": 2, "name": "bbc_news", "norm": "bbc news", "type": "entity",
        "confidence": 0.5, "created_at": "2026-02-01",
        "slots": {"type": "news agency"},
    }
    assert _pick_survivor([hollow, rich])["id"] == rich["id"]


async def test_scheduler_applies_a_capped_plan_instead_of_skipping(tmp_path):
    """12 identical pairs against a cap of 10 must apply 10, not skip the run."""
    from assistant.backend.config import settings
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    db_path, store = await _brain(tmp_path)
    for i in range(12):
        await _frame(store, f"item {i} alpha")
        await _frame(store, f"alpha item {i}")

    class _Orchestrator:
        def embed_fn(self):
            return _embed

    await _run_consolidation(store, _Orchestrator(), has_fresh_backup=True)

    async with aiosqlite_connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) FROM frames WHERE deleted_at IS NOT NULL"
        )
    # The old code logged `12 merges > cap 10` and returned; 0 were tombstoned.
    assert rows[0][0] == settings.consolidation_max_merges_per_run
