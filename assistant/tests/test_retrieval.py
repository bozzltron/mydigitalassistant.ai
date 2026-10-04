from unittest.mock import AsyncMock, MagicMock

from assistant.backend.config import settings
from assistant.backend.memory.models import (
    Association,
    Episode,
    Frame,
    Slot,
)
from assistant.backend.memory.retrieval import (
    MemoryContext,
    RetrievedFrame,
    Retriever,
    format_memory_context,
    frame_to_text,
)
from assistant.backend.pipeline.llm_client import EmbeddingResponse


class TestRetrieverEmbeddingModelDefault:
    """The default must be the model the brain is actually written with.

    `Retriever` defaulted to "nomic-embed-text" while production writes
    `settings.embedding_model` (qwen3-embedding:0.6b). A default that disagrees
    with the store is a silent trap: sqlite-vec partitions vectors by model name,
    so searching the wrong label returns *zero* frames rather than erroring. It
    cost a full experiment run once -- five of eleven queries came back empty and
    the result file still looked plausible.

    Production always passes the model explicitly, so this is about every other
    caller: the eval harness, experiments, scripts, and tests.
    """

    def test_default_resolves_to_configured_model(self, store):
        mock_llm = AsyncMock()
        assert Retriever(store, mock_llm).embedding_model == settings.embedding_model

    def test_explicit_argument_still_wins(self, store):
        mock_llm = AsyncMock()
        r = Retriever(store, mock_llm, embedding_model="some-other-model")
        assert r.embedding_model == "some-other-model"

    async def test_canonicalisation_default_finds_configured_model_vectors(self, store):
        """Same trap one layer down, in the frame-name canonicalisation lookup.

        `resolve_or_create_frame` embeds the normalised name and searches for an
        existing frame with a compatible vector. It has its own `embedding_model`
        parameter, and a wrong label there silently fails the lookup, so
        "mountain wolf album" and "mountain_wolf_lp" become two frames instead of
        one -- the frame-graph fragmentation the canonicalisation exists to stop.
        """
        from assistant.backend.pipeline.extractor import resolve_or_create_frame

        async def flat_embedding(text: str) -> list[float]:
            return [0.5] * 768

        existing = await store.create_frame("mountain_wolf_album", "entity")
        await store.upsert_slot(existing.id, "artist", "wolves")
        await store.embed_frames([existing.id], flat_embedding, settings.embedding_model)

        # No embedding_model passed: the default must still find the vector.
        resolved = await resolve_or_create_frame(
            store, "mountain_wolf_lp", "entity", embed_fn=flat_embedding
        )

        assert resolved == existing.id, (
            "canonicalisation created a duplicate frame: the default model label "
            "did not match the one the frame was indexed under"
        )

    async def test_default_finds_frames_written_with_the_configured_model(self, store):
        """End to end: no explicit model, frame stored the way production stores it."""
        frame = await store.create_frame("guitar", "entity")
        await store.upsert_slot(frame.id, "strings", "6")
        await store.store_frame_embedding(frame.id, [1.0, 0.0, 0.0], settings.embedding_model)

        mock_llm = AsyncMock()
        mock_llm.embed.return_value = MagicMock(embedding=[0.9, 0.1, 0.0])

        retriever = Retriever(store, mock_llm, min_relevance=0.5)
        user = await store.create_user("alice")
        ctx = await retriever.retrieve("tell me about guitars", user.id)

        assert [f.frame.name for f in ctx.retrieved_frames] == ["guitar"], (
            "a default-constructed Retriever found nothing: the default model "
            "label does not match the one the frame was written with"
        )


def test_frame_to_text():
    frame = Frame(id=1, name="guitar", type="entity", confidence=0.8)
    slots = [Slot(id=1, frame_id=1, key="strings", value="6", confidence=0.9)]
    text = frame_to_text(frame, slots)
    assert "guitar" in text
    assert "entity" in text
    assert "strings = 6" in text


def test_format_memory_context_empty():
    ctx = MemoryContext(
        query="test",
        retrieved_frames=[],
        recent_episodes=[],
        formatted="",
    )
    formatted = format_memory_context(ctx)
    assert "no" in formatted.lower()


def test_format_memory_context_with_frames_and_episodes():
    frame = Frame(id=1, name="guitar", type="entity", confidence=0.8)
    slot = Slot(id=1, frame_id=1, key="strings", value="6", confidence=0.9)
    assoc = Association(
        id=1, from_frame_id=1, to_frame_id=2, relation_type="related_to", confidence=0.7
    )
    rf = RetrievedFrame(
        frame=frame,
        slots=[slot],
        associations=[assoc],
        relevance=0.85,
        source="direct_match",
    )
    ep = Episode(
        id=1,
        user_id=1,
        session_id="s1",
        role="user",
        content="I love guitars",
        frame_ids=[1],
    )
    ctx = MemoryContext(query="guitars", retrieved_frames=[rf], recent_episodes=[ep], formatted="")
    formatted = format_memory_context(ctx)
    assert "guitar" in formatted
    assert "strings" in formatted
    assert "6" in formatted
    assert "relevant memory" in formatted.lower()
    assert "recent conversation" in formatted.lower()


def test_memory_context_renders_association_names_not_ids():
    """Relations must render the target's *name*, never a database id.

    Regression: `format_memory_context` emitted `related_to→frame:4812`. The
    model echoed those ids into its replies, where they mean nothing to the
    user. The name is what the model and the reader can both use.
    """
    frame = Frame(id=1, name="guitar", type="entity", confidence=0.8)
    assoc = Association(
        id=1, from_frame_id=1, to_frame_id=752, relation_type="related_to", confidence=0.7
    )
    rf = RetrievedFrame(
        frame=frame,
        slots=[],
        associations=[assoc],
        relevance=0.85,
        source="direct_match",
    )
    ctx = MemoryContext(
        query="guitars",
        retrieved_frames=[rf],
        recent_episodes=[],
        formatted="",
        frame_names={752: "amplifier"},
    )
    formatted = format_memory_context(ctx)

    assert "related_to\u2192amplifier" in formatted
    assert "frame:752" not in formatted
    assert "752" not in formatted


def test_memory_context_omits_association_when_target_unresolvable():
    """An edge whose target cannot be named is dropped, not printed as an id.

    A missing name means we could not resolve the frame (deleted, or a target
    never fetched). Printing `frame:752` in that case reintroduces exactly the
    id leak above, so the edge is omitted instead.
    """
    frame = Frame(id=1, name="guitar", type="entity", confidence=0.8)
    assoc = Association(
        id=1, from_frame_id=1, to_frame_id=752, relation_type="related_to", confidence=0.7
    )
    rf = RetrievedFrame(
        frame=frame,
        slots=[],
        associations=[assoc],
        relevance=0.85,
        source="direct_match",
    )
    ctx = MemoryContext(
        query="guitars",
        retrieved_frames=[rf],
        recent_episodes=[],
        formatted="",
        frame_names={},  # 752 unresolved
    )
    formatted = format_memory_context(ctx)

    assert "related_to" not in formatted
    assert "frame:752" not in formatted
    assert "752" not in formatted


def test_memory_context_names_inbound_association_from_its_source():
    """An edge pointing *at* this frame is named from the other end.

    Associations are stored directionally but rendered as a relation this frame
    participates in, so the target is whichever end is not this frame. Reading
    `to_frame_id` unconditionally would name the frame itself and lose the
    subject.
    """
    frame = Frame(id=500, name="amplifier", type="entity", confidence=0.8)
    assoc = Association(
        id=1, from_frame_id=1, to_frame_id=500, relation_type="used_by", confidence=0.7
    )
    rf = RetrievedFrame(
        frame=frame,
        slots=[],
        associations=[assoc],
        relevance=0.85,
        source="graph_neighbor",
    )
    ctx = MemoryContext(
        query="amplifiers",
        retrieved_frames=[rf],
        recent_episodes=[],
        formatted="",
        frame_names={1: "guitar"},
    )
    formatted = format_memory_context(ctx)

    assert "used_by\u2192guitar" in formatted
    assert "frame:500" not in formatted


def test_format_memory_context_preserves_full_episode_content():
    """Episodes should NOT be truncated - the LLM needs full content."""
    ep = Episode(
        id=1,
        user_id=1,
        session_id="s1",
        role="user",
        content=(
            "This is a long conversation about the Glasgow climate summit "
            "where world leaders discussed carbon emissions and renewable energy targets for 2030."
        ),
        frame_ids=[],
    )
    ctx = MemoryContext(query="test", retrieved_frames=[], recent_episodes=[ep], formatted="")
    formatted = format_memory_context(ctx)
    assert "climate summit" in formatted
    assert "carbon emissions" in formatted
    assert "..." not in formatted


def test_format_memory_context_preserves_long_episodes():
    """Long episodes should be preserved in full, not truncated."""
    ep = Episode(
        id=1,
        user_id=1,
        session_id="s1",
        role="user",
        content="x" * 150,
        frame_ids=[],
    )
    ctx = MemoryContext(query="test", retrieved_frames=[], recent_episodes=[ep], formatted="")
    formatted = format_memory_context(ctx)
    assert "x" * 10 in formatted  # should contain the full content
    assert "..." not in formatted or formatted.count("...") == 0


def test_format_memory_context_truncates_many_frames():
    """The frame cap must bind, and must keep the most relevant frames.

    Asserted against settings.max_frames_in_prompt rather than a literal, so this
    tests that the cap works instead of pinning whatever number is configured.
    The cap was 5 while retrieval returned only 3 frames, which is why it never
    bound; it is now the measured recall peak from
    assistant/experiments/frame_budget.
    """
    from assistant.backend.config import settings

    n = settings.max_frames_in_prompt + 5
    frames = [
        RetrievedFrame(
            frame=Frame(id=i, name=f"frame{i}", type="entity", confidence=0.5),
            slots=[],
            associations=[],
            relevance=1.0 - i * 0.05,
            source="direct_match",
        )
        for i in range(n)
    ]
    ctx = MemoryContext(query="test", retrieved_frames=frames, recent_episodes=[], formatted="")
    formatted = format_memory_context(ctx)
    count = sum(1 for i in range(n) if f"### frame{i}" in formatted)
    assert count == settings.max_frames_in_prompt
    # Relevance descends with i, so the survivors are the strongest frames.
    assert f"### frame{settings.max_frames_in_prompt - 1}" in formatted
    assert f"### frame{n - 1}" not in formatted


def test_format_memory_context_file_frame_points_to_read_file():
    """File frames must not leak truncated content into the prompt.

    Before the fix the 200-char file_content_preview slot was injected as a
    regular slot, and the model answered from that snippet instead of reading
    the real file. File frames now surface metadata plus an explicit
    read_file reference, never the content snapshot.
    """
    frame = Frame(
        id=1,
        name="file_subscribers_active.csv",
        type="entity",
        confidence=0.8,
        source_type="file_upload",
    )
    slots = [
        Slot(
            id=1, frame_id=1, key="file_name",
            value="subscribers_active.csv", confidence=0.9,
        ),
        Slot(
            id=2, frame_id=1, key="file_safe_name",
            value="subscribers_active.csv", confidence=0.9,
        ),
        Slot(
            id=3, frame_id=1, key="file_content_preview",
            value="email,status\na@b.com,active", confidence=0.9,
        ),
        Slot(
            id=4, frame_id=1, key="file_size", value="45545", confidence=0.9,
        ),
    ]
    rf = RetrievedFrame(
        frame=frame,
        slots=slots,
        associations=[],
        relevance=0.85,
        source="direct_match",
    )
    ctx = MemoryContext(
        query="subscribers", retrieved_frames=[rf], recent_episodes=[], formatted=""
    )
    formatted = format_memory_context(ctx)

    assert "file_content_preview" not in formatted            # hint slot hidden
    assert "email,status" not in formatted                    # truncated preview not leaked
    assert "subscribers_active.csv" in formatted              # file_name still shown
    assert "file_size = 45545" in formatted
    assert 'read_file(frame_name="file_subscribers_active.csv")' in formatted
    assert 'read_file(path="subscribers_active.csv")' in formatted


async def test_retrieve_with_no_frames(store):
    """Empty memory should return context with no frames."""
    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[0.1] * 768)

    retriever = Retriever(store, mock_llm)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("hello", user.id)

    assert ctx.retrieved_frames == []
    assert "no" in ctx.formatted.lower()


async def test_retrieve_finds_relevant_frame(store):
    """A query should find a frame with similar embedding."""
    f1 = await store.create_frame("guitar", "entity")
    await store.upsert_slot(f1.id, "strings", "6")
    await store.store_frame_embedding(f1.id, [1.0, 0.0, 0.0], settings.embedding_model)

    f2 = await store.create_frame("pasta", "entity")
    await store.upsert_slot(f2.id, "type", "spaghetti")
    await store.store_frame_embedding(f2.id, [0.0, 1.0, 0.0], settings.embedding_model)

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[0.9, 0.1, 0.0])

    retriever = Retriever(store, mock_llm, min_relevance=0.5)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("tell me about guitars", user.id)

    assert len(ctx.retrieved_frames) >= 1
    assert ctx.retrieved_frames[0].frame.name == "guitar"


async def test_retrieve_graph_walk_finds_neighbors(store):
    """Graph walk should find associated frames via 1-2 hop traversal."""
    f1 = await store.create_frame("guitar", "entity")
    await store.store_frame_embedding(f1.id, [1.0, 0.0, 0.0], settings.embedding_model)

    f2 = await store.create_frame("music", "concept")
    await store.store_frame_embedding(f2.id, [0.7, 0.7, 0.0], settings.embedding_model)

    f3 = await store.create_frame("art", "concept")
    await store.store_frame_embedding(f3.id, [0.5, 0.8, 0.0], settings.embedding_model)

    await store.create_association(f1.id, f2.id, "related_to", confidence=0.9)
    await store.create_association(f2.id, f3.id, "related_to", confidence=0.9)

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[1.0, 0.0, 0.0])

    retriever = Retriever(store, mock_llm, min_relevance=0.1, graph_hops=2)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("guitars", user.id)

    frame_names = {rf.frame.name for rf in ctx.retrieved_frames}
    assert "guitar" in frame_names
    assert "music" in frame_names


async def test_retrieve_includes_recent_episodes(store):
    """Retrieval should include recent episodes for the user."""
    user = await store.create_user("alice")
    session = "s1"
    await store.create_episode(user.id, session, "user", "I have a guitar", frame_ids=[])
    await store.create_episode(user.id, session, "assistant", "Tell me about it", frame_ids=[])

    f1 = await store.create_frame("guitar", "entity")
    await store.store_frame_embedding(f1.id, [1.0, 0.0, 0.0], settings.embedding_model)

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[1.0, 0.0, 0.0])

    retriever = Retriever(store, mock_llm, min_relevance=0.5)
    ctx = await retriever.retrieve("guitars", user.id)

    assert len(ctx.recent_episodes) >= 2
    assert any("guitar" in ep.content for ep in ctx.recent_episodes)


async def test_graph_walk_in_isolation(store):
    f1 = await store.create_frame("a", "entity")
    f2 = await store.create_frame("b", "entity")
    await store.create_association(f1.id, f2.id, "rel", confidence=1.0)

    mock_llm = AsyncMock()
    retriever = Retriever(store, mock_llm, min_relevance=0.1, graph_decay=1.0)
    neighbors = await retriever._graph_walk(f1.id, [1.0, 0.0], 2, 1.0, user_id=1)

    assert len(neighbors) == 1
    assert neighbors[0][0] == f2.id
    assert neighbors[0][2] == "graph_hop_1"


async def test_graph_walk_prevents_cycles(store):
    f1 = await store.create_frame("a", "entity")
    f2 = await store.create_frame("b", "entity")
    await store.create_association(f1.id, f2.id, "rel", confidence=1.0)
    await store.create_association(f2.id, f1.id, "rel", confidence=1.0)

    mock_llm = AsyncMock()
    retriever = Retriever(store, mock_llm, min_relevance=0.1)
    neighbors = await retriever._graph_walk(f1.id, [1.0, 0.0], 5, 1.0, user_id=1)

    assert len(neighbors) == 1
    assert neighbors[0][0] != f1.id


async def test_graph_walk_stops_at_max_hops(store):
    frames = [await store.create_frame(f"f{i}", "entity") for i in range(5)]
    for i in range(len(frames) - 1):
        await store.create_association(frames[i].id, frames[i + 1].id, "next", confidence=1.0)

    mock_llm = AsyncMock()
    retriever = Retriever(store, mock_llm, min_relevance=0.01, graph_decay=1.0)
    neighbors = await retriever._graph_walk(frames[0].id, [1.0, 0.0], 2, 1.0, user_id=1)

    reached = {n[0] for n in neighbors}
    assert frames[1].id in reached
    assert frames[2].id in reached
    assert frames[3].id not in reached


async def test_min_relevance_boundary(store):
    f1 = await store.create_frame("guitar", "entity")
    await store.store_frame_embedding(f1.id, [1.0, 0.0, 0.0], settings.embedding_model)

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[0.5, 0.5, 0.0])
    # cosine([1,0,0], [0.5,0.5,0]) ~ 0.707

    retriever = Retriever(store, mock_llm, min_relevance=0.7)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("guitar", user.id)
    assert len(ctx.retrieved_frames) == 1


async def test_embed_frame(store):
    frame = await store.create_frame("guitar", "entity")
    await store.upsert_slot(frame.id, "strings", "6")

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = MagicMock(embedding=[0.9, 0.1, 0.0])

    retriever = Retriever(store, mock_llm)
    embedding = await retriever.embed_frame(frame, await store.get_slots_for_frame(frame.id))
    assert embedding == [0.9, 0.1, 0.0]
    stored = await store.get_frame_embedding(frame.id, settings.embedding_model)
    assert stored == [0.9, 0.1, 0.0]


class TestIsIdentityQuery:
    @staticmethod
    def patterns():
        return [
            ("What is your name?", True),
            ("what's your name", True),
            ("who are you", True),
            ("what should I call you", True),
            ("how did you get your name", True),
            ("tell me your identity", True),
            ("what do you know about your name", True),
            ("what is your name and who are you", True),
            ("I love your name", True),
            ("the weather today", False),
            ("search for guitars", False),
            ("remember what I said", False),
        ]

    def test_positive_cases(self):
        for query, expected in self.patterns():
            if expected:
                assert Retriever._is_identity_query(query), f"Expected True for: {query}"

    def test_negative_cases(self):
        for query, expected in self.patterns():
            if not expected:
                assert not Retriever._is_identity_query(query), f"Expected False for: {query}"

    def test_case_insensitive(self):
        assert Retriever._is_identity_query("WHAT IS YOUR NAME?")
        assert Retriever._is_identity_query("Who Are You")
        assert Retriever._is_identity_query("What's YOUR name?")


async def test_retrieve_identity_query_boosts_identity_frame(store):
    """Identity queries must ensure identity_name frame is retrieved at relevance 1.0."""
    identity_frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(identity_frame.id, "full_name", "Elysia")
    await store.store_frame_embedding(
        identity_frame.id, [0.5] + [0.5] + [0.0] * 766, settings.embedding_model
    )

    unrelated = await store.create_frame("guitar", "entity")
    await store.store_frame_embedding(unrelated.id, [1.0] + [0.0] * 767, settings.embedding_model)

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[0.9] + [0.1] * 767,
        model=settings.embedding_model,
    )

    retriever = Retriever(store, mock_llm, min_relevance=0.1)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("What is your name?", user.id)

    identity_frames = [rf for rf in ctx.retrieved_frames if rf.frame.name == "identity_name"]
    assert len(identity_frames) == 1
    assert identity_frames[0].relevance == 1.0
    full_name_slot = next(
        s for s in identity_frames[0].slots if s.key == "full_name"
    )
    assert full_name_slot.value == "Elysia"


async def test_retrieve_identity_query_does_not_duplicate_if_already_retrieved(store):
    """If identity_name is already in top results, boost its relevance to 1.0."""
    identity_frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(identity_frame.id, "full_name", "Elysia")
    await store.store_frame_embedding(
        identity_frame.id, [0.9] + [0.1] * 767, settings.embedding_model
    )

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[0.9] + [0.1] * 767,
        model=settings.embedding_model,
    )

    retriever = Retriever(store, mock_llm, min_relevance=0.1)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("What is your name?", user.id)

    identity_frames = [rf for rf in ctx.retrieved_frames if rf.frame.name == "identity_name"]
    assert len(identity_frames) == 1
    assert identity_frames[0].relevance == 1.0


class TestIsUserIdentityQuery:
    @staticmethod
    def patterns():
        return [
            ("What is my name?", True),
            ("what's my name", True),
            ("who am I", True),
            ("I go by Boz", True),
            ("call me Boz", True),
            ("what is your name?", False),
            ("who are you", False),
            ("search for guitars", False),
        ]

    def test_patterns(self):
        for query, expected in self.patterns():
            assert Retriever._is_user_identity_query(query) is expected, query


async def test_retrieve_user_identity_query_boosts_user_frame(store):
    """'What is my name?' must resolve from user_identity, not vector luck."""
    user_frame = await store.create_frame("user_identity", "entity")
    await store.upsert_slot(user_frame.id, "full_name", "Boz")
    await store.store_frame_embedding(
        user_frame.id, [0.5] + [0.5] + [0.0] * 766, settings.embedding_model
    )

    unrelated = await store.create_frame("guitar", "entity")
    await store.store_frame_embedding(
        unrelated.id, [1.0] + [0.0] * 767, settings.embedding_model
    )

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[0.9] + [0.1] * 767,
        model=settings.embedding_model,
    )

    retriever = Retriever(store, mock_llm, min_relevance=0.1)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("What is my name?", user.id)

    frames = [rf for rf in ctx.retrieved_frames if rf.frame.name == "user_identity"]
    assert len(frames) == 1
    assert frames[0].relevance == 1.0
    full_name_slot = next(s for s in frames[0].slots if s.key == "full_name")
    assert full_name_slot.value == "Boz"


async def test_retrieve_user_identity_query_does_not_pull_assistant_name(store):
    """The two identity frames stay distinct: 'my name' must not boost the agent's."""
    user_frame = await store.create_frame("user_identity", "entity")
    await store.upsert_slot(user_frame.id, "full_name", "Boz")
    await store.store_frame_embedding(
        user_frame.id, [0.9] + [0.1] * 767, settings.embedding_model
    )
    identity_frame = await store.create_frame("identity_name", "entity")
    await store.upsert_slot(identity_frame.id, "full_name", "Carl")
    await store.store_frame_embedding(
        identity_frame.id, [0.8] + [0.2] * 767, settings.embedding_model
    )

    mock_llm = AsyncMock()
    mock_llm.embed.return_value = EmbeddingResponse(
        embedding=[0.9] + [0.1] * 767,
        model=settings.embedding_model,
    )

    retriever = Retriever(store, mock_llm, min_relevance=0.1)
    user = await store.create_user("alice")
    ctx = await retriever.retrieve("What is my name?", user.id)

    names = {rf.frame.name for rf in ctx.retrieved_frames}
    assert "user_identity" in names
    # identity_name may still surface by similarity, but only the user frame is
    # forced to relevance 1.0 by the user-identity boost.
    boosted = [rf for rf in ctx.retrieved_frames if rf.relevance == 1.0]
    assert [rf.frame.name for rf in boosted] == ["user_identity"]


def test_format_memory_context_truncates_really_long_episodes():
    """Episodes beyond `max_episode_digest_chars` become digests.

    Verbatim full episodes in the system prompt duplicated the conversation
    history and inflated prefill by thousands of tokens; the most recent
    turns still arrive in full as chat history messages.
    """
    ep = Episode(
        id=1,
        user_id=1,
        session_id="s1",
        role="assistant",
        content="word " * 120,  # 600 chars
        frame_ids=[],
    )
    ctx = MemoryContext(query="test", retrieved_frames=[], recent_episodes=[ep], formatted="")
    formatted = format_memory_context(ctx)
    assert "…" in formatted
    # Digest is bounded: header + role prefix + digest + ellipsis.
    episode_line = [line for line in formatted.splitlines() if "[assistant]" in line][0]
    assert len(episode_line) < 300
