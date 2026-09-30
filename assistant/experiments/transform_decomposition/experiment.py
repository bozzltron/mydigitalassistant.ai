"""Does a long list survive a transform, and does decomposition save it?

The user pasted 45 URLs and asked for them ranked by impact, expecting the agent to
research what each link was first. At 7 links the agent ranks correctly; at 45 it
wrote an essay about one station and the list appeared nowhere in the answer.

Three attempts to fix this with static rules failed (recorded in
tests/test_model_chooses_tools.py). This measures the thing that decides whether a
transform pipeline is warranted at all.

See plan.md for the frozen hypotheses, the corruption bar (0 -- a list you cannot
trust is not one you can work top-down from), and the threats to validity.

Safety: no brain. No store, no database, no memory writes. It DOES search, because
enrichment requires it; every query is recorded so what left the machine is auditable.

Usage:
    python -m assistant.experiments.transform_decomposition.experiment
Env:
    TD_SIZES     comma Ns (default 7,20,45)
    TD_RUNS      runs per cell (default 1)
    TD_MODELS    comma models (default qwen3.5:9b)
    TD_NO_SEARCH 1 to skip enrichment (one-shot ranking only)
    TD_OUT       result json path (default ./result.json)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
SIZES = [int(s) for s in os.environ.get("TD_SIZES", "7,20,45").split(",") if s]
RUNS = int(os.environ.get("TD_RUNS", "1"))
MODELS = [m for m in os.environ.get("TD_MODELS", "qwen3.5:9b").split(",") if m]
SKIP_SEARCH = os.environ.get("TD_NO_SEARCH") == "1"
OUT = Path(os.environ.get("TD_OUT", "result.json"))

# The user's real submission list, frozen. Heterogeneous on purpose: station contact
# pages, a Google Form, a Reddit thread, a contact list, Bandcamp, an eBay listing.
LIST_45: list[str] = [
    "https://wprb.com/contact/executive-director/",
    "https://wprb.com/contact/music-submissions/",
    "https://docs.google.com/forms/d/e/1FAIpQLSc6n_slllmbuRNsBfInZjC5hwsZa4BTSYP3OjC1xfY_xpNvLA/formResponse",
    "https://www.reddit.com/r/WeAreTheMusicMakers/comments/fsglwm/458_us_college_radio_contacts_their_submission/",
    "https://wkdu.org/contact",
    "https://bookingagentinfo.com/indie-music-blogs-contact-list/",
    "https://atxlibrary.musicat.co/submit",
    "https://www.rockrageradio.com/",
    "https://help.kcrw.com/hc/en-us/requests/new",
    "https://www.kgpc969.org/music-submission",
    "https://www.kcss.net/music-submissions",
    "https://www.wildcat919.com/contact",
    "https://www.austinchronicle.com/contact/",
    "https://www.blakmarigold.com/playlist-submission",
    "https://austinmusiclove.com/",
    "https://app.discovrradio.com/create-account",
    "https://kxt.org/submissions/",
    "https://cjsw.com/music/submit/",
    "https://cjsw.fillout.com/t/nayVJqR423us",
    "https://wfuv.org/user/121/contact",
    "https://amazingradio.com/connect/signup",
    "https://www.bbc.co.uk/introducing",
    "https://www.austintexas.org/events/festivals/",
    "https://www.revolution.fm/",
    "https://magicnothing.xyz/",
    "https://unheard.online/submit",
    "https://submit.happymag.tv/",
    "https://www.vibe971.com/contact-us/",
    "https://www.wfmu.org/email.php?id=447",
    "https://stereogum.com/contact",
    "https://avenuear.com/2023/12/19/submit-music-to-blogs/",
    "https://bandcamp.com/guide",
    "https://www.wxut.com/music-submission",
    "https://kboo.fm/submit-your-music",
    "https://www.indiemusicdiscovery.com/submit-music/",
    "https://twostorymelody.com/contact/",
    "https://madindiemedia.com/playlist-submissions/",
    "https://kexp.org/submissions/",
    "https://www.kutx.org/submit-your-music/",
    "https://kcrw.com/music/submissions",
    "https://www.wfmt.com/contact/",
    "https://www.musicconnection.com/",
    "https://www.sonicbids.com/",
    "https://www.reverbnation.com/",
    "https://www.indieonthemove.com/",
]


# ---------------------------------------------------------------------------
# Corruption measurement: mechanical, and deliberately generous
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def urls_in(text: str) -> list[str]:
    """Every URL appearing in a block of text, normalised minimally."""
    return [u.rstrip(".,;:") for u in URL_RE.findall(text or "")]


@dataclass
class Corruption:
    """What happened to the item set. The primary metric of this experiment."""

    n_in: int
    n_out: int
    invented: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    mutated: list[tuple[str, str]] = field(default_factory=list)

    @property
    def rate(self) -> float:
        """Fraction of input items dropped or mutated, plus inventions, itemised."""
        if self.n_in == 0:
            return 0.0
        bad = len(self.dropped) + len(self.mutated) + len(self.invented)
        return bad / self.n_in

    @property
    def passed(self) -> bool:
        """The pre-registered bar: zero. No partial credit."""
        return not (self.invented or self.dropped or self.mutated)

    def summary(self) -> str:
        return (
            f"in={self.n_in} out={self.n_out} "
            f"dropped={len(self.dropped)} mutated={len(self.mutated)} "
            f"invented={len(self.invented)} rate={self.rate:.3f} "
            f"{'PASS' if self.passed else 'FAIL'}"
        )


def measure(items_in: list[str], text_out: str) -> Corruption:
    """Compare the input list to what the model's output contained.

    Matching is by exact URL text after minimal normalisation. A URL present in the
    output but not the input is an invention; a URL in the input missing from the
    output is a drop. Mutation is a URL that resembles an input item but is not
    byte-identical -- e.g. a trailing slash added, a path truncated.
    """
    out_urls = urls_in(text_out)
    out_set = set(out_urls)
    in_set = set(items_in)

    # A drop vs a mutation: if a near-miss of an input URL is present, that is a
    # mutation; if nothing resembling it is present, it is a drop.
    def _norm(u: str) -> str:
        return u.rstrip("/").replace("https://", "").replace("http://", "").lower()

    out_normed = {_norm(u): u for u in out_urls}
    in_normed = {_norm(u) for u in items_in}

    dropped: list[str] = []
    mutated: list[tuple[str, str]] = []
    for item in items_in:
        if item in out_set:
            continue
        near = out_normed.get(_norm(item))
        if near is not None:
            mutated.append((item, near))
        else:
            dropped.append(item)

    # An output URL is an invention only if it is not an input item AND not the
    # near-miss form of one -- otherwise a single trailing-slash change would be
    # counted twice, once as a mutation and once as an invention, and inflate the
    # rate. Mutations are already accounted for above.
    invented = sorted(
        u for u in out_set if u not in in_set and _norm(u) not in in_normed
    )

    return Corruption(
        n_in=len(items_in),
        n_out=len(out_urls),
        invented=invented,
        dropped=dropped,
        mutated=mutated,
    )


# ---------------------------------------------------------------------------
# Search, recorded
# ---------------------------------------------------------------------------

SEARCH_LOG: list[str] = []


async def _search(query: str, num: int = 3) -> str:
    """One search through the app's own backend. Records the query."""
    SEARCH_LOG.append(query)
    if SKIP_SEARCH:
        return ""
    from assistant.backend.pipeline.search import WebSearchTool

    tool = WebSearchTool()
    try:
        results, _ = await tool.search(query, num_results=num)
    except Exception as exc:
        return f"(search failed: {exc})"
    finally:
        try:
            await tool.close()
        except Exception:
            pass
    return "\n".join(f"- {r.title}: {r.snippet[:300]}" for r in results)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ONE_SHOT_SYSTEM = """You rank lists of links for a musician working top-down.

The user gives you a list. They want it ordered so they can start at the top and work
down.

Rules:
- Use EVERY link from the list, exactly as written. Do not drop any.
- Do not add links that were not in the list.
- You may add a short note after each link saying what it is and why it ranks there.
- Respond with the ranked list, numbered, one link per line."""


@dataclass
class Cell:
    model: str
    n: int
    condition: str
    run: int
    items_in: list[str]
    output: str
    corruption: Corruption
    elapsed_ms: float
    searches: list[str] = field(default_factory=list)
    error: str | None = None


async def _chat(client: httpx.AsyncClient, model: str, system: str, user: str,
                timeout: float = 600.0) -> str:
    r = await client.post(
        "/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "")


async def condition_one_shot(client, model: str, items: list[str], run: int) -> Cell:
    """Single prompt: the whole list in, a ranking out."""
    started = time.perf_counter()
    n_before = len(SEARCH_LOG)
    listing = "\n".join(items)
    user = (
        f"Here are {len(items)} links. Rank them by how impactful each would be for "
        f"getting my music heard, most impactful first. Tell me what each one is.\n\n"
        f"{listing}"
    )
    try:
        out = await _chat(client, model, ONE_SHOT_SYSTEM, user)
        err = None
    except Exception as exc:
        out, err = "", str(exc)

    return Cell(
        model=model,
        n=len(items),
        condition="one_shot",
        run=run,
        items_in=items,
        output=out,
        corruption=measure(items, out),
        elapsed_ms=(time.perf_counter() - started) * 1000,
        searches=SEARCH_LOG[n_before:],
        error=err,
    )


PER_ITEM_SYSTEM = """You describe a single web link for a musician submission list.

Given ONE url, say what it is and how useful it would be for getting music heard.
Two sentences at most. Reply with just that description."""

ORDER_SYSTEM = """You order a list of submission links for a musician working top-down.

You are given numbered entries, each "ID: <url> -- <note>". Return the same entries
reordered most-impactful-first.

Rules:
- Include EVERY id exactly once. Do not drop, add, merge, or renumber any.
- Keep each id's url exactly as given.
- Reorder only. Respond as lines of "ID: <url> -- <note>"."""


async def condition_decomposed(client, model: str, items: list[str], run: int) -> Cell:
    """Per item: describe it, then order the annotated set in one call."""
    started = time.perf_counter()
    n_before = len(SEARCH_LOG)
    annotations: list[str] = []
    try:
        for idx, url in enumerate(items, start=1):
            # Enrichment is per item, so a failure here costs one item, not the run.
            snippets = await _search(url, num=2)
            context = f"\n\nSearch results for context:\n{snippets}" if snippets else ""
            note = await _chat(
                client, model, PER_ITEM_SYSTEM, f"URL: {url}{context}", timeout=300.0
            )
            note = " ".join(note.split())[:400]
            annotations.append(f"{idx}: {url} -- {note}")

        ordering_input = "\n".join(annotations)
        out = await _chat(client, model, ORDER_SYSTEM, ordering_input, timeout=900.0)
        err = None
    except Exception as exc:
        out, err = "\n".join(annotations), str(exc)

    return Cell(
        model=model,
        n=len(items),
        condition="decomposed",
        run=run,
        items_in=items,
        output=out,
        corruption=measure(items, out),
        elapsed_ms=(time.perf_counter() - started) * 1000,
        searches=SEARCH_LOG[n_before:],
        error=err,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    print(f"Ollama      : {OLLAMA_URL}")
    print(f"models      : {MODELS}")
    print(f"sizes       : {SIZES}")
    print(f"runs/cell   : {RUNS}")
    print(f"search      : {'OFF' if SKIP_SEARCH else 'ON'}")
    print()

    cells: list[Cell] = []
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=900.0) as client:
        version = (await client.get("/api/version")).json()

        for model in MODELS:
            # Warm up so the first measured call is not paying model load.
            try:
                await _chat(client, model, "Reply with the single word ok.", "ok")
            except Exception as exc:
                print(f"warmup failed for {model}: {exc}")

            for n in SIZES:
                items = LIST_45[:n]
                for run in range(RUNS):
                    c1 = await condition_one_shot(client, model, items, run)
                    print(f"  {model} n={n:<3} one_shot    {c1.corruption.summary()}")
                    cells.append(c1)

                    c2 = await condition_decomposed(client, model, items, run)
                    print(f"  {model} n={n:<3} decomposed  {c2.corruption.summary()}")
                    cells.append(c2)

    # Aggregate per (model, n, condition).
    agg: list[dict] = []
    seen = {(c.model, c.n, c.condition) for c in cells}
    for model, n, cond in sorted(seen):
        group = [c for c in cells if (c.model, c.n, c.condition) == (model, n, cond)]
        agg.append(
            {
                "model": model,
                "n": n,
                "condition": cond,
                "runs": len(group),
                "all_passed": all(c.corruption.passed for c in group),
                "mean_corruption_rate": round(
                    sum(c.corruption.rate for c in group) / len(group), 3
                ),
                "dropped_total": sum(len(c.corruption.dropped) for c in group),
                "mutated_total": sum(len(c.corruption.mutated) for c in group),
                "invented_total": sum(len(c.corruption.invented) for c in group),
                "mean_elapsed_ms": round(
                    sum(c.elapsed_ms for c in group) / len(group), 1
                ),
                "searches": sum(len(c.searches) for c in group),
                "errors": [c.error for c in group if c.error],
            }
        )

    report = {
        "ollama_version": version,
        "models": MODELS,
        "sizes": SIZES,
        "runs_per_cell": RUNS,
        "search_enabled": not SKIP_SEARCH,
        "list": LIST_45,
        "search_log": SEARCH_LOG,
        "cells": [
            {
                **asdict(c),
                "corruption": {
                    **asdict(c.corruption),
                    "passed": c.corruption.passed,
                    "rate": c.corruption.rate,
                },
            }
            for c in cells
        ],
        "aggregate": agg,
    }
    OUT.write_text(json.dumps(report, indent=2))

    print(f"\n{'condition':12} {'n':>4} {'pass':>5} {'rate':>7} {'drop':>5} "
          f"{'mut':>4} {'inv':>4} {'p50-ish ms':>11}")
    for a in agg:
        print(
            f"{a['condition']:12} {a['n']:>4} {str(a['all_passed']):>5} "
            f"{a['mean_corruption_rate']:>7} {a['dropped_total']:>5} "
            f"{a['mutated_total']:>4} {a['invented_total']:>4} "
            f"{a['mean_elapsed_ms']:>11}"
        )
    print(f"\nsearches issued: {len(SEARCH_LOG)}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
