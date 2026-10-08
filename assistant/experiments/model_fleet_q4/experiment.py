"""Utility-role probe: does the Q4 4B extract as well as ``qwen3.5:4b``?

Pre-registered in ``plan.md``. Drives the production extraction path
(``extractor.extract_facts``) with ``utility_model`` set per arm, so it measures
the job the utility model actually does.

    python assistant/experiments/model_fleet_q4/experiment.py

Writes ``result.json`` (override with ``RESULT_PATH``).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.pipeline.extractor import extract_facts
from assistant.backend.pipeline.llm_client import OllamaClient

HERE = Path(__file__).resolve().parent

CANDIDATE = os.environ.get("CANDIDATE_MODEL", "sorc/qwen3.5-claude-4.6-opus-q4:4b")
MODELS: list[tuple[str, str]] = [
    ("candidate (q4:4b)", CANDIDATE),
    ("utility baseline (qwen3.5:4b)", "qwen3.5:4b"),
]

# (user message, values that should appear somewhere in the extracted slots).
# Frame/key names are model-chosen, so the probe checks the *values* land, not a
# specific key.
CASES: list[tuple[str, list[str]]] = [
    ("My name is Ada Lovelace and I live in Austin, Texas.", ["Ada", "Austin"]),
    ("I drive a blue 2019 Subaru Outback.", ["Subaru"]),
    ("My sister Priya's birthday is March 3.", ["Priya", "March"]),
    ("I'm allergic to peanuts.", ["peanut"]),
    ("I work at Acme Corp as a data engineer.", ["Acme"]),
]


async def _run_arm(label: str, model: str) -> list[dict]:
    llm = OllamaClient(
        base_url=settings.ollama_url,
        utility_model=model,
        utility_num_ctx=settings.utility_num_ctx,
    )
    rows: list[dict] = []
    try:
        for message, expected in CASES:
            started = time.time()
            try:
                result = await extract_facts(message, "", llm)
                error = ""
            except Exception as exc:  # extraction is best-effort by contract
                result, error = None, f"{type(exc).__name__}: {exc}"
            wall = round(time.time() - started, 2)
            values = " ".join((s.value or "") for s in result.slots).lower() if result else ""
            found = [e for e in expected if e.lower() in values]
            rows.append(
                {
                    "message": message[:60],
                    "expected": expected,
                    "found": found,
                    "missed": [e for e in expected if e not in found],
                    "slot_count": len(result.slots) if result else 0,
                    "wall_s": wall,
                    "error": error,
                }
            )
            print(
                f"  {'ok  ' if len(found) == len(expected) else 'MISS'} "
                f"found={found} slots={rows[-1]['slot_count']} wall={wall}s {error}"
            )
    finally:
        await llm.close()
    return rows


async def _main() -> None:
    results: dict[str, list[dict]] = {}
    for label, model in MODELS:
        print(f"\n[{label}] {model}")
        results[label] = await _run_arm(label, model)
        total = sum(len(r["expected"]) for r in results[label])
        got = sum(len(r["found"]) for r in results[label])
        print(f"  -> {got}/{total} expected values extracted")
    out = Path(os.environ.get("RESULT_PATH", HERE / "result.json"))
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(_main())
