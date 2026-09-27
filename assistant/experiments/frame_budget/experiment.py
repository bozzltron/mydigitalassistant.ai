"""Frame budget dose-response experiment.

Varies only how many retrieved frames reach the system prompt, and measures
whether the agent can answer questions about the user's own stored knowledge,
and what that costs. See plan.md for the design and the falsification criteria.

The candidate pool is retrieved ONCE per query at top_k_direct=40 and then
truncated to each budget, so every condition sees the same ranked frames and
retrieval variance is removed from the comparison. Because the graph walk
contributes zero frames (measured in probe_results.md), truncating the pool is
exactly equivalent to running the production path at top_k_direct=budget.

Read-only with respect to the database: the digest of every table is captured
before and after the run and written to result.json so non-mutation is
verifiable rather than asserted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.db.schema import _load_sqlite_vec, init_db
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.retrieval import (
    MemoryContext,
    Retriever,
    format_memory_context,
    frame_to_text,
)
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.llm_client import ChatMessage, OllamaClient, build_system_prompt

DB_PATH = "/exp/assistant.db"
OUT_DIR = Path(__file__).parent

# Env overrides exist so the harness can be smoke-tested cheaply and so a future
# re-run can widen the sweep without editing code. A smoke run never produces a
# result.json worth citing: REPLICATES is part of the recorded provenance.
BUDGETS = [int(b) for b in os.environ.get("FRAME_BUDGET_BUDGETS", "0,1,2,3,5,10,20,40").split(",")]
REPLICATES = int(os.environ.get("FRAME_BUDGET_REPLICATES", "3"))
POOL_SIZE = int(os.environ.get("FRAME_BUDGET_POOL", "40"))
SCREEN_REPS = int(os.environ.get("FRAME_BUDGET_SCREEN_REPS", "2"))
SEED = 20260927


@dataclass
class GoldFact:
    """A fact the agent should be able to answer only from memory.

    ``forms`` are accepted surface forms. Grading is exact normalized substring
    matching, so the grader is blind to condition and cannot be satisfied by a
    longer or more fluent answer.
    """

    key: str
    description: str
    forms: list[str]


@dataclass
class Query:
    qid: str
    text: str
    target_frame_id: int
    gold: list[GoldFact]


QUERIES: list[Query] = [
    Query(
        "label_address", "What's the mailing address of the record label?",
        388, [
            GoldFact("brodie_lane", "label street address", ["9901 Brodie", "Brodie Lane"]),
            GoldFact("zip", "label zip code", ["78748"]),
            GoldFact("suite", "label suite number", ["160-302", "160302"]),
        ],
    ),
    Query(
        "cbs_hq", "Where is CBS News headquartered?", 941, [
            GoldFact("postal", "CBS postal code", ["10019"]),
            GoldFact("street", "CBS street address", ["57th St", "57th Street"]),
            GoldFact("director", "CBS news director", ["Alex Suskind"]),
            GoldFact("reporter", "CBS reporter name", ["Shanelle Kaul"]),
        ],
    ),
    Query(
        "perseverance", "What did the Perseverance rover accomplish, and when?",
        2262, [
            GoldFact("achievement", "rover achievement", ["AI-planned drive", "AI planned drive"]),
            GoldFact("when", "rover achievement date",
                     ["Jan. 31, 2026", "January 31, 2026", "Jan 31 2026"]),
        ],
    ),
    Query(
        "penn_state", "What is Penn State's research group working on?", 2261, [
            GoldFact("material", "the material they made", ["hydrogel"]),
            GoldFact("inspiration", "what inspired it", ["octopus"]),
            GoldFact("property", "the property of the material",
                     ["shape-shifting", "shape shifting"]),
        ],
    ),
    Query(
        "mtw_members", "Who are the members of The Mountain & The Wolf?", 276, [
            GoldFact("heaps", "band member", ["Mark Heaps"]),
            GoldFact("schultz", "band member", ["Jack Schultz"]),
            GoldFact("hall", "band member", ["Mike Hall"]),
        ],
    ),
    Query(
        "mtw_genre", "What genre is The Mountain & The Wolf and when does it come out?", 276, [
            GoldFact("genre", "the genre", ["indie alternative rock", "indie alternative"]),
            GoldFact("release", "joint release date", ["January 2027", "Jan 2027", "january_2027"]),
            GoldFact("formed", "when the band formed", ["late 2024"]),
            GoldFact("origin", "whose album it is", ["user's wife", "my wife", "wife"]),
        ],
    ),
    Query(
        "groover", "What is Groover and how big is it?", 869, [
            GoldFact("curators", "number of active curators",
                     ["1,500,000", "1.5 million", "1500000"]),
            GoldFact("professionals", "number of professional curators", ["4,000", "4000"]),
            GoldFact("countries", "where curators are from", ["France"]),
            GoldFact("kind", "what kind of platform",
                     ["music submission platform", "submission platform"]),
        ],
    ),
    Query(
        "encorespot", "When is the Encorespot application deadline?", 1155, [
            GoldFact("deadline", "the deadline", ["August 31", "Aug 31"]),
            GoldFact("time", "the time of day", ["midnight"]),
        ],
    ),
    Query(
        "walking_cow", "Tell me about the Walking the Cow single.", 493, [
            GoldFact("artist", "the artist referenced", ["Daniel Johnston"]),
            GoldFact("date", "release date",
                     ["January 22, 2025", "Jan 22 2025", "January 22 2025"]),
        ],
    ),
    Query(
        "jason_lee", "What is Jason Lee's skateboarding stance and when did he retire?", 1409, [
            GoldFact("stance", "skateboarding stance", ["Goofy"]),
            GoldFact("retired", "retirement year", ["1995"]),
            GoldFact("hall", "hall of fame induction", ["Skateboarding Hall of Fame", "2019"]),
        ],
    ),
    Query(
        "festivaltopia", "What is Festivaltopia?", 1138, [
            GoldFact("tagline", "its tagline", ["Go-To Resource", "Go To Resource"]),
            GoldFact("url", "its url", ["festivaltopia.com"]),
        ],
    ),
    Query(
        "why_not_label", "Which label is Why Not on, and when is the single out?", 274, [
            GoldFact("label", "the label", ["Friend Music Records"]),
            GoldFact("date", "lead single release date", ["sept 24", "september 24", "Sept. 24"]),
        ],
    ),
    Query(
        "studio", "Where is my home studio?", 91, [
            GoldFact("city", "the city", ["Austin"]),
        ],
    ),
    Query(
        "groover_platforms", "Which streaming platforms does Groover submit to?", 869, [
            GoldFact("spotify", "a platform", ["Spotify"]),
            GoldFact("apple", "a platform", ["Apple Music"]),
            GoldFact("amazon", "a platform", ["Amazon Music"]),
        ],
    ),
]

ABSTENTION_MARKERS = (
    "i don't have", "i do not have", "i don't see", "i do not see",
    "not in my memory", "no record of", "i don't recall", "i do not recall",
    "nothing in my memory", "no information about", "i'm not sure",
    "i am not sure", "cannot find", "can't find", "not something i",
    "i don't know", "i do not know", "no mention of", "haven't stored",
    "have not stored", "i'm unable to find", "no data on",
)


def norm(text: str) -> str:
    """Normalize for word-boundary substring matching."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def gold_hits(fact: GoldFact, answer: str) -> bool:
    haystack = f" {norm(answer)} "
    return any(f" {norm(form)} " in haystack for form in fact.forms)


def is_abstention(answer: str) -> bool:
    low = answer.lower()
    return any(marker in low for marker in ABSTENTION_MARKERS)


@contextlib.contextmanager
def frame_cap(n: int):
    """Temporarily set the render cap so format_memory_context cannot re-truncate."""
    prev = settings.max_frames_in_prompt
    settings.max_frames_in_prompt = n
    try:
        yield
    finally:
        settings.max_frames_in_prompt = prev


async def digest_db() -> dict:
    async with aiosqlite_connect(DB_PATH) as db:
        await _load_sqlite_vec(db)
        out = {}
        for t in ("frames", "slots", "associations", "episodes", "slot_history", "conflicts"):
            out[t] = (await db.execute_fetchall(f"SELECT COUNT(*) FROM {t}"))[0][0]
    return out


class Harness:
    def __init__(self) -> None:
        self.store = MemoryStore(DB_PATH)
        self.llm = OllamaClient(
            base_url=settings.ollama_url,
            embedding_model=settings.embedding_model,
            timeout=settings.ollama_timeout,
        )
        # One retrieval per query, at full pool size. Every condition is a
        # truncation of this single ranked list.
        self.retriever = Retriever(
            store=self.store, llm_client=self.llm,
            embedding_model=settings.embedding_model, top_k_direct=POOL_SIZE,
        )
        self.pools: dict[str, MemoryContext] = {}

    async def build_pools(self) -> None:
        for q in QUERIES:
            self.pools[q.qid] = await self.retriever.retrieve(q.text, user_id=1)

    def target_rank(self, q: Query) -> int | None:
        pool = self.pools[q.qid].retrieved_frames
        for i, rf in enumerate(pool):
            if rf.frame.id == q.target_frame_id:
                return i
        return None

    def first_rank_with_fact(self, q: Query, fact: GoldFact) -> int | None:
        """Earliest rank in the pool at which this fact's text is present.

        Gold facts are not unique to their target frame: the label on `why_not`
        is also a slot on `friend_music_records`, so the nominal target rank is
        the wrong x-axis. This returns the first rank whose frame text actually
        carries the fact, which is the budget at which the model can first see it.
        """
        for i, rf in enumerate(self.pools[q.qid].retrieved_frames):
            text = norm(frame_to_text(rf.frame, rf.slots))
            if any(f" {norm(form)} " in f" {text} " for form in fact.forms):
                return i
        return None

    def shown_facts(self, q: Query, budget: int, memory: str, sys_prompt: str,
                    pre_trunc_chars: int) -> dict[str, bool]:
        """Which gold facts actually reached the model.

        Must be measured against the text that was SENT, not the text that was
        rendered. build_system_prompt puts memory last, and the orchestrator
        truncates the system prompt at max_system_prompt_chars, so at high
        budgets the tail of the memory section is discarded before the model
        ever sees it. Crediting the model with facts from the discarded tail
        would overstate recall and corrupt the headline number.
        """
        if budget == 0:
            return {f.key: False for f in q.gold}
        marker = "You have the following relevant memory state:"
        start = sys_prompt.find(marker)
        if start < 0:
            return {f.key: False for f in q.gold}
        # Chars of the memory section that survived truncation.
        survived = min(len(memory), max(0, settings.max_system_prompt_chars - start))
        if survived <= len(marker):
            return {f.key: False for f in q.gold}
        delivered = norm(memory[:survived])
        padded = f" {delivered} "
        return {
            f.key: any(f" {norm(form)} " in padded for form in f.forms)
            for f in q.gold
        }

    def render(self, q: Query, budget: int) -> tuple[str, str, int]:
        """Return (memory_text, system_prompt, frames_actually_rendered)."""
        pool = self.pools[q.qid]
        if budget == 0:
            memory, rendered = "", 0
        else:
            trimmed = MemoryContext(
                query=pool.query,
                retrieved_frames=pool.retrieved_frames[:budget],
                recent_episodes=pool.recent_episodes,
                formatted="",
                past_conversations=pool.past_conversations,
            )
            with frame_cap(budget):
                memory = format_memory_context(trimmed)
            rendered = len(pool.retrieved_frames[:budget])
        sys_prompt = build_system_prompt(memory_context=memory, task_type="functional")
        # Mirror the orchestrator's own ceiling so its effect is measured, not hidden.
        truncated = len(sys_prompt) > settings.max_system_prompt_chars
        if truncated:
            sys_prompt = sys_prompt[: settings.max_system_prompt_chars] + "\n\n[... truncated ...]"
        return memory, sys_prompt, rendered

    async def generate(self, q: Query, budget: int) -> dict:
        memory, sys_prompt, rendered = self.render(q, budget)
        pre_trunc_chars = len(build_system_prompt(
            memory_context=memory, task_type="functional"))
        t0 = time.perf_counter()
        resp = await self.llm.chat(
            [ChatMessage(role="system", content=sys_prompt),
             ChatMessage(role="user", content=q.text)],
            temperature=0.7,          # production default
            think=False,              # chat_think_default
        )
        latency = (time.perf_counter() - t0) * 1000
        return {
            "qid": q.qid, "budget": budget,
            "answer": resp.content,
            "latency_ms": latency,
            "frames_requested": budget,
            "frames_rendered": rendered,
            "memory_chars": len(memory),
            "sys_prompt_chars": len(sys_prompt),
            "sys_prompt_chars_pre_truncation": pre_trunc_chars,
            "truncated": pre_trunc_chars > settings.max_system_prompt_chars,
            "hits": {f.key: gold_hits(f, resp.content) for f in q.gold},
            "shown": self.shown_facts(q, budget, memory, sys_prompt, pre_trunc_chars),
            "abstained": is_abstention(resp.content),
        }


def score(runs: list[dict], queries: dict[str, Query], keys: set[str]) -> dict:
    """Aggregate gold recall, abstention, and cost for one condition."""
    per_q: dict[str, list[bool]] = {}
    abst, lat, mem, trunc, rendered = [], [], [], [], []
    shown_hits: list[bool] = []
    hidden_hits: list[bool] = []
    for r in runs:
        keep = {k: v for k, v in r["hits"].items() if k in keys}
        if keep:
            vals = list(keep.values())
            per_q.setdefault(r["qid"], []).append(all(vals))
        for k in keep:
            (shown_hits if r["shown"].get(k) else hidden_hits).append(keep[k])
        abst.append(r["abstained"])
        lat.append(r["latency_ms"])
        mem.append(r["memory_chars"])
        trunc.append(r["truncated"])
        rendered.append(r["frames_rendered"])
    flat = [v for vs in per_q.values() for v in vs]
    return {
        "gold_recall": round(sum(flat) / len(flat), 4) if flat else None,
        "n_fact_trials": len(flat),
        # The causal quantity: does the model use a fact once it is on screen?
        "recall_when_shown": round(sum(shown_hits) / len(shown_hits), 4) if shown_hits else None,
        "recall_when_hidden": (
            round(sum(hidden_hits) / len(hidden_hits), 4) if hidden_hits else None),
        "n_shown": len(shown_hits),
        "n_hidden": len(hidden_hits),
        "abstention_rate": round(sum(abst) / len(abst), 4),
        "latency_p50_ms": round(statistics.median(lat), 1),
        "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 1),
        "memory_chars": round(statistics.median(mem), 1),
        "sys_prompt_chars": round(statistics.median(
            [r["sys_prompt_chars"] for r in runs]), 1),
        "sys_prompt_chars_max": max(r["sys_prompt_chars"] for r in runs),
        "frames_rendered": round(statistics.median(rendered), 2),
        "truncated_frac": round(sum(trunc) / len(trunc), 4),
        "manipulation_ok": all(r["frames_rendered"] == r["frames_requested"] for r in runs),
    }


async def main() -> None:
    print("=" * 78)
    print("FRAME BUDGET DOSE-RESPONSE EXPERIMENT")
    print("=" * 78)
    await init_db(DB_PATH)
    before = await digest_db()
    print(f"db digest before: {before}")

    h = Harness()
    print(f"\nretrieving {POOL_SIZE}-frame pool once per query "
          f"({len(QUERIES)} queries, one retrieval each)...")
    await h.build_pools()

    ranks = {}
    print(f"\n{'query':<18} {'frames':>6} {'target':>7} {'rank':>5}  gold facts")
    print("-" * 78)
    for q in QUERIES:
        pool = h.pools[q.qid].retrieved_frames
        rank = h.target_rank(q)
        ranks[q.qid] = rank
        flag = "" if rank is not None else "  <-- TARGET NOT IN POOL"
        print(f"{q.qid:<18} {len(pool):>6} {q.target_frame_id:>7} "
              f"{str(rank):>5}  {len(q.gold)}{flag}")
    missing = [q.qid for q in QUERIES if ranks[q.qid] is None]
    if missing:
        print(f"\nWARNING: target frame absent from pool for: {missing}")

    # ---- Stage A: screen out facts the model already knows -------------------
    print("\n" + "=" * 78)
    print("STAGE A — screening: a fact is MEMORY-DEPENDENT only if it is missed at")
    print("budget 0 and found at budget 40. Facts answered at budget 0 came from the")
    print("model's pretraining, not from memory, and would inflate every condition.")
    print("=" * 78)
    screen = {}
    screen_detail = {}
    for q in QUERIES:
        # Screen on more than one sample. A single draw is not enough: a fact the
        # model happens to produce at budget 0 by luck would be wrongly retained as
        # memory-dependent, and one it fails to produce once at budget 40 would be
        # wrongly discarded. Require MISSED at every budget-0 sample and FOUND at
        # least one budget-max sample.
        zero_runs = [await h.generate(q, 0) for _ in range(SCREEN_REPS)]
        max_runs = [await h.generate(q, POOL_SIZE) for _ in range(SCREEN_REPS)]
        keep, detail = set(), {}
        for f in q.gold:
            n0 = sum(gold_hits(f, r["answer"]) for r in zero_runs)
            n_max = sum(gold_hits(f, r["answer"]) for r in max_runs)
            detail[f.key] = {"hit_at_0": n0, "hit_at_max": n_max, "kept": n0 == 0 and n_max > 0}
            if n0 == 0 and n_max > 0:
                keep.add(f.key)
        screen[q.qid] = keep
        screen_detail[q.qid] = detail
        dropped = [k for k, v in detail.items()
                   if not v["kept"] and v["hit_at_0"] > 0]
        lost = [k for k, v in detail.items() if not v["kept"] and v["hit_at_0"] == 0]
        note = ""
        if dropped:
            note += f"   dropped(known at b=0): {dropped}"
        if lost:
            note += f"   dropped(never found even at b={POOL_SIZE}): {lost}"
        print(f"  {q.qid:<18} kept {len(keep)}/{len(q.gold)}{note}")

    live_queries = [q for q in QUERIES if screen[q.qid]]
    all_keys = set().union(*(screen[q.qid] for q in live_queries)) if live_queries else set()
    print(f"\nretained memory-dependent facts: {len(all_keys)} across "
          f"{len(live_queries)}/{len(QUERIES)} queries")
    if not all_keys:
        print("no memory-dependent facts survived; nothing to measure")
        return

    # ---- Stage B: dose-response ---------------------------------------------
    total = len(live_queries) * len(BUDGETS) * REPLICATES
    print("\n" + "=" * 78)
    print(f"STAGE B — dose-response: {len(live_queries)} queries x {len(BUDGETS)} "
          f"budgets x {REPLICATES} reps = {total} generations")
    print("=" * 78)
    runs: list[dict] = []
    rng = random.Random(SEED)
    done = 0
    t_start = time.perf_counter()
    for rep in range(REPLICATES):
        order = list(BUDGETS)
        rng.shuffle(order)          # rotate order per rep so drift cannot load
        for budget in order:        # onto one condition
            for q in live_queries:
                r = await h.generate(q, budget)
                r["rep"] = rep
                runs.append(r)
                done += 1
                if done % 10 == 0 or done == total:
                    rate = (time.perf_counter() - t_start) / done
                    print(f"  {done:>4}/{total}  "
                          f"~{rate * (total - done) / 60:.1f} min left", flush=True)

    after = await digest_db()
    print(f"\ndb digest after:  {after}")
    print(f"db unchanged    : {before == after}")

    by_budget: dict[int, list[dict]] = {}
    for r in runs:
        by_budget.setdefault(r["budget"], []).append(r)

    print(f"\n{'budget':>7} {'recall':>8} {'r|shown':>8} {'r|hidden':>9} {'abstain':>8} "
          f"{'frames':>7} {'mem chars':>10} {'sys chars':>10} {'trunc':>6} {'p50 ms':>8}")
    print("-" * 100)
    table = {}
    for b in BUDGETS:
        s = score(by_budget.get(b, []), {q.qid: q for q in live_queries}, all_keys)
        table[b] = s
        rs = "  -  " if s["recall_when_shown"] is None else f"{s['recall_when_shown']:.3f}"
        rh = "  -  " if s["recall_when_hidden"] is None else f"{s['recall_when_hidden']:.3f}"
        print(f"{b:>7} {s['gold_recall']:>8.3f} {rs:>8} {rh:>9} "
              f"{s['abstention_rate']:>8.3f} "
              f"{s['frames_rendered']:>7.1f} {s['memory_chars']:>10.0f} "
              f"{s['sys_prompt_chars']:>10.0f} {s['truncated_frac']:>6.2f} "
              f"{s['latency_p50_ms']:>8.0f}")

    # Per-query dose-response, keyed on the first rank at which each fact is
    # actually visible. The nominal target frame rank is reported too, but it is
    # not the x-axis: gold facts are duplicated across frames.
    print("\nper-query recall by budget")
    print("  'first' = lowest budget at which a retained fact became visible")
    hdr = "".join(f"{b:>6}" for b in BUDGETS)
    print(f"{'query':<18} {'tgt':>4} {'1st':>4}{hdr}")
    print("-" * (28 + 6 * len(BUDGETS)))
    per_query = {}
    for q in live_queries:
        firsts = {k: h.first_rank_with_fact(q, f) for k, f in
                  ((f.key, f) for f in q.gold) if k in screen[q.qid]}
        vis = [v for v in firsts.values() if v is not None]
        first_budget = min(vis) + 1 if vis else None
        cells, row = [], {}
        for b in BUDGETS:
            rs = [r for r in by_budget.get(b, []) if r["qid"] == q.qid]
            vals = [r["hits"][k] for r in rs for k in screen[q.qid]]
            v = sum(vals) / len(vals) if vals else None
            row[b] = v
            cells.append(f"{v:>6.2f}" if v is not None else f"{'-':>6}")
        per_query[q.qid] = {
            "target_rank": ranks[q.qid],
            "first_visible_budget": first_budget,
            "first_rank_per_fact": firsts,
            "recall": row,
        }
        print(f"{q.qid:<18} {str(ranks[q.qid]):>4} {str(first_budget):>4}" + "".join(cells))

    # The headline: recall conditioned on the fact being on screen or not.
    shown_t = [r["hits"][k] for r in runs
               for k in screen[r["qid"]] if r["shown"].get(k)]
    hidden_t = [r["hits"][k] for r in runs
                for k in screen[r["qid"]] if not r["shown"].get(k)]
    conditional = {
        "recall_when_shown": round(sum(shown_t) / len(shown_t), 4) if shown_t else None,
        "recall_when_hidden": round(sum(hidden_t) / len(hidden_t), 4) if hidden_t else None,
        "n_shown": len(shown_t),
        "n_hidden": len(hidden_t),
    }
    print("\nHEADLINE — recall conditioned on whether the fact was on screen:")
    print(f"  fact WAS shown    : {conditional['recall_when_shown']}  (n={len(shown_t)})")
    print(f"  fact was NOT shown: {conditional['recall_when_hidden']}  (n={len(hidden_t)})")

    payload = {
        "experiment": "frame_budget",
        "run_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "db_path": DB_PATH,
        "pool_size": POOL_SIZE,
        "budgets": BUDGETS,
        "replicates": REPLICATES,
        "screen_reps": SCREEN_REPS,
        "screen_detail": screen_detail,
        "smoke_run": REPLICATES < 3 or BUDGETS != [0, 1, 2, 3, 5, 10, 20, 40],
        "chat_model": settings.chat_model,
        "temperature": 0.7,
        "think": False,
        "max_system_prompt_chars": settings.max_system_prompt_chars,
        "confounds": {
            "latency_is_cache_contaminated": (
                "Every condition is generated REPLICATES times with an identical system "
                "prompt, so Ollama's prompt cache serves all but the first call. In "
                "production the memory section changes every turn (it is last, by "
                "design, so the stable prefix caches but memory never does), so the "
                "marginal prefill cost of more frames is real per turn. Latency here "
                "therefore UNDERSTATES production cost and the ordering across budgets "
                "is not trustworthy. memory_chars is the reliable cost metric; it is "
                "also what max_system_prompt_chars and prefill scale with."
            ),
            "pool_retrieved_once": (
                "The candidate pool is retrieved once at top_k_direct=40 and truncated. "
                "This removes retrieval variance and cost, and because the graph walk "
                "contributes 0 frames this equals running production at "
                "top_k_direct=budget. It does NOT measure retrieval-side effects."
            ),
            "episodes_held_constant": (
                "recent_episodes are included exactly as production includes them and are "
                "identical across conditions. They are a possible alternative source for "
                "an answer; the shown/hidden split is computed against FRAME text only, so "
                "a fact answered from an episode would show as recall_when_hidden > 0. It "
                "is 0.0, so episodes are not leaking these answers."
            ),
        },
        "target_ranks": ranks,
        "screened_facts": {k: sorted(v) for k, v in screen.items()},
        "retained_fact_keys": sorted(all_keys),
        "conditional_recall": conditional,
        "db_digest_before": before,
        "db_digest_after": after,
        "db_unchanged": before == after,
        "by_budget": {str(k): v for k, v in table.items()},
        "per_query": per_query,
        "raw_runs": [{k: v for k, v in r.items() if k != "answer"} for r in runs],
        "answers": {f"{r['qid']}@b{r['budget']}#r{r['rep']}": r["answer"] for r in runs},
    }
    out = OUT_DIR / ("result_smoke.json" if payload["smoke_run"] else "result.json")
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {out}")
    if payload["smoke_run"]:
        print("NOTE: smoke run — provenance is narrowed, this file is not citable")


if __name__ == "__main__":
    asyncio.run(main())
