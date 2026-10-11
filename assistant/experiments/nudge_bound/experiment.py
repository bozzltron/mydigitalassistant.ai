"""Is the one-nudge-per-turn bound costing writes? Pre-registered in plan.md.

Two arms:

- **A (deterministic, no model, no brain):** a scripted LLM through
  ``stream_tool_loop`` in two shapes, to show whether the completion judgement is
  consulted on a read-instead-of-write turn.
- **B (live):** five write-class phrasings x 2 runs through the real pipeline on the
  dev backend, to measure how often the action nudge fires and the turn still ends
  with no write.

    python assistant/experiments/nudge_bound/experiment.py

Arm A writes only to a scratch store. Arm B sends requests to the dev backend and
creates ``nudge_bound_*`` files in the dev sandbox, deleted again at the end. The dev
brain is synthetic; the live brain is never touched. Writes ``result.json``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import httpx

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
BASE = os.environ.get("EXP_BASE", "https://host.docker.internal:8443").rstrip("/")
HERE = Path(__file__).resolve().parent
RUNS = int(os.environ.get("EXP_RUNS", "2"))

# Arm B: write-class phrasings, each run against a fresh, unique filename (so no
# pre-existing file can send the model down a read loop -- the stale-frame trap).
PHRASINGS = [
    ("checklist", "Create a file called {name} with a short three-item release checklist"),
    ("cities", "We want to create a file called {name} listing three tour cities"),
    ("bio", "Please create {name} with a one-paragraph band bio"),
    ("template", "Can you create {name} with a generic press release template?"),
    ("plan", "Write {name} with today's plan"),
]


# --------------------------------------------------------------------------- #
# Arm A -- deterministic, no model, no brain
# --------------------------------------------------------------------------- #


def _parse(raw_events: list[str]) -> list[dict]:
    out = []
    for line in raw_events:
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[6:]))
        except Exception:
            continue
    return out


def _shape(events: list[dict]) -> dict:
    """tool calls in order, stages in order, and whether a write happened."""
    tool_names: list[str] = []
    stages: list[str] = []
    for d in events:
        if d.get("type") == "tool_call":
            tool_names += [c.get("name") for c in (d.get("tool_calls") or [])]
        elif d.get("type") == "stage":
            stages.append(d.get("stage"))
    def _first(pred) -> int | None:
        return next((i for i, d in enumerate(events) if pred(d)), None)

    first_call = _first(lambda d: d.get("type") == "tool_call")
    first_acting = _first(lambda d: d.get("type") == "stage" and d.get("stage") == "acting")
    action_nudge = (
        first_acting is not None and (first_call is None or first_acting < first_call)
    )
    return {
        "tool_names": tool_names,
        "stages": stages,
        "acting_count": stages.count("acting"),
        "action_nudge": action_nudge,
        "wrote": "write_file" in tool_names,
    }


async def arm_a() -> dict:
    """A scripted model: the loop's policy, with no real model in the way."""
    from assistant.backend.pipeline import decision
    from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
    from assistant.backend.pipeline.streaming import stream_tool_loop
    from assistant.backend.pipeline.tool_executor import init_store

    scratch = Path(tempfile.mkdtemp(prefix="nudge_bound_a_"))
    import assistant.backend.pipeline.filesystem as _fs

    _fs.SANDBOX_ROOT = scratch
    init_store(str(scratch / "scratch.db"))

    consulted: list[str] = []

    async def fake_outstanding(request, tools_called):
        consulted.append(request)
        return True  # the calls left the action undone

    real_outstanding = decision.outstanding
    decision.outstanding = fake_outstanding

    class Scripted:
        """Replays one shape; ``rounds`` is a list of ChatResponse factories."""

        def __init__(self, rounds):
            self.rounds = rounds
            self.calls = 0

        async def chat(self, messages, **kwargs):
            i = min(self.calls, len(self.rounds) - 1)
            self.calls += 1
            return self.rounds[i]()

        def context_window(self, model=None):
            return 16384

    def announce():
        return ChatResponse(content="I'll now create the file.", model="m", done=True)

    def read():
        return ChatResponse(
            content="",
            model="m",
            done=True,
            tool_calls=[ToolCall(name="read_file", arguments={"path": "nudge_bound_probe.md"})],
        )

    def final():
        return ChatResponse(content="Done.", model="m", done=True)

    try:
        results = {}
        for label, rounds in (
            ("S_announce_read_final", [announce, read, final]),
            ("C_read_final", [read, final]),
        ):
            consulted.clear()
            llm = Scripted(rounds)
            raw = [
                ev
                async for ev in stream_tool_loop(
                    llm,
                    [{"role": "user", "content": "Create nudge_bound_probe.md"}],
                    tools=[],
                    expects_action=True,
                )
            ]
            shape = _shape(_parse(raw))
            shape["outstanding_consulted"] = len(consulted)
            shape["rounds"] = llm.calls
            results[label] = shape
            print(
                f"  [A {label:22}] acting={shape['acting_count']} "
                f"wrote={shape['wrote']} outstanding_consulted={shape['outstanding_consulted']} "
                f"rounds={shape['rounds']} tools={shape['tool_names']}"
            )
        return results
    finally:
        decision.outstanding = real_outstanding
        shutil.rmtree(scratch, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Arm B -- live, through the real pipeline on the dev backend
# --------------------------------------------------------------------------- #


async def _one_turn(client: httpx.AsyncClient, message: str, session_id: str) -> dict:
    raw: list[str] = []
    async with client.stream(
        "POST",
        f"{BASE}/chat/stream",
        json={"user_id": 1, "message": message, "session_id": session_id},
        timeout=300,
    ) as r:
        async for line in r.aiter_lines():
            raw.append(line)
    return _shape(_parse(raw))


async def arm_b() -> dict:
    from assistant.backend.pipeline.decision import outstanding

    rows: list[dict] = []
    async with httpx.AsyncClient(verify=False) as client:
        for run in range(1, RUNS + 1):
            for key, tmpl in PHRASINGS:
                name = f"nudge_bound_{key}_{run}.md"
                message = tmpl.format(name=name)
                started = time.time()
                try:
                    shape = await _one_turn(client, message, f"nudge_bound_{key}_{run}")
                except Exception as e:
                    shape = {"error": f"{type(e).__name__}: {e}"}
                shape.update(
                    {
                        "key": key,
                        "run": run,
                        "name": name,
                        "message": message,
                        "wall_s": round(time.time() - started, 1),
                    }
                )
                if "error" not in shape:
                    shape["suppressed"] = shape["action_nudge"] and not shape["wrote"]
                    if shape["suppressed"]:
                        verdict = await outstanding(message, shape["tool_names"])
                        shape["outstanding_verdict"] = verdict
                    else:
                        shape["outstanding_verdict"] = None
                rows.append(shape)
                print(
                    f"  [B {key:9} r{run}] action_nudge={shape.get('action_nudge')} "
                    f"wrote={shape.get('wrote')} suppressed={shape.get('suppressed')} "
                    f"verdict={shape.get('outstanding_verdict')} "
                    f"tools={shape.get('tool_names')} {shape.get('wall_s')}s"
                )
    return {"rows": rows}


async def _cleanup() -> None:
    """Delete the nudge_bound_* files Arm B created (frame + bytes together)."""
    async with httpx.AsyncClient(verify=False) as client:
        try:
            r = await client.get(f"{BASE}/files/list", params={"user_id": 1}, timeout=30)
            for f in r.json():
                name = f.get("file_name") or f.get("name") or ""
                if name.startswith("nudge_bound_"):
                    await client.delete(f"{BASE}/files/{f['id']}", params={"user_id": 1})
                    print(f"  [cleanup] deleted {name}")
        except Exception as e:
            print(f"  [cleanup] skipped: {type(e).__name__}: {e}")


async def main() -> None:
    print("[arm A] deterministic -- the loop's policy")
    a = await arm_a()

    print("\n[arm B] live -- the real pipeline")
    b = await arm_b()
    rows = b["rows"]

    ok = [r for r in rows if "error" not in r]
    action_nudged = [r for r in ok if r["action_nudge"]]
    suppressed = [r for r in ok if r.get("suppressed")]
    warranted = [r for r in suppressed if r.get("outstanding_verdict") is True]

    print("\n=== summary ===")
    print(f"  live turns          {len(ok)}/{len(rows)}")
    print(f"  action nudge fired  {len(action_nudged)}")
    print(f"  suppressed (no write after the action nudge)  {len(suppressed)}")
    print(f"  ...of those, the completion judgement says outstanding  {len(warranted)}")

    await _cleanup()

    out = HERE / "result.json"
    out.write_text(json.dumps({"arm_a": a, "arm_b": b}, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
