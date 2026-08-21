# Phase 6 Plan — Model Fleet Re-Evaluation & Reasoning Wiring

**Status:** in planning
**Last updated:** 2026-08-21 (v3 — local cleanup staging + code-replacement scope)
**Hardware target:** MacBook Pro M4 Pro, 48GB unified memory · Ollama 0.32.15 · 278GB disk free
**Prior plans:** Removed (see git history: `ARCHIVED_PROJECT_PLAN.md`, `PHASE_5_TRANSPARENCY_TOOLS_PORTABILITY.md`). Open Phase-5 items carry into M5.

---

## 1. Summary

Re-evaluated every slot against the August 2026 model landscape. Two structural conclusions
replace the original six-model draft:

1. **Reasoning is a *mode*, not a *model*.** Qwen3.8-27B (released Aug 14 2026) has built-in,
   switchable thinking (`GPQA-D 89.2`, `AIME 2026 94.1`) that outruns any dedicated reasoner
   we could co-resident with it — including `deepseek-r1:14b` (a Feb-2025 distill). We drop
   REASONING_MODEL and wire thinking-mode escalation onto the chat model itself.
   This also solves the RAM problem: one 18GB model instead of 17GB + 9GB fighting for Metal.
2. **Consolidate small roles.** Router collapses into the utility model (classification is
   trivial); tool-calling defers to the chat model's own coding scores (SWE-Bench Pro 61.7),
   with the already-downloaded `qwen3-coder:30b` held in reserve.

Net fleet: **3 active roles, ~22GB resident worst-case**, down from 6 roles / 40.6GB weights.

## 2. What Changed and Why

| Role | Old pick | New pick | Why |
|---|---|---|---|
| CHAT | qwen3.6:27b | **qwen3.8:27b** | Released 2026-08-14; sizable gains over 3.6 (SWE-Bench Pro 53.5→61.7, GPQA-D 87.8→89.2, LiveCodeBench v6 90.3). Same 18GB footprint. MLX tag available for Apple Silicon. |
| REASONING | deepseek-r1:14b | **removed** — thinking mode of chat model | r1:14b is a Feb-2025 distill; qwen3.8's thinking beats it decisively while sharing an already-loaded model. Zero added RAM, zero model-swap latency. |
| UTILITY | phi4-mini | **qwen3.5:4b** | phi4-mini is Feb-2025 era. qwen3.5:4b (3.4GB): newer, thinking+tools, text+image, same family as chat model (consistent tool-call format). phi4-mini kept as A/B candidate. |
| ROUTER | llama3.2:3b | **merged into UTILITY** | Sep-2024 model; classification doesn't justify a second resident model. One fewer load/unload cycle. |
| EMBEDDING | mxbai-embed-large | **qwen3-embedding:0.6b** | Ties mxbai's English MTEB (64.33 vs 64.68) on a harder multilingual track, **32K context vs 512** (mxbai silently truncates), MRL-truncatable, actively maintained, family-consistent. Migration cost identical (both are 1024-dim vs current nomic 768). |
| TOOL_CALL | qwen2.5-coder:14b | **deferred** (reserve: qwen3-coder:30b) | qwen2.5-coder is mid-2024. qwen3.8's own coding benchmarks make a dedicated coder unnecessary for household-scale codegen until proven otherwise. Reserve model is **already pulled locally** (18.6GB). |

## 3. Verified Lineup (checked against Ollama library + local instance, 2026-08-21)

| Role | Tag | Size | Notes |
|---|---|---|---|
| CHAT | `qwen3.8:27b` | 18 GB (q4_K_M) | Dense 27.8B, hybrid linear+full attention, 262K native ctx, multimodal (text+image). Thinking default-on; per-request `"think": false` supported. Requires recent Ollama — we have 0.32.15 ✅. Optional `qwen3.8:27b-mlx` (same size, Apple-Silicon-optimized) worth benchmarking. |
| UTILITY | `qwen3.5:4b` | 3.4 GB | Thinking+tools+vision, 256K ctx label. Family-matched to chat model. |
| EMBEDDING | `qwen3-embedding:0.6b` | 0.64 GB | 1024-dim (MRL to 32), 32K ctx. Query side benefits from instruction-style prompt; document side bare. |
| reserve | `qwen3-coder:30b` | 18.6 GB | Already local. Only pulled into service at M5 if needed. |
| fallback | `qwen3.6:27b` | 17.4 GB | Already local. Instant rollback if qwen3.8 GGUF has teething issues (it is 1 week old). |

None of the three active picks are downloaded yet — total new downloads ≈ 22 GB.

## 3.1 Local Inventory — Cleanup Staging (16 models, 140GB on disk)

Drops are staged behind milestone gates: nothing is deleted until its replacement has
passed the eval gate that retires it. Immediate drops have no role in any milestone.

**Drop now (~96 GB reclaimed):**

| Model | Size | Why it's dead weight |
|---|---|---|
| `qwen3.5:35b-a3b-coding-nvfp4` | 21 GB | **NVFP4 is NVIDIA Blackwell-native FP4 — effectively unusable on Apple Metal.** Dead download on this hardware. |
| `laguna-xs-2.1` | 20 GB | Third coding MoE; redundant with qwen3-coder:30b + qwen3.8's own coding scores |
| `qwen2.5-coder:32b` | 19 GB | Mid-2024 dense coder; superseded twice over |
| `devstral:24b` | 14 GB | Agentic coder for IDE workflows we don't run |
| `deepseek-r1:7b` | 4.7 GB | Reasoning role eliminated (§2); 8 months unused |
| `mistral:latest` | 4.4 GB | Sep-2024 generalist, unused by pipeline |
| `llama3:latest` + `llama3.1:8b` | 9.6 GB | 2024-era generalists, superseded |
| `phi3:mini` | 2.2 GB | Two generations behind utility pick |
| `qwen2.5-coder:1.5b-base` | 1 GB | Base (non-instruct) completion model — can't chat |

**Keep through transition, drop when its gate passes:**

| Model | Size | Retire after |
|---|---|---|
| `qwen2.5:7b` | 4.7 GB | M4 verified (chat rollback) |
| `qwen2.5:3b` | 1.9 GB | M2 A/B gate passed (extraction baseline) |
| `nomic-embed-text` | 0.27 GB | M3: candidate failed gate; nomic stays (no rollback needed) |
| `llama3.2:latest` | 2 GB | M2 (router role eliminated entirely) |

**Keep long-term:** `qwen3.6:27b` (17 GB, fallback if qwen3.8 GGUF breaks — revisit in
30 days), `qwen3-coder:30b` (18 GB, M5 reserve).

## 4. Config Corrections (unchanged from v1 review, plus context note)

1. `CACHE_DIR` → **`OLLAMA_MODELS`** (wrong var name in draft).
2. `OLLAMA_MAX_VRAM` not usable on macOS unified memory → use `OLLAMA_MAX_LOADED_MODELS=2`,
   `OLLAMA_KEEP_ALIVE`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`.
3. `OFFLINE_ONLY` / `ALLOW_CLOUD_API` / `MODEL_AUTO_UPDATE` are not Ollama vars — implement in
   app Settings (enforced by security tests) or drop them.
4. Context defaults matter for KV sizing: Ollama defaults to **32K context on 24–48 GiB
   machines**. Set explicit `num_ctx` (e.g., 8–16K for chat turns) until long-context work
   demands more; verify actual allocation with `ollama ps`.
5. With this fleet, memory stops being the binding constraint: 18 + 3.4 + 0.64 ≈ 22 GB
   weights + KV fits comfortably; `NUM_PARALLEL` 2 is safe for the 27B.

### 4.1 Apple Silicon (M4 Pro) specifics — every pick above was filtered through these

1. **Quant formats:** NVFP4 is NVIDIA Blackwell-native FP4. It does not accelerate on
   Apple Metal — which is why `qwen3.5:35b-a3b-coding-nvfp4` (21 GB, already local) is a
   definite drop, not just a redundant one. Our fleet uses Q4_K_M GGUF (universal) with an
   optional MLX build (`qwen3.8:27b-mlx`) that exists *specifically* for Apple Silicon.
2. **Speed expectations from memory bandwidth:** M4 Pro has ~273 GB/s unified-memory
   bandwidth. Dense 27B at Q4 (~18 GB weights) decodes at roughly **12–17 tok/s**;
   prompt processing of injected memory context adds TTFT on top. This is exactly why
   §6 caps thinking escalations (<15% of turns, `num_predict` limits): a 500-token think
   chain costs ~30–40 s. The small models (qwen3.5:4b ≈ 60–100 tok/s) and the MoE reserve
   (`qwen3-coder:30b`, ~3B active ≈ 40–60 tok/s) are where this machine is genuinely fast.
3. **Memory ceiling:** macOS gives Metal roughly 65–75% of unified RAM by default
   (~31–36 GB of 48). If ever needed, `sudo sysctl iogpu.wired_limit_mb` raises it —
   but the §4 fleet budget means we should never have to.
4. **GPU placement:** Ollama runs natively on the host so Metal acceleration applies;
   containers (backend/CLI) reach it via `host.docker.internal` as today. Never move
   Ollama itself into Docker — the Docker VM on macOS has no GPU access.

## 5. Routing Matrix

| Pipeline stage | File | Today | New |
|---|---|---|---|
| Task classification LLM fallback | `pipeline/task_router.py:139` | utility_model (qwen2.5:3b) | utility role → `qwen3.5:4b` (heuristics still first) |
| Fact extraction / correction parse | `pipeline/extractor.py` | utility_model | utility role → `qwen3.5:4b` (A/B gated, see M2) |
| User-facing chat | `pipeline/orchestrator.py` | chat_model (qwen2.5:7b) | chat role → `qwen3.8:27b`, `think=false` fast path |
| Deep reasoning / planning escalation | `pipeline/reasoner.py` | heuristic-only | **thinking mode on chat model** (`think=true`), see §6 |
| Codegen for tools | M5 | — | chat role with thinking; escalate to reserve coder only if quality insufficient |
| Query/document embeddings | `memory/retrieval.py` | nomic (768d) | `qwen3-embedding:0.6b` after re-embed migration |

## 6. Reasoning Wiring Design (the core deliverable)

The reasoner stays a cheap, always-available **policy module**; heavy inference happens by
flipping the chat model into thinking mode — same loaded weights, no swap.

### 6.1 Client layer (`pipeline/llm_client.py`)
- `chat(..., think: bool | None = None)` → passes `"think"` through in the `/api/chat`
  payload (supported for thinking-capable models on Ollama 0.32.x).
- Parse response: prefer structured `message.thinking` field; fall back to stripping legacy
  `<think>...</think>` blocks (covers qwen3.6 fallback + deepseek-r1 if ever revived).
- Return shape: extend `ChatResponse` with `thinking: str` so callers can log/audit chains
  without leaking them to the UI.
- Optional `reasoning_effort` mapping (low/medium/high) for escalated calls; hard
  `num_predict` cap + timeout on every thinking call.

### 6.2 Escalation policy (`pipeline/orchestrator.py` + `reasoner.py`)
Default every turn to `think=false`. Escalate to `think=true` when any of:
- reasoner assesses retrieved memory as `PARTIAL`/`NONE` **and** the query is multi-step
  (existing heuristics in `reasoner.py` provide the signal);
- correction-validation is ambiguous (corroboration vs conflict both present);
- scheduled-task planning (cron composition, multi-constraint requests);
- explicit user intent ("think carefully", "step by step").

### 6.3 Multi-turn hygiene
Thinking content must NOT be fed back into subsequent turns' message history (provider
best practice); use `preserve_thinking` only inside agentic/scheduled multi-step flows.

### 6.4 Evaluation gate
Extend `eval/dataset.json` runs to grade three configs: baseline (today's models),
`think=false`, `think=true-always`. Tune the escalation trigger thresholds so
think=true fires rarely (<15% of turns) while accuracy matches or beats think-always.

### 6.5 Do the new models replace code? Yes — four concrete deletions/avoids

Reasoning itself needs no rethink — thinking-mode escalation stands. But the fleet
re-evaluation lets us delete hand-written NLP code that existed *because* old models
couldn't be trusted with these jobs cheaply:

1. **`scheduler/cron.py` `parse_schedule` (largest win).** A hand-rolled NL→cron parser
   ("every 30 minutes", "daily at 9am") built from regexes and vocab lists — classic
   brittle NLP that qwen3.5:4b replaces wholesale with one JSON structured-output call.
   It runs in the scheduler loop (not latency-critical), so LLM-first is safe here.
   Keep `parse_schedule` as offline fallback only.
2. **No tool-call client fork.** qwen3.8 has native `tools` capability — M5 uses the
   standard Ollama tools API on the chat model directly. The planned separate
   TOOL_CALL role/client never gets written; reserve coder is a config swap if ever needed.
3. **No router plumbing.** ROUTER_MODEL env/config/task-router wiring disappears;
   `task_router.py` keeps its free regex fast-path (per AGENTS.md rule) and sends
   ambiguous cases to utility — the heuristic pattern lists get **frozen**, not grown.
4. **No reasoner-LLM integration.** The original plan wired `reasoning_model` into
   reasoner.py with its own prompt/parsing path. That entire branch is replaced by the
   ~20-line `think=true` escalation in §6.1–6.2. Same for deepseek-r1-specific
   `<think>` plumbing beyond the shared strip helper.

Net: less pipeline code than today's design implies, not more.

## 7. Code Refactor Scope

1. **`backend/config.py`** — roles become `chat_model`, `utility_model`, `embedding_model`
   (+ reserved `coder_model`, unused until M5). Remove standalone `router_model`;
   `task_router.py` uses utility. Add `chat_think_default: bool = False`,
   `think_num_predict_cap`, escalation threshold settings.
2. **`pipeline/llm_client.py`** — §6.1 changes; drop flat ctor duplication by taking
   `Settings`; expose model capabilities probe (`/api/show`) so tests can assert
   thinking/tools support.
3. **`pipeline/task_router.py`** — LLM fallback → utility model.
4. **`pipeline/reasoner.py`** — becomes escalation-policy owner (§6.2); its existing
   `Plan`/`Action` types unchanged; no LLM call of its own.
5. **`pipeline/extractor.py`** — stays on utility role; JSON-mode conformance fixtures gate.
6. **`memory/retrieval.py` + `cli/db.py` re-embed command** — embedding swap (M3):
   metadata/dimension tracking already exists; add query-instruction prefix handling.
6b. **`scheduler/cron.py`** — LLM-first NL→cron via utility model JSON output;
    `parse_schedule` demoted to offline fallback (see §6.5.1).
7. **`main.py` /health + `cli/app.py status`** — report roles, thinking capability, and
   which model instance serves each role.
8. **`docker-compose.yml`** — un-hardcode models (`${CHAT_MODEL}` style).
9. **Tests** — MockLLM gains `think` handling + `ChatResponse.thinking`; new units for
   think-stripping, escalation policy, capability probe; update health assertions;
   security tests enforce `offline_only` if we adopt those flags.

## 8. Milestones

- **M1 — Plumbing (no behavior change).** Role renames, `think` param + parsing, health
  reporting, compose de-hardcoding, AGENTS.md fleet docs. Defaults still qwen2.5 pair.
- **M2 — Utility consolidation.** ✅ DONE (2026-08-21). Pulled `qwen3.5:4b` + `phi4-mini`;
  A/B via `python -m assistant.eval.utility_ab` (14 fixtures: facts/classify/scheduled,
  production call paths, `think=false`). Results: qwen3.5:4b 100% / 0 JSON errors
  (mean 1.41s), baseline qwen2.5:3b 85.7% / 0, phi4-mini 78.6% / 0 → **phi4-mini eliminated**,
  `UTILITY_MODEL=qwen3.5:4b` flipped in `.env`. All utility-role calls now send explicit
  `think=false` (qwen3.5 is thinking-capable; prevents reasoning chains on extraction).
- **M3 — Embedding migration.** ✅ EVALUATED (2026-08-21): **candidate FAILED gate, staying
  on `nomic-embed-text`**. Built `python -m assistant.eval.retrieval_ab` (12-frame synthetic
  brain, 20 paraphrase queries, production `search_similar_frames` path). Baseline nomic:
  hit@1 95% / hit@3 100% / MRR 0.975 (embed ~26ms). Candidate qwen3-embedding:0.6b:
  hit@1 85% / hit@3 100% / MRR 0.917 (embed ~33ms) → below the −5% floor (90%). Failures
  were razor-thin margins on ambiguous paraphrases ("my wife's birthday" → Anniversary vs
  Alice at Δ0.08); both models retrieve the right frame in top-3 always, but nomic also has
  lower latency and smaller vectors (768 vs 1024). No migration performed; no DB backup/reembed
  needed. Byproducts kept: (1) fixed latent crash in `store.search_similar_frames` where mixed-
  dimension rows from different embedding models could break distance computation depending on
  row order — now filtered via MATERIALIZED CTE + regression tests; (2) reusable retrieval A/B
  harness for future candidates; (3) measured insight: per-model absolute cosine distances are
  NOT comparable (qwen3 clusters ~0.5–0.85 vs nomic ~0.3–0.5), so a model swap would also
  require retuning `min_distance=0.7`. Follow-up idea (not done): nomic's documented
  `search_document:`/`search_query:` prefixes might lift baseline further.
  Rollback = repoint env var.
- **M4 — Chat + reasoning (coupled).** Pull `qwen3.8:27b` (fallback: local qwen3.6:27b).
  Ship §6 wiring: think=false default, escalation policy live. Measure TTFT/tok/s both
  modes; run §6.4 eval gate; benchmark MLX tag vs GGUF before committing.
- **M5 — Tools (carries Phase-5 leftovers).** Tool framework + reaction/correction UI
  polish. Codegen via chat-role thinking; promote reserve `qwen3-coder:30b` only on
  measured failure.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| qwen3.8 GGUF is 1 week old (telemetry of breakage unknown) | qwen3.6:27b already local as instant fallback; pin digest; smoke suite before flip |
| Thinking-mode latency spikes on escalation | `num_predict` caps, effort=low interactively, timeouts, <15% escalation-rate target |
| `<think>` leakage to UI | Central strip helper + `ChatResponse.thinking` separation + unit tests |
| qwen3.5:4b JSON-extraction regression | M2 A/B gate vs two baselines before default flip |
| Retrieval regression post embed swap | Recorded nomic baseline; per-model embeddings PK supports coexistence; env-var rollback |
| MLX vs GGUF divergence | Treat MLX as experiment; production default stays GGUF unless M4 numbers say otherwise |
| Ollama ctx default (32K) inflating KV on 48GB box | Explicit `num_ctx` per call class; verify with `ollama ps` |

## 10. Acceptance Criteria

- [ ] `./run_ci.sh` green (plain + encrypted)
- [ ] Learn-recall + contradiction-auto-resolve e2e pass on the new fleet
- [ ] Reasoning escalation demonstrably improves eval accuracy vs think=false at <15% trigger rate
- [ ] Zero `<think>` content reaches API responses/UI surfaces
- [ ] Retrieval eval within −5% of recorded nomic baseline or better
- [ ] `/health` reports all roles + thinking capability; CLI `status` matches
- [ ] Security tests pass; no cloud egress; no hardcoded model names outside config defaults/`.env*`
