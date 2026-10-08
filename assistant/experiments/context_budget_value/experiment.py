"""Does the context budget earn its place? Pre-registered in ``plan.md``.

Builds a scratch database and sandbox (never the live brain), constructs the real
tool-loop prompt with the real builtin tools, installs the budget from the
measured components, and drives ``stream_tool_loop`` with the local Ollama model.

    python assistant/experiments/context_budget_value/experiment.py

Writes ``result.json`` next to this file. ``result.md`` only after
``verification.md``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline import filesystem
from assistant.backend.pipeline.context_budget import measure_budget, set_turn_budget
from assistant.backend.pipeline.llm_client import (
    OllamaClient,
    build_system_prompt,
)
from assistant.backend.pipeline.search import WebSearchTool
from assistant.backend.pipeline.streaming import stream_tool_loop
from assistant.backend.pipeline.tool_executor import init_store
from assistant.backend.pipeline.tools import builtin_tools

HERE = Path(__file__).resolve().parent

BIG_CSV = "subscribers_active.csv"
BIG_CSV_B = "subscribers_archived.csv"
BIG_CSV_ROWS = 6000
BIG_CSV_B_ROWS = 5000
MANY_FILES = 300
# A long history: enough that the allowance visibly shrinks for scenario 4.
LONG_HISTORY_TURNS = settings.verbatim_history_turns
LONG_HISTORY_CHARS = 3000  # per turn

SCENARIOS: list[tuple[str, str, bool]] = [
    # name, user message, use a long history
    ("read_big_csv", f"Read {BIG_CSV} and tell me how many rows it has.", False),
    (
        "compare_two_csvs",
        f"Compare {BIG_CSV} and {BIG_CSV_B} and tell me which has more rows.",
        False,
    ),
    ("long_history", "What did we decide about the launch?", True),
]


def _seed_sandbox() -> list[Path]:
    root = filesystem.SANDBOX_ROOT
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    big = root / BIG_CSV
    big.write_text(
        "email,name,state\n"
        + "\n".join(f"user{i}@example.com,Name {i},TX" for i in range(BIG_CSV_ROWS)),
        encoding="utf-8",
    )
    created.append(big)
    big_b = root / BIG_CSV_B
    big_b.write_text(
        "email,name,state\n"
        + "\n".join(f"old{i}@example.com,Old {i},CA" for i in range(BIG_CSV_B_ROWS)),
        encoding="utf-8",
    )
    created.append(big_b)
    for i in range(MANY_FILES):
        p = root / f"note_{i:03d}.txt"
        p.write_text(f"note {i}\n", encoding="utf-8")
        created.append(p)
    return created


async def _run_scenario(
    name: str,
    user_message: str,
    long_history: bool,
    *,
    store: MemoryStore,
    llm: OllamaClient,
    tools: list[dict],
    user_id: int,
) -> dict:
    from types import SimpleNamespace

    from assistant.backend.pipeline.orchestrator import Orchestrator

    if long_history:
        raw_history = [
            SimpleNamespace(role="assistant", content="x" * LONG_HISTORY_CHARS)
            for _ in range(LONG_HISTORY_TURNS)
        ]
    else:
        raw_history = [SimpleNamespace(role="assistant", content="ok")]
    # Apply the production history bound (T4) -- otherwise the harness measures a
    # prompt production would never build, and the history change is invisible.
    history = Orchestrator._bounded_history(raw_history)

    system_prompt = build_system_prompt(
        memory_context="", task_type="functional", current_datetime=""
    )
    fixed_chars = (
        len(system_prompt)
        + len(json.dumps(tools))
        + sum(len(m.content) for m in history)
        + len(user_message)
    )
    window = llm.context_window(settings.tools_model)
    budget = measure_budget(
        window,
        system_prompt_chars=len(system_prompt),
        tool_schema_chars=len(json.dumps(tools)),
        history_chars=sum(len(m.content) for m in history),
        user_message_chars=len(user_message),
    )
    token = set_turn_budget(budget)
    messages = (
        [{"role": "system", "content": system_prompt}]
        + [{"role": m.role, "content": m.content} for m in history]
        + [{"role": "user", "content": user_message}]
    )
    finalize: dict = {}
    try:
        async for event in stream_tool_loop(
            llm,
            messages,
            tools,
            model=settings.tools_model,
            user_id=str(user_id),
            session_id="context-budget-experiment",
            max_turns=settings.max_tool_rounds,
        ):
            data = json.loads(event.replace("data: ", "").strip())
            if data.get("type") == "finalize":
                finalize = data
    finally:
        from assistant.backend.pipeline.context_budget import reset_turn_budget

        reset_turn_budget(token)

    peak = finalize.get("prompt_tokens") or 0
    window = finalize.get("context_window") or window
    return {
        "scenario": name,
        "fixed_cost_chars": fixed_chars,
        "allowance_chars": budget.content_chars,
        "allowance_tokens": budget.content_tokens,
        "prompt_tokens": peak,
        "context_window": window,
        "truncated": bool(window) and peak >= window,
        "tool_result_chars": finalize.get("tool_result_chars") or 0,
        "tool_results_dropped": finalize.get("tool_results_dropped") or 0,
        "answer_head": (finalize.get("answer") or "")[:200],
    }


async def _main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "experiment.db")
        await init_db(db_path)
        store = MemoryStore(db_path)
        user = await store.create_user("experiment")

        llm = OllamaClient(
            base_url=settings.ollama_url,
            chat_model=settings.chat_model,
            utility_model=settings.utility_model,
            embedding_model=settings.embedding_model,
            tools_model=settings.tools_model,
            chat_num_ctx=settings.chat_num_ctx,
            tools_num_ctx=settings.tools_num_ctx,
            utility_num_ctx=settings.utility_num_ctx,
        )
        search_tool = WebSearchTool(enabled=False)
        init_store(db_path, embed_fn=llm.embed_one, embedding_model=settings.embedding_model)
        tools = builtin_tools(
            search_tool, store=store, llm_client=llm, embed_fn=llm.embed_one
        )

        created = _seed_sandbox()
        try:
            results = []
            for name, message, long_history in SCENARIOS:
                print(f"\n[{name}] {message[:60]}")
                row = await _run_scenario(
                    name,
                    message,
                    long_history,
                    store=store,
                    llm=llm,
                    tools=tools,
                    user_id=user.id or 1,
                )
                results.append(row)
                print(
                    f"  allowance={row['allowance_chars']} chars  "
                    f"peak={row['prompt_tokens']}/{row['context_window']} tok  "
                    f"truncated={row['truncated']}  "
                    f"tool_result_chars={row['tool_result_chars']}  "
                    f"dropped={row['tool_results_dropped']}"
                )
            (HERE / "result.json").write_text(json.dumps(results, indent=2))
            print(f"\nwrote {HERE / 'result.json'}")
        finally:
            for p in created:
                p.unlink(missing_ok=True)
            await llm.close()
            await search_tool.close()


if __name__ == "__main__":
    asyncio.run(_main())
