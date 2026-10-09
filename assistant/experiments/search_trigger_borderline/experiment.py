"""Does the router search on borderline queries? Pre-registered in ``plan.md``.

Borderline = the answer could come from knowledge but would be better with a
current source (recommendations, "recent research", "current best practices").
Control = stable, definitional knowledge that should not search.

Drives the decision path only — router (``route``) → reasoner
(``classify_intent``) → the storage veto, as in ``_run_turn``. No generation, no
brain writes.

    python assistant/experiments/search_trigger_borderline/experiment.py

Writes ``result.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import MemoryContext
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.reasoner import MemorySufficiency, classify_intent
from assistant.backend.pipeline.task_router import TaskType, route

HERE = Path(__file__).resolve().parent

# ("borderline_search" | "general_knowledge", query)
QUERIES: list[tuple[str, str]] = [
    # Borderline: a current source would improve the answer.
    ("borderline_search", "What's the best electric guitar for a beginner?"),
    ("borderline_search", "Explain recent research on lithium-ion battery degradation."),
    ("borderline_search", "What are the current best practices for password managers?"),
    ("borderline_search", "Which laptop should I buy for video editing?"),
    ("borderline_search", "Summarize the latest research on sleep and memory."),
    ("borderline_search", "What's the current state of quantum computing?"),
    ("borderline_search", "Are standing desks worth it?"),
    ("borderline_search", "What's a good budget air fryer?"),
    ("borderline_search", "Is intermittent fasting still recommended?"),
    ("borderline_search", "Compare the top project management tools."),
    # Control: stable, definitional knowledge; no search needed.
    ("general_knowledge", "Explain how photosynthesis works."),
    ("general_knowledge", "What is a confidence interval?"),
    ("general_knowledge", "How do I convert Celsius to Fahrenheit?"),
    ("general_knowledge", "Explain recursion with an example."),
    ("general_knowledge", "What causes a rainbow?"),
    ("general_knowledge", "What is the Pythagorean theorem?"),
    ("general_knowledge", "Explain the difference between TCP and UDP."),
    ("general_knowledge", "What is the difference between affect and effect?"),
]


async def _decide(query: str, llm: OllamaClient) -> dict:
    classification = await route(query, llm)
    empty = MemoryContext(
        query=query, retrieved_frames=[], recent_episodes=[], formatted=""
    )
    plan = classify_intent(query, classification.task_type.value, empty)

    # The router's search decision from _run_turn: veto on an explicit False,
    # force on an explicit True when nothing relevant is stored.
    searches = plan.search_needed
    vetoed = (
        plan.search_needed
        and classification.task_type != TaskType.SEARCH
        and classification.wants_search is False
    )
    if vetoed:
        searches = False
    forced = (
        not plan.search_needed
        and classification.task_type != TaskType.SEARCH
        and classification.wants_search is True
        and plan.sufficiency == MemorySufficiency.NONE
    )
    if forced:
        searches = True

    return {
        "query": query,
        "task_type": classification.task_type.value,
        "wants_search": classification.wants_search,
        "search_query": classification.search_query,
        "search_needed": plan.search_needed,
        "vetoed": vetoed,
        "forced": forced,
        "final_search": searches,
    }


async def main() -> None:
    llm = OllamaClient(
        base_url=settings.ollama_url,
        utility_model=settings.utility_model,
        utility_num_ctx=settings.utility_num_ctx,
    )
    rows: list[dict] = []
    try:
        for klass, query in QUERIES:
            row = await _decide(query, llm)
            row["class"] = klass
            rows.append(row)
            flag = "SEARCH" if row["final_search"] else "  no  "
            print(
                f"  [{klass:17}] {flag}  wants={row['wants_search']!s:5} "
                f"q={row['search_query']!r}"
            )
    finally:
        await llm.close()

    border = [r for r in rows if r["class"] == "borderline_search"]
    general = [r for r in rows if r["class"] == "general_knowledge"]
    b_hits = sum(1 for r in border if r["final_search"])
    g_correct = sum(1 for r in general if not r["final_search"])
    b_recall = b_hits / len(border) * 100
    g_spec = g_correct / len(general) * 100
    print(f"\nborderline recall:       {b_recall:.0f}% ({b_hits}/{len(border)})")
    print(f"general-knowledge spec:  {g_spec:.0f}% ({g_correct}/{len(general)})")
    print("\nmissed borderline (should search, did not):")
    for r in border:
        if not r["final_search"]:
            print(f"  - {r['query']!r} (wants_search={r['wants_search']})")
    print("false searches (general knowledge, did search):")
    for r in general:
        if r["final_search"]:
            print(f"  - {r['query']!r} (wants_search={r['wants_search']})")

    out = Path(os.environ.get("RESULT_PATH", HERE / "result.json"))
    out.write_text(
        json.dumps(
            {
                "borderline_recall_pct": round(b_recall, 1),
                "general_knowledge_specificity_pct": round(g_spec, 1),
                "rows": rows,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
