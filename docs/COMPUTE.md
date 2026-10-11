# Computation — how the assistant does math

## The shape of it

The assistant does **not** do arithmetic in its head. When a request needs a calculation,
the `compute` tool runs a **write-and-execute** loop:

1. The **model writes Python** — it returns a tool call `execute_python(code=...)`.
2. The **backend executes** that code in a subprocess.
3. The **answer is the process's stdout.** The model never states the result itself.

So the arithmetic is **exact by construction**: the number the user sees was computed by
CPython, not sampled by a language model. What the model contributes is **codegen** — and
that is the only place it can fail.

## The contract — and the two ways it breaks

The model must do **both**:

- emit the `execute_python` tool call (not prose), **and**
- write code that **prints** its answer.

Both failure modes are real:

| failure | what happens | observed |
|---|---|---|
| no tool call | nothing executes; stdout is empty | — |
| no `print()` | the code runs, stdout is empty, the answer never reaches the assistant | the **Q8** build on "sum of the squares": `sum(i**2 for i in range(1, 101))`, no print |

`tool_choice="required"` is sent, but **Ollama ignores it** — the model chooses whether to
call the tool. The system prompt *asks* for the tool call; nothing enforces it.

## Which models can do it

Any model advertising the Ollama **`tools`** capability (`supports_tools()`, probed via
`/api/show`). In the shipped fleet that is the chat/tools model (`qwen3.5:9b`), the
utility model (`qwen3.5:4b`), the math model and the max tier. The **decision models
(`tev1:0.8b`, `nimble`) are not tool models** — they answer typed questions, they do not
write code.

The role is `MATH_MODEL`. **Empty disables the `compute` tool entirely** — it is dropped
from the tool set and math requests fall back to the chat model's own arithmetic.

**The shipped configuration points `MATH_MODEL` at the warm utility model**
(`qwen3.5:4b`): it **ties** the dedicated 9B on business arithmetic (30/30 each) and
**beats** it on hard math (8/12 vs 5/12), and it reuses a runner that is already
resident. `MATH_NUM_CTX` **must** equal `UTILITY_NUM_CTX` (4096) — Ollama reloads a model
when the requested context changes (measured: 2.8 s vs 0.75 s warm).

## What is executed — and what is *not* protected

> **This is not a security sandbox.** The subprocess runs model-authored Python with the
> backend's own privileges. No namespace isolation, no filesystem restriction, no seccomp,
> no memory cap, no network block — `import socket` works. Treat its **execution** as a
> trust decision, not a containment boundary.

What **is** enforced:

- a **wall-clock timeout**, killing the whole process **group**, so a grandchild spawned
  by the code cannot outlive the call;
- a **minimal environment (PATH only)** — it previously passed the full environment, which
  in the deployed container carries `DB_KEY` (the SQLCipher key for the user's brain) and
  `BRAVE_API_KEY`;
- **no `PYTHONPATH`**, so user site-packages are not importable;
- it runs **off the event loop**, so a long computation cannot stall other requests.

The convenience prefix (`math`, `statistics`, `numpy`, `sympy`, …) is a **prefix, not a
restriction** — it removes nothing.

## Cost and residency

As shipped, `compute` shares the **warm utility runner**, so it adds no model load and no
eviction. That is a deliberate change: pointing `MATH_MODEL` at a *dedicated* on-demand
model means loading it **evicts a warm model** when `OLLAMA_MAX_LOADED_MODELS` is
saturated (default 3, and the warm set fills it) — the next normal turn then pays a
reload. See `docs/MODEL_SELECTION.md`.

**If you do point `MATH_MODEL` at a different model**, remember that Ollama reloads a
runner whenever the requested context size changes — so a `MATH_NUM_CTX` that differs
from that model's other use costs a reload per switch.

## What is measured — and what is not

- `assistant/experiments/business_math/` — **the deciding probe.** Business arithmetic:
  the warm utility model **ties** the dedicated 9B (30/30 each) and **beats** it on hard
  math (8/12 vs 5/12); the chat model is worse on business (27/30, a deterministic
  empty-stdout miss). The dedicated model was retired on this evidence.
- `assistant/experiments/math_model_value/` — the earlier math model vs the 27B on
  *generic* problems. Its Q8 verdict was **quant-confounded**, superseded by
  `model_fleet_q4`.
- **Known limit:** compute is reliable on business arithmetic (~100%) and **unreliable on
  hard math (42–67%) for every model** — the failures look like codegen (a traceback or a
  missing print), and the likely fix is the **compute prompt**, which never asks for a
  single printed final value.

## Config

| var | meaning |
|---|---|
| `MATH_MODEL` | the model that writes compute code. **Empty disables the `compute` tool.** |
| `MATH_NUM_CTX` | its context window — keep it modest, it must load beside the warm set. |
| `MATH_KEEP_ALIVE` | how long it stays resident after use (`10m`). |
