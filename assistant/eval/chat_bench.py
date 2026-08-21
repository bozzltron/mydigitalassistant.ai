"""Chat-model benchmark: TTFT and tok/s across models and thinking modes (M4).

Measures time-to-first-token and generation speed via streaming /api/chat on
three prompt classes (short QA, memory-context injection, reasoning).

Usage:
    python -m assistant.eval.chat_bench --models qwen2.5:7b qwen3.8:27b \
        [--think false true] [--rounds 3]

Requires real Ollama. Read-only.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"

SHORT_QA = "How many strings does a standard guitar have? Answer in one sentence."

MEMORY_CONTEXT = (
    "You are a household assistant. Use the memory below when relevant.\n\n"
    "[MEMORY]\n"
    + "\n".join(
        "- entity: Fender Stratocaster | strings=6, year_made=1985, finish=sunburst"
        for _ in range(40)
    )
    + "\n[/MEMORY]\n\n"
    "What year was my guitar made and what finish does it have? Be brief."
)

REASONING = (
    "Three friends split a 42 dollar pizza bill. Alice pays a third, Bob pays "
    "0.25 of the total, and Casey pays the rest. Casey also adds a 5 dollar tip. "
    "How much did Casey pay in total? Show the final number."
)

PROMPTS = [
    ("short_qa", SHORT_QA),
    ("memory_ctx", MEMORY_CONTEXT),
    ("reasoning", REASONING),
]

NUM_CTX = 8192


async def bench_one(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    think: bool,
    prompt: str,
) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": think,
        "options": {"num_ctx": NUM_CTX, "temperature": 0.7},
    }
    t0 = time.perf_counter()
    ttft: float | None = None
    out_tokens = 0
    think_tokens = 0
    in_thinking = False
    async with client.stream("POST", f"{base_url}/api/chat", json=payload) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line:
                continue
            data = json.loads(line)
            msg = data.get("message", {})
            content = msg.get("content") or ""
            thinking = msg.get("thinking") or ""
            if content or thinking:
                if ttft is None:
                    ttft = time.perf_counter() - t0
            if thinking:
                in_thinking = True
                think_tokens += max(1, len(thinking) // 4)
            elif content and not thinking:
                if in_thinking:
                    in_thinking = False
                out_tokens += max(1, len(content) // 4)
            if data.get("done"):
                break
    total_s = time.perf_counter() - t0
    gen_tokens = out_tokens + think_tokens
    return {
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "total_s": round(total_s, 2),
        "out_tokens_approx": out_tokens,
        "think_tokens_approx": think_tokens,
        "tok_per_s": round(gen_tokens / (total_s - (ttft or 0)), 1)
        if ttft and total_s > ttft
        else None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--think", nargs="+", type=lambda v: v.lower() == "true",
                        default=[False])
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()

    results = []
    async with httpx.AsyncClient(timeout=600) as client:
        health = await client.get(f"{args.ollama_url}/api/version")
        health.raise_for_status()

        for model in args.models:
            for think in args.think:
                mode = f"{model} think={think}"
                print(f"\n=== {mode} ===")
                for name, prompt in PROMPTS:
                    runs = []
                    for _ in range(args.rounds):
                        runs.append(
                            await bench_one(client, args.ollama_url, model, think, prompt)
                        )
                    valid_ttft = [r["ttft_s"] for r in runs if r["ttft_s"] is not None]
                    valid_tps = [r["tok_per_s"] for r in runs if r["tok_per_s"]]
                    ttft_med = round(statistics.median(valid_ttft), 2) if valid_ttft else None
                    tok_med = round(statistics.median(valid_tps), 1) if valid_tps else None
                    agg = {
                        "model": model,
                        "think": think,
                        "prompt": name,
                        "ttft_med_s": ttft_med,
                        "total_med_s": round(statistics.median([r["total_s"] for r in runs]), 2),
                        "tok_s_med": tok_med,
                        "think_tok_med": int(statistics.median(
                            [r["think_tokens_approx"] for r in runs])),
                    }
                    results.append(agg)
                    print(f"  {name:11s} ttft={agg['ttft_med_s']}s"
                          f" total={agg['total_med_s']}s"
                          f" tok/s={agg['tok_s_med']}"
                          f" think_tok={agg['think_tok_med']}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / (
        "chat_bench_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + ".json"
    )
    out.write_text(json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "num_ctx": NUM_CTX,
        "rounds": args.rounds,
        "results": results,
    }, indent=2))
    print(f"\nSaved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
