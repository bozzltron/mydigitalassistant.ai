"""Does the agent still know what was said 20 messages ago?

Pre-registered in `plan.md`. Two conditions against the same retrieval: the
production prompt (last six messages + retrieved memory), and that prompt with
the session's stored summary prepended. Search and tools are off — this measures
what the prompt carries.

Read-only: the store is opened for reads and the orchestrator is not used, so no
episode or frame is written.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.memory.retrieval import Retriever, format_memory_context
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import (
    ChatMessage,
    OllamaClient,
    build_system_prompt,
)

HERE = Path(__file__).parent
OUT = HERE / "result.json"
SESSION = "conv_f8df1353725c"
USER_ID = 1
REPLICATES = 3

# (question, gold tokens, group)
QUESTIONS = [
    (
        "What total profit did we calculate for the Gold Rush vinyl?",
        ["1,008", "1008"],
        "summary",
    ),
    (
        "Which platforms did we prioritise for Mozworth's outreach?",
        ["submitfinder"],
        "summary",
    ),
    (
        "How many Instagram followers does Mozworth have?",
        ["296"],
        "episode",
    ),
    (
        "How did we split the email broadcast segments?",
        ["non-austin", "non austin"],
        "episode",
    ),
    (
        'What release date did we set for "Why Not"?',
        ["oct 1", "october 1", "oct. 1"],
        "episode",
    ),
]

DECLINE_MARKERS = (
    "don't have",
    "do not have",
    "no record",
    "not in my memory",
    "can't find",
    "cannot find",
    "don't recall",
    "do not recall",
    "no information",
)

CONDITIONS = ("baseline", "summary")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace(",", ""))


def grade(answer: str, gold: list[str]) -> dict:
    a = norm(answer)
    return {
        "found": any(norm(g) in a for g in gold),
        "declined": any(m in a for m in DECLINE_MARKERS),
    }


async def main() -> int:
    store = MemoryStore(settings.database_path)
    client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
    )
    retriever = Retriever(store=store, llm_client=client)

    summary_frame = await store.get_frame_by_name(f"conversation_summary_{SESSION}")
    summary = ""
    if summary_frame:
        slots = {s.key: s.value for s in await store.get_slots_for_frame(summary_frame.id)}
        summary = slots.get("summary") or ""
    print(f"summary: {len(summary)} chars")

    # The six messages the production prompt carries, oldest first.
    episodes = await store.get_episodes_for_session(SESSION, user_id=USER_ID, limit=7)
    history = [
        ChatMessage(role=e.role, content=e.content or "")
        for e in episodes[:-1]
    ]

    rows: list[dict] = []
    for question, gold, group in QUESTIONS:
        context = await retriever.retrieve(question, user_id=USER_ID, session_id=SESSION)
        memory_text = format_memory_context(context)
        summary_in_context = (
            "conversation_summary" in memory_text or (summary and summary[:80] in memory_text)
        )
        for replicate in range(1, REPLICATES + 1):
            for condition in CONDITIONS:
                body = memory_text
                if condition == "summary":
                    body = f"## This conversation so far\n{summary}\n\n{memory_text}"
                system_prompt = build_system_prompt(body, task_type="functional")
                response = await client.chat(
                    [
                        ChatMessage(role="system", content=system_prompt),
                        *history,
                        ChatMessage(role="user", content=question),
                    ],
                    model=client.chat_model,
                    temperature=0.7,
                    think=False,
                )
                answer = (response.content or "").strip()
                row = {
                    "question": question,
                    "group": group,
                    "condition": condition,
                    "replicate": replicate,
                    **grade(answer, gold),
                    "summary_in_retrieved_context": summary_in_context,
                    "prompt_tokens": response.prompt_eval_count,
                    "answer": answer,
                }
                rows.append(row)
                print(
                    f"  [{condition} r{replicate}] {group:7} found={row['found']} "
                    f"declined={row['declined']} tokens={row['prompt_tokens']} :: "
                    f"{question[:46]}"
                )

    def rate(condition: str, group: str | None = None, field: str = "found") -> float:
        subset = [
            r
            for r in rows
            if r["condition"] == condition and (group is None or r["group"] == group)
        ]
        return round(sum(r[field] for r in subset) / len(subset), 3) if subset else 0.0

    summary_stats = {
        "recall": {
            "baseline": rate("baseline"),
            "summary": rate("summary"),
            "baseline_summary_q": rate("baseline", "summary"),
            "summary_summary_q": rate("summary", "summary"),
            "baseline_episode_q": rate("baseline", "episode"),
            "summary_episode_q": rate("summary", "episode"),
        },
        "declines": {
            "baseline": rate("baseline", field="declined"),
            "summary": rate("summary", field="declined"),
        },
        "prompt_tokens": {
            c: round(
                sum(r["prompt_tokens"] for r in rows if r["condition"] == c)
                / max(1, sum(1 for r in rows if r["condition"] == c)),
                0,
            )
            for c in CONDITIONS
        },
        "summary_chars": len(summary),
        "summary_already_retrieved": any(
            r["summary_in_retrieved_context"] for r in rows
        ),
        "history_messages": len(history),
    }
    payload = {
        "started": datetime.now(UTC).isoformat(),
        "session": SESSION,
        "model": client.chat_model,
        "replicates": REPLICATES,
        "summary": summary_stats,
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(summary_stats, indent=2))
    print(f"\nwrote {OUT}")
    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
