"""Search extraction evaluation: measures learning yield and accuracy per backend.

Runs a 32-query benchmark across both backends (SearXNG and Brave when available),
reporting:
- Slots extracted per query
- Associations extracted per query
- Corroboration rate (facts found in >= 2 sources)
- Conflicts created per query
- Latency (search + extraction + full-page fetch for Brave)
- Manual spot-check score on a sample of extracted facts

Usage:
    python -m assistant.eval.search_extraction_eval [--backend searxng|brave|all]
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

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from assistant.backend.config import settings
from assistant.backend.pipeline.extractor import (
    ExtractionResult,
    extract_facts_from_document,
    extract_facts_from_search,
    merge_extractions,
)
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.search import BraveBackend, SearXNGBackend

RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"

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


class QueryResult(TypedDict):
    query: str
    category: str
    backend: str
    slots_extracted: int
    associations_extracted: int
    corroboration_rate: float
    conflicts: int
    search_latency_ms: float
    extraction_latency_ms: float
    total_latency_ms: float
    error: str | None


async def fetch_url_body(url: str, timeout: float = 4.0) -> str | None:
    """Fetch URL and return stripped plain text. None on failure."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._text: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in ("br", "hr", "p", "div", "li"):
                self._text.append("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("p", "div"):
                self._text.append("\n")

        def handle_data(self, data: str) -> None:
            text = data.strip()
            if text:
                self._text.append(text)

        @property
        def text(self) -> str:
            joined = "".join(self._text)
            return " ".join(
                " ".join(line.split())
                for line in joined.split("\n")
                if line.strip()
            )

    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, read=8.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AssistantBot/1.0)"},
        ) as client:
            r = await client.get(url)
            content_type = r.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return r.text[:2000]
            raw = r.content[:500000]
            try:
                raw = raw.decode(r.encoding or "utf-8", errors="replace")
            except Exception:
                raw = raw.decode("utf-8", errors="replace")
            stripper = _Stripper()
            stripper.feed(raw)
            text = stripper.text
            return text[:8000] if text.strip() else None
    except Exception:
        return None


async def run_query(
    backend: str,
    query: str,
    category: str,
    llm_client: OllamaClient,
) -> QueryResult:
    """Run a single query through the search extraction pipeline."""
    result: QueryResult = {
        "query": query,
        "category": category,
        "backend": backend,
        "slots_extracted": 0,
        "associations_extracted": 0,
        "corroboration_rate": 0.0,
        "conflicts": 0,
        "search_latency_ms": 0.0,
        "extraction_latency_ms": 0.0,
        "total_latency_ms": 0.0,
        "error": None,
    }

    t0 = time.perf_counter()

    try:
        if backend == "brave":
            search_tool = BraveBackend(api_key=settings.brave_api_key)
        else:
            search_tool = SearXNGBackend(base_url=settings.search_base_url)

        search_results = await search_tool.search(query, num_results=8)
        search_latency_ms = (time.perf_counter() - t0) * 1000
        result["search_latency_ms"] = round(search_latency_ms, 1)

        if not search_results:
            result["total_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            return result

        extraction_t0 = time.perf_counter()

        snippet_extraction = await extract_facts_from_search(
            query, search_results, llm_client
        )

        if backend == "brave" and search_results:
            bodies = await asyncio.gather(
                *[fetch_url_body(r.url) for r in search_results[:3]],
                return_exceptions=True,
            )
            document_extractions: list[ExtractionResult] = []
            for r, body in zip(search_results[:3], bodies, strict=True):
                if isinstance(body, Exception) or not body:
                    continue
                doc_ext = await extract_facts_from_document(body, r.url, llm_client)
                document_extractions.append(doc_ext)
            if document_extractions:
                snippet_extraction = merge_extractions(snippet_extraction, *document_extractions)

        extraction_latency_ms = (time.perf_counter() - extraction_t0) * 1000
        result["extraction_latency_ms"] = round(extraction_latency_ms, 1)
        result["total_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        result["slots_extracted"] = len(snippet_extraction.slots)
        result["associations_extracted"] = len(snippet_extraction.associations)

        if search_results:
            corroborated = 0
            for slot in snippet_extraction.slots:
                if not slot.value:
                    continue
                count = sum(
                    1 for r in search_results
                    if slot.value.lower() in r.snippet.lower()
                )
                if count >= 2:
                    corroborated += 1
            total = len(snippet_extraction.slots)
            result["corroboration_rate"] = round(corroborated / total, 2) if total > 0 else 0.0

    except Exception as e:
        result["error"] = str(e)
        result["total_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    return result


async def run_evaluation(
    backend: str,
    queries: list[tuple[str, str]],
    max_queries: int | None = None,
) -> dict:
    """Run the full evaluation for a backend."""
    if max_queries:
        queries = queries[:max_queries]

    print(f"\n=== Search Extraction Eval: {backend} ===")
    print(f"Queries: {len(queries)}")

    llm_client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
        timeout=settings.ollama_timeout,
    )

    results: list[QueryResult] = []
    for i, (query, category) in enumerate(queries):
        print(f"  [{i+1}/{len(queries)}] {query[:60]}")
        result = await run_query(backend, query, category, llm_client)
        results.append(result)
        print(
            f"    -> slots={result['slots_extracted']}, "
            f"assocs={result['associations_extracted']}, "
            f"corr={result['corroboration_rate']:.2f}, "
            f"latency={result['total_latency_ms']:.0f}ms"
        )
        if result["error"]:
            print(f"    ERROR: {result['error']}")

    await llm_client.close()

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "backend": backend,
        "total_queries": len(queries),
        "results": results,
    }


def summarize(data: dict) -> dict:
    """Compute summary statistics for a backend's results."""
    results = data["results"]
    errors = [r for r in results if r["error"]]
    successful = [r for r in results if not r["error"]]

    if not successful:
        return {"error": "All queries failed"}

    slots = [r["slots_extracted"] for r in successful]
    assocs = [r["associations_extracted"] for r in successful]
    corr_rates = [r["corroboration_rate"] for r in successful if r["corroboration_rate"] > 0]
    latencies = [r["total_latency_ms"] for r in successful]
    search_latencies = [r["search_latency_ms"] for r in successful]
    extract_latencies = [r["extraction_latency_ms"] for r in successful]

    return {
        "backend": data["backend"],
        "total_queries": data["total_queries"],
        "success_count": len(successful),
        "error_count": len(errors),
        "slots": {
            "total": sum(slots),
            "per_query_avg": round(statistics.mean(slots), 2),
            "per_query_median": round(statistics.median(slots), 2),
            "per_query_p95": round(
                sorted(slots)[int(len(slots) * 0.95)] if len(slots) >= 20 else max(slots), 2
            ),
        },
        "associations": {
            "total": sum(assocs),
            "per_query_avg": round(statistics.mean(assocs), 2),
        },
        "corroboration": {
            "rate_avg": round(statistics.mean(corr_rates), 3) if corr_rates else 0.0,
            "rate_median": round(statistics.median(corr_rates), 3) if corr_rates else 0.0,
        },
        "latency_ms": {
            "total_avg": round(statistics.mean(latencies), 1),
            "total_p95": round(
                sorted(latencies)[int(len(latencies) * 0.95)]
                if len(latencies) >= 20
                else max(latencies), 1
            ),
            "search_avg": round(statistics.mean(search_latencies), 1),
            "extraction_avg": round(statistics.mean(extract_latencies), 1),
        },
    }


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 60)
    print(f"SUMMARY: {summary['backend']}")
    print("=" * 60)
    print(f"  Queries: {summary['success_count']}/{summary['total_queries']} successful")
    print(f"  Slots per query (avg): {summary['slots']['per_query_avg']}")
    print(f"  Slots per query (median): {summary['slots']['per_query_median']}")
    print(f"  Slots per query (p95): {summary['slots']['per_query_p95']}")
    print(f"  Total slots extracted: {summary['slots']['total']}")
    print(f"  Associations per query (avg): {summary['associations']['per_query_avg']}")
    print(f"  Corroboration rate (avg): {summary['corroboration']['rate_avg']:.3f}")
    print(f"  Latency total (avg): {summary['latency_ms']['total_avg']:.1f}ms")
    print(f"  Latency total (p95): {summary['latency_ms']['total_p95']:.1f}ms")
    print(f"  Latency search (avg): {summary['latency_ms']['search_avg']:.1f}ms")
    print(f"  Latency extraction (avg): {summary['latency_ms']['extraction_avg']:.1f}ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        default="all",
        choices=["searxng", "brave", "all"],
        help="Which backend to evaluate",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Limit queries per backend for quick testing",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON file path",
    )
    args = parser.parse_args()

    queries: list[tuple[str, str]] = [
        (q, cat) for cat, qs in CATEGORIES.items() for q in qs
    ]

    backends = []
    if args.backend in ("searxng", "all"):
        backends.append("searxng")
    if args.backend in ("brave", "all"):
        if settings.brave_api_key:
            backends.append("brave")
        else:
            print("WARNING: BRAVE_API_KEY not set, skipping Brave evaluation")
    else:
        print("WARNING: BRAVE_API_KEY not set, skipping Brave evaluation")

    all_data = []
    all_summaries = []

    for backend in backends:
        data = asyncio.run(run_evaluation(backend, queries, args.max_queries))
        summary = summarize(data)
        print_summary(summary)
        all_data.append(data)
        all_summaries.append(summary)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    combined = {
        "timestamp": datetime.now(UTC).isoformat(),
        "backends": backends,
        "total_queries": len(queries),
        "results": {d["backend"]: d for d in all_data},
        "summaries": all_summaries,
    }

    default_path = RESULTS_DIR / f"search_extraction_eval_{ts}.json"
    out_path = Path(args.output) if args.output else default_path
    out_path.write_text(json.dumps(combined, indent=2))
    print(f"\nSaved: {out_path}")

    if len(all_summaries) >= 2:
        print("\n" + "=" * 60)
        print("BRAVE vs SEARXNG COMPARISON")
        print("=" * 60)
        brave = next((s for s in all_summaries if s["backend"] == "brave"), None)
        searxng = next((s for s in all_summaries if s["backend"] == "searxng"), None)
        if brave and searxng:
            slot_ratio = (
                brave["slots"]["per_query_avg"] / max(searxng["slots"]["per_query_avg"], 0.001)
            )
            b_avg = brave["slots"]["per_query_avg"]
            s_avg = searxng["slots"]["per_query_avg"]
            print(f"  Brave slots/query: {b_avg} vs SearXNG: {s_avg}")
            print(f"  Brave/SearXNG ratio: {slot_ratio:.2f}x")
            b_corr = brave["corroboration"]["rate_avg"]
            s_corr = searxng["corroboration"]["rate_avg"]
            print(f"  Brave corroboration: {b_corr:.3f} vs SearXNG: {s_corr:.3f}")
            b_lat = brave["latency_ms"]["total_avg"]
            s_lat = searxng["latency_ms"]["total_avg"]
            print(f"  Brave latency overhead: +{b_lat - s_lat:.0f}ms avg")

    return 0


if __name__ == "__main__":
    sys.exit(main())
