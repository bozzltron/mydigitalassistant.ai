# ruff: noqa: E501 — this file holds a prompt verbatim, and the user's own line is
# longer than the project limit. Reformatting it would change the thing under test.
"""Ship-tracker task efficacy and consistency — the instrument for `plan.md`.

Runs the user's task prompt K times through the same path the scheduler uses
(`execute_and_record_task`), against a **scratch** sandbox, and measures what came out.
Real LLM, real web search: the question is what the agent does, not what a stub does.

    EXP_SANDBOX=/tmp/ship-exp EXP_DB=/tmp/ship-exp/brain.db \
      python -m assistant.experiments.ship_tracker_efficacy.experiment

`EXP_TRIALS` overrides K (default 10). Writes `result.json` next to this file.

The design, the hypotheses, the bar and the threats are in `plan.md` — written before any
trial was run. This file is deliberately mechanical: it must not be the place where a
result is decided.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

LIVE_SANDBOX = Path("/app/data")
SCRATCH = os.environ.get("EXP_SANDBOX", "")


def _assert_scratch() -> Path:
    """Abort unless EXP_SANDBOX is set and outside the live sandbox.

    Every trial overwrites `vessels.csv` and `ship-tracker.geojson`. One wrong path would
    eat the user's real map, so this is an assert rather than a convention — the same
    guard `file_write_parity` uses.
    """
    if not SCRATCH:
        print("ABORT: EXP_SANDBOX is not set; refusing to run against the live sandbox")
        sys.exit(2)
    scratch = Path(SCRATCH).resolve()
    if scratch == LIVE_SANDBOX.resolve() or LIVE_SANDBOX in scratch.parents:
        print(f"ABORT: EXP_SANDBOX={scratch} is inside the live sandbox {LIVE_SANDBOX}")
        sys.exit(2)
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


SCRATCH_DIR = _assert_scratch()
DB_PATH = os.environ.get("EXP_DB", str(SCRATCH_DIR / "assistant.db"))
TRIALS = int(os.environ.get("EXP_TRIALS", "10"))
OUT_DIR = Path(__file__).parent

# The filesystem module resolves everything against a module constant, so patch it to the
# scratch root *before* any file is touched.
import assistant.backend.pipeline.filesystem as _fs  # noqa: E402

_fs.SANDBOX_ROOT = SCRATCH_DIR

from assistant.backend.config import settings  # noqa: E402
from assistant.backend.db.schema import init_db  # noqa: E402
from assistant.backend.memory.retrieval import Retriever  # noqa: E402
from assistant.backend.memory.store import MemoryStore  # noqa: E402
from assistant.backend.pipeline import streaming, tool_executor  # noqa: E402
from assistant.backend.pipeline.llm_client import OllamaClient  # noqa: E402
from assistant.backend.pipeline.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratorDeps,
)
from assistant.backend.pipeline.search import WebSearchTool  # noqa: E402
from assistant.backend.scheduler import execute_and_record_task  # noqa: E402

# --- the prompt under test, verbatim from plan.md ---------------------------

PROMPT = """1. Look up ships we are tracking in the vessels.csv
2. Pull vessel location data from available sources (e.g., AIS feeds, maritime databases)
3. update the ship-tracker.geojson file. For each vessel, include:

    shipName: Proper name of the vessel
    type: Vessel type (e.g., Container Ship, Bulk Carrier)
    location: Current position or region (e.g., 'Near Suez Canal', 'Indian Ocean')
    description: A human-readable string combining ship name, type, and location for map display (e.g., 'EVER GIVEN - Container Ship near Suez Canal')

4. Read the ship-tracker.geojson file to make sure our changes above were written and are accurate.
5. If the file is not updated, try again."""

# --- the seeded world --------------------------------------------------------

# The user's real `vessels.csv` is a bare list of names — no header, no types. Seeded
# verbatim: a synthetic `ship_name,ship_type` file would hand the model the type field it
# is supposed to have to look up.
VESSEL_NAMES = [
    "Ever Given",
    "MSC GULSUN",
    "MAERSK ESSEX",
    "COSTA DIAMANTE",
    "SEAWISE GIANT",
]
SEED_CSV = "\n".join(VESSEL_NAMES) + "\n"
# A known-wrong state, so "the file was updated" is detectable from content rather than
# from a timestamp.
EMPTY_FC = '{\n  "type": "FeatureCollection",\n  "features": []\n}\n'

# The prompt asks for `type`; the user's own `ship-tracker.geojson` uses `vesselType`.
# Either satisfies the field, because scoring one of them as a failure would be measuring
# the prompt's inconsistency with the user's data, not the agent. Decided before any trial
# — see the amendment in plan.md.
TYPE_KEYS = ("type", "vesselType")
REQUIRED_FIELDS = ("shipName", "location", "description")


def seed() -> None:
    """Reset the scratch world to the known state, before every trial."""
    (_fs.get_sandbox_root() / "vessels.csv").write_text(SEED_CSV, encoding="utf-8")
    (_fs.get_sandbox_root() / "ship-tracker.geojson").write_text(EMPTY_FC, encoding="utf-8")


# --- measurement -------------------------------------------------------------


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


class _LoopExitReason(logging.Handler):
    """The loop's own statement of why it stopped.

    A round that exhausts the ceiling returns a *report*, produced with
    `tool_choice="none"` and a prompt asking the model what blocked it — indistinguishable
    from an ordinary answer at the file level. Run 4 therefore had to establish which trials
    exited that way *by construction*, inferring it from the absence of a nudge in the
    record. This reads the loop's `tool_loop_exit` line instead, so the exit is observed
    rather than deduced. `streaming.py` logs it at INFO, pinned by
    `test_streaming_events.py::TestToolLoopExitReasonIsLogged`.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.reason: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith("tool_loop_exit:"):
            self.reason = message.split("reason=", 1)[1].split()[0]


_LOOP_EXIT = _LoopExitReason()


def _summarise_args(raw_args: object) -> str:
    """The argument that says *what* a call was about, for the record.

    Tool *names* alone cannot tell a read of `vessels.csv` from a read of
    `ship-tracker.geojson`, and that distinction is the question run 4 exists to answer:
    the prompt's step 4 tells the model to read the map back, so `read_file, read_file` is
    ambiguous between "verified the write" and "never got there". The value is truncated —
    this is an identifier for a log, not a copy of the payload.
    """
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except (TypeError, ValueError):
            return raw_args[:80]
    if not isinstance(raw_args, dict):
        return str(raw_args)[:80]
    for key in ("path", "file", "filename", "url", "query", "name"):
        if key in raw_args:
            return f"{key}={str(raw_args[key])[:60]}"
    return json.dumps(raw_args, sort_keys=True)[:80]


def measure(text: str) -> dict:
    """The dependent variables, from the file the trial left behind.

    Only what the file says. Correctness of a fetched coordinate is not measurable here
    without ground truth, and `plan.md` says so rather than implying it.
    """
    out: dict = {
        "written": text.strip() != EMPTY_FC.strip(),
        "valid": False,
        "features": 0,
        "coverage": 0.0,
        "fields_complete": False,
        "description_shape": False,
        "missing_fields": [],
        "vessels_present": [],
    }
    try:
        data = json.loads(text)
    except ValueError:
        return out
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return out
    out["valid"] = True

    features = [f for f in (data.get("features") or []) if isinstance(f, dict)]
    out["features"] = len(features)
    props = [(f.get("properties") or {}) for f in features]

    names = {_norm(p.get("shipName")).upper() for p in props}
    out["vessels_present"] = sorted(n for n in names if n)
    out["coverage"] = sum(1 for n in VESSEL_NAMES if n.upper() in names) / len(VESSEL_NAMES)

    gaps: list[str] = []
    for p in props:
        for field in REQUIRED_FIELDS:
            if not _norm(p.get(field)):
                gaps.append(field)
        if not any(_norm(p.get(k)) for k in TYPE_KEYS):
            gaps.append("type")
    out["missing_fields"] = sorted(set(gaps))
    out["fields_complete"] = bool(features) and not gaps

    # The prompt gives the shape: '<name> - <type> <location>'.
    out["description_shape"] = bool(features) and all(
        " - " in _norm(p.get("description")) for p in props
    )
    return out


def classify(m: dict, tool_calls: list[str]) -> str:
    """Which of `plan.md`'s outcome classes this trial is.

    The distinction that matters: `refused_stale` (did not write, said why) is not the same
    as `no_attempt` (did not write, never tried), and a binary "written" would merge them.
    """
    if m["written"] and m["valid"] and m["coverage"] == 1.0 and m["fields_complete"]:
        return "wrote_complete"
    if m["written"]:
        return "wrote_partial"
    # Did it try to write? The call list is the evidence, not the answer's claim.
    tried = any(c.startswith(("write_file", "edit_file", "append_file")) for c in tool_calls)
    if tried:
        return "refused_stale" if m["written"] is False else "wrote_partial"
    return "no_attempt"


# --- one trial ---------------------------------------------------------------


async def one_trial(orch: Orchestrator, store: MemoryStore, n: int) -> dict:
    seed()
    _LOOP_EXIT.reason = None
    calls: list[str] = []
    call_log: list[dict] = []
    nudges: list[str] = []

    # Capture the call list by wrapping the executor: `execute_and_record_task` does not
    # return it, and the *calls* are the evidence for `no_attempt` vs `refused_stale`.
    # Each entry also records whether the call **succeeded** — a tool that fails silently
    # looks exactly like a model that chose not to act, and that is the fault that made
    # runs 1-3 unreadable.
    real_execute = tool_executor.execute_tool

    async def recording(tool_name, raw_args, user_id, session_id):
        calls.append(tool_name)
        entry: dict = {"tool": tool_name, "args": _summarise_args(raw_args)}
        try:
            result = await real_execute(tool_name, raw_args, user_id, session_id)
        except Exception as exc:
            entry["error"] = f"raised {type(exc).__name__}: {exc}"[:200]
            call_log.append(entry)
            raise
        entry["ok"] = bool(getattr(result, "success", True))
        if getattr(result, "error", None):
            entry["error"] = str(result.error)[:200]
        call_log.append(entry)
        return result

    # The nudge was invisible in the record: a trial that was nudged and then wrote nothing
    # looked identical to one that was never nudged. `_maybe_nudge` *returns* the note it
    # hands the model, so wrapping it records the event without altering it. This is the gap
    # that let runs 2 and 3 be explained twice by guesswork.
    real_nudge = streaming._maybe_nudge

    async def recording_nudge(**kwargs: object) -> str | None:
        note = await real_nudge(**kwargs)  # type: ignore[arg-type]
        if note:
            nudges.append("completion" if kwargs.get("tool_calls_made") else "empty")
        return note

    tool_executor.execute_tool = recording
    streaming._maybe_nudge = recording_nudge
    started = time.monotonic()
    try:
        task = await store.upsert_scheduled_task(
            name="ship_tracker",
            description="",
            schedule_cron="daily",
            prompt=PROMPT,
            enabled=True,
            owner_user_id=1,
        )
        success, answer = await execute_and_record_task(
            store=store,
            orchestrator=orch,
            task_frame_id=task,
            task_name="ship_tracker",
            task_prompt=PROMPT,
            owner_user_id=1,
        )
    finally:
        tool_executor.execute_tool = real_execute
        streaming._maybe_nudge = real_nudge
    elapsed_ms = int((time.monotonic() - started) * 1000)

    text = (_fs.get_sandbox_root() / "ship-tracker.geojson").read_text(encoding="utf-8")
    m = measure(text)
    m.update(
        {
            "trial": n,
            "success_flag": bool(success),
            "tool_calls": calls,
            "call_log": call_log,
            "nudges": nudges,
            # The final answer as recorded on the task frame (capped at 2000 chars there).
            # Whether the model *claims* it wrote the file is only visible here, and it is
            # what separates a claim/reality gap from a silent decline.
            "answer": answer or "",
            # How the loop stopped: `answered`, `finalize`, or `exhausted` (the ceiling
            # falling through to a report that cannot act). "(unlogged)" means the
            # instrument lost the line, which is a harness fault and not a result.
            "exit_reason": _LOOP_EXIT.reason or "(unlogged)",
            "elapsed_ms": elapsed_ms,
            "outcome_class": classify(m, calls),
        }
    )
    print(
        f"  trial {n:>2}: {m['outcome_class']:<15} "
        f"coverage={m['coverage']:.2f} fields={m['fields_complete']!s:<5} "
        f"nudge={','.join(nudges) or '-':<10} "
        f"calls={','.join(calls) or '(none)'}"
    )
    return m


# --- the run -----------------------------------------------------------------


async def run() -> None:
    print(f"scratch sandbox: {SCRATCH_DIR}")
    print(f"scratch brain  : {DB_PATH}")
    print(f"trials         : {TRIALS}\n")

    await init_db(DB_PATH)
    store = MemoryStore(DB_PATH)
    if not await store.list_users():
        await store.create_user("experimenter")
    # `OllamaClient()`'s `base_url` **defaults to 127.0.0.1**, so a bare construction
    # ignores `settings.ollama_url` entirely and silently talks to localhost. Inside a
    # container that is nothing, and every trial dies on the first embedding with
    # `ConnectError` — which the first run of this experiment did, and which is a harness
    # fault rather than a result. Same wiring as `frame_budget` and `main.py`.
    llm = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
        tools_model=settings.tools_model,
        chat_num_ctx=settings.chat_num_ctx,
        utility_num_ctx=settings.utility_num_ctx,
        timeout=settings.ollama_timeout,
        keep_alive=settings.ollama_keep_alive,
    )
    retriever = Retriever(store=store, llm_client=llm)
    search_tool = WebSearchTool()
    orch = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm,
            search_tool=search_tool,
        )
    )
    # `init_store` defaults both `search_tool` and `orchestrator` to **None**, and
    # `main.py` passes both. This runner passed neither, so every `web_search` the model
    # made returned "Search tool not initialized" — in all 30 trials of runs 1-3 — and
    # `fetch_url` ran with no `llm_client`, losing its extraction. A tool that fails
    # silently is indistinguishable from a model that chose not to act, which is exactly
    # how three runs came to be read as a completion-check problem. Wired to match
    # production, because "the same path the scheduler uses" is this experiment's premise.
    # The loop logs its exit reason at INFO; the handler must be attached to its logger,
    # whose effective level would otherwise drop the record.
    _streaming_logger = logging.getLogger("assistant.backend.pipeline.streaming")
    _streaming_logger.setLevel(logging.INFO)
    _streaming_logger.addHandler(_LOOP_EXIT)

    tool_executor.init_store(
        store.db_path,
        embed_fn=orch.embed_fn(),
        embedding_model=settings.embedding_model,
        search_tool=search_tool,
        orchestrator=orch,
    )
    if tool_executor._search_tool is None:
        # An `assert` would be stripped by `python -O`, and this is a *validity* guard
        # rather than a convenience — the same reason `_assert_scratch` exits explicitly.
        # With `_search_tool` unset every `web_search` returns "Search tool not
        # initialized", the model reports that it could not get the data, and the trial is
        # scored as a completion failure. That is exactly how runs 1-3 were misread.
        print(
            "ABORT: tool_executor has no search tool; every web_search would return an "
            "error and every trial would be invalid (the fault that invalidated runs 1-3)"
        )
        sys.exit(2)

    trials = [await one_trial(orch, store, n) for n in range(1, TRIALS + 1)]

    # The result is the counts. No p-values: 10 trials cannot resolve a 10% difference and
    # `plan.md` says so in advance.
    classes: dict[str, int] = {}
    for t in trials:
        classes[t["outcome_class"]] = classes.get(t["outcome_class"], 0) + 1
    wrote_complete = classes.get("wrote_complete", 0)
    partial = classes.get("wrote_partial", 0)

    print("\n=== outcome classes ===")
    for cls, n in sorted(classes.items(), key=lambda kv: -kv[1]):
        print(f"  {cls:<15} {n}/{TRIALS}")
    h1 = "PASS" if wrote_complete >= 8 else "FAIL"
    print(f"\n  H1 (>= 8/10 complete)      : {h1}")
    print(f"  H3 (no silent partial write): {'PASS' if partial == 0 else 'FAIL'}")
    print(f"  H5 (no_attempt == 0)        : {'PASS' if classes.get('no_attempt', 0) == 0 else 'FAIL'}")

    # The instrument's new reads — facts about the run, not decisions about it.
    print(f"\n  trials nudged               : {sum(1 for t in trials if t['nudges'])}/{TRIALS}")
    print(f"  completion-check nudges     : {sum('completion' in t['nudges'] for t in trials)}")
    print(f"  empty-turn nudges           : {sum('empty' in t['nudges'] for t in trials)}")
    print(f"  trials reading the map back : {sum(1 for t in trials if any('ship-tracker' in c['args'] for c in t['call_log']))}")

    # A tool error is a harness fault, not a result, and it must be impossible to read
    # past. Runs 1-3 could not show this line.
    errors = [
        (t["trial"], c["tool"], c["error"])
        for t in trials
        for c in t["call_log"]
        if c.get("error")
    ]
    print(f"  tool errors                 : {len(errors)}")
    exits: dict[str, int] = {}
    for t in trials:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    print(f"  loop exits                  : {exits}")
    for trial_no, tool, err in errors[:6]:
        print(f"    trial {trial_no:>2} {tool}: {err}")

    payload = {
        "trials": trials,
        "outcome_classes": classes,
        "k": TRIALS,
        "prompt": PROMPT,
        "seed_vessels": VESSEL_NAMES,
    }
    (OUT_DIR / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_DIR / 'result.json'}")


if __name__ == "__main__":
    asyncio.run(run())
