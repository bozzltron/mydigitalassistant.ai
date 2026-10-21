"""Temporal validity: does dating memory stop stale facts being presented as current?

Pre-registered in `plan.md`; this harness is the measurement, not the conclusion.
Two arms, because the question has two halves:

- **Routing** — does the router's cue list make a period-scoped question ("image of
  the day") want search? Calls the LLM classifier directly, so no search happens
  and no network is used beyond Ollama.
- **Attribution** — given the same memory and no search at all, does showing each
  fact's observation date (and a time-validity rule) change whether the model
  repeats a 45-day-old value as if it were current?

Scope limits are in the plan; the important one here is that Arm 2 measures
*presentation*. Retrieval is held constant by constructing the memory context
once and rendering it three ways.

Run:
    python -m assistant.experiments.temporal_validity
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from assistant.backend.config import settings
from assistant.backend.memory.models import Frame, Slot
from assistant.backend.memory.retrieval import (
    MemoryContext,
    RetrievedFrame,
    format_memory_context,
)
from assistant.backend.pipeline import task_router
from assistant.backend.pipeline.llm_client import (
    ChatMessage,
    OllamaClient,
    build_system_prompt,
)
from assistant.backend.pipeline.task_router import classify_with_llm

HERE = Path(__file__).parent
OUT = HERE / "result.json"

# Fixed clock so the run is reproducible: the facts were observed 45 days before
# "today". A relative date would make yesterday's result file wrong tomorrow.
TODAY = "2026-10-20"
NOW_LINE = f"{TODAY} 08:00 (Tuesday)"
STALE_DATE = "2026-09-05"
STALE_AGE_DAYS = 45

REPLICATES = 3

# ---------------------------------------------------------------------------
# Arm 1 — routing
# ---------------------------------------------------------------------------

PERIOD_QUERIES = [
    "What's NASA's image of the day?",
    "What's the price of bitcoin right now?",
    "What's the weather today?",
    "What's on my calendar this afternoon?",
    "What's the latest version of Python?",
    "Any news from the music industry this morning?",
    "What's the current stock price of NVIDIA?",
    "Who won the game tonight?",
    "What's the release date for the next iPhone?",
    "What's trending on YouTube today?",
]

STABLE_QUERIES = [
    "How many strings does a standard guitar have?",
    "What's my name?",
    "What did I tell you about my dog?",
    "Explain how a rainbow forms.",
    "What's the capital of France?",
    "What files do I have?",
    "Remember that I prefer tea over coffee.",
    "How do I convert Celsius to Fahrenheit?",
    "What do you know about my sister?",
    "Define photosynthesis.",
]

# The candidate cue paragraph. Production keeps the baseline line (the experiment
# measured no effect, so the change was reverted); the treatment is derived from
# the production prompt by swapping this one paragraph, which is why the two
# prompts cannot drift apart without the assert firing.
BASELINE_CUES = "- any freshness cue: latest, current, recent, new, now, this year."
EXTENDED_CUES = """- any freshness cue: latest, current, recent, new, now, this year,
  today, tonight, this morning/afternoon/evening, this week/month, of the day,
  daily, right now, as of. A phrase like "image of the day" is period-scoped even
  though it contains no "latest": the answer changes with the date."""


def extended_router_prompt() -> str:
    prompt = task_router.ROUTER_SYSTEM_PROMPT
    if BASELINE_CUES not in prompt:
        raise SystemExit(
            "the production router prompt no longer contains the baseline cue "
            "line; update BASELINE_CUES so the comparison stays exact"
        )
    return prompt.replace(BASELINE_CUES, EXTENDED_CUES)


async def arm_routing(client: OllamaClient) -> list[dict]:
    """`wants_search` per query under each cue list. No search, no DB."""
    production = task_router.ROUTER_SYSTEM_PROMPT
    extended = extended_router_prompt()
    rows: list[dict] = []
    try:
        for name, prompt in (("baseline", production), ("extended", extended)):
            task_router.ROUTER_SYSTEM_PROMPT = prompt
            for group, queries in (("period", PERIOD_QUERIES), ("stable", STABLE_QUERIES)):
                for query in queries:
                    result = await classify_with_llm(query, client)
                    rows.append(
                        {
                            "arm": "routing",
                            "condition": name,
                            "group": group,
                            "query": query,
                            "wants_search": bool(result.wants_search),
                            "search_query": result.search_query,
                            "task_type": result.task_type.value,
                        }
                    )
                    print(f"  routing [{name}/{group}] {query[:44]:<44} -> {result.wants_search}")
    finally:
        task_router.ROUTER_SYSTEM_PROMPT = production
    return rows


# ---------------------------------------------------------------------------
# Arm 2 — attribution
# ---------------------------------------------------------------------------

SEEDED = [
    ("nasa_apod", "concept", "image_of_the_day", "Apollo 11 spacewalk photograph"),
    ("acme_widget", "concept", "latest_version", "9.7.3"),
    ("acme_widget", "concept", "price_usd", "412.00"),
    ("today_schedule", "event", "afternoon", "dentist at 3:30"),
]

ATTRIBUTION_QUERIES = [
    "What's NASA's image of the day?",
    "What's the latest version of Acme Widget?",
    "What does the Acme Widget Pro cost right now?",
    "What's on my schedule this afternoon?",
]

# One distinctive token per question, not the whole stored phrase.
#
# Pre-registered grading used exact substring of the seeded value ("Apollo 11
# spacewalk photograph"). The first run showed that undercounts: the model
# paraphrased to "a photograph of an Apollo 11 spacewalk", which is the same
# stale claim and scored as absent. Grading was corrected to the distinctive
# token the claim cannot avoid, and every answer is re-graded from the stored
# text — no model call is repeated. Disclosed in verification.md.
STALE_MARKERS = {
    "What's NASA's image of the day?": ["apollo 11"],
    "What's the latest version of Acme Widget?": ["9.7.3"],
    "What does the Acme Widget Pro cost right now?": ["412"],
    "What's on my schedule this afternoon?": ["dentist"],
}

DATE_PATTERNS = [
    re.compile(r"\b20\d{2}-\d{2}-\d{2}\b"),          # 2026-09-05
    re.compile(r"\b(19|20)\d{2}\b"),                  # a year
    re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
        re.IGNORECASE,
    ),                                                # Sep 5 / September 5
]


def build_memory() -> MemoryContext:
    """The same context in every condition: one frame per seeded fact."""
    frames: list[RetrievedFrame] = []
    for index, (name, kind, key, value) in enumerate(SEEDED, start=1):
        frame = Frame(id=index, name=name, type=kind, confidence=0.8)
        slot = Slot(
            frame_id=index,
            key=key,
            value=value,
            confidence=0.8,
            source_type="web_search",
            source_url="https://example.invalid/source",
            updated_at=f"{STALE_DATE} 09:12:00",
        )
        frames.append(
            RetrievedFrame(
                frame=frame, slots=[slot], associations=[], relevance=0.88,
                source="direct_match",
            )
        )
    return MemoryContext(
        query="temporal validity probe",
        retrieved_frames=frames,
        recent_episodes=[],
        formatted="",
        frame_names={},
    )


def grade(answer: str, query: str) -> dict:
    """Deterministic: presence of the stale claim, and presence of any date."""
    lowered = answer.lower()
    repeats = [m for m in STALE_MARKERS.get(query, []) if m.lower() in lowered]
    cites_date = any(p.search(answer) for p in DATE_PATTERNS)
    return {
        "repeats_stale_value": bool(repeats),
        "repeated_values": repeats,
        "cites_date": bool(cites_date),
        # The pre-registered failure: the old value is given with no date.
        "presents_stale_as_current": bool(repeats) and not cites_date,
    }


CONDITIONS = {
    # name: (show_dates, include_time_rule)
    "A_plain": (False, False),
    "B_dates": (True, False),
    "C_dates_and_rule": (True, True),
}


async def arm_attribution(client: OllamaClient) -> tuple[list[dict], dict]:
    memory = build_memory()
    rendered = {c: format_memory_context(memory, show_dates=d) for c, (d, _) in CONDITIONS.items()}
    rows: list[dict] = []

    # Manipulation validity: A and B must differ only by the date annotation.
    assert "as of" not in rendered["A_plain"], "baseline rendering contains a date"
    assert "as of" in rendered["B_dates"], "dated rendering has no date"
    assert rendered["B_dates"] != rendered["A_plain"]

    for replicate in range(1, REPLICATES + 1):
        # Rotate condition order per replicate so a warm/cold model cannot load
        # onto one condition.
        order = list(CONDITIONS)
        shift = (replicate - 1) % len(order)
        order = order[shift:] + order[:shift]
        for query in ATTRIBUTION_QUERIES:
            for condition in order:
                _, time_rule = CONDITIONS[condition]
                system_prompt = build_system_prompt(
                    rendered[condition],
                    task_type="functional",
                    current_datetime=NOW_LINE,
                    include_time_rule=time_rule,
                )
                response = await client.chat(
                    [
                        ChatMessage(role="system", content=system_prompt),
                        ChatMessage(role="user", content=query),
                    ],
                    model=client.chat_model,
                    temperature=0.7,
                    think=False,
                )
                answer = (response.content or "").strip()
                rows.append(
                    {
                        "arm": "attribution",
                        "condition": condition,
                        "replicate": replicate,
                        "query": query,
                        **grade(answer, query),
                        "answer": answer,
                    }
                )
                print(
                    f"  attribution [{condition} r{replicate}] {query[:40]:<40} "
                    f"stale={rows[-1]['repeats_stale_value']} "
                    f"date={rows[-1]['cites_date']}"
                )

    cost = {
        "memory_chars_plain": len(rendered["A_plain"]),
        "memory_chars_dated": len(rendered["B_dates"]),
        "memory_char_delta": len(rendered["B_dates"]) - len(rendered["A_plain"]),
        "system_prompt_chars_C": len(
            build_system_prompt(
                rendered["C_dates_and_rule"],
                task_type="functional",
                current_datetime=NOW_LINE,
                include_time_rule=True,
            )
        ),
        "system_prompt_chars_A": len(
            build_system_prompt(
                rendered["A_plain"],
                task_type="functional",
                current_datetime=NOW_LINE,
                include_time_rule=False,
            )
        ),
    }
    return rows, cost


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def rate(rows: list[dict], **match) -> float:
    subset = [r for r in rows if all(r.get(k) == v for k, v in match.items())]
    return round(sum(bool(r["wants_search"]) for r in subset) / len(subset), 3) if subset else 0.0


def share(rows: list[dict], field: str, **match) -> float:
    subset = [r for r in rows if all(r.get(k) == v for k, v in match.items())]
    return round(sum(bool(r[field]) for r in subset) / len(subset), 3) if subset else 0.0


def summarize(routing: list[dict], attribution: list[dict], cost: dict) -> dict:
    return {
        "routing": {
            "period_search_rate": {
                c: rate(routing, condition=c, group="period")
                for c in ("baseline", "extended")
            },
            "stable_search_rate": {
                c: rate(routing, condition=c, group="stable")
                for c in ("baseline", "extended")
            },
        },
        "attribution": {
            "presents_stale_as_current": {
                c: share(attribution, "presents_stale_as_current", condition=c)
                for c in CONDITIONS
            },
            "cites_date": {
                c: share(attribution, "cites_date", condition=c) for c in CONDITIONS
            },
            "repeats_stale_value": {
                c: share(attribution, "repeats_stale_value", condition=c)
                for c in CONDITIONS
            },
        },
        "cost": cost,
    }


def regrade() -> int:
    """Re-apply `grade` to the answers already stored. No model call is made.

    Exists because the first grading rule (exact substring of the seeded phrase)
    missed paraphrases; see the note on `STALE_MARKERS`. Re-grading the stored
    text is the same measurement with a corrected instrument, and no new data.
    """
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    rows = payload["rows"]
    for row in rows:
        if row["arm"] == "attribution":
            row.update(grade(row["answer"], row["query"]))
    payload["summary"] = summarize(
        [r for r in rows if r["arm"] == "routing"],
        [r for r in rows if r["arm"] == "attribution"],
        payload["summary"]["cost"],
    )
    payload["regraded"] = datetime.now(UTC).isoformat()
    payload["grading"] = "distinctive-token"
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    return 0


async def main() -> int:
    started = datetime.now(UTC).isoformat()
    client = OllamaClient(
        base_url=settings.ollama_url,
        chat_model=settings.chat_model,
        utility_model=settings.utility_model,
        embedding_model=settings.embedding_model,
    )
    print(f"chat={client.chat_model} utility={client.utility_model}")

    print("Arm 1 — routing")
    routing = await arm_routing(client)
    print("Arm 2 — attribution")
    attribution, cost = await arm_attribution(client)

    summary = summarize(routing, attribution, cost)

    payload = {
        "started": started,
        "finished": datetime.now(UTC).isoformat(),
        "clock": {"today": TODAY, "stale_date": STALE_DATE, "age_days": STALE_AGE_DAYS},
        "replicates": REPLICATES,
        "models": {"chat": client.chat_model, "utility": client.utility_model},
        "grading": "distinctive-token",
        "summary": summary,
        "rows": routing + attribution,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT}")
    await client.close()
    return 0


if __name__ == "__main__":
    import sys

    if "--regrade" in sys.argv:
        raise SystemExit(regrade())
    raise SystemExit(asyncio.run(main()))
