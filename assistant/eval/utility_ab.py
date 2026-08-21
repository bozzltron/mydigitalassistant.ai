"""A/B harness for utility-role model candidates (Phase 6 M2).

Runs extraction, routing, and scheduled-task fixtures through the production
utility path for each candidate model and reports accuracy, JSON conformance,
and latency.

Usage:
    python -m assistant.eval.utility_ab --models qwen2.5:3b phi4-mini qwen3.5:4b
        [--baseline qwen2.5:3b]

Requires real Ollama. Read-only: no DB writes, no search.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.pipeline.extractor import (
    extract_facts,
    extract_scheduled_task_fields,
)
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient

RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"

CLASSIFY_PROMPT = """Analyze the user's message and classify its intent.

Classify into exactly one of these five types based on what the user is TRYING to do:

FUNCTIONAL: The user wants information, help with a task, an explanation, or an action.
  Any question that seeks facts, explanations, or help accomplishing something.
  Even casual questions like "what's the capital of France?" are functional.

INTROSPECTIVE: The user is asking about YOUR memory, knowledge, or past interactions.
  Questions that use "you" to refer to yourself: "what do you know/remember?",
  "what have we discussed?", "do you recall X?", "tell me what you learned".

CORRECTION: The user is asserting that something you said or stored is WRONG and needs correcting.
  Look for: disagreement words ("actually", "no", "wrong", "not right", "mistake"),
  specificity about what is wrong ("it's 12, not 6", "you said X but it's actually Y"),
  self-corrections ("I meant...", "let me rephrase"), or contradiction signals.

SEARCH: The user is explicitly asking you to search the web.
  Direct requests like "search for X", "look up Y", "find info about Z".
  Not: questions that COULD be answered by search, but actual requests to search.

SCHEDULED: The user wants to set up, manage, or ask about a recurring scheduled task.
  Requests like "set up a daily briefing", "remind me to X every day", "schedule weekly check",
  "what scheduled tasks do I have?", "delete my Monday task", "run my AI briefing now".

IMPORTANT: When uncertain between FUNCTIONAL and INTROSPECTIVE, prefer FUNCTIONAL.
When uncertain between FUNCTIONAL and CORRECTION, look for explicit disagreement signals.

Respond with ONLY valid JSON:
{"task_type": "functional"|"introspective"|"correction"|"search"|"scheduled"}"""

FACTS_FIXTURES = [
    {
        "name": "guitar_strings",
        "text": "Remember that my Fender Stratocaster guitar has 6 strings.",
        "ack": "Got it - your Fender Stratocaster has 6 strings.",
        # Each group is a set of slot patterns that must ALL be satisfied by some
        # slot; the fixture passes if ANY group fully matches. Accepts both a
        # single combined slot and a correct split-slot decomposition.
        "groups": [
            [
                {
                    "frame_any": ["fender", "stratocaster", "guitar"],
                    "key_any": ["string"],
                    "value_any": ["6"],
                }
            ]
        ],
    },
    {
        "name": "friend_location",
        "text": "My friend Alice Chen lives in Portland.",
        "ack": "Noted - Alice Chen lives in Portland.",
        "groups": [
            [{"frame_any": ["alice"], "key_any": None, "value_any": ["portland"]}],
            [
                {
                    "frame_any": ["alice", "person", "friend"],
                    "key_any": ["name"],
                    "value_any": ["alice"],
                },
                {
                    "frame_any": ["alice", "location", "portland", "city"],
                    "key_any": None,
                    "value_any": ["portland"],
                },
            ],
        ],
    },
    {
        "name": "wifi_password",
        "text": "The wifi password is hunter2.",
        "ack": "Saved - wifi password updated.",
        "groups": [
            [
                {
                    "frame_any": ["wifi", "network", "router"],
                    "key_any": ["password"],
                    "value_any": ["hunter2"],
                }
            ]
        ],
    },
    {
        "name": "car_model",
        "text": "My car is a blue Toyota Prius.",
        "ack": "Noted - you drive a blue Toyota Prius.",
        "groups": [
            [
                {
                    "frame_any": ["car", "toyota", "prius", "vehicle"],
                    "key_any": None,
                    "value_any": ["prius", "toyota", "blue"],
                }
            ]
        ],
    },
]

CLASSIFY_FIXTURES = [
    {"name": "introspective_recall", "text": "What do you remember about my guitar?",
     "expect": "introspective"},
    {"name": "search_request", "text": "Search the web for the latest news about Mars rovers.",
     "expect": "search"},
    {"name": "correction", "text": "That's wrong, my guitar has 12 strings not 6.",
     "expect": "correction"},
    {"name": "scheduled_setup", "text": "Set up a daily AI news briefing at 9am.",
     "expect": "scheduled"},
    {"name": "functional_draft",
     "text": "Help me draft an email to my landlord about the broken heater.",
     "expect": "functional"},
    {"name": "fact_storage_remember_that", 
     "text": "Remember that my Fender Stratocaster guitar has 6 strings.",
     "expect": "functional"},
    {"name": "fact_storage_note_for_later",
     "text": "Note for later: our anniversary dinner is booked for June 12.",
     "expect": "functional"},
]

SCHEDULED_FIXTURES = [
    {"name": "create_daily", "text": "set up a daily AI news briefing at 9am",
     "intent": "create", "field_contains": {"schedule": ["daily"]}},
    {"name": "list_tasks", "text": "what scheduled tasks do I have?",
     "intent": "list", "field_contains": None},
    {"name": "delete_task", "text": "delete my Monday task",
     "intent": "delete", "field_contains": None},
    {"name": "run_now", "text": "run my AI briefing right now",
     "intent": "run_now", "field_contains": None},
    {"name": "pause_task", "text": "pause the weather check task",
     "intent": "pause", "field_contains": None},
]


def _slot_matches(slot, expect: dict) -> bool:
    frame_ok = any(sub in slot.frame_name.lower() for sub in expect["frame_any"])
    if not frame_ok:
        return False
    if expect["key_any"] is not None:
        if not any(sub in (slot.key or "").lower() for sub in expect["key_any"]):
            return False
    value = str(slot.value or "").lower()
    return any(sub in value for sub in expect["value_any"])


async def _run_facts(client: OllamaClient, fixture: dict) -> tuple[bool, float, bool, str]:
    start = time.perf_counter()
    try:
        result = await extract_facts(fixture["text"], fixture["ack"], client)
    except Exception as e:
        return False, time.perf_counter() - start, True, f"exception: {e}"
    elapsed = time.perf_counter() - start
    ok = any(
        all(any(_slot_matches(s, pattern) for s in result.slots) for pattern in group)
        for group in fixture["groups"]
    )
    got = "; ".join(
        f"{s.frame_name}.{s.key}={s.value}" for s in result.slots
    )
    return ok, elapsed, False, got


async def _run_classify(client: OllamaClient, fixture: dict) -> tuple[bool, float, bool, str]:
    system = ChatMessage(role="system", content=CLASSIFY_PROMPT)
    user = ChatMessage(role="user", content=fixture["text"])
    start = time.perf_counter()
    try:
        response = await client.chat(
            [system, user],
            model=client.utility_model,
            format="json",
            temperature=0.0,
            think=False,
        )
        data = json.loads(response.content)
        label = data.get("task_type", "").lower()
    except Exception as e:
        return False, time.perf_counter() - start, True, f"exception: {e}"
    elapsed = time.perf_counter() - start
    return label == fixture["expect"], elapsed, False, label


async def _run_scheduled(client: OllamaClient, fixture: dict) -> tuple[bool, float, bool, str]:
    start = time.perf_counter()
    try:
        data = await extract_scheduled_task_fields(fixture["text"], client)
    except Exception as e:
        return False, time.perf_counter() - start, True, f"exception: {e}"
    elapsed = time.perf_counter() - start
    if data.get("intent") != fixture["intent"]:
        return False, elapsed, False, json.dumps(data)[:200]
    checks = fixture.get("field_contains") or {}
    for field, subs in checks.items():
        val = str(data.get(field) or "").lower()
        if not any(sub.lower() in val for sub in subs):
            return False, elapsed, False, json.dumps(data)[:200]
    return True, elapsed, False, json.dumps(data)[:200]


RUNNERS = {
    "facts": _run_facts,
    "classify": _run_classify,
    "scheduled": _run_scheduled,
}

ALL_FIXTURES = (
    [("facts", f) for f in FACTS_FIXTURES]
    + [("classify", f) for f in CLASSIFY_FIXTURES]
    + [("scheduled", f) for f in SCHEDULED_FIXTURES]
)


def _p95(latencies: list[float]) -> float:
    if not latencies:
        return 0.0
    ordered = sorted(latencies)
    idx = max(0, round(0.95 * len(ordered)) - 1)
    return ordered[idx]


async def run_model(model: str) -> dict:
    client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=model,
        utility_model=model,
        embedding_model=settings.embedding_model,
        coder_model="",
    )
    per_fixture = []
    kind_stats: dict[str, dict] = {}

    for kind, fixture in ALL_FIXTURES:
        ok, elapsed, json_error, got = await RUNNERS[kind](client, fixture)
        stats = kind_stats.setdefault(
            kind, {"pass": 0, "total": 0, "json_errors": 0, "latencies": []}
        )
        stats["total"] += 1
        stats["pass"] += int(ok)
        stats["json_errors"] += int(json_error)
        stats["latencies"].append(elapsed)
        per_fixture.append(
            {"kind": kind, "name": fixture["name"], "pass": ok,
             "json_error": json_error, "seconds": round(elapsed, 2), "got": got}
        )

    total_pass = sum(s["pass"] for s in kind_stats.values())
    total = sum(s["total"] for s in kind_stats.values())
    all_latencies = [x for s in kind_stats.values() for x in s["latencies"]]
    summary = {
        "model": model,
        "accuracy": round(total_pass / total * 100, 1),
        "json_errors": sum(s["json_errors"] for s in kind_stats.values()),
        "latency_mean_s": round(statistics.mean(all_latencies), 2),
        "latency_p95_s": round(_p95(all_latencies), 2),
        "kinds": {
            k: {
                "pass": s["pass"],
                "total": s["total"],
                "json_errors": s["json_errors"],
                "latency_mean_s": round(statistics.mean(s["latencies"]), 2),
            }
            for k, s in kind_stats.items()
        },
        "fixtures": per_fixture,
    }
    await client.close()
    return summary


def print_report(summaries: list[dict]) -> None:
    print(f"\n{'='*78}")
    print("UTILITY MODEL A/B REPORT")
    print(f"{'='*78}")
    header = f"{'model':<18} {'acc%':>6} {'json_err':>9} {'mean_s':>7} {'p95_s':>7}"
    kinds = ("facts", "classify", "scheduled")
    header += "".join(f"  {k[:8]:>8}" for k in kinds)
    print(header)
    print("-" * len(header))
    for s in summaries:
        line = (
            f"{s['model']:<18} {s['accuracy']:>6} {s['json_errors']:>9}"
            f" {s['latency_mean_s']:>7} {s['latency_p95_s']:>7}"
        )
        for k in kinds:
            ks = s["kinds"][k]
            line += f"  {ks['pass']}/{ks['total']:>3}"
        print(line)
    print(f"{'='*78}\n")

    for s in summaries:
        failures = [f for f in s["fixtures"] if not f["pass"]]
        if failures:
            print(f"{s['model']} failures:")
            for f in failures:
                tag = "JSON_ERROR" if f["json_error"] else "WRONG"
                print(f"  [{tag}] {f['kind']}/{f['name']} ({f['seconds']}s)")
                print(f"         got: {str(f['got'])[:160]}")
            print()


def gate_check(summaries: list[dict], baseline: str) -> list[str]:
    by_model = {s["model"]: s for s in summaries}
    base = by_model.get(baseline)
    if base is None:
        return [f"baseline model '{baseline}' not in run"]
    violations = []
    for s in summaries:
        if s["model"] == baseline:
            continue
        if s["accuracy"] < base["accuracy"]:
            violations.append(
                f"{s['model']}: accuracy {s['accuracy']}% < baseline"
                f" {base['accuracy']}%"
            )
        if s["json_errors"] > base["json_errors"]:
            violations.append(
                f"{s['model']}: json_errors {s['json_errors']} > baseline"
                f" {base['json_errors']}"
            )
    return violations


async def main() -> dict:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+",
        default=["qwen2.5:3b", "phi4-mini", "qwen3.5:4b"],
    )
    parser.add_argument("--baseline", default="qwen2.5:3b")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    print(f"\nUtility model A/B — fixtures={len(ALL_FIXTURES)} "
          f"models={args.models} baseline={args.baseline}")
    print(f"Ollama: {settings.ollama_url}\n")

    summaries = []
    for model in args.models:
        print(f"Running {model}...", end=" ", flush=True)
        summary = await run_model(model)
        summaries.append(summary)
        print(f"acc={summary['accuracy']}% json_err={summary['json_errors']} "
              f"mean={summary['latency_mean_s']}s p95={summary['latency_p95_s']}s")

    print_report(summaries)

    violations = gate_check(summaries, args.baseline)
    gate_pass = not violations
    for v in violations:
        print(f"GATE FAIL: {v}")
    print(f"GATE: {'PASS' if gate_pass else 'FAIL'}\n")

    results_path = RESULTS_DIR / f"utility_ab_{timestamp}.json"
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "baseline": args.baseline,
        "gate_pass": gate_pass,
        "violations": violations,
        "results": summaries,
    }
    results_path.write_text(json.dumps(payload, indent=2))
    print(f"Results: {results_path}")
    return payload


if __name__ == "__main__":
    try:
        report = asyncio.run(main())
        sys.exit(0 if report["gate_pass"] else 1)
    except Exception as e:  # noqa: BLE001
        print(f"\nA/B harness failed: {e}", file=sys.stderr)
        sys.exit(2)
