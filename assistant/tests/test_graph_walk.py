"""Regression tests for the association graph walk.

The graph walk's relevance gate was unreachable by construction, so it admitted
zero frames in production: 0 of 877 live in-scope edges across 12 probe queries.
These tests pin the fix. They deliberately use the *default* association and
frame confidences rather than the 1.0 used by the older `test_graph_walk_in_isolation`
— that older test only ever exercised the one configuration in which the old
product could clear the gate, which is why it passed while the walk was dead.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from assistant.backend.memory.retrieval import Retriever

UNIT = [1.0, 0.0, 0.0]
ORTHOGONAL = [0.0, 1.0, 0.0]


def _mock_llm(embedding=UNIT):
    mock = AsyncMock()
    mock.embed.return_value = MagicMock(embedding=embedding)
    return mock


async def test_walk_follows_edges_at_default_confidence(store):
    """At default confidence (0.5), the old gate could never fire.

    Old score: 1.0 * 0.5 (decay) * 0.5 (assoc) * 0.5 (prio) * 0.5 * 0.5
             = 0.03125, against a 0.3 gate.
    """
    a = await store.create_frame("a", "entity")
    b = await store.create_frame("b", "entity")

    # create_association defaults: confidence=0.5, priority=0.5
    assoc = await store.create_association(a.id, b.id, "related_to")
    assert assoc.confidence == 0.5
    assert assoc.priority == 0.5

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1)

    assert [fid for fid, _, _ in found] == [b.id]
    assert found[0][2] == "graph_hop_1"


async def test_walk_crosses_two_hops_at_default_confidence(store):
    """Hop 2 must not collapse.

    The frontier carries depth (relevance x decay), not the full product. If it
    carried the product, hop 2 would score 0.5^5 * 0.5^4 ≈ 0.00098 and the
    second hop would starve too.
    """
    a = await store.create_frame("a", "entity")
    b = await store.create_frame("b", "entity")
    c = await store.create_frame("c", "entity")
    await store.create_association(a.id, b.id, "related_to")
    await store.create_association(b.id, c.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1)
    by_id = {fid: (score, src) for fid, score, src in found}

    assert set(by_id) == {b.id, c.id}
    assert by_id[b.id][1] == "graph_hop_1"
    assert by_id[c.id][1] == "graph_hop_2"
    # Shorter path outranks longer.
    assert by_id[b.id][0] > by_id[c.id][0]


async def test_hop_limit_still_bounds_the_walk(store):
    """Bounded traversal, not flooding: 3 hops must not reach the 3rd frame."""
    frames = [await store.create_frame(f"f{i}", "entity") for i in range(4)]
    for left, right in zip(frames, frames[1:], strict=False):
        await store.create_association(left.id, right.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)

    two = await retriever._graph_walk(frames[0].id, UNIT, 2, 0.5, user_id=1)
    assert {fid for fid, _, _ in two} == {frames[1].id, frames[2].id}

    three = await retriever._graph_walk(frames[0].id, UNIT, 3, 0.5, user_id=1)
    assert {fid for fid, _, _ in three} == {frames[1].id, frames[2].id, frames[3].id}


async def test_walk_honours_limit_and_keeps_best(store):
    """limit= keeps the highest-scoring neighbours, not the first found."""
    a = await store.create_frame("a", "entity")
    weak = await store.create_frame("weak", "entity")
    strong = await store.create_frame("strong", "entity")

    await store.create_association(a.id, weak.id, "related_to", confidence=0.5)
    await store.create_association(a.id, strong.id, "related_to", confidence=0.9)

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1, limit=1)

    assert [fid for fid, _, _ in found] == [strong.id]


async def test_walk_does_not_visit_a_node_twice(store):
    """Diamond a→b, a→c, b→d, c→d: d must appear once."""
    a, b, c, d = [await store.create_frame(n, "entity") for n in "abcd"]
    await store.create_association(a.id, b.id, "related_to")
    await store.create_association(a.id, c.id, "related_to")
    await store.create_association(b.id, d.id, "related_to")
    await store.create_association(c.id, d.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1)
    ids = [fid for fid, _, _ in found]

    assert sorted(ids) == sorted([b.id, c.id, d.id])
    assert len(ids) == len(set(ids))


async def test_excludes_tombstoned_neighbor(store):
    """GC-tombstoned frames keep their edges but must never re-enter context."""
    a = await store.create_frame("a", "entity")
    b = await store.create_frame("b", "entity")
    await store.create_association(a.id, b.id, "related_to")
    # GC retires frames by stamping deleted_at; there is no store method for it
    # outside the gc/merge paths, so do it the way they do.
    async with store._connect() as db:
        await db.execute(
            "UPDATE frames SET deleted_at = datetime('now') WHERE id = ?", (b.id,)
        )
        await db.commit()

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1)
    assert b.id not in {fid for fid, _, _ in found}


async def test_excludes_other_users_neighbor(store):
    alice = await store.create_user("alice")
    bob = await store.create_user("bob")
    a = await store.create_frame("a", "entity", owner_user_id=alice.id)
    b = await store.create_frame("b", "entity", owner_user_id=bob.id)
    await store.create_association(a.id, b.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=alice.id)
    assert b.id not in {fid for fid, _, _ in found}


async def test_includes_shared_neighbor(store):
    alice = await store.create_user("alice")
    a = await store.create_frame("a", "entity", owner_user_id=alice.id)
    shared = await store.create_frame("shared", "entity", owner_user_id=None)
    await store.create_association(a.id, shared.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=alice.id)
    assert shared.id in {fid for fid, _, _ in found}


async def test_excludes_edge_below_trust_floor(store):
    """The floor is a guard: an edge written below it is not followed."""
    a = await store.create_frame("a", "entity")
    b = await store.create_frame("b", "entity")
    await store.create_association(a.id, b.id, "related_to", confidence=0.4)

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3)
    found = await retriever._graph_walk(a.id, UNIT, 2, 0.5, user_id=1)
    assert b.id not in {fid for fid, _, _ in found}


async def test_retrieve_returns_more_than_top_k_direct(store):
    """The core production symptom: retrieval always returned exactly 3 frames."""
    alice = await store.create_user("alice")

    seed = await store.create_frame("seed", "entity", owner_user_id=alice.id)
    await store.store_frame_embedding(seed.id, UNIT, "nomic-embed-text")
    for i in range(6):
        n = await store.create_frame(f"n{i}", "entity", owner_user_id=alice.id)
        await store.store_frame_embedding(n.id, ORTHOGONAL, "nomic-embed-text")
        await store.create_association(seed.id, n.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3, top_k_direct=1)
    ctx = await retriever.retrieve("anything", alice.id)

    names = {rf.frame.name for rf in ctx.retrieved_frames}
    assert "seed" in names
    assert len([n for n in names if n.startswith("n")]) == 6


async def test_retrieve_caps_graph_frames(store):
    """max_graph_frames bounds the associative expansion."""
    alice = await store.create_user("alice")

    seed = await store.create_frame("seed", "entity", owner_user_id=alice.id)
    await store.store_frame_embedding(seed.id, UNIT, "nomic-embed-text")
    for i in range(10):
        n = await store.create_frame(f"n{i}", "entity", owner_user_id=alice.id)
        await store.store_frame_embedding(n.id, ORTHOGONAL, "nomic-embed-text")
        await store.create_association(seed.id, n.id, "related_to")

    retriever = Retriever(
        store,
        _mock_llm(),
        min_relevance=0.3,
        top_k_direct=1,
        max_graph_frames=4,
    )
    ctx = await retriever.retrieve("anything", alice.id)
    graph = [rf for rf in ctx.retrieved_frames if rf.source.startswith("graph_hop")]

    assert len(graph) == 4


async def test_graph_frames_rank_below_direct_matches(store):
    """Semantic matches outrank associative ones for the same prompt."""
    alice = await store.create_user("alice")

    seed = await store.create_frame("seed", "entity", owner_user_id=alice.id)
    await store.store_frame_embedding(seed.id, UNIT, "nomic-embed-text")
    neighbour = await store.create_frame("neighbour", "entity", owner_user_id=alice.id)
    await store.store_frame_embedding(neighbour.id, ORTHOGONAL, "nomic-embed-text")
    await store.create_association(seed.id, neighbour.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3, top_k_direct=1)
    ctx = await retriever.retrieve("anything", alice.id)

    assert ctx.retrieved_frames[0].frame.name == "seed"
    assert ctx.retrieved_frames[0].source == "direct_match"
    assert ctx.retrieved_frames[-1].source == "graph_hop_1"


async def test_walk_does_not_return_a_direct_match_again(store):
    """A frame reachable both ways appears once, as the direct match."""
    alice = await store.create_user("alice")

    a = await store.create_frame("a", "entity", owner_user_id=alice.id)
    b = await store.create_frame("b", "entity", owner_user_id=alice.id)
    await store.store_frame_embedding(a.id, UNIT, "nomic-embed-text")
    await store.store_frame_embedding(b.id, [0.99, 0.01, 0.0], "nomic-embed-text")
    await store.create_association(a.id, b.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3, top_k_direct=2)
    ctx = await retriever.retrieve("anything", alice.id)
    ids = [rf.frame.id for rf in ctx.retrieved_frames]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("hops", [0, 1, 2])
async def test_walk_is_empty_when_graph_hops_is_zero(store, hops):
    """Disabling the walk must actually disable it."""
    a = await store.create_frame("a", "entity")
    b = await store.create_frame("b", "entity")
    await store.create_association(a.id, b.id, "related_to")

    retriever = Retriever(store, _mock_llm(), min_relevance=0.3, graph_hops=hops)
    found = await retriever._graph_walk(a.id, UNIT, hops, 0.5, user_id=1)

    assert found == [] if hops == 0 else len(found) > 0


class TestBatchHelpers:
    """Batched lookups back the graph walk; per-call _connect() costs ~60ms
    on an encrypted DB, so batching is what keeps the walk affordable."""

    async def test_get_frames_by_ids_returns_all(self, store):
        frames = [await store.create_frame(f"f{i}", "entity") for i in range(5)]
        got = await store.get_frames_by_ids([f.id for f in frames])
        assert set(got) == {f.id for f in frames}
        assert got[frames[0].id].name == "f0"

    async def test_get_frames_by_ids_omits_missing(self, store):
        a = await store.create_frame("a", "entity")
        got = await store.get_frames_by_ids([a.id, 999_999])
        assert set(got) == {a.id}

    async def test_get_frames_by_ids_empty(self, store):
        assert await store.get_frames_by_ids([]) == {}

    async def test_get_frames_by_ids_dedupes_and_chunks(self, store):
        """Chunking is invisible to callers, and repeat ids are harmless."""
        frames = [await store.create_frame(f"f{i}", "entity") for i in range(7)]
        ids = [f.id for f in frames] * 2
        got = await store.get_frames_by_ids(ids)
        assert set(got) == {f.id for f in frames}

    async def test_get_slots_for_frames_groups_by_frame(self, store):
        a = await store.create_frame("a", "entity")
        b = await store.create_frame("b", "entity")
        empty = await store.create_frame("empty", "entity")
        for key, value in (("strings", "6"), ("brand", "fender")):
            await store.upsert_slot(a.id, key, value)
        await store.upsert_slot(b.id, "strings", "12")

        got = await store.get_slots_for_frames([a.id, b.id, empty.id])

        assert {s.key for s in got[a.id]} == {"strings", "brand"}
        assert {s.value for s in got[b.id]} == {"12"}
        assert empty.id not in got

    async def test_get_slots_for_frames_empty(self, store):
        assert await store.get_slots_for_frames([]) == {}

    async def test_associations_for_frames_keys_both_endpoints(self, store):
        a, b, c = [await store.create_frame(n, "entity") for n in "abc"]
        await store.create_association(a.id, b.id, "related_to")
        await store.create_association(b.id, c.id, "part_of")

        got = await store.get_all_associations_for_frames([a.id, c.id])

        # a sees its outgoing edge, c sees its incoming edge
        assert len(got[a.id]) == 1
        assert len(got[c.id]) == 1
        assert got[a.id][0].relation_type == "related_to"
        assert got[c.id][0].relation_type == "part_of"

    async def test_associations_for_frames_shared_edge_appears_twice(self, store):
        a, b = [await store.create_frame(n, "entity") for n in "ab"]
        await store.create_association(a.id, b.id, "related_to")

        got = await store.get_all_associations_for_frames([a.id, b.id])

        assert len(got[a.id]) == 1
        assert len(got[b.id]) == 1
        assert got[a.id][0].id == got[b.id][0].id

    async def test_associations_for_frames_empty_and_unrelated(self, store):
        a, b = [await store.create_frame(n, "entity") for n in "ab"]
        assert await store.get_all_associations_for_frames([]) == {}
        assert await store.get_all_associations_for_frames([a.id]) == {a.id: []}
        assert await store.get_all_associations_for_frames([a.id, b.id]) == {
            a.id: [],
            b.id: [],
        }
