# Model Selection & Assessment

How we choose and re-assess the local Ollama model fleet. This is the living
reference for the question "which model should back each role?" — re-run the
assessment whenever a notable model family lands, and update this file with the
decision.

## Constraints (hard rules)

1. **Local-only.** All inference via Ollama on `127.0.0.1:11434`. No cloud LLM
   APIs. The model must exist in the local Ollama library (or be pullable from
   it over the network on the user's explicit action).
2. **Fits the box.** M4 Pro, 48GB unified memory. Budget the **warm set** to
   stay resident at all times, and every **on-demand tier** must fit *next to*
   the warm set at peak, with headroom for macOS + Docker + the other services
   (Caddy, SearXNG, frontend).
3. **Latency targets.** Basic interactions feel instant (<500ms to first
   feedback); a complete LLM cycle stays under ~2s of *perceivable wait*.
   Anything that loads a cold model mid-turn pays a tens-of-seconds tax — that
   is why the hot path uses warm models and escalation tiers are on-demand.
4. **Tool calling works.** Roles that drive the tool loop (chat/tools/math/max)
   must pass the `supports_tools()` / `/api/show` capabilities probe.
5. **Privacy preserved.** No telemetry; the fleet never phones home.

## Roles

| Role | Env var | Job | Residency | Capabilities needed |
|------|---------|-----|-----------|---------------------|
| Chat | `CHAT_MODEL` | User-facing generation | **warm** | thinking (optional), good general quality |
| Tools | `TOOLS_MODEL` | Tool loop (tool calls + final answer) | warm | `tools` capability, reliable function calling |
| Utility | `UTILITY_MODEL` | Extraction, routing, summarization | warm | fast, cheap, structured output |
| Embedding | `EMBEDDING_MODEL` | Frame/query vectors | warm | fixed output dims (see re-embed rule) |
| Max | `MAX_MODEL` | Escalation tier (auto reasoner / "Max" toggle) | **on-demand** (short `keep_alive`) | thinking + tools, high quality |
| Math | `MATH_MODEL` | `compute` tool: the model writes Python, the backend executes it, the answer is **stdout** (`docs/COMPUTE.md`) | on-demand | tools + codegen quality |
| Coder | `CODER_MODEL` | Reserved for tool codegen | — | empty = chat model handles it |
| Decision | `DECISION_MODEL`, `DECISION_COMPLETION_MODEL` | Typed judgements for the tool loop (`needs_action`, `outstanding`) | on-demand (short `keep_alive`) | decision model — ollama's `/v1/systemone` |

## Memory budget math (per role)

Count **weights** (Ollama `ollama list` size) + a small KV overhead. On 48GB:

- **Always-warm set** (chat + tools + utility + embedding) should be its own sum
  of sizes and stay well under ~24GB so it never thrashes.
- **On-demand tiers** (max, math) are sized so *warm + the largest single
  on-demand tier* fits with headroom — a 27B (~17GB) next to a ~11GB warm set ≈
  28GB is comfortable; a second concurrent 30B would push ~46GB and is not safe.
- Keep-alive is the residency control: warm roles get `-1`/long, on-demand tiers
  get `10m` so they evict themselves and never squat next to the warm set.
- **OLLAMA_MAX_LOADED_MODELS=4** on the host caps concurrent resident models.

Current (Sept 2026) warm set ≈ 10.6GB (6.6 + 3.4 + 0.6); peak with the on-demand
27B ≈ 28GB. Fits 48GB.

## Latency budget (per role)

| Interaction | Budget |
|-------------|--------|
| Tool call (warm tool model) | ~500ms |
| Utility extraction | ~500ms-1s |
| Full 9b chat turn | <2s |
| 27B escalation turn | 5-15s (user-visible "Max" affordance; on-demand load tax) |
| Embedding | <100ms |

A base chat + utility + embedding turn must complete well inside 2s. If a
candidate model can't hold that on the hot path, it belongs in an on-demand tier
or out of the fleet.

## Assessment process (run per candidate)

1. **Pull + probe** the candidate: `ollama pull <model>`, then check
   `/api/show` for `capabilities` (needs `tools`, maybe `thinking`/`vision`).
2. **Size it**: `ollama list` for weights; load the warm set + candidate and
   check `ollama ps` residency, then let `keep_alive=10m` evict the on-demand
   tier and confirm RAM returns.
3. **Real latency**: run a scripted chat turn and a tool-loop turn; time first
   token and full cycle. Keep the performance timing logs (UI/backend logs).
4. **Quality spot-checks**: one set of representative prompts per role —
   general QA (chat), a multi-tool task (tools), a structured extract (utility),
   a hard multi-step math/code task (max), a Python-executed calculation
   (math/compute).
5. **Escalation sanity**: with the candidate as `MAX_MODEL`, verify the
   auto-escalation path (`plan.max_intelligence`) and the UI "Max" toggle route
   to it, and that `supports_tools(max)` guard falls back when needed.
6. **Record the result** here: table row + date + one-line rationale.

## Re-embed rule (embedding swaps)

Changing `EMBEDDING_MODEL` changes vector dimensionality and orphans every old
vector. After a swap, run `assistant db reembed` — it re-embeds all frames
**and** tops up episodes missing a vector under the new model (episodes carry
per-model vectors; the top-up is what keeps "related past conversations"
searchable after a swap). Verify with: no frames/episodes lack a vector for the
new model. Scheduler is off in this deployment, so the CLI is the only top-up.

## Current fleet (Oct 2026)

One turn's stages and the role each uses: **`docs/PIPELINE.md`**.

| Role | Model | Size | keep_alive | num_ctx |
|------|-------|------|------------|---------|
| Chat | `qwen3.5:9b` | 6.6GB | `-1` (warm) | 16384 |
| Tools | `qwen3.5:9b` | — shares chat | `-1` | shares chat (16384) |
| Utility | `qwen3.5:4b` | 3.4GB | `-1` (warm) | 4096 |
| Embedding | `qwen3-embedding:0.6b` | 0.6GB | `-1` (warm) | — |
| Max | `qwen3.8:27b` | 17GB | `10m` (on-demand) | 16384 |
| Math | `qwen3.5:4b` (the warm utility runner) | — shares utility | `-1` (warm) | 4096 |
| Coder | (empty → chat) | — | — | — |
| Decision | `tev1:0.8b` (**required**) + `nimble` (opt-in) | 0.8 GB (+ 9.5 GB) | `10m` (on-demand) | — |

**The minimum fleet is four models (~11.4 GB):**

| required | why |
|---|---|
| `qwen3.5:9b` | chat + tools — one runner, shared context |
| `qwen3.5:4b` | utility (extraction/routing) **and** compute |
| `qwen3-embedding:0.6b` | memory retrieval |
| `tev1:0.8b` | the `needs_action` judgement that gates the nudge |

Three of the four are resident. **Compute shares the utility runner** (`MATH_MODEL=qwen3.5:4b`,
`MATH_NUM_CTX=4096` — the context must match `UTILITY_NUM_CTX` or the runner reloads; see
`docs/COMPUTE.md`).

**Two opt-in tiers are OFF by default**, each costing a model load and adding no required
capability: **`nimble`** (the *completion* nudge — see "Considered and not adopted") and
**`qwen3.8:27b`** (MAX escalation). Empty them and the corresponding feature degrades
cleanly rather than failing.

> **The binding constraint is `OLLAMA_MAX_LOADED_MODELS`, not RAM.** It defaults to
> **3**, and the warm set fills it. A fourth model *evicts a warm one* — measured while
> probing the extraction council: running `sorc/q4:4b` alone evicted `qwen3.5:4b`, and
> the next turn pays its reload. Host RAM (48 GB on the reference box) is not the limit;
> the cap is. Raise it deliberately if a new role must coexist with the warm set.

Rationale highlights:

- **9b as both chat and tools** — one brain, shared hot KV cache, tool loop stays
  warm and fast. Because they are the same loaded runner they share one context
  window: `CHAT_NUM_CTX` (16384), not the smaller `TOOLS_NUM_CTX`, which applies
  only to a *distinct* tools model. The tool loop sends the system prompt + every
  tool schema + history — the largest prompt in the system — so 8192 left no room
  to generate and the turn finalized empty (incident 2026-10-01).
- **4b utility** — cheap extraction/routing fallback that never slows the hot path.
- **27b max, 9b math (Oct 2026)** — the 27B is now the on-demand escalation tier
  only; the math/compute role runs the Q4 candidate (equal accuracy to the 27B on
  the probe, ~4–10× faster per problem, 6.6 GB vs 17 GB). It no longer shares
  weights with max, so compute is a separate on-demand load — both are `10m`, so
  neither stays resident next to the warm set. See the re-assessment below.
- **0.6b embedding** — fast, tiny, 1024-dim vectors.
- Max/math are **never resident** (`10m` keep-alive) so the warm set survives.

## Re-assessment: `qwen3.5-claude-4.6-opus-q4` (Oct 2026)

A community family (`sorc/qwen3.5-claude-4.6-opus-q4`) whose Q4 builds match the
incumbents' sizes exactly (`:9b` 6.59 GB, `:4b` 3.39 GB), so each role can be
compared **like-for-like** (unlike the Q8 `:latest` build, which is heavier and
misleading — see `assistant/experiments/model_fleet_q4/`).

| Role | Incumbent | Candidate | Result |
|---|---|---|---|
| Chat / Tools | `qwen3.5:9b` | `q4:9b` | **tie** — tools ✓, JSON ✓, ~40 tok/s both; blind pairwise quality found **no win** (5 tie / 2 cand / 0 inc / 3 judge-flips) |
| Utility | `qwen3.5:4b` | `q4:4b` | candidate **worse** — 4/7 vs 7/7 facts extracted |
| Math | `qwen3.8:27b` | `q4:9b` | candidate **wins** — 8/8 both, 2–4 s vs 7–50 s, 6.6 GB vs 17 GB |

**Decision:** keep `qwen3.5:9b` (chat/tools) and `qwen3.5:4b` (utility); use
`qwen3.5-claude-4.6-opus-q4:9b` for the **math** role — equal accuracy on the
probe, far faster, and 10 GB smaller than the 27B.

**Adopted on trial (2026-10-08).** `MATH_MODEL` is set to it in both dev and prod
(the shared `.env`), and it was verified end-to-end: `/health` reports it as
`math`, a direct `execute_python("What is 17% of 2,480?")` from inside the dev
container returned `421.6`, and a live chat turn emitted a `compute` tool call and
answered "17% of 2,480 is exactly **421.6**". Rollback is one line in `.env`
(`MATH_MODEL=qwen3.8:27b`) plus a `docker compose up -d` in each project.

**Caveats:** the probes are small (one chat prompt, five extraction turns, eight
easy math problems, and a 10-prompt blind quality run whose judge flipped on 3 of
10 pairs). The math tie does not prove superiority on hard problems, and the chat
quality run found no win but is too small and too noisy to be conclusive — a swap
would need the larger run described in
`assistant/experiments/chat_quality_q4/result.md`.

### MAX re-assessment: the same candidate, against the 27B (Oct 2026)

The natural follow-up — "if the Q4 9B matches the 27B for math, make it MAX too" —
was measured and **rejected** (`assistant/experiments/max_model_choice/`). Ten
hard, multi-step problems with exact answers, `think=True`:

| Role | Incumbent | Candidate | Result |
|---|---|---|---|
| Max | `qwen3.8:27b` | `q4:9b` | incumbent **wins** — 9/10 vs 6/10 (the incumbent's single miss was a timeout, not a wrong answer), and only **1.25×** faster on the median |

**Decision:** MAX stays `qwen3.8:27b`. The math probe used *eight easy* problems
where both scored 8/8 — a ceiling that cannot separate a 9B from a 27B. The MAX
role is open-ended hard reasoning, and there the candidate is 30% less accurate
and not meaningfully faster. The reason the 27B felt unusable was that
*mechanical* turns were being escalated to it (see
`assistant/experiments/escalation_trigger/`), not that the role needs a smaller
model.

### Decision models (Oct 2026)

Ollama 0.35 added `/v1/systemone` — a typed decision API (`choice` / `noul` /
`score`) for fast local judgements. Two are in use, and they are **deliberately
different models**, because no single one does both jobs
(`assistant/experiments/decision_model_routing/` and `.../decision_model_completion/`):

| Judgement | Model | Accuracy | Latency |
|---|---|---|---|
| `needs_action` — does the request need a change or an external lookup? | `tev1:0.8b` | 95% | 70 ms |
| `outstanding` — did the calls leave it undone? | `nimble` | 93.8% | 604 ms |
| ~~`outstanding`~~ — **superseded by `action_performed`, which is structural and calls no model** (a `read_file` satisfied `nimble`, so 7 of 10 scheduled-task trials were judged done without writing — `experiments/ship_tracker_efficacy/`) | — | — | — |

`tev1:0.8b` cannot separate the completion classes at all (0.44 vs 0.42), and
`nimble` cannot match it on routing (90%). Both beat the mechanism they replaced —
a `qwen3.5:4b` chat call emitting JSON — which scored 90% at 7,162 ms.

**The `needs_action` scope is narrow, and measured.** It fires on a *change* or an
*external lookup* — the deeds whose result words can counterfeit — and not on a read
of the user's own stored content, which an answer can satisfy
(`experiments/decision_nudge_scope/`: 10/10 change recall, 0/15 read/answer fires,
3/3 lookup fires). **Those figures were measured against the earlier two-way question.**
The question is now three-way (`change` / `lookup` / `answer`, with the threshold applied
per kind), so the scope owes a re-measurement before these numbers are cited again as
describing the current code.

**Cost:** `nimble` is a 9.5 GB on-demand load, but it runs only on turns that
already need an action; `tev1:0.8b` is 0.8 GB and runs every turn, in parallel with
routing and retrieval. **Both are best-effort** — if a model is missing the
judgement returns "unknown" and nothing nudges, never a guess.

## Considered and not adopted (Oct 2026)

Work that was built, measured, and **rejected** — recorded so it is not re-proposed from
first principles. Each has an experiment under `assistant/experiments/`.

| Idea | Verdict | Evidence |
|---|---|---|
| **A council of models over extraction** | **not adopted** | A two-model council raises extraction recall to **1.00** from the best single's 0.89 — but the member that does the work is `sorc/qwen3.5-claude-4.6-opus-q4:4b`, **already on disk**. The two *cross-lineage* models pulled for the probe added **nothing**: `llama3.2:3b` ties the fine-tune at ~2× latency, `gemma3:4b` is simply a worse extractor (0.56). Cross-lineage miss-overlap (0.20) *exceeded* same-lineage (0.15) — the hypothesis that a different lineage buys diversity **failed**. Cost: a second model on the learning path for ~one fact in nine. `experiments/extraction_council/` |
| **An explicit terminal declaration** replacing the `needs_action` judgement | **retired** | Correct — the model complies and completion holds — but it costs ~1–2 extra rounds on action turns, and a contract forbidding the pre-`finalize` re-check made it *worse* (mean rounds 2.60 → 3.18). The cost is inherent to an explicit end state. `experiments/terminal_declaration/` |
| **A per-kind nudge bound** | **not adopted** | The one-nudge-per-turn bound *can* suppress a warranted completion nudge, but on 10 live write-class turns it never did — the model wrote in round 1 every time. `experiments/nudge_bound/` |
| **`math_model_value`: swap MATH to the Q8 `sorc/…:latest`** | **superseded — the proof was faulty** | It compared a **Q8** candidate against **Q4** incumbents and concluded "keep the 27B" (7/8 vs 8/8, the miss being a forgotten `print()`). `model_fleet_q4` found the confound: at equal quant the `-q4:9b` build is **8/8** (ties the 27B), 4–10× faster, 6.6 GB. The Q8 verdict is void. |
| **A dedicated `MATH_MODEL` at all** | **retired — no measured advantage** | On business arithmetic it **ties** the warm utility model (30/30 each) while costing a 6.6 GB on-demand load that evicts a warm model — so the tie goes to the resident runner. Its only possible edge was hard math, and that was **not established**: the hard arm scored stdout by parsing numbers, and on hard problems it scraped unexplained values, so its model ranking is **void** (see `experiments/business_math/verification.md`). `MATH_MODEL` now points at the utility runner. |
| **The completion nudge (`nimble`)** | **opt-in, off by default** | It fires only on a turn that already needed an action, ran a tool, and *still* left it undone. `experiments/nudge_bound/` found the **first** nudge fired on **0 of 10** live write-class turns, so the second is rarer still — and it needs a ~9.5 GB model that `tev1:0.8b` cannot replace. `DECISION_COMPLETION_MODEL=nimble` re-enables it. |

**The pattern worth remembering:** four probes, **zero** net additions to the fleet. The
instinct has been to reach for another model or another guard; the measurement has said
the simple setup was fine every time. Propose, pre-register, and expect to be told no.

## Re-assessment cadence

- **Trigger:** a new notable Ollama model family (bigger qwen, llama, mistral,
  mini-embedding refresh), or a measured latency regression on the hot path
  (perf timing logs).
- **Scope:** re-run the assessment process above for affected roles; prefer
  *laddering* (escalate/swap one role at a time) over wholesale fleet changes.
- **Rollback:** each candidate stays behind its env var — a bad swap is a
  one-line revert in `.env`, no code change.