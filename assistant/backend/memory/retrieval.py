import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from assistant.backend.config import settings
from assistant.backend.memory.models import Association, Episode, Frame, Slot
from assistant.backend.pipeline.file_profile import profile_summary_lines

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.memory.working_memory import WorkingMemory
    from assistant.backend.pipeline.llm_client import OllamaClient

logger = logging.getLogger(__name__)

# Frame source types whose full content lives on disk (read via read_file)
# rather than in slots: user uploads and tool-created sandbox files.
FILE_FRAME_SOURCE_TYPES = ("file_upload", "file_create")

# Content slots on file frames are truncated hints, not the file itself.
#
# Retained as a re-export because `retrieval` historically owned this list; the one
# definition now lives in `memory.store`, where the write-time refusal uses it, so the
# rule has a single home rather than two that can drift.
from assistant.backend.memory.store import (  # noqa: E402, F401
    FILE_CONTENT_HINT_SLOTS,
)

# Minimum association confidence for the graph walk to *follow* an edge.
#
# This is a trust floor on the edge, not a relevance threshold: association
# structure is topological, and "is this edge real" is a different question from
# "is this neighbour semantically similar to the query". Those must not be
# multiplied into one score — see Retriever._graph_walk.
#
# It is currently non-binding. create_association defaults confidence to 0.5 and
# no write path in the backend produces a lower value, so every edge in the
# database sits at or above this floor. It stays as a guard for edges that
# arrive weaker later (e.g. the correction path revising an existing edge).
ASSOC_MIN_CONFIDENCE = 0.5


@dataclass
class RetrievedFrame:
    frame: Frame
    slots: list[Slot]
    associations: list[Association]
    relevance: float  # 0.0 to 1.0
    source: str  # "direct_match" | "graph_neighbor" | "episode_link"


@dataclass
class MemoryContext:
    query: str
    retrieved_frames: list[RetrievedFrame]
    recent_episodes: list[Episode]
    formatted: str  # ready-to-inject text for LLM system prompt
    # Semantic matches over archived conversation turns (older than the
    # recency window, any session). (episode, similarity) best-first.
    past_conversations: list[tuple[Episode, float]] = field(default_factory=list)
    # id -> name for association targets, so the prompt can render
    # `relation -> name` instead of `relation -> frame:4812`. A database id means
    # nothing to the model or the user, and the model echoed them back into its
    # replies. Populated during retrieval from the frames already fetched, plus
    # one batched read for targets not in the selection. A target that cannot be
    # resolved is *absent* here, and the renderer omits that edge rather than
    # printing an id.
    frame_names: dict[int, str] = field(default_factory=dict)


def frame_to_text(frame: Frame, slots: list[Slot]) -> str:
    """Convert a frame + its slots to embeddable text."""
    parts = [f"{frame.type}: {frame.name}"]
    for slot in slots:
        parts.append(f"  {slot.key} = {slot.value} (confidence: {slot.confidence:.2f})")
    return "\n".join(parts)


def _fits(
    lines: list[str],
    block: list[str],
    max_memory_chars: int | None,
    emitted: int,
) -> bool:
    """Whether appending `block` keeps the memory section within budget.

    Always true when no budget was supplied, so the default path is unchanged.
    Frames are offered in relevance order, so refusing the first frame that does
    not fit drops the weakest remaining evidence rather than the strongest.

    The first frame is always admitted even if it alone overruns: dropping it
    would return an empty memory section, and one oversized frame is worth more
    to the model than none. The caller's flat character cut remains the backstop
    for that case.
    """
    if max_memory_chars is None or emitted == 0:
        return True
    used = sum(len(line) + 1 for line in lines) + sum(len(b) + 1 for b in block)
    return used <= max_memory_chars


def format_memory_context(
    context: "MemoryContext", max_memory_chars: int | None = None
) -> str:
    """Format a MemoryContext as structured text for LLM injection.

    `max_memory_chars` makes the fit frame-aware: whole frames are dropped
    least-relevant-first until the section fits, rather than letting the
    caller's flat character cut slice through a frame's slots.

    That matters for more than tidiness. A frame cut mid-way still renders its
    "### name" header, so it reads as present to the model while the facts in its
    tail are simply gone. The measured 0.91 "recall when shown" is a property of
    *complete* frames; a half-frame spends its slot in the prompt budget and
    returns nothing for it. Dropping whole frames keeps every frame the model
    sees usable.

    The episode sections are a floor, not a participant: past-conversation
    matches and recent-session digests are rendered regardless of budget (each
    digest is itself capped at settings.max_episode_digest_chars, so the tail
    tops out around 2.6k chars). A budget below that floor is not honoured --
    there would be no memory left to spend it on. The caller's flat character
    cut remains the backstop.
    """
    lines: list[str] = []
    # The episode sections are appended after the frames but are still part of
    # the memory section, so their cost has to come out of the frame allowance.
    # Otherwise the fit overshoots by whatever the digests weigh (~1.1k chars
    # in the live corpus) and the caller's flat cut lands mid-frame again.
    tail = _render_episode_sections(context)
    if max_memory_chars is not None:
        tail_cost = sum(len(line) + 1 for line in tail)
        max_memory_chars = max(0, max_memory_chars - tail_cost)
    if context.retrieved_frames:
        lines.append("## Relevant memory")
        # Separate conversation summaries from other frames
        summary_type = "conversation_summary"
        summary_frames = [
            rf for rf in context.retrieved_frames if rf.frame.type == summary_type
        ]
        other_frames = [
            rf for rf in context.retrieved_frames if rf.frame.type != summary_type
        ]

        emitted = 0
        if summary_frames:
            lines.append("\n## Conversation summaries")
            for rf in summary_frames[: settings.max_frames_in_prompt]:
                block = [f"\n### {rf.frame.name} [relevance: {rf.relevance:.2f}]"]
                for slot in rf.slots:
                    source_note = ""
                    if slot.source_url:
                        if "//" in slot.source_url:
                            domain = slot.source_url.split("/")[2]
                        else:
                            domain = slot.source_url
                        source_note = f", src: {slot.source_type or 'unknown'} ({domain})"
                        if slot.source_reliability:
                            source_note += f", reliability: {slot.source_reliability:.2f}"
                    block.append(
                        f"  - {slot.key} = {slot.value} "
                        f"(conf: {slot.confidence:.2f}{source_note})"
                    )
                if _fits(lines, block, max_memory_chars, emitted):
                    lines.extend(block)
                    emitted += 1

        for rf in other_frames[: settings.max_frames_in_prompt]:
            block = [
                f"\n### {rf.frame.name} ({rf.frame.type}) [relevance: {rf.relevance:.2f}]"
            ]
            is_file_frame = rf.frame.source_type in FILE_FRAME_SOURCE_TYPES
            file_safe_name = ""
            profile_json = ""
            for slot in rf.slots:
                if is_file_frame:
                    # Content snapshots are truncated on-disk hints. Full
                    # content lives in the sandbox and is read via read_file —
                    # never prefill it, or the model answers from a snippet.
                    if slot.key in FILE_CONTENT_HINT_SLOTS:
                        continue
                    if slot.key == "file_safe_name":
                        file_safe_name = slot.value or ""
                        continue  # surfaced via the read pointer below
                    if slot.key == "file_profile":
                        profile_json = slot.value or ""
                        continue  # rendered compactly below, never as raw JSON
                source_note = ""
                if slot.source_url:
                    if "//" in slot.source_url:
                        domain = slot.source_url.split("/")[2]
                    else:
                        domain = slot.source_url
                    source_note = f", src: {slot.source_type or 'unknown'} ({domain})"
                    if slot.source_reliability:
                        source_note += f", reliability: {slot.source_reliability:.2f}"
                slot_line = (
                    f"  - {slot.key} = {slot.value} "
                    f"(conf: {slot.confidence:.2f}{source_note})"
                )
                block.append(slot_line)
            if is_file_frame:
                if not file_safe_name and rf.frame.name.startswith("file_"):
                    file_safe_name = rf.frame.name[len("file_"):]
                pointer = f'  read full contents: read_file(frame_name="{rf.frame.name}")'
                if file_safe_name:
                    pointer += f' or read_file(path="{file_safe_name}")'
                block.append(pointer)
                if file_safe_name:
                    block.append(
                        "  search or grow without reading: "
                        f'search_file(path="{file_safe_name}", query="…") / '
                        f'append_file(path="{file_safe_name}", content="…")'
                    )
                if profile_json:
                    try:
                        block.extend(profile_summary_lines(json.loads(profile_json)))
                    except (ValueError, TypeError):
                        # A malformed profile is not worth failing the prompt over.
                        pass
            if rf.associations:
                # Render the *name* of what each edge points at, never the id.
                # The target is whichever end is not this frame, so an inbound
                # edge is named from its source rather than shown as a dangling
                # id. An unresolvable target is dropped: an edge the model
                # cannot name is not worth a prompt slot, and printing
                # `frame:4812` is what made replies leak database ids.
                named: list[str] = []
                for a in rf.associations[:3]:
                    target_id = (
                        a.from_frame_id
                        if a.to_frame_id == rf.frame.id
                        else a.to_frame_id
                    )
                    target_name = context.frame_names.get(target_id)
                    if target_name:
                        named.append(f"{a.relation_type}\u2192{target_name}")
                if named:
                    block.append(f"  relations: {', '.join(named)}")
            if _fits(lines, block, max_memory_chars, emitted):
                lines.extend(block)
                emitted += 1

    lines.extend(tail)
    return "\n".join(lines) if lines else "(no relevant memory found)"


def _render_episode_sections(context: "MemoryContext") -> list[str]:
    """Past-conversation matches and recent-session digests, in prompt order."""
    out: list[str] = []
    if context.past_conversations:
        out.append("\n## Related past conversations")
        for ep, sim in context.past_conversations:
            content = " ".join(ep.content.split())
            if len(content) > settings.max_episode_digest_chars:
                content = (
                    content[: settings.max_episode_digest_chars].rsplit(" ", 1)[0] + "…"
                )
            when = (ep.timestamp or "")[:10]
            out.append(
                f"   [{when} · {ep.role} · {round(sim * 100)}% match] {content}"
            )
    if context.recent_episodes:
        out.append("\n## Recent conversation (this session)")
        for ep in context.recent_episodes[-settings.max_episodes_in_prompt :]:
            # Digests, not verbatim text. The orchestrator passes the most
            # recent turns as proper history messages, so full content here
            # duplicated the conversation and inflated every prompt's
            # prefill by thousands of tokens. Full episode text stays in
            # the DB; these digest lines keep older context citable.
            content = " ".join(ep.content.split())
            if len(content) > settings.max_episode_digest_chars:
                content = (
                    content[: settings.max_episode_digest_chars].rsplit(" ", 1)[0] + "…"
                )
            out.append(f"   [{ep.role}] {content}")
    return out


class Retriever:
    """Retrieves relevant memory for a query.

    Pipeline:
    1. Embed query via Ollama
    2. Cosine similarity vs all frame embeddings
    3. Graph-walk 1-2 hops from top-k frames
    4. Get recent user episodes for touched frames
    5. Assemble MemoryContext
    """

    def __init__(
        self,
        store: "MemoryStore",
        llm_client: "OllamaClient",
        embedding_model: str | None = None,
        top_k_direct: int = 3,
        graph_hops: int = 2,
        graph_decay: float = 0.5,  # relevance decay per hop
        min_relevance: float = 0.3,
        max_graph_frames: int = 7,
        working_memory: "WorkingMemory | None" = None,
    ):
        self.store = store
        self.llm_client = llm_client
        # sqlite-vec partitions vectors by model name, so searching the wrong
        # label returns zero frames instead of erroring. Defaulting to anything
        # other than the configured model is therefore a silent failure, which is
        # why this resolves from settings rather than carrying a literal.
        self.embedding_model = embedding_model or settings.embedding_model
        self.top_k_direct = top_k_direct
        self.graph_hops = graph_hops
        self.graph_decay = graph_decay
        self.min_relevance = min_relevance
        # Bounds the associative expansion so the walk competes for prompt room
        # rather than crowding out the direct semantic matches. Default 7 puts
        # top_k_direct=3 + 7 graph frames = 10 frames, which is where recall
        # peaks in assistant/experiments/frame_budget (10 → 0.697, 20 → 0.636,
        # 40 → 0.636 while the 12000-char cap starts truncating memory).
        self.max_graph_frames = max_graph_frames
        self.working_memory = working_memory

    async def embed_frame(self, frame: Frame, slots: list[Slot]) -> list[float]:
        """Embed a frame and store its embedding."""
        text = frame_to_text(frame, slots)
        response = await self.llm_client.embed(text)
        await self.store.store_frame_embedding(frame.id, response.embedding, self.embedding_model)
        return response.embedding

    # Patterns that indicate a self-identity / name query
    _AGENT_IDENTITY_PATTERNS = (
        r"\byour name\b",
        r"\byour name is\b",
        r"\bwhat('s| is) your name\b",
        r"\bwho are you\b",
        r"\bhow did you get your name\b",
        r"\bwhat should i call you\b",
        r"\byour identity\b",
        r"\bcall you\b",
    )
    _AGENT_IDENTITY_RE = None  # compiled lazily

    # Patterns that indicate a question about the USER's own name.
    _USER_IDENTITY_PATTERNS = (
        r"\bmy name\b",
        r"\bwhat('s| is) my name\b",
        r"\bwho am i\b",
        r"\bi go by\b",
        r"\bcall me\b",
        r"\bmy identity\b",
    )
    _USER_IDENTITY_RE = None  # compiled lazily

    @staticmethod
    def _is_identity_query(query: str) -> bool:
        import re
        if Retriever._AGENT_IDENTITY_RE is None:
            Retriever._AGENT_IDENTITY_RE = re.compile(
                "|".join(Retriever._AGENT_IDENTITY_PATTERNS),
                re.IGNORECASE,
            )
        return bool(Retriever._AGENT_IDENTITY_RE.search(query))

    @staticmethod
    def _is_user_identity_query(query: str) -> bool:
        import re
        if Retriever._USER_IDENTITY_RE is None:
            Retriever._USER_IDENTITY_RE = re.compile(
                "|".join(Retriever._USER_IDENTITY_PATTERNS),
                re.IGNORECASE,
            )
        return bool(Retriever._USER_IDENTITY_RE.search(query))

    async def _boost_named_frame(
        self, frame_name: str, retrieved_frames: list[RetrievedFrame]
    ) -> None:
        """Ensure a reserved identity frame is retrieved at relevance 1.0.

        A name query must be answered from the name frame even when its embedding
        sits below the relevance gate, so identity frames are pulled in directly
        rather than left to vector similarity.
        """
        frame = await self.store.get_frame_by_name(frame_name)
        if frame is None:
            return
        for rf in retrieved_frames:
            if rf.frame.id == frame.id:
                rf.relevance = max(rf.relevance, 1.0)
                return
        slots = await self.store.get_slots_for_frame(frame.id)
        assocs = await self.store.get_all_associations_for_frame(frame.id)
        retrieved_frames.insert(
            0,
            RetrievedFrame(
                frame=frame,
                slots=slots,
                associations=assocs,
                relevance=1.0,
                source="identity_boost",
            ),
        )

    async def retrieve(
        self,
        query: str,
        user_id: int,
        session_id: str | None = None,
     ) -> MemoryContext:
        """Retrieve relevant memory for a query.

        Returns MemoryContext with retrieved frames and recent episodes.
        Uses sqlite-vec vec_distance_cosine() for efficient similarity search.
        For self-identity queries, always includes the identity_name frame.
        """
         # 1. Embed query
        query_response = await self.llm_client.embed(query)
        query_embedding = query_response.embedding

         # 2. Vector similarity search via sqlite-vec
        all_results = await self.store.search_similar_frames(
            embedding=query_embedding,
            user_id=user_id,
            embedding_model=self.embedding_model,
            limit=self.top_k_direct * 2,  # fetch more to account for graph neighbors
            min_distance=settings.retrieval_min_distance,
         )

        if not all_results:
             # No frames in memory yet — archived conversations may still be
             # directly relevant, so episode recall still runs.
            past = await self._search_past_conversations(query_embedding, user_id, session_id)
            recent = await self.store.get_episodes_for_user(user_id, limit=10)
            empty = MemoryContext(
                query=query,
                retrieved_frames=[],
                recent_episodes=recent,
                past_conversations=past,
                formatted="(no memory frames yet)",
             )
            empty.formatted = format_memory_context(empty)
            return empty

         # 3. Filter by distance threshold and sort by similarity × confidence × priority
        #    Apply working memory boost if available
        wm_boost_map: dict[int, float] = {}
        if self.working_memory is not None:
            wm_boost_map = await self.working_memory.get_boost_map()

        scored: list[tuple[int, float, str]] = []
        for frame, _slots, similarity in all_results:
            if similarity < self.min_relevance:
                continue
            base_score = similarity * frame.confidence * frame.priority
            boost = wm_boost_map.get(frame.id, 1.0)
            scored.append((frame.id, base_score * boost, "direct_match"))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_direct = scored[: self.top_k_direct]

         # 4. Graph-walk from top-k frames
        seen_frame_ids = {fid for fid, _, _ in top_direct}
        graph_neighbors: list[tuple[int, float, str]] = []
        for frame_id, _sim, _ in top_direct:
            neighbors = await self._graph_walk(
                frame_id, query_embedding, self.graph_hops, self.graph_decay, user_id
             )
            for neighbor_id, neighbor_sim, source in neighbors:
                if neighbor_id not in seen_frame_ids:
                    seen_frame_ids.add(neighbor_id)
                    graph_neighbors.append((neighbor_id, neighbor_sim, source))

        # Rank across all seeds together, not per-seed: a strong neighbour of the
        # 3rd-ranked direct match should beat a weak neighbour of the 1st. Then
        # cap, so the walk competes for prompt room rather than flooding it.
        graph_neighbors.sort(key=lambda x: x[1], reverse=True)
        graph_neighbors = graph_neighbors[: self.max_graph_frames]

         # 5. Assemble RetrievedFrame objects
        # Batched: three queries total rather than three per frame, which on an
        # encrypted DB means three SQLCipher key derivations instead of thirty.
        selected = top_direct + graph_neighbors
        frames_by_id = await self.store.get_frames_by_ids([fid for fid, _, _ in selected])
        slots_by_frame = await self.store.get_slots_for_frames(
            [fid for fid, _, _ in selected]
        )
        assocs_by_frame = await self.store.get_all_associations_for_frames(
            [fid for fid, _, _ in selected]
        )
        retrieved_frames: list[RetrievedFrame] = []
        for frame_id, relevance, source in selected:
            frame = frames_by_id.get(frame_id)
            if frame is None:
                continue
            retrieved_frames.append(
                RetrievedFrame(
                    frame=frame,
                    slots=slots_by_frame.get(frame_id, []),
                    associations=assocs_by_frame.get(frame_id, []),
                    relevance=relevance,
                    source=source,
                 )
             )

        # Association targets are whichever end is not the retrieved frame, and
        # they may not be in `selected` at all. Collect the names we do not hold
        # and fetch them in one batched read; `get_frames_by_ids` chunks
        # internally, so this is a single round trip regardless of how many edges
        # there are — nothing here scales per-association on the hot path.
        frame_names: dict[int, str] = {
            fid: f.name for fid, f in frames_by_id.items()
        }

        async def _resolve_assoc_names() -> None:
            """Fetch names for association targets not already known.

            Called again after the identity boost, which inserts a frame with
            associations of its own after the first pass has run.
            """
            wanted: set[int] = set()
            for rf in retrieved_frames:
                for a in rf.associations:
                    other = (
                        a.from_frame_id
                        if a.to_frame_id == rf.frame.id
                        else a.to_frame_id
                    )
                    if other not in frame_names:
                        wanted.add(other)
            if wanted:
                extra = await self.store.get_frames_by_ids(list(wanted))
                frame_names.update({fid: f.name for fid, f in extra.items()})

        await _resolve_assoc_names()

         # 5b. Identity queries must resolve from the identity frames directly:
        # the assistant's own name for "what's your name", the user's own name
        # for "what's my name".
        if self._is_identity_query(query):
            await self._boost_named_frame("identity_name", retrieved_frames)
        if self._is_user_identity_query(query):
            await self._boost_named_frame("user_identity", retrieved_frames)
        # The identity frame brings associations the first pass never saw, so
        # its targets need naming too or its edges render nameless.
        await _resolve_assoc_names()

         # 6. Recent episodes — prefer session-scoped when session_id is provided
        if session_id:
            # Owner-scoped and bounded in SQL. This was previously an unscoped
            # read, so a session id shared across household members could pull
            # the other member's turns straight into the prompt.
            session_episodes = await self.store.get_episodes_for_session(
                session_id, user_id=user_id, limit=settings.max_episodes_in_prompt
            )
            if len(session_episodes) >= 2:
                recent_episodes = session_episodes[-settings.max_episodes_in_prompt :]
            else:
                user_episodes = await self.store.get_episodes_for_user(
                    user_id, limit=settings.max_episodes_in_prompt * 2
                )
                recent_episodes = (session_episodes + user_episodes)[
                    -settings.max_episodes_in_prompt :
                ]
        else:
            recent_episodes = await self.store.get_episodes_for_user(
                user_id, limit=settings.max_episodes_in_prompt
            )

         # Build and return context
        past_conversations = await self._search_past_conversations(
            query_embedding, user_id, session_id
        )
        context = MemoryContext(
            query=query,
            retrieved_frames=retrieved_frames,
            recent_episodes=recent_episodes,
            past_conversations=past_conversations,
            formatted="",
            frame_names=frame_names,
         )
        context.formatted = format_memory_context(context)

        # Record all retrieved frame IDs in working memory (LRU tracking)
        if self.working_memory is not None and retrieved_frames:
            frame_ids = [rf.frame.id for rf in retrieved_frames]
            await self.working_memory.touch_frames(frame_ids)

        # Log retrieval for daily run frames (helps verify tuning)
        daily_run_frames = [rf for rf in retrieved_frames if rf.frame.name.startswith("daily_run_")]
        if daily_run_frames:
            logger.info("Retrieved daily run frames: %s (query: %s)",
                        [rf.frame.name for rf in daily_run_frames], query[:80])

        return context

    async def _search_past_conversations(
        self,
        query_embedding: list[float],
        user_id: int,
        session_id: str | None,
    ) -> list[tuple[Episode, float]]:
        """Semantic recall over archived conversation turns.

        Strictly owner-scoped; skips the current session (those turns are
        already present verbatim as chat history). Best-effort: if the
        episode_embeddings table has no rows for this model yet (fresh brain
        or pre-upgrade archive), this quietly returns nothing.
        """
        if settings.retrieval_episode_limit <= 0:
            return []
        try:
            hits = await self.store.search_similar_episodes(
                embedding=query_embedding,
                user_id=user_id,
                embedding_model=self.embedding_model,
                limit=settings.retrieval_episode_limit * 3,
                min_distance=settings.retrieval_min_distance,
                exclude_session_ids=[session_id] if session_id else None,
            )
        except Exception as exc:
            logger.warning("Episode recall unavailable: %s", exc)
            return []
        return [(ep, sim) for ep, sim in hits[: settings.retrieval_episode_limit]]

    async def _graph_walk(
        self,
        start_frame_id: int,
        query_embedding: list[float],
        max_hops: int,
        decay: float,
        user_id: int,
        limit: int | None = None,
    ) -> list[tuple[int, float, str]]:
        """Walk the association graph from start_frame_id, up to max_hops.

        Returns (frame_id, score, source) tuples, best-scoring first.
        Filters to frames owned by user_id or shared (owner_user_id IS NULL).

        Inclusion and ranking are deliberately separate questions:

        - *Inclusion* is topological: is the neighbour live, in scope, and
          reachable over an edge we trust (ASSOC_MIN_CONFIDENCE)? Association
          structure is not a similarity score and is never gated by
          min_relevance.
        - *Ranking* among followed edges uses the full quality product
          (relevance x decay x assoc.conf x assoc.prio x neighbour.conf x
          neighbour.prio), which is a good ordering signal but a bad gate.

        The previous version gated on that product directly. Because every factor
        is <= 1 and decay is 0.5, the best attainable hop-1 score is 0.5, so the
        four confidence factors would each have to average ~0.88 to clear
        min_relevance=0.3. The measured median was 0.038, and across 12 probe
        queries 0 of 877 reachable in-scope edges were admitted — the graph walk
        contributed no frames at all in production.

        The frontier carries *depth* (relevance x decay), not the full product.
        Propagating the product instead would compound the same collapse one hop
        further out and starve the walk at hop 2.

        `limit` caps how many neighbours are returned, keeping the best by score.
        Without it the walk is bounded only by the graph itself (809 frames at
        2 hops on the probe corpus), which overflows the system prompt cap and
        costs more than it can earn.
        """
        results: list[tuple[int, float, str]] = []
        visited: set[int] = {start_frame_id}
        frontier: list[tuple[int, float]] = [(start_frame_id, 1.0)]

        for hop in range(max_hops):
            # Two queries per hop, regardless of frontier width. Fetching edges
            # and neighbours one frame at a time cost ~800 SQLCipher connection
            # opens on a 2-hop walk, which measured at 7.4s per retrieval.
            frontier_ids = [fid for fid, _ in frontier]
            depth_of = dict(frontier)
            edges_by_frame = await self.store.get_all_associations_for_frames(frontier_ids)

            # (neighbour, the frontier frame it hangs off, the edge joining them)
            candidates: list[tuple[int, int, Association]] = []
            for fid in frontier_ids:
                for assoc in edges_by_frame.get(fid, []):
                    neighbor_id = (
                        assoc.to_frame_id
                        if assoc.from_frame_id == fid
                        else assoc.from_frame_id
                    )
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    candidates.append((neighbor_id, fid, assoc))

            if not candidates:
                break

            neighbors_by_id = await self.store.get_frames_by_ids(
                [nid for nid, _, _ in candidates]
            )

            next_frontier: list[tuple[int, float]] = []
            for neighbor_id, parent_id, assoc in candidates:
                neighbor = neighbors_by_id.get(neighbor_id)
                if neighbor is None:
                    continue
                # GC-tombstoned frames keep their edges but must never
                # re-enter context via the graph walk.
                if neighbor.deleted_at is not None:
                    continue
                if neighbor.owner_user_id not in (None, user_id):
                    continue
                # Inclusion: trust the edge, nothing else.
                if assoc.confidence < ASSOC_MIN_CONFIDENCE:
                    continue
                child_depth = depth_of[parent_id] * decay
                score = (
                    child_depth
                    * assoc.confidence
                    * assoc.priority
                    * neighbor.confidence
                    * neighbor.priority
                )
                results.append((neighbor_id, score, f"graph_hop_{hop + 1}"))
                next_frontier.append((neighbor_id, child_depth))
            frontier = next_frontier
            if not frontier:
                break

        results.sort(key=lambda r: r[1], reverse=True)
        if limit is not None:
            results = results[:limit]
        return results
