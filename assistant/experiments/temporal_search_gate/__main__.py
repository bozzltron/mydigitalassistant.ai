"""Should a confident "needs current info" outrank sufficient memory?

Pre-registered in `plan.md`. The search decision is deterministic given two inputs:
the router's `wants_search` (a model judgement) and the reasoner's sufficiency
(a heuristic). This harness records the judgement once per query and applies both
policies to it, against a memory context that is deliberately *sufficient* — the
live failure had relevant memory and still did not refresh.

No search is executed and no answer is generated: the measurement is the decision.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.memory.models import Frame
from assistant.backend.memory.retrieval import MemoryContext, RetrievedFrame
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.reasoner import (
    MemorySufficiency,
    assess_memory_sufficiency,
)
from assistant.backend.pipeline.task_router import classify_with_llm

HERE = Path(__file__).parent
OUT = HERE / "result.json"
REPLICATES = 3

PERIOD_QUERIES = [
    "What's NASA's image of the day?",
    "What's the price of bitcoin right now?",
    "What's the weather today?",
    "What's on my calendar this afternoon?",
    "What's the latest version of Python?",
    "Any news from the music industry this morning?",
    "What's the current stock price of NVIDIA?",
    "Who won the game tonight?",
    "What's the release date for the next iPhone?",
    "What's trending on YouTube today?",
]

STABLE_QUERIES = [
    "How many strings does a standard guitar have?",
    "What's my name?",
    "What did I tell you about my dog?",
    "Explain how a rainbow forms.",
    "What's the capital of France?",
    "What files do I have?",
    "Remember that I prefer tea over coffee.",
    "How do I convert Celsius to Fahrenheit?",
    "What do you know about my sister?",
    "Define photosynthesis.",
]

POLICIES = ("baseline", "router_positive")


def sufficient_memory(query: str) -> MemoryContext:
    """A relevant frame, the shape that suppresses search today."""
    frame = Frame(id=1, name="nasa", type="entity", confidence=0.8)
    rf = RetrievedFrame(
        frame=frame, slots=[], associations=[], relevance=0.85, source="direct_match"
    )
    return MemoryContext(
        query=query, retrieved_frames=[rf], recent_episodes=[], formatted=""
    )


def decides(wants_search: bool | None, sufficiency: MemorySufficiency, policy: str) -> bool:
    """The orchestrator's gate (`orchestrator.py:1567-1591`), parameterised.

    baseline: search only when memory is NONE, with the router veto/force applied.
    router_positive: the model's judged need for current information wins over
    sufficient memory; the veto still applies (a storage-style turn never searches).
    """
    search = sufficiency == MemorySufficiency.NONE
    if wants_search is False:
        return False  # the veto, unchanged in both policies
    if wants_search is True:
        if policy == "router_positive":
            return True
        if sufficiency == MemorySufficiency.NONE:
            return True
    return search


async def main() -> int:
    client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
    )
    print(f"router model={client.utility_model}")

    rows: list[dict] = []
    for group, queries in (("period", PERIOD_QUERIES), ("stable", STABLE_QUERIES)):
        for query in queries:
            memory = sufficient_memory(query)
            sufficiency, _ = assess_memory_sufficiency(query, memory)
            labels: list[bool | None] = []
            for _ in range(REPLICATES):
                result = await classify_with_llm(query, client)
                labels.append(result.wants_search)
            # Majority for the decision; flapping is reported, not hidden.
            true_count = sum(1 for label in labels if label is True)
            wants = True if true_count * 2 > REPLICATES else False
            row = {
                "group": group,
                "query": query,
                "labels": labels,
                "flapped": len(set(labels)) > 1,
                "wants_search": wants,
                "sufficiency": sufficiency.value,
                "decisions": {
                    p: decides(wants, sufficiency, p) for p in POLICIES
                },
            }
            rows.append(row)
            print(
                f"  [{group}] {query[:44]:<44} wants={wants} suff={sufficiency.value} "
                f"baseline={row['decisions']['baseline']} "
                f"router_positive={row['decisions']['router_positive']}"
            )

    # Manipulation validity: the context must be sufficient, or the experiment is
    # not testing the gate at all.
    assert all(r["sufficiency"] == "high" for r in rows), "memory was not sufficient"

    def rate(group: str, policy: str) -> float:
        subset = [r for r in rows if r["group"] == group]
        return round(sum(r["decisions"][policy] for r in subset) / len(subset), 3)

    summary = {
        "search_rate": {
            group: {p: rate(group, p) for p in POLICIES}
            for group in ("period", "stable")
        },
        "router_recall_period": round(
            sum(r["wants_search"] for r in rows if r["group"] == "period")
            / len(PERIOD_QUERIES),
            3,
        ),
        "router_false_positive_stable": round(
            sum(r["wants_search"] for r in rows if r["group"] == "stable")
            / len(STABLE_QUERIES),
            3,
        ),
        "flapped_queries": [r["query"] for r in rows if r["flapped"]],
        "replicates": REPLICATES,
    }
    payload = {
        "started": datetime.now(UTC).isoformat(),
        "model": client.utility_model,
        "summary": summary,
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT}")
    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
