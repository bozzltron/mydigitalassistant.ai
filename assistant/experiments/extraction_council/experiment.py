"""Which models make a good extraction council? Pre-registered in plan.md.

Runs the production extraction path (``extractor.extract_facts``) with each candidate as
``utility_model`` over one labelled set, then compares recall and — the thing a council
lives on — the **overlap of their misses**.

    python assistant/experiments/extraction_council/experiment.py

Needs local Ollama and the two cross-lineage candidates pulled:
``llama3.2:3b``, ``gemma3:4b``. No brain, no tools. Writes ``result.json``.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import statistics
import time
from pathlib import Path

from assistant.backend.pipeline.extractor import extract_facts
from assistant.backend.pipeline.llm_client import OllamaClient

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
HERE = Path(__file__).resolve().parent

# (label, user message, assistant reply, [(expected value, frame, key)])
# The expected value is what a human would say the turn states; frame/key are the
# canonical shape (used only for the secondary schema-level metric).
TURNS: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
    ("user_name", "My name is Boz.", "Nice to meet you, Boz.",
     [("Boz", "user_identity", "full_name")]),
    ("assistant_name", "I'll call you Hermes.", "Hermes it is.",
     [("Hermes", "identity_name", "full_name")]),
    ("guitar_strings", "My guitar has 6 strings.", "Six strings, noted.",
     [("6", "guitar", "strings")]),
    ("car", "I drive a red Mazda MX-5.", "Nice car.",
     [("Mazda MX-5", "car", "model"), ("red", "car", "colour")]),
    ("dog", "My dog is called Rufus.", "Rufus is a good name.",
     [("Rufus", "dog", "name")]),
    ("allergy", "I'm allergic to peanuts.", "I'll remember that.",
     [("peanuts", "allergy", "trigger")]),
    ("favourite_colour", "My favourite colour is teal.", "Teal is a lovely colour.",
     [("teal", "favourite_colour", "value")]),
    ("trip", "We're flying to Lisbon in March.", "Lisbon in March sounds good.",
     [("Lisbon", "trip", "destination"), ("March", "trip", "month")]),
    ("sister", "My sister's name is Dana.", "Noted.",
     [("Dana", "sister", "name")]),
    ("occupation", "I work as a marine biologist.", "That's a great field.",
     [("marine biologist", "occupation", "title")]),
    ("children", "I have two children.", "Two children, noted.",
     [("2", "family", "children")]),
    ("router", "My wifi router is a Netgear Nighthawk.", "Good router.",
     [("Netgear Nighthawk", "router", "model")]),
    ("coffee", "I take my coffee black.", "Black coffee it is.",
     [("black", "coffee", "preference")]),
    ("birthday", "My birthday is on 12 June.", "I'll remember the 12th of June.",
     [("12 June", "birthday", "date")]),
    ("language", "I'm learning Spanish.", "Buen suerte with Spanish.",
     [("Spanish", "language", "learning")]),
    ("bedrooms", "The house has four bedrooms.", "Four bedrooms.",
     [("4", "house", "bedrooms")]),
    ("spare_key", "I keep the spare key under the blue pot.", "Understood.",
     [("under the blue pot", "spare_key", "location")]),
    ("agreement", "Please keep answers short.", "I'll keep them short.",
     [("keep answers short", "identity_name", "working_agreement")]),
]

# candidate -> lineage. The fleet is nearly all one lineage; two cross-lineage models
# are pulled for this probe (plan.md).
CANDIDATES: dict[str, str] = {
    "qwen3.5:4b": "qwen",          # the incumbent utility model
    "qwen3.5:9b": "qwen",
    "qwen2.5:7b": "qwen",
    "sorc/qwen3.5-claude-4.6-opus-q4:4b": "qwen",
    "llama3.2:3b": "llama",        # pulled
    "gemma3:4b": "gemma",          # pulled
}

_NUM_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


def _norm(s: str | None) -> str:
    """Lowercase, strip, collapse whitespace, drop trailing punctuation."""
    if s is None:
        return ""
    s = " ".join(str(s).lower().split()).strip(" .,;:!?")
    return _NUM_WORDS.get(s, s)


def _found_value(value: str, slots: list) -> bool:
    """A fact is found if any slot carries the value (frame/key ignored)."""
    target = _norm(value)
    for sl in slots:
        got = _norm(getattr(sl, "value", None))
        if got and (got == target or target in got or got in target):
            return True
    return False


def _found_schema(value: str, frame: str, key: str, slots: list) -> bool:
    """Stricter: frame + key + value all match."""
    for sl in slots:
        if _norm(getattr(sl, "frame_name", None)) != _norm(frame):
            continue
        if _norm(getattr(sl, "key", None)) != _norm(key):
            continue
        if _found_value(value, [sl]):
            return True
    return False


async def run_model(model: str) -> dict:
    client = OllamaClient(base_url=OLLAMA)
    client.utility_model = model
    found: list[int] = []
    found_schema: list[int] = []
    raw: list[dict] = []
    latencies: list[float] = []
    for idx, (label, user, reply, expected) in enumerate(TURNS):
        started = time.time()
        try:
            result = await extract_facts(user, reply, client)
            slots = result.slots
            err = ""
        except Exception as e:  # best-effort: a broken candidate is a data point
            slots, err = [], f"{type(e).__name__}: {e}"
        latencies.append(time.time() - started)
        hit = all(_found_value(v, slots) for v, _, _ in expected)
        hit_schema = all(_found_schema(v, f, k, slots) for v, f, k in expected)
        if hit:
            found.append(idx)
        if hit_schema:
            found_schema.append(idx)
        raw.append(
            {
                "label": label,
                "expected": [v for v, _, _ in expected],
                "got": [getattr(s, "value", None) for s in slots],
                "found": hit,
                "found_schema": hit_schema,
                "error": err,
            }
        )
    n = len(TURNS)
    return {
        "model": model,
        "lineage": CANDIDATES[model],
        "recall": round(len(found) / n, 3),
        "recall_schema": round(len(found_schema) / n, 3),
        "found_turns": sorted(found),
        "missed_turns": sorted(set(range(n)) - set(found)),
        "median_ms": round(statistics.median(latencies) * 1000),
        "raw": raw,
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return round(len(a & b) / len(a | b), 3)


def _pairwise(results: dict[str, dict]) -> list[dict]:
    n = len(TURNS)
    rows = []
    for m1, m2 in itertools.combinations(results, 2):
        miss1 = set(results[m1]["missed_turns"])
        miss2 = set(results[m2]["missed_turns"])
        union_found = set(results[m1]["found_turns"]) | set(results[m2]["found_turns"])
        rows.append(
            {
                "pair": [m1, m2],
                "lineages": [results[m1]["lineage"], results[m2]["lineage"]],
                "cross_lineage": results[m1]["lineage"] != results[m2]["lineage"],
                "miss_overlap": _jaccard(miss1, miss2),
                "union_recall": round(len(union_found) / n, 3),
            }
        )
    return rows


async def arm_parallel() -> dict:
    """Time each member alone, then the pairs concurrently; watch residency."""
    import httpx

    async def extract(model: str, user: str, reply: str) -> None:
        client = OllamaClient(base_url=OLLAMA)
        client.utility_model = model
        await extract_facts(user, reply, client)

    async def loaded() -> list[str]:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{OLLAMA}/api/ps", timeout=10)
            return [m["name"] for m in r.json().get("models", [])]

    arms: dict[str, list[str]] = {
        "qwen3.5:4b_alone": ["qwen3.5:4b"],
        "sorc_q4_4b_alone": ["sorc/qwen3.5-claude-4.6-opus-q4:4b"],
        "best_pair_parallel": ["qwen3.5:4b", "sorc/qwen3.5-claude-4.6-opus-q4:4b"],
        "warm_pair_parallel": ["qwen3.5:4b", "qwen3.5:9b"],
    }
    out: dict[str, dict] = {}
    for name, models in arms.items():
        before = await loaded()
        times: list[float] = []
        for _, user, reply, _ in TURNS:
            t0 = time.time()
            await asyncio.gather(*(extract(m, user, reply) for m in models))
            times.append(time.time() - t0)
        after = await loaded()
        out[name] = {
            "models": models,
            "median_ms": round(statistics.median(times) * 1000),
            "mean_ms": round(statistics.mean(times) * 1000),
            "loaded_before": before,
            "loaded_after": after,
        }
        print(
            f"  [{name:20}] median {out[name]['median_ms']:6}ms  "
            f"loaded_after={len(after)} {after}"
        )
    return out


async def main() -> None:
    if os.environ.get("EXP_ARM") == "parallel":
        print("[parallel] council members run concurrently")
        par = await arm_parallel()
        single = par["qwen3.5:4b_alone"]["median_ms"]
        pair = par["best_pair_parallel"]["median_ms"]
        sum_alone = single + par["sorc_q4_4b_alone"]["median_ms"]
        print("\n=== summary ===")
        print(f"  incumbent alone        {single}ms")
        print(f"  slower member alone    {max(single, par['sorc_q4_4b_alone']['median_ms'])}ms")
        print(f"  pair in parallel       {pair}ms   (serial sum would be {sum_alone}ms)")
        print(f"  warm pair in parallel  {par['warm_pair_parallel']['median_ms']}ms")
        (HERE / "result_parallel.json").write_text(json.dumps(par, indent=2))
        print(f"\nwrote {HERE / 'result_parallel.json'}")
        return

    results: dict[str, dict] = {}
    for model in CANDIDATES:
        print(f"\n[{model}]")
        res = await run_model(model)
        results[model] = res
        print(
            f"  recall {res['recall']:.2f}  schema {res['recall_schema']:.2f}  "
            f"missed {res['missed_turns']}  {res['median_ms']}ms"
        )

    pairs = _pairwise(results)
    best_single = max(results.values(), key=lambda r: r["recall"])
    best_pair = max(pairs, key=lambda p: p["union_recall"])
    same = [p for p in pairs if not p["cross_lineage"]]
    cross = [p for p in pairs if p["cross_lineage"]]

    print("\n=== summary ===")
    print("  model                                 lineage  recall  schema  missed")
    for m, r in results.items():
        print(
            f"  {m:37} {r['lineage']:7} {r['recall']:6.2f} {r['recall_schema']:7.2f}  "
            f"{r['missed_turns']}"
        )
    print(f"\n  best single            {best_single['model']}  recall {best_single['recall']:.2f}")
    print(
        f"  best pair              {best_pair['pair']}  union {best_pair['union_recall']:.2f}"
    )
    if same and cross:
        same_ov = statistics.mean(p["miss_overlap"] for p in same)
        cross_ov = statistics.mean(p["miss_overlap"] for p in cross)
        print(f"  mean miss-overlap      same-lineage {same_ov:.2f}   cross-lineage {cross_ov:.2f}")

    out = HERE / "result.json"
    out.write_text(json.dumps({"models": results, "pairs": pairs}, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
