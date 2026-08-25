"""Brain-search fixes: tokenized lexical matching + semantic/keyword blend.

Locks:
- Multi-word queries match partial frame names ('mountain wolf' finds
  'mountain_in_the_wolf' and 'The Mountain & The Wolf') — the old whole-query
  LIKE never did.
- Coverage ranking: frames matching more query tokens rank first.
- Tombstoned / GC'd frames are excluded.
- Endpoint blend: lexical matches carry similarity 0.55+0.4·coverage so an
  exact topic hit outranks the flat ~0.55 fuzzy band.
"""

import asyncio

import pytest

from assistant.backend.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    from assistant.backend.db.schema import init_db

    db_path = str(tmp_path / "brain.db")

    async def seed():
        await init_db(db_path)
        s = MemoryStore(db_path)
        user = await s.create_user("alice")
        album = await s.create_frame("mountain_in_the_wolf", "entity")
        await s.create_frame("The Mountain & The Wolf", "concept")  # variant name
        await s.create_frame("germany", "entity")
        await s.upsert_slot(album.id, "title", "The Mountain & The Wolf")
        return s, user.id, album.id, db_path

    return asyncio.run(seed())


def _run(coro):
    return asyncio.run(coro)


def test_multiword_query_finds_partial_names(store):
    s, _uid, album_id, _db = store
    results = _run(s.search_frames_lexical("mountain wolf", limit=10))
    ids = [f.id for f, _ in results]
    assert album_id in ids


def test_coverage_ranks_full_matches_first(store):
    s, _uid, album_id, _db = store
    results = _run(s.search_frames_lexical("mountain wolf", limit=10))
    # Album (name has both tokens) must rank first; germany-like zero hits
    # must not appear at all.
    names = [f.name for f, _strength in results]
    assert "germany" not in names
    assert results[0][0].id == album_id
    assert results[0][1] == 1.0


def test_endpoint_uses_blend_helper_for_lexical_matches():
    """The endpoint blends lexical matches via lexical_blend_similarity;
    lock the contract the /memory/search ranking depends on."""
    from assistant.backend.memory.store import lexical_blend_similarity

    assert lexical_blend_similarity(1.0) == 0.95   # exact topic hit
    assert lexical_blend_similarity(0.5) == 0.75   # half coverage
    assert lexical_blend_similarity(0.25) == 0.65
    # Half-coverage already outranks the ~0.55-0.56 fuzzy noise band that
    # vector search returns for unrelated frames.
    assert lexical_blend_similarity(0.5) > 0.56


async def _tombstone(db_path, frame_id):
    from assistant.backend.db.sqlcipher import aiosqlite_connect

    async with aiosqlite_connect(db_path) as db:
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?",
            (frame_id,),
        )
        await db.commit()


def test_tombstoned_frames_excluded(store):
    s, _uid, album_id, db_path = store
    _run(_tombstone(db_path, album_id))
    results = _run(s.search_frames_lexical("mountain wolf", limit=10))
    assert all(f.id != album_id for f, _ in results)


def test_single_token_query_still_works(store):
    s, _uid, _album, _db = store
    results = _run(s.search_frames_lexical("germany", limit=5))
    assert any(f.name == "germany" for f, _ in results)

    # Legacy wrapper still returns plain frames.
    frames = _run(s.search_frames_keyword("germany", limit=5))
    assert any(f.name == "germany" for f in frames)


def test_merge_upgrades_weak_semantic_with_strong_lexical():
    """Regression: #276 sat in the semantic candidate set at 0.309 while its
    exact-name lexical score was 0.95 — the old dedupe kept the weak score
    and brain search missed the album entirely."""
    from assistant.backend.memory.store import merge_match_scores

    semantic = {276: 0.309, 338: 0.56, 112: 0.549}
    merged = merge_match_scores(semantic, [(276, 1.0), (200, 0.5)])

    assert merged[276] == 0.95      # upgraded to full-coverage lexical
    assert merged[200] == 0.75      # new lexical-only entry
    assert merged[338] == 0.56      # untouched noise
    assert merged[112] == 0.549

    # Stronger semantic evidence is never downgraded by lexical.
    again = merge_match_scores({9: 0.9}, [(9, 0.5)])
    assert again[9] == 0.9
