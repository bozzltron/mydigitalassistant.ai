"""Does a typed decision model route a transform turn better than the chat model?

Measures the tool choice for turns that supply content to transform. Instrumentation
on 2026-09-30 showed the pipeline was correct (`search_ms=0`) while `qwen3.5:9b`
still called `web_search` itself inside the tool loop, so the fault is the choice,
not the plumbing.

See plan.md for the design, the frozen labels, the pre-registered bars, and the
threats to validity.

Isolated by construction: this reads no brain, opens no database, runs no retrieval,
and executes no tool. Condition A asks the model what it would call and does not
call it, so no search is ever issued and no query text leaves the machine.

Usage:
    python -m assistant.experiments.decision_routing_value.experiment
Env:
    OLLAMA_URL   default http://127.0.0.1:11434
    DRV_MODELS   comma list for the decision conditions (default tev1:0.8b,tev1,nimble)
    DRV_REPEATS  samples per turn per condition (default 1)
    DRV_OUT      result json path (default ./result.json)
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODELS = [m for m in os.environ.get("DRV_MODELS", "tev1:0.8b,tev1:latest,nimble:latest").split(",") if m]
REPEATS = int(os.environ.get("DRV_REPEATS", "1"))
OUT = Path(os.environ.get("DRV_OUT", "result.json"))

CHAT_MODEL = "qwen3.5:9b"

# The three routes the decision must choose between. Named as the plan fixes them.
ROUTES = ("answer_from_content", "search_to_enrich", "search_instead")

# ---------------------------------------------------------------------------
# The labelled set. Labels are authored by the human and frozen in plan.md before
# any model ran. "out_of_scope" marks the negative control: the turn must NOT be
# routed to search.
# ---------------------------------------------------------------------------

URLS_45 = """https://wprb.com/contact/executive-director/
https://wprb.com/contact/music-submissions/
https://docs.google.com/forms/d/e/1FAIpQLSc6n_slllmbuRNsBfInZjC5hwsZa4BTSYP3OjC1xfY_xpNvLA/formResponse
https://www.reddit.com/r/WeAreTheMusicMakers/comments/fsglwm/458_us_college_radio_contacts_their_submission/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
https://help.kcrw.com/hc/en-us/requests/new
https://www.kgpc969.org/music-submission
https://www.kcss.net/music-submissions
https://www.wildcat919.com/contact
https://www.austinchronicle.com/contact/
https://www.blakmarigold.com/playlist-submission
https://austinmusiclove.com/
https://app.discovrradio.com/create-account
https://kxt.org/submissions/
https://cjsw.com/music/submit/
https://cjsw.fillout.com/t/nayVJqR423us
https://wfuv.org/user/121/contact
https://amazingradio.com/connect/signup
https://www.bbc.co.uk/introducing
https://www.austintexas.org/events/festivals/
https://www.revolution.fm/
https://magicnothing.xyz/
https://unheard.online/submit
https://submit.happymag.tv/
https://www.vibe971.com/contact-us/
https://www.wfmu.org/email.php?id=447
https://stereogum.com/contact
https://avenuear.com/2023/12/19/submit-music-to-blogs/
https://bandcamp.com/guide
https://www.wxut.com/music-submission
https://kboo.fm/submit-your-music
https://www.indiemusicdiscovery.com/submit-music/
https://twostorymelody.com/contact/
https://madindiemedia.com/playlist-submissions/"""

URLS_7 = """https://wprb.com/contact/executive-director/
https://wprb.com/contact/music-submissions/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
https://www.kgpc969.org/music-submission"""

URLS_5 = """https://wprb.com/contact/executive-director/
https://wkdu.org/contact
https://atxlibrary.musicat.co/submit
https://www.rockrageradio.com/
https://kboo.fm/submit-your-music"""

URLS_6 = """https://wprb.com/contact/music-submissions/
https://wkdu.org/contact
https://bookingagentinfo.com/indie-music-blogs-contact-list/
https://www.rockrageradio.com/
https://www.kgpc969.org/music-submission
https://kboo.fm/submit-your-music"""


@dataclass
class Turn:
    id: int
    message: str
    label: str  # one of ROUTES, or "out_of_scope"
    note: str = ""


TURNS: list[Turn] = [
    Turn(1, f"Here are all my manual submission links.  Let's search about them if "
            f"we need to and rank them by which would be most impactful to mozworth.\n\n{URLS_45}",
         "answer_from_content", "the real failure"),
    Turn(2, f"Sort these in order of impact.\n\n{URLS_7}", "answer_from_content", ""),
    Turn(3, f"Summarise these links for me.\n\n{URLS_7}", "answer_from_content", ""),
    Turn(4, f"Which of these accept physical submissions?\n\n{URLS_7}",
         "search_to_enrich", "needs page facts to add to the list"),
    Turn(5, "what is the capital of France?", "search_instead", ""),
    Turn(6, "search for austin music festivals", "search_instead", ""),
    Turn(7, f"Rank these, and tell me anything notable about them.\n\n{URLS_5}",
         "search_to_enrich", "tempting: enrich after ranking"),
    Turn(8, "what do you remember about my guitar?", "out_of_scope", "negative control"),
    Turn(9, f"Here are my links:\n\n{URLS_6}\n\nnow sort them", "answer_from_content", ""),
    Turn(10, "how are you today?", "out_of_scope", "negative control"),
]


# ---------------------------------------------------------------------------
# Condition A: what the chat model chooses in the real tool loop
# ---------------------------------------------------------------------------

TOOL_LIST_FOR_A = ["recall", "search_episodes", "web_search", "fetch_url", "think", "finalize"]

A_SYSTEM = """You are a cognitive digital assistant with a structured memory system.

The user may give you content to work on, or ask you a question. Choose exactly ONE
next action.

- "answer_from_content": the user supplied content (a list, a document) and wants it
  ranked, sorted, summarised, or compared. Work from their content directly.
- "search_to_enrich": the user supplied content AND the request needs facts from the
  web to add context. Search adds to the content, it does not replace it.
- "search_instead": the user did not supply content and the request needs web facts.
- "no_search": the request needs no web lookup at all.

Reply with ONLY a JSON object: {"route": "<one of the four>"}"""


async def _condition_a(client: httpx.AsyncClient) -> dict:
    """Ask qwen3.5:9b what it would do. Does not execute any tool."""
    results = []
    for turn in TURNS:
        started = time.perf_counter()
        try:
            r = await client.post(
                "/api/chat",
                json={
                    "model": CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": A_SYSTEM},
                        {"role": "user", "content": turn.message},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.0},
                },
                timeout=120.0,
            )
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
            route = None
            try:
                route = json.loads(content).get("route")
            except (json.JSONDecodeError, AttributeError):
                route = None
            results.append(
                {
                    "turn_id": turn.id,
                    "label": turn.label,
                    "route": route,
                    "raw": content[:200],
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "turn_id": turn.id,
                    "label": turn.label,
                    "route": None,
                    "raw": "",
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "error": str(exc),
                }
            )
    return {"condition": "A_chat_model", "model": CHAT_MODEL, "results": results}


# ---------------------------------------------------------------------------
# Conditions B/C/D: the decision endpoint
# ---------------------------------------------------------------------------

DECISION_STATE_KEY = "conversation"

DECISION_QUESTION = {
    "type": "choice",
    "instructions": (
        "The assistant has a user turn and may use web search. Choose how this turn "
        "should be handled."
    ),
    "criteria": {
        "answer_from_content": (
            "The user supplied content (a list, document, or body of text) and wants "
            "it ranked, sorted, summarised, or compared. Work from their content."
        ),
        "search_to_enrich": (
            "The user supplied content AND the request needs facts from the web to "
            "add context. Search adds to the content, never replaces it."
        ),
        "search_instead": (
            "The user did NOT supply content and the request needs web facts."
        ),
        "no_search": "The request needs no web lookup at all.",
    },
}


async def _condition_decision(client: httpx.AsyncClient, model: str) -> dict:
    results = []
    # Warm the model once so the first measured call is not paying load cost.
    try:
        await client.post(
            "/v1/systemone",
            json={
                "model": model,
                "state": {DECISION_STATE_KEY: "warmup"},
                "questions": {"route": DECISION_QUESTION},
            },
            timeout=300.0,
        )
    except Exception:
        pass

    for turn in TURNS:
        started = time.perf_counter()
        try:
            r = await client.post(
                "/v1/systemone",
                json={
                    "model": model,
                    "state": {DECISION_STATE_KEY: turn.message},
                    "questions": {"route": DECISION_QUESTION},
                },
                timeout=300.0,
            )
            r.raise_for_status()
            body = r.json()
            ans = (body.get("answers") or {}).get("route") or {}
            results.append(
                {
                    "turn_id": turn.id,
                    "label": turn.label,
                    "route": ans.get("choice"),
                    "confidence": ans.get("confidence"),
                    "probabilities": ans.get("probabilities"),
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "usage": body.get("usage"),
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "turn_id": turn.id,
                    "label": turn.label,
                    "route": None,
                    "confidence": None,
                    "probabilities": None,
                    "elapsed_ms": (time.perf_counter() - started) * 1000,
                    "usage": None,
                    "error": str(exc),
                }
            )
    return {"condition": f"decision_{model}", "model": model, "results": results}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score(condition: dict) -> dict:
    """Correct = the route matches the label.

    `out_of_scope` turns are correct when the route is `no_search`; routing them to
    any search-flavoured answer is wrong, which is the negative control.
    """
    correct = 0
    searched = 0
    out_of_scope_wrong = 0
    latencies = []
    confidences = []
    per_turn = []
    for r in condition["results"]:
        label = r["label"]
        expected = "no_search" if label == "out_of_scope" else label
        got = r["route"]
        ok = got == expected
        correct += int(ok)
        if got in ("search_to_enrich", "search_instead"):
            searched += 1
        if label == "out_of_scope" and not ok:
            out_of_scope_wrong += 1
        if r.get("elapsed_ms"):
            latencies.append(r["elapsed_ms"])
        if r.get("confidence") is not None:
            confidences.append(r["confidence"])
        per_turn.append(
            {
                "turn_id": r["turn_id"],
                "label": label,
                "expected": expected,
                "route": got,
                "correct": ok,
                "confidence": r.get("confidence"),
                "elapsed_ms": round(r.get("elapsed_ms") or 0, 1),
                "error": r.get("error"),
            }
        )

    return {
        "condition": condition["condition"],
        "model": condition["model"],
        "correct": correct,
        "total": len(condition["results"]),
        "searched": searched,
        "out_of_scope_wrong": out_of_scope_wrong,
        "latency_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        "latency_p95_ms": (
            round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1)
            if len(latencies) >= 2
            else (round(latencies[0], 1) if latencies else None)
        ),
        "confidence_min": round(min(confidences), 3) if confidences else None,
        "confidence_mean": round(statistics.mean(confidences), 3) if confidences else None,
        "per_turn": per_turn,
    }


async def main() -> int:
    print(f"Ollama: {OLLAMA_URL}")
    print(f"decision models: {MODELS}")
    print(f"turns: {len(TURNS)}  repeats: {REPEATS}")
    print()

    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=300.0) as client:
        version = (await client.get("/api/version")).json()
        print(f"ollama version: {version}")

        raw_conditions = []
        raw_conditions.append(await _condition_a(client))
        for model in MODELS:
            raw_conditions.append(await _condition_decision(client, model))

    summaries = [score(c) for c in raw_conditions]

    report = {
        "ollama_version": version,
        "chat_model": CHAT_MODEL,
        "decision_models": MODELS,
        "routes": list(ROUTES),
        "turns": [asdict(t) for t in TURNS],
        "summaries": summaries,
        "raw": raw_conditions,
    }
    OUT.write_text(json.dumps(report, indent=2))

    print(f"\n{'condition':28} {'correct':>8} {'searched':>9} {'oos_wrong':>10} "
          f"{'p50ms':>8} {'conf_mean':>10}")
    for s in summaries:
        print(
            f"{s['condition']:28} {s['correct']:>4}/{s['total']:<3} {s['searched']:>9} "
            f"{s['out_of_scope_wrong']:>10} "
            f"{str(s['latency_p50_ms']):>8} {str(s['confidence_mean']):>10}"
        )
    print(f"\nwrote {OUT}")

    print("\nper-turn (route vs expected):")
    for s in summaries:
        print(f"\n  {s['condition']}")
        for t in s["per_turn"]:
            mark = "ok " if t["correct"] else "XX "
            err = f"  ERR {t['error']}" if t["error"] else ""
            print(
                f"    {mark}turn {t['turn_id']:>2} label={t['label']:<20} "
                f"route={str(t['route']):<20} conf={t['confidence']}{err}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
