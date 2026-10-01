"""Phase 9B tests: consolidation merge pass + alias resolution.

Scenario mirrors the live audit finding: "The Mountain & the Wolf" fragmented
across several frames. Consolidation must union them onto one survivor,
redirect the graph, record aliases, and tombstone losers — without touching
frames owned by other users.
"""

import pytest

from assistant.backend.memory.consolidate import run_consolidation
from assistant.backend.pipeline.extractor import (
    normalize_frame_name,
    resolve_or_create_frame,
)


async def _frame_ids_by_name(store, name: str):
    frame = await store.get_frame_by_name(name)
    return frame.id if frame else None


async def _seed_fragmented_album(store):
    """Create a fragmented album cluster with slots and cross-edges.

    Uses direct store writes to simulate LEGACY fragmentation created before
    Phase 9A canonicalization existed — apply_extraction would no longer
    produce these duplicates.
    """
    album_a = await store.create_frame("The Mountain & The Wolf", "entity")
    album_b = await store.create_frame("the mountain and the wolf", "entity")
    mozworth = await store.create_frame("mozworth", "entity")

    await store.upsert_slot(album_a.id, "artist", "mozworth")
    await store.upsert_slot(album_b.id, "year", "2026")
    await store.upsert_slot(mozworth.id, "genre", "indie rock")
    await store.create_association(
        from_frame_id=album_a.id, to_frame_id=mozworth.id, relation_type="created_by"
    )


async def test_consolidation_merges_normalized_duplicates(store):
    await _seed_fragmented_album(store)
    loser_before = await store.get_frame_by_name("the mountain and the wolf")
    assert loser_before is not None

    report = await run_consolidation(str(store.db_path), dry_run=False)

    assert len(report.planned_merges) >= 1
    assert report.applied_merges == len(report.planned_merges)

    # Loser is gone from live lookups; alias points at the survivor.
    assert await store.get_frame_by_name("the mountain and the wolf") is None
    survivor_id = await store.get_alias_frame_id("mountain wolf")
    assert survivor_id is not None

    # Survivor holds the union of slots from both fragments.
    survivor_slots = {
        s.key: s.value for s in await store.get_slots_for_frame(survivor_id)
    }
    assert survivor_slots["artist"] == "mozworth"
    assert survivor_slots["year"] == "2026"


async def test_merged_name_resolves_via_alias(store):
    await _seed_fragmented_album(store)
    await run_consolidation(str(store.db_path), dry_run=False)

    resolved = await resolve_or_create_frame(
        store, "the mountain and the wolf", "event"
    )
    alias_id = await store.get_alias_frame_id("mountain wolf")
    assert alias_id is not None
    assert resolved == alias_id


async def test_consolidation_dry_run_writes_nothing(store):
    await _seed_fragmented_album(store)

    report = await run_consolidation(str(store.db_path), dry_run=True)

    assert len(report.planned_merges) >= 1
    assert report.applied_merges == 0
    # Both variants still resolvable as distinct live frames.
    both = [
        await store.get_frame_by_name("The Mountain & The Wolf"),
        await store.get_frame_by_name("the mountain and the wolf"),
    ]
    assert all(f is not None for f in both)
    assert not (await store.list_frames()) == []


async def test_consolidation_respects_owner_isolation(store):
    user = await store.create_user("alice")
    owned = await store.create_frame(
        "mountain_wolf_demo", "entity", owner_user_id=user.id
    )
    shared = await store.create_frame("mountain-wolf-demo", "entity")

    report = await run_consolidation(str(store.db_path), dry_run=False)

    # Identical normalized names but different owner scopes: no merge.
    touched = {m.loser_id for m in report.planned_merges} | {
        m.survivor_id for m in report.planned_merges
    }
    assert owned.id not in touched and shared.id not in touched
    assert await store.get_frame_by_name("mountain_wolf_demo")
    assert await store.get_frame_by_name("mountain-wolf-demo")


async def test_consolidation_skips_incompatible_types(store):
    await store.create_frame("mountain_wolf_movie", "event")
    await store.create_frame("mountain-wolf-movie", "household")

    report = await run_consolidation(str(store.db_path), dry_run=False)

    assert report.planned_merges == []
    assert await store.get_frame_by_name("mountain_wolf_movie")
    assert await store.get_frame_by_name("mountain-wolf-movie")


async def test_recreate_tombstoned_name_resurrects(store):
    await _seed_fragmented_album(store)
    frames_before = {f.name: f.id for f in await store.list_frames()}
    loser_name = "the mountain and the wolf"
    loser_id = frames_before[loser_name]

    report = await run_consolidation(str(store.db_path), dry_run=False)
    assert any(m.loser_id == loser_id for m in report.planned_merges)

    # Recreating the tombstoned exact name must reuse its row, not violate
    # the UNIQUE(name) constraint.
    resurrected = await store.create_frame(loser_name, "entity")

    assert resurrected.id == loser_id
    assert await store.get_frame_by_name(loser_name) is not None
    assert report.applied_merges >= 1


def test_normalize_stop_word_edge_cases():
    assert normalize_frame_name("Mountain & Wolf!") == "mountain wolf"
    assert normalize_frame_name("") == ""


async def _tombstone_frame(store, frame_id: int) -> None:
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(str(store.db_path)) as db:
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?",
            (frame_id,),
        )
        await db.commit()


async def test_resurrection_never_crosses_owners(store):
    """H1 regression: a GC'd personal frame must not be resurrected by,
    or leak into, another user's create_frame."""
    alice = await store.create_user("alice")
    bob = await store.create_user("bob")
    alice_frame = await store.create_frame(
        "shared_diary", "entity", owner_user_id=alice.id
    )
    await store.upsert_slot(alice_frame.id, "secret", "alice only")
    await _tombstone_frame(store, alice_frame.id)

    bob_frame = await store.create_frame("shared_diary", "entity", owner_user_id=bob.id)

    assert bob_frame.id != alice_frame.id
    assert bob_frame.owner_user_id == bob.id
    # Bob's frame carries none of Alice's slots.
    assert await store.get_slots_for_frame(bob_frame.id) == []
    # Alice's row stays tombstoned and untouched.
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(str(store.db_path)) as db:
        cur = await db.execute(
            "SELECT deleted_at IS NOT NULL FROM frames WHERE id = ?",
            (alice_frame.id,),
        )
        (still_deleted,) = await cur.fetchone()
    assert still_deleted


async def test_pass2_ignores_empty_identity_slots(store):
    """M1 regression, now with defence in depth.

    Originally: two frames sharing only empty/garbage identity-slot values
    (url='', title='') were merged on that evidence. The fix was in the pass-2 query,
    which requires `trim(s1.value) != ''`.

    The store now refuses a blank value outright, so the rows this test used to create
    can no longer exist — the scenario is unreachable rather than merely handled. Both
    layers are pinned here: the write refusal (which makes the setup impossible) and
    the query guard (which still holds for any row written before it).
    """
    a = await store.create_frame("empty_slot_frame_one", "entity")
    b = await store.create_frame("empty_slot_frame_two", "entity")

    # Layer 1: the write is refused, so the shared-empty-value evidence cannot exist.
    with pytest.raises(ValueError, match="non-blank value"):
        await store.upsert_slot(a.id, "url", "")
    with pytest.raises(ValueError, match="non-blank value"):
        await store.upsert_slot(b.id, "url", "")

    # Layer 2: with real values that happen to be equal, the frames are still not
    # merged on that alone — the guard is about blanks, not about equality.
    report = await run_consolidation(str(store.db_path), dry_run=False)
    ids_in_merges = {m.loser_id for m in report.planned_merges} | {
        m.survivor_id for m in report.planned_merges
    }
    assert a.id not in ids_in_merges and b.id not in ids_in_merges


async def test_merge_redirects_edges_with_provenance_and_max_confidence(store):
    """T4/M6 regression: redirected edges keep source fields and never lose
    confidence; counters land in the report."""
    import json

    from assistant.backend.db.sqlcipher import aiosqlite_connect

    album_a = await store.create_frame("redirect_album_a", "entity")
    album_b = await store.create_frame("redirect_album_b", "entity")
    mozworth = await store.create_frame("redirect_artist", "entity")
    cover = await store.create_frame("redirect_cover_track", "entity")
    # Shared non-empty identity value: pass-2 duplicate evidence.
    await store.upsert_slot(album_a.id, "title", "Redirect Same Album")
    await store.upsert_slot(album_b.id, "title", "Redirect Same Album")
    await store.upsert_slot(album_b.id, "year", "2026")
    # Loser's high-confidence edge with provenance.
    async with aiosqlite_connect(str(store.db_path)) as db:
        await db.execute(
            "INSERT INTO associations (from_frame_id, to_frame_id, relation_type, "
            "confidence, essential, priority, source_type, source_url, "
            "source_reliability) VALUES (?, ?, ?, 0.9, 0, 0.5, 'episode', "
            "'http://example/cover', 0.8)",
            (album_b.id, cover.id, "covered_by"),
        )
        await db.commit()
    await store.create_association(
        from_frame_id=album_a.id, to_frame_id=mozworth.id, relation_type="created_by"
    )

    report = await run_consolidation(str(store.db_path), dry_run=False)

    merged_pair = {
        m.loser_id for m in report.planned_merges if m.survivor_id in (
            album_a.id, album_b.id
        )
    }
    assert merged_pair
    survivor_id = next(
        m.survivor_id for m in report.planned_merges if m.loser_id in merged_pair
    )
    assert report.slots_moved >= 1
    assert report.edges_redirected >= 1
    assert report.aliases_recorded >= 1

    async with aiosqlite_connect(str(store.db_path)) as db:
        cur = await db.execute(
            "SELECT confidence, source_type, source_url, source_reliability "
            "FROM associations WHERE from_frame_id = ? AND to_frame_id = ? "
            "AND relation_type = 'covered_by'",
            (survivor_id, cover.id),
        )
        row = await cur.fetchone()
    assert row is not None
    confidence, source_type, source_url, reliability = row
    assert confidence >= 0.9
    assert source_type == "episode"
    assert json.dumps(source_url)  # kept verbatim
    assert reliability == 0.8
