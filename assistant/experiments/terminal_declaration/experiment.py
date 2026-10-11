"""Does an explicit terminal declaration beat the `needs_action` judgement?

Pre-registered in plan.md. Two policies, the same requests, the real chat model:

- **C (current):** the current tool contract; the nudge armed by ``needs_action``.
- **F (proposed):** a contract that requires ending with ``finalize``; the nudge armed
  structurally (no ``finalize`` seen).

Arms are interleaved per request so both see the same state.

    EXP_SANDBOX=/tmp/td_scratch EXP_DB=/tmp/td_scratch/assistant.db \
        python assistant/experiments/terminal_declaration/experiment.py

Scratch store and sandbox only (aborts if pointed at the live sandbox). Needs local
Ollama; no brain. Writes ``result.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

# --- scratch-only guard, asserted before anything else -------------------------

LIVE_SANDBOX = Path("/app/data")


def _scratch() -> Path:
    raw = os.environ.get("EXP_SANDBOX", "")
    if not raw:
        # Default to a temp dir so the probe is runnable without ceremony, but never
        # inside the live sandbox.
        raw = tempfile.mkdtemp(prefix="td_scratch_")
    scratch = Path(raw).resolve()
    if scratch == LIVE_SANDBOX.resolve() or LIVE_SANDBOX in scratch.parents:
        print(f"ABORT: EXP_SANDBOX={scratch} is inside the live sandbox {LIVE_SANDBOX}")
        sys.exit(2)
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


SCRATCH = _scratch()
DB_PATH = os.environ.get("EXP_DB", str(SCRATCH / "assistant.db"))

# The filesystem module resolves every path against SANDBOX_ROOT; patch it to scratch
# before any file is touched.
import assistant.backend.pipeline.filesystem as _fs  # noqa: E402

_fs.SANDBOX_ROOT = SCRATCH

os.environ.setdefault("OLLAMA_URL", OLLAMA)

from assistant.backend.config import settings  # noqa: E402
from assistant.backend.db.schema import init_db  # noqa: E402
from assistant.backend.memory.store import MemoryStore  # noqa: E402
from assistant.backend.pipeline import decision, tool_executor  # noqa: E402
from assistant.backend.pipeline.llm_client import (  # noqa: E402
    _TOOL_CONTRACT_TERMINAL,
    _TOOL_CONTRACT_TERMINAL_TIGHT,
    OllamaClient,
    build_system_prompt,
)
from assistant.backend.pipeline.streaming import stream_tool_loop  # noqa: E402
from assistant.backend.pipeline.tools import builtin_tools  # noqa: E402

settings.ollama_url = OLLAMA

# (class, request). "announce" is the phrasing the original live failure used.
REQUESTS: list[tuple[str, str]] = [
    ("answer", "What is my name?"),
    ("answer", "Explain how photosynthesis works"),
    ("answer", "What's the capital of France?"),
    ("answer", "Thanks, that's helpful"),
    ("answer", "Why is the sky blue?"),
    ("change", "Create a file called {name} with a short release checklist"),
    ("change", "We want to create {name} listing three tour cities"),
    ("change", "Please create {name} with a one-paragraph band bio"),
    ("change", "Write {name} with today's plan"),
    ("announce", "No edits to the notes. We want to create {name}"),
    ("announce", "Can you create {name} with a generic press release template?"),
]


def _parse(raw: list[str]) -> list[dict]:
    out = []
    for line in raw:
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[6:]))
        except Exception:
            continue
    return out


def _shape(raw: list[str]) -> dict:
    events = _parse(raw)
    tool_names: list[str] = []
    acting = 0
    for d in events:
        if d.get("type") == "tool_call":
            tool_names += [c.get("name") for c in (d.get("tool_calls") or [])]
        elif d.get("type") == "stage" and d.get("stage") == "acting":
            acting += 1
    return {"tool_names": tool_names, "finalized": "finalize" in tool_names, "acting": acting}


class CountingClient:
    """Forward everything to the real client; count model rounds."""

    def __init__(self, inner):
        self._inner = inner
        self.rounds = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def chat(self, *a, **k):
        self.rounds += 1
        return await self._inner.chat(*a, **k)


async def run_turn(policy: str, request: str, name: str, tools: list[dict]) -> dict:
    """One turn under ``policy`` ('C' judged | 'F' terminal | 'F2' terminal-tight)."""
    require_terminal = policy in ("F", "F2")
    contract = {
        "C": None,
        "F": _TOOL_CONTRACT_TERMINAL,
        "F2": _TOOL_CONTRACT_TERMINAL_TIGHT,
    }[policy]
    system = build_system_prompt(
        memory_context="",
        task_type="functional",
        contract=contract,
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": request},
    ]
    expects_action = False
    if not require_terminal:
        expects_action = bool(await decision.needs_action(request))

    client = CountingClient(OllamaClient(base_url=OLLAMA))
    started = time.time()
    raw = [
        ev
        async for ev in stream_tool_loop(
            client,
            messages,
            tools=tools,
            user_id="1",
            expects_action=expects_action,
            require_terminal=require_terminal,
        )
    ]
    shape = _shape(raw)
    shape.update(
        {
            "policy": policy,
            "request": request,
            "name": name,
            "expects_action": expects_action,
            "rounds": client.rounds,
            "acted": (SCRATCH / name).exists(),
            "wall_s": round(time.time() - started, 1),
        }
    )
    return shape


async def arm_deterministic() -> dict:
    """H4: a prose-ending turn is re-prompted structurally, with no judgement."""
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall

    calls = {"outstanding": 0}

    async def fake_outstanding(request, tools_called):
        calls["outstanding"] += 1
        return True

    real = decision.outstanding
    decision.outstanding = fake_outstanding

    class Scripted:
        tools_model = "test-model"

        def __init__(self):
            self.rounds = 0

        async def chat(self, messages, **kwargs):
            self.rounds += 1
            if self.rounds == 1:
                return ChatResponse(content="I'll now create the file.", model="m", done=True)
            return ChatResponse(
                content="",
                model="m",
                done=True,
                tool_calls=[ToolCall(name="finalize", arguments={"answer": "Done."})],
            )

        def context_window(self, model=None):
            return 16384

    try:
        llm = Scripted()
        raw = [
            ev
            async for ev in stream_tool_loop(
                llm,
                [{"role": "user", "content": "Create term_x.md"}],
                tools=[],
                require_terminal=True,
            )
        ]
        shape = _shape(raw)
        shape["rounds"] = llm.rounds
        shape["outstanding_consulted"] = calls["outstanding"]
        print(
            f"  [D terminal] rounds={shape['rounds']} finalized={shape['finalized']} "
            f"acting={shape['acting']} outstanding_consulted={shape['outstanding_consulted']}"
        )
        return shape
    finally:
        decision.outstanding = real


async def main() -> None:
    await init_db(DB_PATH)
    store = MemoryStore(DB_PATH)
    tool_executor._store = store
    await store.create_user("exp_user")

    tools = builtin_tools(
        None, store=store, llm_client=OllamaClient(base_url=OLLAMA), embed_fn=None
    )

    print("[D] deterministic -- the structural catch, no judgement")
    deterministic = await arm_deterministic()

    print("\n[live] C = judged, F = terminal, F2 = terminal-tight (interleaved, paired)")
    rows: list[dict] = []
    policies = ["C", "F", "F2"]
    for i, (cls, tmpl) in enumerate(REQUESTS):
        # Rotate the order per request, to control for warm-up/state order.
        order = policies[i % 3:] + policies[: i % 3]
        for policy in order:
            name = f"term_{policy.lower()}_{i}.md"
            request = tmpl.format(name=name)
            try:
                row = await run_turn(policy, request, name, tools)
            except Exception as e:
                row = {"policy": policy, "request": request, "error": f"{type(e).__name__}: {e}"}
            row["class"] = cls
            rows.append(row)
            print(
                f"  [{cls:8} {policy}] finalized={row.get('finalized')} acted={row.get('acted')} "
                f"rounds={row.get('rounds')} acting={row.get('acting')} {row.get('wall_s')}s "
                f"tools={row.get('tool_names')} {row.get('error', '')}"
            )

    ok = [r for r in rows if "error" not in r]

    def rate(policy: str, cls: str, field: str) -> str:
        rs = [r for r in ok if r["policy"] == policy and r["class"] == cls]
        if not rs:
            return "n/a"
        n = sum(1 for r in rs if r.get(field))
        return f"{n}/{len(rs)}"

    def mean_rounds(policy: str) -> float:
        rs = [r["rounds"] for r in ok if r["policy"] == policy]
        return round(statistics.mean(rs), 2) if rs else 0.0

    print("\n=== summary ===")
    print("  policy  answer finalized  change acted  announce acted  mean rounds")
    for p in ("C", "F", "F2"):
        print(
            f"  {p:6}  {rate(p, 'answer', 'finalized'):>16}  {rate(p, 'change', 'acted'):>12}  "
            f"{rate(p, 'announce', 'acted'):>14}  {mean_rounds(p):>11}"
        )

    out = HERE / "result.json"
    out.write_text(json.dumps({"deterministic": deterministic, "rows": rows}, indent=2))
    print(f"\nwrote {out}")
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
