"""Does the assistant search when it should? Pre-registered in ``plan.md``.

Drives the decision path only — router (``route``) → reasoner
(``classify_intent``) → the storage veto, as in ``_run_turn`` — with the utility
model. No generation, no brain writes.

    python assistant/experiments/search_trigger/experiment.py

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
from assistant.backend.pipeline.reasoner import classify_intent
from assistant.backend.pipeline.task_router import TaskType, route

HERE = Path(__file__).resolve().parent

# ("must_search" | "must_not_search", query)
QUERIES: list[tuple[str, str]] = [
    # Needs the internet: current, live, or external facts the user cannot know.
    ("must_search", "What are the latest developments in celestial holography?"),
    ("must_search", "What is the current price of a Nintendo Switch 2?"),
    ("must_search", "Who won the most recent Austin mayoral election?"),
    ("must_search", "What events are happening in Austin this weekend?"),
    ("must_search", "Is the new Dune movie out yet?"),
    ("must_search", "What is the weather in Chicago tomorrow?"),
    ("must_search", "What is the latest news about the James Webb telescope?"),
    ("must_search", "What are the 2026 ACL submission deadlines?"),
    # Must NOT search: storage, the user's own files/memory, general knowledge.
    ("must_not_search", "My name is Ada and I live in Austin."),
    ("must_not_search", "Remember that I drive a Subaru Outback."),
    ("must_not_search", "What files do I have?"),
    ("must_not_search", "What is my name?"),
    ("must_not_search", "Explain what a confidence interval is."),
    ("must_not_search", "What did we decide about the launch?"),
    ("must_not_search", "Add eggs to my shopping list."),
]


async def _decide(query: str, llm: OllamaClient) -> dict:
    classification = await route(query, llm)
    # The reasoner runs on an empty context in production only when memory is
    # empty; here it isolates the trigger. Same call signature as _run_turn.
    empty = MemoryContext(
        query=query, retrieved_frames=[], recent_episodes=[], formatted=""
    )
    plan = classify_intent(query, classification.task_type.value, empty)

    # The storage veto from _run_turn, verbatim.
    searches = plan.search_needed
    vetoed = (
        plan.search_needed
        and classification.task_type != TaskType.SEARCH
        and classification.wants_search is False
    )
    if vetoed:
        searches = False

    return {
        "query": query,
        "task_type": classification.task_type.value,
        "wants_search": classification.wants_search,
        "search_query": classification.search_query,
        "search_needed": plan.search_needed,
        "vetoed": vetoed,
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
                f"  [{klass:15}] {flag}  wants={row['wants_search']!s:5} "
                f"q={row['search_query']!r}"
            )
    finally:
        await llm.close()

    must = [r for r in rows if r["class"] == "must_search"]
    must_not = [r for r in rows if r["class"] == "must_not_search"]
    hits = sum(1 for r in must if r["final_search"])
    correct = sum(1 for r in must_not if not r["final_search"])
    recall = hits / len(must) * 100
    specificity = correct / len(must_not) * 100
    print(f"\nmust-search recall:      {recall:.0f}% ({hits}/{len(must)})")
    print(f"must-not specificity:    {specificity:.0f}% ({correct}/{len(must_not)})")
    print("\nmissed (should search, did not):")
    for r in must:
        if not r["final_search"]:
            print(f"  - {r['query']!r} (wants_search={r['wants_search']})")
    print("false searches (should not, did):")
    for r in must_not:
        if r["final_search"]:
            print(f"  - {r['query']!r} (wants_search={r['wants_search']})")

    out = Path(os.environ.get("RESULT_PATH", HERE / "result.json"))
    out.write_text(
        json.dumps(
            {
                "must_search_recall_pct": round(recall, 1),
                "must_not_specificity_pct": round(specificity, 1),
                "rows": rows,
            },
            indent=2,
        )
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
