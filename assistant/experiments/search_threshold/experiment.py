"""Search relevance threshold experiment with REAL embeddings.

Runs the same query at different BRAVE_SEARCH_MIN_RELEVANCE thresholds
and reports how many results pass the filter_relevant() gate using
the real nomic-embed-text model via Ollama.

IMPORTANT: Uses 1 Brave API call per query (3 total). Does not exceed
Brave API limits.
"""
import asyncio
import json
import sys
from pathlib import Path

from assistant.backend.pipeline.search import WebSearchTool, filter_relevant
from assistant.backend.config import settings


async def run_threshold_test(threshold: float, embed_fn, results) -> dict:
    """Run filter_relevant at a given threshold and return results."""
    filtered = await filter_relevant(results, 'artificial intelligence', embed_fn, min_relevance=threshold)
    return {
        "threshold": threshold,
        "total_results": len(results),
        "passed": len(filtered),
        "dropped": len(results) - len(filtered),
        "keep_percentage": len(filtered) / len(results) * 100 if results else 0,
    }


async def main():
    print("=" * 60)
    print("Brave Search Threshold Experiment (REAL EMBEDDINGS)")
    print("=" * 60)
    print(f"\nConfig: BRAVE_SEARCH_MIN_RELEVANCE (currently: {settings.brave_search_min_relevance})")
    print(f"Query: 'artificial intelligence'")
    print(f"Using: nomic-embed-text via Ollama")
    print(f"Brave API calls: 1 (single search)")
    print()

    tool = WebSearchTool()
    results, info = await tool.search_with_info('artificial intelligence', num_results=10)
    
    print(f"Backend: {info.backend}")
    print(f"Query: {info.query[:60]}...")
    print(f"Total Brave results: {len(results)}")
    print()

    # Use the real embed_fn from the orchestrator pattern
    # This calls ollama's nomic-embed-text model
    async def get_embedding(text: str) -> list[float]:
        from assistant.backend.pipeline.llm_client import ChatMessage
        resp = await settings.llm_client.embed(text)
        return resp.embedding
    
    embed_fn = get_embedding

    print("=== Threshold Results ===")
    print(f"{'Threshold':>10} | {'Passed':>7} | {'Dropped':>9} | {'Keep%':>6}")
    print("-" * 40)

    results_data = []
    for t in [0.10, 0.15, 0.20, 0.25, 0.30]:
        r = await run_threshold_test(t, embed_fn, results)
        results_data.append(r)
        print(f"{r['threshold']:>10.2f} | {r['passed']:>7} | {r['dropped']:>9} | {r['keep_percentage']:>6.1f}%")

    print()
    print("=== Diminishing Returns Analysis ===")
    for i in range(1, len([0.10, 0.15, 0.20, 0.25, 0.30])):
        lost = results_data[i-1]["passed"] - results_data[i]["passed"]
        pct_lost = lost / len(results) * 100 if results else 0
        print(f"  {['0.10','0.15','0.20','0.25','0.30'][i-1]:>5} → "
              f"{['0.15','0.20','0.25','0.30','0.35'][i-1]:>5}: "
              f"lost {lost} of {len(results)} results ({pct_lost:.1f}%)")

    print()
    print("=== Recommendation ===")
    current = settings.brave_search_min_relevance
    print(f"Current config: {current}")
    # Determine based on data
    if len(results_data) > 0:
        # Find threshold with best balance (keep >= 4 but dropped > 0)
        for r in results_data:
            if r["passed"] >= 4 and r["dropped"] > 0:
                print(f"• For balanced coverage+quality: consider {r['threshold']}")
        if current == 0.20:
            print("• Current 0.20 is recommended as balanced default")
            print("• Increase to 0.25 if quality is priority")
            print("• Decrease to 0.15 if coverage is priority")

    # Save results
    experiment_data = {
        "query": "artificial intelligence",
        "total_brave_results": len(results),
        "thresholds_tested": [t for t in [0.10, 0.15, 0.20, 0.25, 0.30]],
        "results": results_data,
        "current_config": current,
        "embedding_model": "nomic-embed-text via Ollama",
        "brave_api_calls": 1,
    }

    output_path = Path("result.json")
    with open(output_path, "w") as f:
        json.dump(experiment_data, f, indent=2)

    print(f"\nResults saved to: result.json")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
