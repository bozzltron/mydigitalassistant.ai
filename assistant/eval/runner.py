"""Evaluation harness for the cognitive assistant.

Runs a dataset of learn-then-recall test cases and reports precision/recall.

Usage:
    python -m assistant.eval.runner

Requires real Ollama + SearXNG. Uses an isolated test DB per run.
"""

import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import OllamaClient
from assistant.backend.pipeline.orchestrator import ChatRequest, Orchestrator, OrchestratorDeps
from assistant.backend.pipeline.search import WebSearchTool

DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_DIR = Path(__file__).parent.parent.parent / "eval_results"
MAX_SETUP_TURNS = 3


class ThinkControlClient(OllamaClient):
    """OllamaClient that records and optionally forces the chat-role think flag.

    Used by the §6.4 gate: 'never'/'always' bracket production escalation,
    'auto' records how often the reasoner escalates (<15% target).
    Only chat-model calls are counted; utility-role calls pass through.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.think_calls: list[bool] = []
        self.force_think: bool | None = None

    async def chat(self, messages, *args, **kwargs):
        is_chat_role = kwargs.get("model") in (None, self.chat_model)
        forced = None if self.force_think is None else self.force_think
        if is_chat_role and kwargs.get("stream") is not True:
            effective = True if self.force_think else kwargs.get("think", False)
            self.think_calls.append(bool(effective))
        if forced is not None:
            kwargs["think"] = forced
            kwargs.pop("num_predict", None)
        return await super().chat(messages, *args, **kwargs)


def grade_answer(answer: str, keywords: list[str]) -> tuple[int, int, str]:
    """Grade an answer by keyword presence.

    Returns (score, max_score, feedback).
    Score is the fraction of keywords found (0 to len(keywords)).
    """
    answer_lower = answer.lower()
    found = sum(1 for kw in keywords if kw.lower() in answer_lower)
    max_score = len(keywords)
    score = found
    matched = [kw for kw in keywords if kw.lower() in answer_lower]
    feedback = f"{found}/{max_score} keywords matched: {matched}"
    return score, max_score, feedback


async def run_case(
    case: dict,
    store: MemoryStore,
    orchestrator: Orchestrator,
    user_id: int,
) -> dict:
    """Run a single evaluation case. Returns per-question results."""
    case_name = case["name"]
    setup = case.get("setup", "")
    correction = case.get("correction", "")
    questions = case["questions"]

    results = []

    async def ask(prompt: str) -> str:
        resp = await orchestrator.chat(ChatRequest(user_id=user_id, message=prompt))
        await asyncio.sleep(0.5)  # Rate limit
        return resp.response

    # Setup phase: teach the agent
    if setup:
        await ask(setup)

    # Correction phase if present
    if correction:
        await ask(correction)

    # Exam phase
    for q_item in questions:
        question = q_item["q"]
        keywords = q_item.get("keywords", [])
        expected = q_item.get("answer", "")

        answer = await ask(question)
        score, max_score, feedback = grade_answer(answer, keywords)

        results.append({
            "question": question,
            "expected": expected,
            "answer": answer,
            "keywords": keywords,
            "score": score,
            "max_score": max_score,
            "feedback": feedback,
        })

    total_score = sum(r["score"] for r in results)
    total_max = sum(r["max_score"] for r in results)
    pct = (total_score / total_max * 100) if total_max > 0 else 0

    return {
        "name": case_name,
        "description": case.get("description", ""),
        "questions": results,
        "total_score": total_score,
        "total_max": total_max,
        "pct": round(pct, 1),
    }


async def main() -> dict:
    think_mode = "auto"
    if "--think-mode" in sys.argv:
        i = sys.argv.index("--think-mode")
        think_mode = sys.argv[i + 1]
        assert think_mode in ("auto", "never", "always"), think_mode

    dataset = json.loads(DATASET_PATH.read_text())
    cases = dataset["cases"]

    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_id = f"eval_{timestamp}"
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir()

    print(f"\n{'='*60}")
    print("Cognitive Assistant Evaluation Harness")
    print(f"{'='*60}")
    print(f"Dataset:   {DATASET_PATH.name} ({len(cases)} cases)")
    print(f"Ollama:    {settings.ollama_url}")
    print(f"Search:    {settings.search_base_url} (disabled for eval)")
    print(f"Run ID:    {run_id}")
    print(f"{'='*60}\n")

    # Init test DB
    test_db_path = run_dir / "eval_brain.db"
    await init_db(str(test_db_path))

    store = MemoryStore(str(test_db_path))
    user = await store.create_user("eval_student")

    llm = ThinkControlClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
        coder_model=settings.coder_model,
    )
    llm.force_think = {"never": False, "always": True}.get(think_mode)
    retriever = Retriever(store=store, llm_client=llm)
    # Eval determinism: learn-then-recall cases run without web search.
    search = WebSearchTool(base_url=settings.search_base_url, enabled=False)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=llm,
            search_tool=search,
        )
    )

    all_results = []
    start_time = time.time()

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] Running: {case['name']}...", end=" ", flush=True)
        case_result = await run_case(case, store, orchestrator, user.id)
        all_results.append(case_result)
        print(f"✓ {case_result['pct']}%")

        for qr in case_result["questions"]:
            print(f"    Q: {qr['question']}")
            print(f"    A: {qr['answer'][:80]}{'...' if len(qr['answer']) > 80 else ''}")
            print(f"    → {qr['feedback']}")

    elapsed = time.time() - start_time

    # Summary
    total_score = sum(r["total_score"] for r in all_results)
    total_max = sum(r["total_max"] for r in all_results)
    overall_pct = (total_score / total_max * 100) if total_max > 0 else 0

    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "dataset": DATASET_PATH.name,
        "think_mode": think_mode,
        "chat_model": settings.chat_model,
        "total_cases": len(cases),
        "total_score": total_score,
        "total_max": total_max,
        "overall_pct": round(overall_pct, 1),
        "escalated_calls": sum(llm.think_calls),
        "total_chat_calls": len(llm.think_calls),
        "escalation_rate": round(
            sum(llm.think_calls) / len(llm.think_calls), 3
        ) if llm.think_calls else 0.0,
        "elapsed_seconds": round(elapsed, 1),
        "cases": all_results,
    }

    # Save results
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(summary, indent=2))

    print(f"\n{'='*60}")
    print("RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"Overall:   {total_score}/{total_max} ({overall_pct}%)")
    print(f"Mode:      think={think_mode}  model={settings.chat_model}")
    if think_mode == "auto":
        rate = summary["escalation_rate"]
        flag = "OK" if rate < 0.15 else "OVER TARGET"
        print(f"Escalation: {summary['escalated_calls']}/{summary['total_chat_calls']}"
              f" ({rate:.0%}) target <15% [{flag}]")
    print(f"Duration:  {elapsed:.1f}s")
    print(f"Results:   {results_path}")
    print(f"{'='*60}")

    for r in all_results:
        status = "PASS" if r["pct"] >= 60 else "FAIL"
        print(f"  [{status}] {r['name']}: {r['pct']}%")

    print(f"{'='*60}\n")

    await llm.close()
    await search.close()

    return summary


if __name__ == "__main__":
    try:
        summary = asyncio.run(main())
        sys.exit(0 if summary["overall_pct"] >= 60 else 1)
    except Exception as e:
        print(f"\nEvaluation failed: {e}", file=sys.stderr)
        sys.exit(2)
