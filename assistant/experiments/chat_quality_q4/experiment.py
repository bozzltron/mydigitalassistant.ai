"""Does the Q4 candidate write better chat answers than ``qwen3.5:9b``?

Pre-registered in ``plan.md``. Blind, pairwise judging by a different-family model,
each pair judged in both orders so a position bias cannot fake a win.

    python assistant/experiments/chat_quality_q4/experiment.py

Writes ``result.json`` (override with ``RESULT_PATH``).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
HERE = Path(__file__).resolve().parent
NUM_CTX = int(os.environ.get("EXP_NUM_CTX", "16384"))

CANDIDATE = os.environ.get("CANDIDATE_MODEL", "sorc/qwen3.5-claude-4.6-opus-q4:9b")
INCUMBENT = os.environ.get("INCUMBENT_MODEL", "qwen3.5:9b")
# A different family from both contestants, so self-preference is at least not
# aligned with either.
JUDGE = os.environ.get("JUDGE_MODEL", "qwen3.8:27b")

PROMPTS: list[str] = [
    "In two sentences, explain what a confidence interval is.",
    "What are three practical ways to reduce household energy use in winter?",
    "Explain the difference between a Roth IRA and a traditional IRA in three sentences.",
    "A recipe needs 2/3 cup of sugar but I want to make 1.5x the recipe. How much sugar?",
    "Summarize this in one sentence: The city council approved a new park after months "
    "of debate, citing community feedback and a budget surplus.",
    "List exactly three benefits of unit testing, each under 10 words.",
    "If it is 3pm in Chicago, what time is it in Los Angeles?",
    "Give a one-paragraph explanation of why the sky is blue.",
    "What should I consider before adopting a rescue dog?",
    "Write a two-line haiku about autumn.",
]


def _chat(model: str, prompt: str) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0},
    }
    started = time.time()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        data = json.load(response)
    return (data.get("message", {}).get("content") or ""), round(time.time() - started, 2)


def _judge(prompt: str, answer_a: str, answer_b: str) -> tuple[str, str]:
    judge_prompt = (
        "You are judging two answers to the same question. Judge only on "
        "correctness, helpfulness, and clarity -- not length.\n\n"
        f"Question:\n{prompt}\n\n"
        f"Answer A:\n{answer_a}\n\n"
        f"Answer B:\n{answer_b}\n\n"
        "Reply with a one-sentence reason, then a final line containing only "
        "A, B, or TIE."
    )
    payload = {
        "model": JUDGE,
        "messages": [{"role": "user", "content": judge_prompt}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        text = json.load(response).get("message", {}).get("content") or ""
    # The last standalone A/B/TIE token is the verdict; TIE wins a tie of tokens.
    tokens = re.findall(r"\b(A|B|TIE)\b", text.upper())
    return (tokens[-1] if tokens else "?"), text.strip()


def main() -> None:
    print(f"candidate={CANDIDATE}\nincumbent={INCUMBENT}\njudge={JUDGE}\n")
    rows: list[dict] = []
    for prompt in PROMPTS:
        cand, cand_s = _chat(CANDIDATE, prompt)
        inc, inc_s = _chat(INCUMBENT, prompt)
        # Order 1: A=candidate, B=incumbent. Order 2: swapped.
        v1, r1 = _judge(prompt, cand, inc)
        v2, r2 = _judge(prompt, inc, cand)

        cand_wins = v1 == "A" and v2 == "B"
        inc_wins = v1 == "B" and v2 == "A"
        if cand_wins:
            outcome = "candidate"
        elif inc_wins:
            outcome = "incumbent"
        elif v1 == "TIE" and v2 == "TIE":
            outcome = "tie"
        else:
            outcome = "flip"  # position-biased or inconsistent

        rows.append(
            {
                "prompt": prompt[:60],
                "outcome": outcome,
                "order1": v1,
                "order2": v2,
                "candidate_answer": cand[:600],
                "incumbent_answer": inc[:600],
                "candidate_s": cand_s,
                "incumbent_s": inc_s,
                "judge_order1": r1[:400],
                "judge_order2": r2[:400],
            }
        )
        print(f"  {outcome:9} (o1={v1} o2={v2})  {prompt[:50]}")

    tally = {
        o: sum(1 for r in rows if r["outcome"] == o)
        for o in ("candidate", "incumbent", "tie", "flip")
    }
    decided = tally["candidate"] + tally["incumbent"]
    rate = (tally["candidate"] / decided * 100) if decided else 0.0
    print(
        f"\ncandidate {tally['candidate']}  incumbent {tally['incumbent']}  "
        f"tie {tally['tie']}  flip {tally['flip']}"
    )
    print(f"position-robust win rate: {rate:.0f}% of decided pairs")

    out = Path(os.environ.get("RESULT_PATH", HERE / "result.json"))
    out.write_text(
        json.dumps({"tally": tally, "win_rate": round(rate, 1), "rows": rows}, indent=2)
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
