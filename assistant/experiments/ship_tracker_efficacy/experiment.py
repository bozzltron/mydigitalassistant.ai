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

from assistant.backend.db.schema import init_db  # noqa: E402
from assistant.backend.memory.retrieval import Retriever  # noqa: E402
from assistant.backend.memory.store import MemoryStore  # noqa: E402
from assistant.backend.pipeline import tool_executor  # noqa: E402
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

VESSELS = [
    ("EVER GIVEN", "Container Ship"),
    ("MSC GULSUN", "Container Ship"),
    ("MAERSK ESSEX", "Container Ship"),
    ("COSTA DIAMANTE", "Cruise Ship"),
    ("SEAWISE GIANT", "Bulk Carrier"),
]
SEED_CSV = "ship_name,ship_type\n" + "\n".join(f"{n},{t}" for n, t in VESSELS) + "\n"
# A known-wrong state, so "the file was updated" is detectable from content rather than
# from a timestamp.
EMPTY_FC = '{\n  "type": "FeatureCollection",\n  "features": []\n}\n'

REQUIRED_FIELDS = ("shipName", "type", "location", "description")


def seed() -> None:
    """Reset the scratch world to the known state, before every trial."""
    (_fs.get_sandbox_root() / "vessels.csv").write_text(SEED_CSV, encoding="utf-8")
    (_fs.get_sandbox_root() / "ship-tracker.geojson").write_text(EMPTY_FC, encoding="utf-8")


# --- measurement -------------------------------------------------------------


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


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
    out["coverage"] = sum(1 for n, _ in VESSELS if n in names) / len(VESSELS)

    gaps: list[str] = []
    for p in props:
        for field in REQUIRED_FIELDS:
            if not _norm(p.get(field)):
                gaps.append(field)
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
    calls: list[str] = []
    nudges: list[str] = []

    # Capture the call list by wrapping the executor: `execute_and_record_task` does not
    # return it, and the *calls* are the evidence for `no_attempt` vs `refused_stale`.
    real_execute = tool_executor.execute_tool

    async def recording(tool_name, raw_args, user_id, session_id):
        calls.append(tool_name)
        return await real_execute(tool_name, raw_args, user_id, session_id)

    tool_executor.execute_tool = recording
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
        success, _result = await execute_and_record_task(
            store=store,
            orchestrator=orch,
            task_frame_id=task,
            task_name="ship_tracker",
            task_prompt=PROMPT,
            owner_user_id=1,
        )
    finally:
        tool_executor.execute_tool = real_execute
    elapsed_ms = int((time.monotonic() - started) * 1000)

    text = (_fs.get_sandbox_root() / "ship-tracker.geojson").read_text(encoding="utf-8")
    m = measure(text)
    m.update(
        {
            "trial": n,
            "success_flag": bool(success),
            "tool_calls": calls,
            "nudges": len(nudges),
            "elapsed_ms": elapsed_ms,
            "outcome_class": classify(m, calls),
        }
    )
    print(
        f"  trial {n:>2}: {m['outcome_class']:<15} "
        f"coverage={m['coverage']:.2f} fields={m['fields_complete']!s:<5} "
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
    llm = OllamaClient()
    retriever = Retriever(store=store, llm_client=llm)
    orch = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm,
            search_tool=WebSearchTool(),
        )
    )
    tool_executor.init_store(store.db_path, embed_fn=orch.embed_fn(), embedding_model=None)

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

    payload = {
        "trials": trials,
        "outcome_classes": classes,
        "k": TRIALS,
        "prompt": PROMPT,
        "seed_vessels": [name for name, _ in VESSELS],
    }
    (OUT_DIR / "result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT_DIR / 'result.json'}")


if __name__ == "__main__":
    asyncio.run(run())
