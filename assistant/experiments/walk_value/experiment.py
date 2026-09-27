"""Does the fixed graph walk make the agent better, or just busier?

The yield experiment (assistant/experiments/graph_walk_yield) established that
the walk supplies 68% of the candidate pool and 65% of what the model actually
sees, and that max_graph_frames=7 is exactly the number that survives the
frame-aware fit. Neither fact says the agent answers better.

This measures the user-visible outcome: gold recall and abstention, with and
without the walk, plus attribution of which frames supplied the answers. See
plan.md for the design, the falsification conditions, and the threats.

Read-only. Runs against a copy in a scratch volume; the live volume is never
mounted into the container this runs in. Table digests are captured before and
after so non-mutation is verifiable rather than asserted.

Usage:
    EXP_DB=/work/assistant.db python experiment.py
Env:
    WALK_ARMS=0,7           max_graph_frames per arm
    WALK_VALUE_REPS=3       replicates
    WALK_VALUE_SEED=...     recorded provenance
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import _load_sqlite_vec, init_db
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.retrieval import Retriever, format_memory_context
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import (
    ChatMessage,
    OllamaClient,
    build_system_prompt,
    system_prompt_overhead,
)

# The queries and gold facts come from the frame budget experiment unchanged, so
# recall|shown stays comparable to its published 0.910 and the queries are not
# re-screened against a fix that had not shipped when they were chosen. See
# plan.md threat 1.
from assistant.experiments.frame_budget.experiment import (  # noqa: E402
    QUERIES as ALL_QUERIES,
)
from assistant.experiments.frame_budget.experiment import (
    is_abstention,
    norm,
)

# The frame budget experiment screens at FACT level, not query level: of its 14
# queries, 11 had at least one fact that the agent could actually answer from
# memory (22 facts total). Reuse that screening verbatim rather than re-deriving
# it, so this experiment's query set is exactly the set behind the published
# recall|shown figure and 38 unfiltered gold facts do not quietly enter as
# unanswerable noise. The screening lives in the published result.json.
_FRAME_BUDGET_RESULT = (
    Path(__file__).resolve().parent.parent / "frame_budget" / "result.json"
)
_screened = json.loads(_FRAME_BUDGET_RESULT.read_text())["screened_facts"]


@dataclass
class Case:
    """A query plus only the gold facts the frame budget run retained."""

    qid: str
    text: str
    target_frame_id: int
    gold: tuple  # tuple[GoldFact, ...]


CASES: list[Case] = [
    Case(
        q.qid,
        q.text,
        q.target_frame_id,
        tuple(f for f in q.gold if f.key in _screened.get(q.qid, [])),
    )
    for q in ALL_QUERIES
    if _screened.get(q.qid)
]

DB_PATH = os.environ.get("EXP_DB", "/work/assistant.db")
OUT_DIR = Path(__file__).parent
ARMS = [int(a) for a in os.environ.get("WALK_ARMS", "0,7").split(",")]
REPLICATES = int(os.environ.get("WALK_VALUE_REPS", "3"))
SEED = os.environ.get("WALK_VALUE_SEED", "20260927")
USER_ID = int(os.environ.get("EXP_USER_ID", "1"))
USER_TEXT = os.environ.get("EXP_USER_TEXT", "What's the mailing address of the record label?")
TEMPERATURE = 0.7  # settings.chat_temperature in production
THINK = False  # settings.chat_think_default in production

DIGEST_TABLES = ("frames", "slots", "associations", "episodes", "slot_history", "conflicts")

# The memory section's real allowance, as Orchestrator._memory_char_budget
# computes it: the prompt cap minus the *measured* persona/task prefix. Using the
# bare cap would report a fit ~2k chars looser than production and hide
# truncation the user actually experiences.
MEMORY_CHAR_BUDGET = max(
    0,
    settings.max_system_prompt_chars - system_prompt_overhead("functional"),
)

MEMORY_MARKER = "You have the following relevant memory state:"


async def digest_db() -> dict:
    """Row counts per table, as a non-mutation check."""
    async with aiosqlite_connect(DB_PATH) as db:
        await _load_sqlite_vec(db)
        out: dict[str, int] = {}
        for table in DIGEST_TABLES:
            try:
                out[table] = (await db.execute_fetchall(f"SELECT COUNT(*) FROM {table}"))[0][0]
            except Exception as exc:  # table absent in an older schema
                out[table] = f"n/a ({type(exc).__name__})"
        return out


def hits(forms: list[str], text: str) -> bool:
    """Word-boundary substring match, the frame budget experiment's grader."""
    padded = f" {norm(text)} "
    return any(f" {norm(form)} " in padded for form in forms)


def shown(forms: list[str], memory: str, sys_prompt: str) -> bool:
    """Whether the fact reached the model in the text that was SENT.

    Measured against the delivered prompt, not the rendered memory: the memory
    section is appended last, so the orchestrator's flat cut at
    max_system_prompt_chars discards its tail. Crediting the model with facts
    from the discarded tail would overstate recall.
    """
    start = sys_prompt.find(MEMORY_MARKER)
    if start < 0 or not memory:
        return False
    survived = min(len(memory), max(0, settings.max_system_prompt_chars - start))
    if survived <= len(MEMORY_MARKER):
        return False
    return hits(forms, memory[:survived])


def deliver(memory: str) -> str:
    """Apply the orchestrator's flat ceiling, as production does."""
    prompt = build_system_prompt(memory_context=memory, task_type="functional")
    if len(prompt) > settings.max_system_prompt_chars:
        prompt = prompt[: settings.max_system_prompt_chars] + "\n\n[... truncated ...]"
    return prompt


async def main() -> None:
    if not Path(DB_PATH).exists():
        raise SystemExit(f"brain not found at {DB_PATH}")

    await init_db(DB_PATH)
    store = MemoryStore(DB_PATH)
    llm = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        keep_alive=settings.ollama_keep_alive,
    )
    # Must mirror main.py's construction. Retriever's own default is
    # "nomic-embed-text" (768 dims) while the brain is embedded with
    # settings.embedding_model (1024 dims); omitting the argument makes every
    # search die on a dimension mismatch. min_relevance is a *similarity* gate
    # (0.3) and is NOT settings.retrieval_min_distance, which is a *distance*
    # threshold already passed to search_similar_frames as min_distance.
    retriever = Retriever(
        store=store,
        llm_client=llm,
        embedding_model=settings.embedding_model,
    )
    print(
        f"user_id={USER_ID}  arms={ARMS}  reps={REPLICATES}  seed={SEED}  "
        f"top_k_direct={retriever.top_k_direct}  min_relevance={retriever.min_relevance}  "
        f"memory_budget={MEMORY_CHAR_BUDGET}"
    )

    before = await digest_db()
    print("db digest before:", json.dumps(before))

    # Retrieve ONCE per arm so both replicates of a query see the same frames and
    # generation variance is not confounded with retrieval variance.
    pools: dict[int, dict[str, object]] = {}
    for arm in ARMS:
        retriever.max_graph_frames = arm
        per_query: dict[str, object] = {}
        for q in CASES:
            t0 = time.perf_counter()
            ctx = await retriever.retrieve(q.text, user_id=USER_ID)
            latency = (time.perf_counter() - t0) * 1000
            by_source = {rf.frame.id: rf.source for rf in ctx.retrieved_frames}
            per_query[q.qid] = {
                "context": ctx,
                "retrieval_ms": round(latency, 1),
                "target_present": q.target_frame_id in by_source,
                "target_source": by_source.get(q.target_frame_id),
                "frames": len(ctx.retrieved_frames),
                "walk_frames": sum(1 for s in by_source.values() if s.startswith("graph_hop")),
            }
        pools[arm] = per_query
        n_tgt = sum(1 for v in per_query.values() if v["target_present"])
        n_walk = sum(
            1
            for v in per_query.values()
            if str(v["target_source"]).startswith("graph_hop")
        )
        mean_frames = statistics.fmean(v["frames"] for v in per_query.values())
        print(
            f"  arm max_graph_frames={arm}: mean frames {mean_frames:.1f}, "
            f"target present in {n_tgt}/{len(CASES)}, "
            f"target reached VIA WALK in {n_walk}/{len(CASES)}"
        )

    runs: list[dict] = []
    for arm in ARMS:
        for rep in range(REPLICATES):
            for q in CASES:
                entry = pools[arm][q.qid]
                ctx = entry["context"]
                memory = format_memory_context(ctx, max_memory_chars=MEMORY_CHAR_BUDGET)
                sys_prompt = deliver(memory)
                t0 = time.perf_counter()
                resp = await llm.chat(
                    [ChatMessage(role="system", content=sys_prompt),
                     ChatMessage(role="user", content=q.text)],
                    temperature=TEMPERATURE,
                    think=THINK,
                )
                gen_ms = (time.perf_counter() - t0) * 1000
                runs.append(
                    {
                        "arm": arm,
                        "rep": rep,
                        "qid": q.qid,
                        "answer": resp.content,
                        "recall": {f.key: hits(f.forms, resp.content) for f in q.gold},
                        "shown": {
                            f.key: shown(f.forms, memory, sys_prompt) for f in q.gold
                        },
                        "abstained": is_abstention(resp.content),
                        "memory_chars": len(memory),
                        "frames": entry["frames"],
                        "walk_frames": entry["walk_frames"],
                        "target_present": entry["target_present"],
                        "target_source": entry["target_source"],
                        "generation_ms": round(gen_ms, 1),
                    }
                )
            arm_recall = statistics.fmean(
                1.0 if v else 0.0
                for r in runs if r["arm"] == arm
                for v in r["recall"].values()
            )
            print(f"  arm={arm} rep={rep} running recall={arm_recall:.3f}")

    after = await digest_db()
    print("db digest after: ", json.dumps(after))
    mutated = {k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}

    summary: dict[str, dict] = {}
    for arm in ARMS:
        arm_runs = [r for r in runs if r["arm"] == arm]
        per_rep = [
            round(
                statistics.fmean(
                    1.0 if v else 0.0
                    for r in arm_runs
                    if r["rep"] == rep
                    for v in r["recall"].values()
                ),
                3,
            )
            for rep in range(REPLICATES)
        ]
        flat = [(r["qid"], k, v) for r in arm_runs for k, v in r["recall"].items()]
        seen = [(r["qid"], k, r["shown"][k]) for r in arm_runs for k in r["recall"]]
        shown_map = {(q, k): s for q, k, s in seen}
        cond = [(v if shown_map[(q, k)] else None) for q, k, v in flat]
        shown_vals = [v for v in cond if v is not None]
        hidden_vals = [v for v in cond if v is None]
        summary[str(arm)] = {
            "recall": round(statistics.fmean(1.0 if v else 0.0 for _, _, v in flat), 4),
            "recall_per_replicate": per_rep,
            "n_fact_observations": len(flat),
            "recall_given_shown": (
                round(statistics.fmean(1.0 if v else 0.0 for v in shown_vals), 4)
                if shown_vals else None
            ),
            "n_shown": len(shown_vals),
            "recall_given_hidden": (
                round(statistics.fmean(1.0 if v else 0.0 for v in hidden_vals), 4)
                if hidden_vals else None
            ),
            "n_hidden": len(hidden_vals),
            "abstention": round(
                statistics.fmean(1.0 if r["abstained"] else 0.0 for r in arm_runs), 4
            ),
            "mean_frames": round(statistics.fmean(r["frames"] for r in arm_runs), 2),
            "mean_walk_frames": round(statistics.fmean(r["walk_frames"] for r in arm_runs), 2),
            "target_present_rate": round(
                statistics.fmean(1.0 if r["target_present"] else 0.0 for r in arm_runs), 4
            ),
            "target_via_walk_rate": round(
                statistics.fmean(
                    1.0 if str(r["target_source"]).startswith("graph_hop") else 0.0
                    for r in arm_runs
                ),
                4,
            ),
            "generation_ms_p50": round(statistics.median(r["generation_ms"] for r in arm_runs), 1),
        }

    payload = {
        "experiment": "walk_value",
        "db_path": DB_PATH,
        "user_id": USER_ID,
        "arms": ARMS,
        "replicates": REPLICATES,
        "seed": SEED,
        "temperature": TEMPERATURE,
        "think": THINK,
        "n_queries": len(CASES),
        "memory_char_budget": MEMORY_CHAR_BUDGET,
        "retrieval": {
            str(arm): {q: {k: v for k, v in pools[arm][q].items() if k != "context"}
                       for q in pools[arm]}
            for arm in ARMS
        },
        "summary": summary,
        "db_digest_before": before,
        "db_digest_after": after,
        "db_mutated": mutated,
    }
    out = OUT_DIR / "result.json"
    out.write_text(json.dumps(payload, indent=2))
    runs_out = OUT_DIR / "runs.json"
    runs_out.write_text(json.dumps(runs, indent=2))
    print(f"\nwrote {out}\nwrote {runs_out}")
    print("db mutated:", mutated or "NO")
    for arm in ARMS:
        print(f"  arm={arm}: {json.dumps(summary[str(arm)])}")

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
