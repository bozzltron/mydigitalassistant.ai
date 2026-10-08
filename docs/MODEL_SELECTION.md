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
| Math | `MATH_MODEL` | `compute` tool: writes Python for sandboxed exec | on-demand | tools + codegen quality |
| Coder | `CODER_MODEL` | Reserved for tool codegen | — | empty = chat model handles it |

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

## Current fleet (Sept 2026)

| Role | Model | Size | keep_alive | num_ctx |
|------|-------|------|------------|---------|
| Chat | `qwen3.5:9b` | 6.6GB | global (30m) | 16384 |
| Tools | `qwen3.5:9b` | — shares chat | `-1` | shares chat (16384) |
| Utility | `qwen3.5:4b` | 3.4GB | global | 4096 |
| Embedding | `qwen3-embedding:0.6b` | 0.6GB | — | — |
| Max | `qwen3.8:27b` | 17GB | `10m` | 16384 |
| Math | `qwen3.8:27b` | — shares max | `10m` | 16384 |
| Coder | (empty → chat) | — | — | — |

Rationale highlights:

- **9b as both chat and tools** — one brain, shared hot KV cache, tool loop stays
  warm and fast. Because they are the same loaded runner they share one context
  window: `CHAT_NUM_CTX` (16384), not the smaller `TOOLS_NUM_CTX`, which applies
  only to a *distinct* tools model. The tool loop sends the system prompt + every
  tool schema + history — the largest prompt in the system — so 8192 left no room
  to generate and the turn finalized empty (incident 2026-10-01).
- **4b utility** — cheap extraction/routing fallback that never slows the hot path.
- **27b shared by max + math** — a single on-demand load serves both escalation
  and exact computation; compute adds zero extra resident RAM.
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

**Decision:** keep `qwen3.5:9b` (chat/tools) and `qwen3.5:4b` (utility); prefer
`qwen3.5-claude-4.6-opus-q4:9b` for the **math** role when it is enabled —
equal accuracy on the probe, far faster, and 10 GB smaller than the 27B.

**Caveats:** the probes are small (one chat prompt, five extraction turns, eight
easy math problems, and a 10-prompt blind quality run whose judge flipped on 3 of
10 pairs). The math tie does not prove superiority on hard problems, and the chat
quality run found no win but is too small and too noisy to be conclusive — a swap
would need the larger run described in
`assistant/experiments/chat_quality_q4/result.md`.

## Re-assessment cadence

- **Trigger:** a new notable Ollama model family (bigger qwen, llama, mistral,
  mini-embedding refresh), or a measured latency regression on the hot path
  (perf timing logs).
- **Scope:** re-run the assessment process above for affected roles; prefer
  *laddering* (escalate/swap one role at a time) over wholesale fleet changes.
- **Rollback:** each candidate stays behind its env var — a bad swap is a
  one-line revert in `.env`, no code change.