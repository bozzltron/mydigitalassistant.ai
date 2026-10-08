"""Side-by-side probe: can the candidate replace the chat and math models?

Pre-registered in ``plan.md`` (committed before any data). Pure Ollama I/O — it
does not open the brain, so ``preflight.py`` does not apply.

    python assistant/experiments/model_swap_qwen35_claude/experiment.py

Writes ``result.json`` next to this file. ``result.md`` only after
``verification.md``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CANDIDATE = os.environ.get("CANDIDATE_MODEL", "sorc/qwen3.5-claude-4.6-opus:latest")
CHAT_BASELINE = os.environ.get("CHAT_BASELINE", "qwen3.5:9b")
MATH_BASELINE = os.environ.get("MATH_BASELINE", "qwen3.8:27b")
NUM_CTX = int(os.environ.get("EXP_NUM_CTX", "16384"))
HERE = Path(__file__).resolve().parent

# One tool, so a tool-call probe is unambiguous.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]

# name -> spec. `fmt="json"` mirrors the production structured-output mode; every
# call is think=False, the production default (without it a thinking model leaks
# its analysis into `content`).
PROMPTS: dict[str, dict] = {
    "qa": {"prompt": "In two sentences, explain what a confidence interval is."},
    "json": {"prompt": "List three benefits of unit testing as JSON.", "fmt": "json"},
    "tool": {
        "prompt": "Read the file notes/budget.csv and tell me the total.",
        "tools": TOOLS,
    },
    "code": {
        "prompt": (
            "Write Python that computes the sum of squares from 1 to 100 and "
            "prints the result. Reply with only the code."
        )
    },
}

# The candidate runs every prompt (it may replace both roles); each baseline runs
# only its own.
ARMS: list[tuple[str, str, set[str]]] = [
    (CHAT_BASELINE, "chat baseline", {"qa", "json", "tool"}),
    (MATH_BASELINE, "math baseline", {"code"}),
    (CANDIDATE, "candidate (all roles)", {"qa", "json", "tool", "code"}),
]


def _chat(model: str, prompt: str, tools: list | None, fmt: str | None) -> dict:
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"num_ctx": NUM_CTX, "temperature": 0},
    }
    if fmt:
        payload["format"] = fmt
    if tools:
        payload["tools"] = tools
    request = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    data["_wall_s"] = round(time.time() - started, 3)
    return data


def _summarize(name: str, data: dict) -> dict:
    message = data.get("message", {})
    content = message.get("content") or ""
    eval_count = data.get("eval_count", 0)
    eval_ns = data.get("eval_duration", 0)

    row: dict = {
        "prompt": name,
        "wall_s": data["_wall_s"],
        "load_s": round(data.get("load_duration", 0) / 1e9, 3),
        "prompt_s": round(data.get("prompt_eval_duration", 0) / 1e9, 3),
        "eval_tokens": eval_count,
        "tok_per_s": round(eval_count / (eval_ns / 1e9), 1) if eval_ns else None,
        "content": content[:600],
    }
    if name == "tool":
        row["tool_call"] = bool(message.get("tool_calls"))
    if name == "json":
        # Valid JSON of any shape -- the models return an object under format=json,
        # not the array the prompt asked for.
        try:
            json.loads(content)
            row["json_ok"] = True
        except (ValueError, TypeError):
            row["json_ok"] = False
    if name == "code":
        row["code_signal"] = "print" in content and ("**" in content or "sum" in content)
    return row


def _run(model: str, names: set[str]) -> list[dict]:
    rows = []
    for name, spec in PROMPTS.items():
        if name not in names:
            continue
        row = _summarize(
            name, _chat(model, spec["prompt"], spec.get("tools"), spec.get("fmt"))
        )
        rows.append(row)
        signal = row.get("tool_call", row.get("json_ok", row.get("code_signal", "")))
        print(
            f"  {name:5} wall={row['wall_s']:>6}s load={row['load_s']:>6}s "
            f"tok/s={row['tok_per_s']} signal={signal}"
        )
    return rows


def main() -> None:
    print(f"Ollama: {OLLAMA}")
    results: dict[str, list[dict]] = {}
    for model, label, names in ARMS:
        print(f"\n[{label}] {model}")
        results[label] = _run(model, names)
    (HERE / "result.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote {HERE / 'result.json'}")


if __name__ == "__main__":
    main()
