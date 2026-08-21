"""Retrieval A/B harness for embedding-model candidates (Phase 6 M3).

Builds a synthetic household brain in a temp DB, embeds it with each candidate
model, and measures query->frame retrieval quality (hit@1, hit@3, MRR, latency).

Usage:
    python -m assistant.eval.retrieval_ab --models nomic-embed-text qwen3-embedding:0.6b
        [--baseline nomic-embed-text] [--gate 0.05]

Requires real Ollama. Writes only to a throwaway temp DB.
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient

RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"

# Synthetic brain: (frame_type, frame_name, [(key, value), ...])
FRAMES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("entity", "Fender Stratocaster", [
        ("strings", "6"), ("year_made", "1985"), ("finish", "sunburst"),
        ("case_color", "black"), ("strings_brand", "Ernie Ball"),
    ]),
    ("person", "Alice", [
        ("relation", "wife"), ("city", "Portland"), ("birthday", "March 14"),
        ("favorite_food", "thai curry"), ("employer", "Legacy Health"),
    ]),
    ("person", "Bob", [
        ("relation", "friend"), ("city", "Seattle"), ("hobby", "kayaking"),
        ("birthday", "November 2"),
    ]),
    ("place", "Home WiFi", [
        ("network_name", "Bosworth-5G"), ("password_hint", "guitar book page"),
        ("router_location", "office shelf"),
    ]),
    ("vehicle", "Toyota Prius", [
        ("year", "2019"), ("color", "blue"), ("last_oil_change", "2026-05-10"),
        ("license_plate_end", "4K2"),
    ]),
    ("event", "Dentist appointment", [
        ("date", "2026-09-15"), ("time", "9:30 AM"), ("location", "Dr. Patel"),
    ]),
    ("identity", "Michael Bosworth", [("role", "owner")]),
    ("concept", "Digital Assistant Name", [("name", "Sage")]),
    ("event", "Wedding Anniversary", [
        ("date", "June 22"), ("years_married", "12"), ("tradition", "coast trip"),
    ]),
    ("task", "Car registration renewal", [
        ("due_date", "October 31"), ("state", "Oregon"),
    ]),
    ("entity", "Kitchen Thermostat", [
        ("winter_setting", "68 degrees"), ("summer_setting", "74 degrees"),
        ("brand", "Nest"),
    ]),
    ("person", "Emma", [
        ("relation", "daughter"), ("age", "8"), ("school", "Maple Elementary"),
        ("favorite_animal", "red panda"),
    ]),
]

# (query, expected frame_name). Paraphrases on purpose; retrieval must bridge them.
CASES: list[tuple[str, str]] = [
    ("how many strings does my guitar have", "Fender Stratocaster"),
    ("when was my guitar made", "Fender Stratocaster"),
    ("what brand of strings do I use", "Fender Stratocaster"),
    ("where does my wife live", "Alice"),
    ("my wife's birthday", "Alice"),
    ("what should I cook for my wife tonight", "Alice"),
    ("where does bob go kayaking", "Bob"),
    ("wifi password", "Home WiFi"),
    ("what's the network name for our internet", "Home WiFi"),
    ("what do I drive", "Toyota Prius"),
    ("when did I last change the oil", "Toyota Prius"),
    ("upcoming medical appointments", "Dentist appointment"),
    ("who am i", "Michael Bosworth"),
    ("what are you called", "Digital Assistant Name"),
    ("our anniversary coming up", "Wedding Anniversary"),
    ("car paperwork deadline", "Car registration renewal"),
    ("what temperature should the house be in winter", "Kitchen Thermostat"),
    ("my daughter's school", "Emma"),
    ("how old is emma", "Emma"),
    ("emma's favorite animal", "Emma"),
]


async def run_candidate(model: str, ollama_url: str, min_distance: float) -> dict:
    """Embed synthetic frames with `model`, run all cases, return metrics."""
    client = OllamaClient(base_url=ollama_url, embedding_model=model)
    try:
        await client.health_check()

        with tempfile.TemporaryDirectory(prefix="retrieval_ab_") as tmp:
            db_path = str(Path(tmp) / "brain.db")
            await init_db(db_path)
            store = MemoryStore(db_path)

            user = await store.create_user("eval")
            uid = user.id

            frame_ids: dict[str, int] = {}
            texts: list[str] = []
            names: list[str] = []
            for ftype, name, slots in FRAMES:
                frame = await store.create_frame(
                    name=name, type=ftype, source_type="conversation",
                    owner_user_id=uid,
                )
                frame_ids[name] = frame.id
                for key, value in slots:
                    await store.upsert_slot(
                        frame_id=frame.id, key=key, value=value,
                        source_type="conversation",
                    )
                texts.append(store._frame_to_embed_text(
                    frame, await store.get_slots_for_frame(frame.id)
                ))
                names.append(name)

            embed_latencies: list[float] = []
            vectors: list[list[float]] = []
            for text, fid in zip(texts, [frame_ids[n] for n in names], strict=True):
                t0 = time.perf_counter()
                vec = (await client.embed(text)).embedding
                embed_latencies.append(time.perf_counter() - t0)
                vectors.append(vec)
                await store.store_frame_embedding(fid, vec, model)

            hits1 = 0
            hits3 = 0
            rr_sum = 0.0
            query_latencies: list[float] = []
            misses: list[str] = []

            for query, expected in CASES:
                t0 = time.perf_counter()
                qvec = (await client.embed(query)).embedding
                results = await store.search_similar_frames(
                    embedding=qvec, user_id=uid,
                    embedding_model=model, limit=5,
                    min_distance=min_distance,
                )
                query_latencies.append(time.perf_counter() - t0)

                ranked_names = [fr.name for fr, _, _ in results]
                if expected in ranked_names:
                    rank = ranked_names.index(expected) + 1
                    rr_sum += 1.0 / rank
                    if rank == 1:
                        hits1 += 1
                    if rank <= 3:
                        hits3 += 1
                else:
                    misses.append(f"  {query!r} -> expected {expected!r}, "
                                  f"got {ranked_names[:3]}")

        n = len(CASES)
        return {
            "model": model,
            "dim": len(vectors[0]),
            "hit_at_1": round(hits1 / n, 4),
            "hit_at_3": round(hits3 / n, 4),
            "mrr": round(rr_sum / n, 4),
            "embed_latency_ms": round(statistics.mean(embed_latencies) * 1000, 1),
            "query_latency_ms": round(statistics.mean(query_latencies) * 1000, 1),
            "misses": misses,
        }
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--ollama-url", default=None)
    parser.add_argument(
        "--baseline", default=None,
        help="Model name whose saved results set the gate floor",
    )
    parser.add_argument(
        "--gate", type=float, default=0.05,
        help="Max allowed relative drop of hit@1 vs baseline (default 0.05)",
    )
    parser.add_argument(
        "--min-distance", type=float, default=0.7,
        help="Distance cutoff passed to search (production default 0.7)",
    )
    args = parser.parse_args()

    from assistant.backend.config import settings
    ollama_url = args.ollama_url or settings.ollama_url

    results = []
    for model in args.models:
        print(f"\n=== {model} ===")
        try:
            r = asyncio.run(run_candidate(model, ollama_url, args.min_distance))
        except Exception as e:
            print(f"FAILED: {e}")
            continue
        results.append(r)
        print(f"dim={r['dim']}  hit@1={r['hit_at_1']:.0%}  hit@3={r['hit_at_3']:.0%}"
              f"  mrr={r['mrr']:.3f}  embed={r['embed_latency_ms']}ms"
              f"  query={r['query_latency_ms']}ms")
        for miss in r["misses"]:
            print(miss)

    exit_code = 0
    baseline_row = None
    if args.baseline:
        baseline_row = next((r for r in results if r["model"] == args.baseline), None)
        if baseline_row is None or not baseline_row.get("hit_at_1"):
            print(f"Baseline {args.baseline} not among successful runs; no gate applied.")
            floor = None
        else:
            floor = baseline_row["hit_at_1"] * (1 - args.gate)
        for r in results:
            if r is baseline_row or not r.get("hit_at_1") or floor is None:
                continue
            ok = r["hit_at_1"] >= floor
            verdict = "PASS" if ok else "FAIL"
            print(f"\n[{verdict}] {r['model']}: hit@1 {r['hit_at_1']:.0%} vs floor"
                  f" {floor:.0%} ({args.baseline} -{args.gate:.0%})")
            if not ok:
                exit_code = 1

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / (
        "retrieval_ab_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + ".json"
    )
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "cases": len(CASES),
        "gate": args.gate,
        "baseline": args.baseline,
        "results": results,
    }
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved: {out}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
