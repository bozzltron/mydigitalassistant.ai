"""Search engine comparison harness.

Compares each enabled SearXNG engine (brave, mojeek, bing, wikipedia,
startpage) across a diverse set of real-world queries. Reports result
count, latency, and quality per engine per category.

Usage:
    python -m assistant.eval.search_engine_comparison [--category news|all] [--warmup]
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import httpx

RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"
SEARCH_BASE = "http://127.0.0.1:8080"

ENGINES = ["brave", "mojeek", "bing", "wikipedia", "startpage", "marginalia", "mwmbl"]

CATEGORIES = {
    "news": [
        "latest AI news 2026",
        "breaking science news today",
        "climate change latest developments 2026",
        "stock market today news",
        "sports championship results this week",
    ],
    "howto": [
        "how to change a tire step by step",
        "how to cook perfect rice without a rice cooker",
        "how to fix a leaky faucet under the sink",
        "how to start a vegetable garden in backyard",
        "how to troubleshoot WiFi connection problems",
    ],
    "product": [
        "best laptop for remote work 2026",
        "top rated coffee makers under 200",
        "reliable family SUV recommendations 2026",
        "best noise cancelling headphones for travel",
    ],
    "person": [
        "who is the current CEO of NVIDIA",
        "Ada Lovelace biography",
        "who invented the World Wide Web",
        "current NASA administrator name",
    ],
    "code": [
        "Python list comprehension tutorial",
        "fix Python TypeError 'str' object is not callable",
        "Docker container networking explained",
        "async await Python example",
    ],
    "definition": [
        "what is quantum computing simply explained",
        "definition of machine learning for beginners",
        "what does API mean in software development",
        "explain large language model in plain terms",
    ],
    "local": [
        "best pizza restaurant near Portland Oregon",
        "trusted plumber recommendations Seattle",
        "good hiking trails near Mount Hood",
    ],
    "opinion": [
        "is ChatGPT safe for children's education",
        "are electric vehicles actually better for environment",
        "AI art good or bad for artists",
    ],
}

ALL_QUERIES = [q for cat_queries in CATEGORIES.values() for q in cat_queries]


class EngineResult(TypedDict):
    query: str
    category: str
    engine: str
    result_count: int
    latency_ms: float
    results: list[dict]
    error: str | None
    suspended: bool


async def query_engine(
    client: httpx.AsyncClient, engine: str, query: str, timeout: float = 15.0
) -> EngineResult:
    url = f"{SEARCH_BASE}/search"
    params = {"q": query, "format": "json", "engines": engine}
    t0 = time.perf_counter()
    try:
        resp = await client.get(url, params=params, timeout=timeout)
        latency_ms = (time.perf_counter() - t0) * 1000
        data = resp.json()
        suspended = any(
            eng == engine for eng, _reason in data.get("unresponsive_engines", [])
        )
        error = None
        if suspended:
            error = "suspended"
        result_count = len(data.get("results", []))
        return EngineResult(
            query=query,
            category="",
            engine=engine,
            result_count=result_count,
            latency_ms=round(latency_ms, 1),
            results=data.get("results", []),
            error=error,
            suspended=suspended,
        )
    except Exception as e:
        return EngineResult(
            query=query,
            category="",
            engine=engine,
            result_count=0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            results=[],
            error=str(e),
            suspended=False,
        )


async def warmup_engine(client: httpx.AsyncClient, engine: str) -> bool:
    """Run 3 warmup queries to clear any temporary suspensions."""
    for q in ["current weather in Portland", "latest movie releases", "Python tutorial"]:
        r = await query_engine(client, engine, q, timeout=10.0)
        if r["suspended"] or r["error"]:
            await asyncio.sleep(2)
    return True


async def run_evaluation(
    categories: list[str], warmup: bool, max_queries: int | None
) -> dict:
    queries_by_cat = {
        cat: CATEGORIES[cat]
        for cat in categories
        if cat in CATEGORIES
    }
    all_queries: list[tuple[str, str]] = [
        (q, cat) for cat, qs in queries_by_cat.items() for q in qs
    ]
    if max_queries:
        all_queries = all_queries[:max_queries]

    print("\n=== Search Engine Comparison ===")
    print(f"Categories: {categories}")
    print(f"Queries: {len(all_queries)}")
    print(f"Warmup: {warmup}")
    print(f"Engines: {ENGINES}\n")

    async with httpx.AsyncClient() as client:
        if warmup:
            print("Warming up engines (3 queries each)...")
            for engine in ENGINES:
                await warmup_engine(client, engine)
                print(f"  {engine}: warmed up")
            print()

        print("Running queries...")
        all_results: list[dict] = []

        for i, (query, category) in enumerate(all_queries):
            print(f"  [{i+1}/{len(all_queries)}] {query[:60]}")
            engine_results: dict[str, EngineResult] = {}

            tasks = [query_engine(client, e, query) for e in ENGINES]
            results = await asyncio.gather(*tasks)
            for r in results:
                r["category"] = category
                engine_results[r["engine"]] = r

            all_results.append(
                {"query": query, "category": category, "engine_results": engine_results}
            )

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "categories": categories,
        "engines": ENGINES,
        "total_queries": len(all_queries),
        "results": all_results,
    }


def print_summary(data: dict) -> None:
    results = data["results"]
    engines = data["engines"]

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print("\n### Per-engine statistics\n")
    hdr1 = f"{'Engine':<12} {'Queries':>7} {'Results/q':>9} {'Latency':>10}"
    hdr2 = f"{'Suspended':>10} {'Errors':>7}"
    print(hdr1 + hdr2)
    print("-" * 55)

    for engine in engines:
        qs_with_results = [
            r
            for r in results
            if r["engine_results"].get(engine, {}).get("result_count", 0) > 0
        ]
        latencies = [
            r["engine_results"].get(engine, {}).get("latency_ms", 0)
            for r in results
        ]
        suspended_count = sum(
            1
            for r in results
            if r["engine_results"].get(engine, {}).get("suspended", False)
        )
        error_count = sum(
            1
            for r in results
            if r["engine_results"].get(engine, {}).get("error")
            and not r["engine_results"].get(engine, {}).get("suspended", False)
        )

        avg_lat = statistics.mean([x for x in latencies if x > 0]) if latencies else 0
        avg_res = statistics.mean(
            [
                r["engine_results"].get(engine, {}).get("result_count", 0)
                for r in results
            ]
        ) if results else 0

        print(
            f"{engine:<12} "
            f"{len(qs_with_results):>7} "
            f"{avg_res:>9.1f} "
            f"{avg_lat:>9.1f}ms "
            f"{suspended_count:>10} "
            f"{error_count:>7}"
        )

    print("\n### Per-category result counts\n")
    cats = sorted(set(r["category"] for r in results))
    for cat in cats:
        cat_results = [r for r in results if r["category"] == cat]
        print(f"  {cat}:")
        for engine in engines:
            counts = [
                r["engine_results"].get(engine, {}).get("result_count", 0)
                for r in cat_results
            ]
            avg = statistics.mean(counts) if counts else 0
            print(f"    {engine}: avg {avg:.1f} results")
        print()


def interactive_rate(data: dict) -> dict:
    """Let user rate each query's results interactively."""
    results = data["results"]
    ratings: dict[int, dict[str, int | None]] = {}

    print("\n" + "=" * 80)
    print("INTERACTIVE RATING")
    print("=" * 80)
    print("Rate each engine's results for each query: 0=bad, 1=ok, 2=good, 3=great")
    print("Press Enter to skip a query. Type 'q' to quit rating.\n")

    for i, row in enumerate(results):
        query = row["query"]
        cat = row["category"]
        print(f"\n[{i+1}/{len(results)}] CATEGORY: {cat}")
        print(f"QUERY: {query}")
        print("-" * 60)

        for engine in data["engines"]:
            er = row["engine_results"].get(engine, {})
            count = er.get("result_count", 0)
            suspended = er.get("suspended", False)
            error = er.get("error")
            print(f"\n  {engine}: ", end="")
            if suspended:
                print(f"SUSPENDED ({error})")
            elif error:
                print(f"ERROR: {error}")
            elif count == 0:
                print("0 results")
            else:
                print(f"{count} results")
                for r in er.get("results", [])[:3]:
                    print(f"    - {r.get('title', 'no title')}")

        print()
        try:
            prompt = "  Rate (0/1/2/3/Enter=skip): "
            rating_str = input(prompt).strip()
            if rating_str.lower() == "q":
                print("Stopping rating.")
                break
            if rating_str == "":
                continue
            for part in rating_str.split(","):
                if "=" in part:
                    eng, val = part.strip().split("=", 1)
                    eng = eng.strip()
                    val = int(val.strip())
                    if i not in ratings:
                        ratings[i] = {}
                    ratings[i][eng] = val
        except (EOFError, KeyboardInterrupt):
            print("\nStopping rating.")
            break

    return ratings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category",
        nargs="+",
        default=["all"],
        choices=list(CATEGORIES.keys()) + ["all"],
        help="Category subset to test",
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help="Run warmup queries before the main eval to clear suspensions",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Limit number of queries (for quick testing)",
    )
    parser.add_argument(
        "--rate",
        action="store_true",
        help="Interactive rating after collecting results",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path (default: auto in eval_results/)",
    )
    args = parser.parse_args()

    categories = list(CATEGORIES.keys()) if "all" in args.category else args.category

    data = asyncio.run(run_evaluation(categories, args.warmup, args.max_queries))

    print_summary(data)

    if args.rate:
        ratings = interactive_rate(data)
        data["human_ratings"] = ratings

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    default_path = RESULTS_DIR / f"search_engine_compare_{ts}.json"
    out_path = Path(args.output) if args.output else default_path
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\nSaved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
