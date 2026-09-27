"""Multi-vector frames: entering a memory by any of the things it says.

One vector per frame assumes a frame is one idea. A frame with 24 slots is not
one idea, it is 24, and averaging them puts the vector in the middle of the pile
where it matches nothing in particular. Measured on the live index:

    query "Why Not"                        rank 453 / 500   (sim 0.554)
    query "album my wife is in"            rank 1541 / 500  (sim 0.408)
    the same frame, name-only vector       rank 1   / 500   (sim 0.848)
    the same frame, best per-slot vector   rank 4   / 500   (sim 0.666)

So slot-rich frames get one vector for the name and one per slot, and search
collapses them back to a single hit. The graph was never the problem -- `why_not`
has 39 live one-hop neighbours, all embedded, 131 at two hops -- so getting in
the front door is the whole fix.
"""

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore

MODEL = "qwen3-embedding:0.6b"


def cosine(a: list[float], b: list[float]) -> float:
    """Both sides are unit vectors, so the dot product is the cosine."""
    return sum(x * y for x, y in zip(a, b, strict=True))


async def _store(tmp_path) -> MemoryStore:
    db_path = str(tmp_path / "chunks.db")
    await init_db(db_path)
    return MemoryStore(db_path)


async def _embedder():
    """An embedder that models the one behaviour that causes the bug: averaging.

    A real embedding of a multi-line document is much closer to the mean of its
    parts than to any one of them, and that is precisely why a 24-slot frame
    stops answering for its own name. A stub that merely flags keywords cannot
    express that, so it would pass both before and after the fix -- a test that
    cannot tell the two apart is not a regression test.

    Each line contributes a unit vector over four topic axes; the text embeds as
    their normalised mean, so a document with 12 topics lands between them all
    and matches none strongly, while a single-topic chunk matches strongly.
    """
    calls: list[str] = []
    markers = ("why_not", "friend", "wolf", "label")

    def line_vector(line: str) -> list[float]:
        vec = [0.0] * len(markers)
        lowered = line.lower()
        for i, marker in enumerate(markers):
            if marker in lowered:
                vec[i] = 1.0
        if not any(vec):
            vec[0] = 0.1
        return vec

    def normalise(vec: list[float]) -> list[float]:
        norm = sum(v * v for v in vec) ** 0.5
        return [v / norm for v in vec] if norm else vec

    async def embed(text: str) -> list[float]:
        calls.append(text)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        acc = [0.0] * len(markers)
        for line in lines:
            for i, v in enumerate(line_vector(line)):
                acc[i] += v
        return normalise(acc)

    return embed, calls


async def _rich_frame(store: MemoryStore, name: str, slots: int) -> int:
    frame = await store.create_frame(name, "entity")
    for i in range(slots):
        await store.upsert_slot(frame.id, f"key_{i}", f"value_{i}")
    return frame.id


async def _chunks_of(store: MemoryStore, frame_id: int) -> list[str]:
    """The chunk texts a frame currently resolves to."""
    return store._frame_to_embed_chunks(
        await store.get_frame(frame_id), await store.get_slots_for_frame(frame_id)
    )


# --- how many vectors a frame gets ------------------------------------------


async def test_a_thin_frame_keeps_exactly_one_vector(tmp_path):
    """Most frames are small and must not be multiplied.

    Sized from the live distribution: 1811 of 1978 frames have fewer than 6
    slots. Chunking them too would multiply the index for no measured gain.
    """
    store = await _store(tmp_path)
    frame = await store.create_frame("small", "entity")
    # 5 slots, literally: the largest thin frame the live index has. Derived
    # from CHUNK_MIN_SLOTS this would insert 9998 slots whenever chunking is
    # switched off to prove discrimination, which is a slow way to learn nothing.
    for i in range(5):
        await store.upsert_slot(frame.id, f"k{i}", f"v{i}")

    chunks = await _chunks_of(store, frame.id)
    assert len(chunks) == 1
    # Unchanged from the old behaviour, so its existing vector stays valid.
    assert chunks[0].count("\n") == 5


async def test_a_rich_frame_gets_the_name_plus_one_vector_per_slot(tmp_path):
    store = await _store(tmp_path)
    slots = 8
    fid = await _rich_frame(store, "why_not", slots)

    chunks = await _chunks_of(store, fid)
    assert len(chunks) == slots + 1
    assert chunks[0] == "entity: why_not"
    for chunk in chunks[1:]:
        assert chunk.startswith("entity: why_not\n  ")


async def test_chunking_actually_stores_the_extra_vectors(tmp_path):
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 7)
    embed, _ = await _embedder()

    assert await store.embed_frames([fid], embed, MODEL) == 1
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 8


# --- the reason it exists ---------------------------------------------------


async def test_chunking_scores_higher_than_one_averaged_vector(tmp_path):
    """The measurement, as an assertion.

    Same frame, same query, two index shapes. This is the whole reason the
    change exists, and it is the assertion that fails on the old code: with one
    vector per frame the query scores `averaged` and the frame does not surface,
    because the query is about one of 12 slots and the vector is their mean.
    """
    store = await _store(tmp_path)
    frame = await store.create_frame("why_not", "entity")
    # Twelve slots, exactly one of which is what the query will ask about --
    # the shape of every frame this was measured on.
    for i in range(12):
        value = "Friend Music Records" if i == 3 else f"filler_{i}"
        await store.upsert_slot(frame.id, f"key_{i}", value)
    frame_obj = await store.get_frame(frame.id)
    slots = await store.get_slots_for_frame(frame.id)
    embed, _ = await _embedder()

    query = await embed("Friend Music Records")
    # What the old code stored: the whole frame as one document.
    averaged = await embed(store._frame_to_embed_text(frame_obj, slots))
    # What the new code stores, and what a search picks: the closest chunk.
    vectors = [
        await embed(c) for c in store._frame_to_embed_chunks(frame_obj, slots)
    ]
    best = max(vectors, key=lambda v: cosine(query, v))

    assert cosine(query, best) > 1.5 * cosine(query, averaged)


async def test_a_query_about_one_slot_reaches_the_frame(tmp_path):
    """The regression: a slot-rich frame must answer for its own contents.

    Under one averaged vector this frame does not appear at all -- it sat at
    rank 1541 of 500 candidates for exactly this phrasing.
    """
    store = await _store(tmp_path)
    thin = await store.create_frame("why_not_page", "entity")
    await store.upsert_slot(thin.id, "name", "Why Not page")
    rich = await _rich_frame(store, "why_not", 12)
    for key, value in (("label_name", "Friend Music Records"), ("band", "why not the band")):
        await store.upsert_slot(rich, key, value)
    embed, _ = await _embedder()
    await store.embed_frames([thin.id, rich], embed, MODEL)

    # A query about one slot of the rich frame only.
    query = await embed("entity: why_not\n  label_name = Friend Music Records")
    hits = await store.search_similar_frames(query, None, MODEL, limit=5, min_distance=0.1)

    assert rich in [f.id for f, _, _ in hits]


# --- chunks must not crowd out other memories -------------------------------


async def test_a_chunked_frame_occupies_exactly_one_result_slot(tmp_path):
    """One memory, one slot in the answer.

    With the reduction done in Python after LIMIT, a 20-slot frame would take 20
    of the 10 rows and every other memory would vanish from the reply.
    """
    store = await _store(tmp_path)
    hog = await _rich_frame(store, "why_not", 20)
    others = []
    for i in range(5):
        other = await store.create_frame(f"other_{i}", "entity")
        await store.upsert_slot(other.id, "wolf", f"track_{i}")
        others.append(other.id)

    embed, _ = await _embedder()
    await store.embed_frames([hog, *others], embed, MODEL)

    query = await embed("entity: why_not")
    hits = await store.search_similar_frames(query, None, MODEL, limit=3, min_distance=1.01)

    assert len(hits) == 3
    assert len({f.id for f, _, _ in hits}) == 3  # no repeats of the same frame


async def test_a_frame_reports_its_best_chunk_not_its_worst(tmp_path):
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 9)
    await store.upsert_slot(fid, "label_name", "Friend Music Records")
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)

    exact = await embed("entity: why_not")
    hits = await store.search_similar_frames(exact, None, MODEL, limit=5, min_distance=1.01)
    sims = [sim for f, _, sim in hits if f.id == fid]
    assert len(sims) == 1
    # chunk 0 is the bare name and scores 0.1; the slot chunks score higher.
    assert sims[0] > 0.1


# --- changing slots must not leave ghosts behind ----------------------------


async def test_a_deleted_slot_loses_its_chunk(tmp_path):
    """Re-embedding replaces the whole set, so nothing stale survives.

    An upsert per chunk would keep answering questions about slots the frame no
    longer has -- a memory that denies having been edited.
    """
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 8)
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 9

    async with store._connect() as db:
        await db.execute("DELETE FROM slots WHERE key = 'key_0'")
        await db.commit()

    await store.embed_frames([fid], embed, MODEL)
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 8


async def test_re_embedding_does_not_accumulate_chunks(tmp_path):
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 7)
    embed, _ = await _embedder()
    for _ in range(4):
        await store.embed_frames([fid], embed, MODEL)
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 8


# --- the other readers ------------------------------------------------------


async def test_clearing_a_frame_removes_every_chunk(tmp_path):
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 7)
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)

    await store.clear_frame_embedding(fid, MODEL)
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 0


async def test_get_all_frame_embeddings_can_ask_for_the_primary_only(tmp_path):
    """Consolidation compares frames to each other, so it wants identity vectors.

    Without the flag, dict() over the full list keeps whichever chunk came last
    and uses an arbitrary slot as if it were the frame.
    """
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 7)
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)

    assert len(await store.get_all_frame_embeddings(MODEL)) == 8
    assert len(await store.get_all_frame_embeddings(MODEL, primary_only=True)) == 1


async def test_the_owner_filter_and_the_threshold_are_not_swapped(tmp_path):
    """Guards the placeholder order of the grouped search.

    `user_id` binds in WHERE and `min_distance` in HAVING, so the two `?` are
    easy to transpose -- and transposing them does not raise. It just quietly
    searches as if the user id were a cosine distance, which returns nothing and
    looks exactly like an empty memory. Both are exercised here at once: a
    concrete owner *and* a threshold that actually excludes.
    """
    store = await _store(tmp_path)
    owner = await store.create_user("Owner")
    stranger = await store.create_user("Stranger")
    mine = await store.create_frame("why_not", "entity", owner_user_id=owner.id)
    await store.upsert_slot(mine.id, "label", "Friend Music Records")
    theirs = await store.create_frame("other", "entity", owner_user_id=stranger.id)
    await store.upsert_slot(theirs.id, "label", "Unrelated")

    embed, _ = await _embedder()
    await store.embed_frames([mine.id, theirs.id], embed, MODEL)
    # Deliberately not the frame's own text: an exact match has cosine distance
    # 0.0, which no positive threshold can exclude, so the threshold would never
    # be exercised.
    query = await embed("Friend Music Records")

    # Owner filter applied (the stranger excluded), threshold loose enough to
    # admit the hit.
    assert [f.id for f, _, _ in await store.search_similar_frames(
        query, owner.id, MODEL, 10, 0.7
    )] == [mine.id]
    # Same query, threshold tight enough to exclude it (the hit sits at cosine
    # distance 0.423). If the two were transposed the owner filter would match
    # nothing and the threshold would admit everything, returning both frames
    # rather than none.
    assert await store.search_similar_frames(query, owner.id, MODEL, 10, 0.3) == []


async def test_get_frame_embedding_returns_the_primary_chunk(tmp_path):
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 7)
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)

    primary = await store.get_frame_embedding(fid, MODEL)
    assert primary is not None
    # Chunk 0 is the frame's name and nothing else: it matches on the frame's
    # own name, and carries no slot's marker (the "wolf" axis stays empty).
    assert primary[0] == pytest.approx(1.0)
    assert primary[2] == pytest.approx(0.0)


# --- does the agent keep this up on its own? --------------------------------
#
# The user's question, answered in tests. New and extracted-on frames are
# embedded by their write path, so they need no migration. A frame whose slots
# change *elsewhere* -- the correction pipeline writes slots with no re-embed,
# and the backend has 28 upsert_slot call sites -- needs something to notice.
# That's this backstop.


async def test_a_healthy_index_costs_nothing_to_check(tmp_path):
    """The guard that matters most: a no-op on a synced index.

    Runs twice daily. If this re-embedded every frame, the chunking change would
    quietly turn housekeeping into ~2700 local embed calls every 12 hours, which
    is the cost the whole exercise was supposed to avoid.
    """
    store = await _store(tmp_path)
    thin = await store.create_frame("thin", "entity")
    await store.upsert_slot(thin.id, "k", "v")
    fat = await _rich_frame(store, "why_not", 8)
    embed, calls = await _embedder()
    await store.embed_frames([thin.id, fat], embed, MODEL)
    calls.clear()

    assert await store.embed_stale_frames(embed, MODEL) == 0
    assert calls == []


async def test_a_frame_that_grows_past_the_threshold_gets_picked_up(tmp_path):
    """A frame that was thin, then wasn't.

    The write path embedded one vector when it had two slots. Six slots later it
    is a slot-rich frame being searched as a single averaged vector -- the
    original bug, re-entering through the back door.
    """
    store = await _store(tmp_path)
    frame = await store.create_frame("why_not", "entity")
    await store.upsert_slot(frame.id, "k", "v")
    embed, _ = await _embedder()
    await store.embed_frames([frame.id], embed, MODEL)
    assert await store.count_frame_embedding_chunks(frame.id, MODEL) == 1

    for i in range(1, 7):
        await store.upsert_slot(frame.id, f"key_{i}", "Friend Music Records")

    assert await store.embed_stale_frames(embed, MODEL) == 1
    assert await store.count_frame_embedding_chunks(frame.id, MODEL) == 8


async def test_a_corrected_slot_replaces_what_the_frame_answers(tmp_path):
    """A value edit, which the count-based backstop provably cannot see.

    The slot count is unchanged, so `embed_stale_frames` reports nothing stale
    and does nothing. This is why `apply_correction` re-embeds at the write path
    instead of trusting housekeeping: a correction is the one edit whose text
    changes while the shape does not, and it is the edit a user is most likely
    to make and most upset to find ignored.
    """
    from assistant.backend.pipeline.extractor import CorrectionResult, apply_correction

    store = await _store(tmp_path)
    frame = await store.create_frame("why_not", "entity")
    await store.upsert_slot(frame.id, "label", "Nonesuch Records")
    for i in range(6):
        await store.upsert_slot(frame.id, f"key_{i}", "filler")
    embed, _ = await _embedder()
    await store.embed_frames([frame.id], embed, MODEL)

    old = await embed("entity: why_not\n  label = Nonesuch Records")
    new = await embed("entity: why_not\n  label = Friend Music Records")

    async def ranks(query: list[float]) -> list[int]:
        return [
            f.id
            for f, _, _ in await store.search_similar_frames(
                query, None, MODEL, 5, 0.05
            )
        ]

    # Before: the frame answers to the value the user is about to reject, and
    # is unfindable by the value they give instead.
    assert frame.id in await ranks(old)
    assert frame.id not in await ranks(new)

    # The housekeeping check cannot see this one, and correctly does nothing.
    assert await store.embed_stale_frames(embed, MODEL) == 0
    assert frame.id in await ranks(old)

    # The write path can.
    await apply_correction(
        CorrectionResult(
            frame_name="why_not", slot_key="label", new_value="Friend Music Records"
        ),
        store,
        embed_fn=embed,
        embedding_model=MODEL,
    )

    assert frame.id in await ranks(new)
    assert frame.id not in await ranks(old)


async def test_a_frame_that_loses_slots_is_picked_up(tmp_path):
    """The other direction: a shrunk frame keeps vectors for slots it dropped."""
    store = await _store(tmp_path)
    fid = await _rich_frame(store, "why_not", 8)
    embed, _ = await _embedder()
    await store.embed_frames([fid], embed, MODEL)

    async with store._connect() as db:
        await db.execute("DELETE FROM slots WHERE frame_id = ?", (fid,))
        await db.commit()

    assert await store.embed_stale_frames(embed, MODEL) == 1
    assert await store.count_frame_embedding_chunks(fid, MODEL) == 1

