from dataclasses import dataclass
from typing import TYPE_CHECKING

from assistant.backend.memory.models import Association, Episode, Frame, Slot

if TYPE_CHECKING:
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import OllamaClient


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


def frame_to_text(frame: Frame, slots: list[Slot]) -> str:
    """Convert a frame + its slots to embeddable text."""
    parts = [f"{frame.type}: {frame.name}"]
    for slot in slots:
        parts.append(f"  {slot.key} = {slot.value} (confidence: {slot.confidence:.2f})")
    return "\n".join(parts)


def format_memory_context(context: "MemoryContext") -> str:
    """Format a MemoryContext as structured text for LLM injection."""
    lines: list[str] = []
    if context.retrieved_frames:
        lines.append("## Relevant memory")
        for rf in context.retrieved_frames[:5]:
            lines.append(
                f"\n### {rf.frame.name} ({rf.frame.type}) [relevance: {rf.relevance:.2f}]"
            )
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
                slot_line = (
                    f"  - {slot.key} = {slot.value} "
                    f"(conf: {slot.confidence:.2f}{source_note})"
                )
                lines.append(slot_line)
            if rf.associations:
                assoc_str = ", ".join(
                    f"{a.relation_type}\u2192frame:{a.to_frame_id}" for a in rf.associations[:3]
                )
                lines.append(f"  relations: {assoc_str}")
    if context.recent_episodes:
        lines.append("\n## Recent conversation (this session)")
        for ep in context.recent_episodes[-10:]:
            lines.append(f"   [{ep.role}] {ep.content}")
    return "\n".join(lines) if lines else "(no relevant memory found)"


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
        top_k_direct: int = 5,
        graph_hops: int = 2,
        graph_decay: float = 0.5,  # relevance decay per hop
        min_relevance: float = 0.3,
    ):
        self.store = store
        self.llm_client = llm_client
        self.top_k_direct = top_k_direct
        self.graph_hops = graph_hops
        self.graph_decay = graph_decay
        self.min_relevance = min_relevance

    async def embed_frame(self, frame: Frame, slots: list[Slot]) -> list[float]:
        """Embed a frame and store its embedding."""
        text = frame_to_text(frame, slots)
        response = await self.llm_client.embed(text)
        await self.store.store_frame_embedding(frame.id, response.embedding)
        return response.embedding

    async def retrieve(
        self,
        query: str,
        user_id: int,
        session_id: str | None = None,
     ) -> MemoryContext:
        """Retrieve relevant memory for a query.

        Returns MemoryContext with retrieved frames and recent episodes.
        Uses sqlite-vec vec_distance_cosine() for efficient similarity search.
        """
         # 1. Embed query
        query_response = await self.llm_client.embed(query)
        query_embedding = query_response.embedding

         # 2. Vector similarity search via sqlite-vec
        all_results = await self.store.search_similar_frames(
            embedding=query_embedding,
            user_id=user_id,
            limit=self.top_k_direct * 2,  # fetch more to account for graph neighbors
            min_distance=0.7,
         )

        if not all_results:
             # No frames in memory yet — just return empty context
            recent = await self.store.get_episodes_for_user(user_id, limit=10)
            empty = MemoryContext(
                query=query,
                retrieved_frames=[],
                recent_episodes=recent,
                formatted="(no memory frames yet)",
             )
            empty.formatted = format_memory_context(empty)
            return empty

         # 3. Filter by distance threshold and sort by similarity × confidence × priority
        scored: list[tuple[int, float, str]] = [
            (frame.id, similarity * frame.confidence * frame.priority, "direct_match")
            for frame, _slots, similarity in all_results
            if similarity >= self.min_relevance
        ]
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

         # Combine and sort
        all_relevant = top_direct + graph_neighbors
        all_relevant.sort(key=lambda x: x[1], reverse=True)

         # 5. Assemble RetrievedFrame objects
        retrieved_frames: list[RetrievedFrame] = []
        for frame_id, relevance, source in all_relevant:
            frame = await self.store.get_frame(frame_id)
            if frame is None:
                continue
            slots = await self.store.get_slots_for_frame(frame_id)
            assocs = await self.store.get_all_associations_for_frame(frame_id)
            retrieved_frames.append(
                RetrievedFrame(
                    frame=frame,
                    slots=slots,
                    associations=assocs,
                    relevance=relevance,
                    source=source,
                 )
             )

         # 6. Recent episodes — prefer session-scoped when session_id is provided
        if session_id:
            session_episodes = await self.store.get_episodes_for_session(session_id)
            if len(session_episodes) >= 2:
                recent_episodes = session_episodes
            else:
                user_episodes = await self.store.get_episodes_for_user(user_id, limit=20)
                recent_episodes = session_episodes + user_episodes
        else:
            recent_episodes = await self.store.get_episodes_for_user(user_id, limit=10)

         # Build and return context
        context = MemoryContext(
            query=query,
            retrieved_frames=retrieved_frames,
            recent_episodes=recent_episodes,
            formatted="",
         )
        context.formatted = format_memory_context(context)
        return context

    async def _graph_walk(
        self,
        start_frame_id: int,
        query_embedding: list[float],
        max_hops: int,
        decay: float,
        user_id: int,
    ) -> list[tuple[int, float, str]]:
        """Walk the association graph from start_frame_id, up to max_hops.

        Returns (frame_id, relevance, source) tuples.
        Filters to frames owned by user_id or shared (owner_user_id IS NULL).
        Applies frame.confidence × frame.priority to relevance score.
        """
        results: list[tuple[int, float, str]] = []
        visited: set[int] = {start_frame_id}
        frontier: list[tuple[int, float]] = [(start_frame_id, 1.0)]

        for hop in range(max_hops):
            next_frontier: list[tuple[int, float]] = []
            for frame_id, relevance in frontier:
                assocs = await self.store.get_all_associations_for_frame(frame_id)
                for assoc in assocs:
                    neighbor_id = (
                        assoc.to_frame_id
                        if assoc.from_frame_id == frame_id
                        else assoc.from_frame_id
                    )
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    neighbor = await self.store.get_frame(neighbor_id)
                    if neighbor is None:
                        continue
                    if neighbor.owner_user_id not in (None, user_id):
                        continue
                    score = (
                        relevance
                        * decay
                        * assoc.confidence
                        * assoc.priority
                        * neighbor.confidence
                        * neighbor.priority
                    )
                    if score >= self.min_relevance:
                        results.append((neighbor_id, score, f"graph_hop_{hop + 1}"))
                        next_frontier.append((neighbor_id, score))
            frontier = next_frontier
            if not frontier:
                break

        return results
