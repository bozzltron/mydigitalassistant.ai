# Cognitive Digital Assistant — Project Plan

**Status:** draft  
**Last updated:** 2026-08-08  
**Goal:** Build a privacy-first, Python-based cognitive agent that remembers, reasons, searches when necessary, and continuously corrects its world model.

This is a personal project that may be shared later. The plan is intentionally balanced: a tight MVP, room to grow, and not over-engineered.

---

## 1. MVP Requirements

The agent must satisfy six core behaviors:

1. **Accept prompts and respond** — natural language chat via CLI (FastAPI backend).
2. **Efficient memory for useful recall** — structured semantic memory (frames, slots, associations) plus episodic memory.
3. **Memory has priority** — priority is separate from confidence; users can elevate priority explicitly (e.g. "remember this forever").
4. **Search only when needed** — reason about whether memory, local LLM inference, or web search is required.
5. **Learn from the web** — facts discovered via search are stored in memory for faster future recall.
6. **Error correction** — the agent refutes bad information automatically and accepts user corrections; truth is preserved and falsehoods decay or are removed.

---

## 2. MVP Scope

### In scope (must ship)

- Memory with priority separate from confidence; user can say "remember this forever."
- Basic reasoner: decide whether to answer from memory, search, or both.
- Web search that learns and stores results.
- Automatic conflict resolution + user corrections.
- Multi-user with shared household memory and private frames.
- Docker deployment.
- Pluggable local Ollama models.
- All 177 existing tests still pass; security verification still passes.

### Out of scope (future)

- Reranking models, cross-encoders, advanced RAG.
- Voice / audio I/O.
- Mobile or web UI.
- Multi-host sync.
- Cloud inference.
- Fine-tuned models.
- Per-user trust levels, child-safety knobs.
- Automated garbage collection.
- Schema version migration tooling.

### Quality bar

- Code passes `ruff check .` and `pytest assistant/tests/`.
- `assistant-verify-security` passes.
- `docker compose build && docker compose up` works.
- A new contributor can read the plan, clone the repo, and be productive.

---

## 3. Current State Assessment

- **All 177 unit/integration tests pass** (`pytest assistant/tests/`).
- **Security verification passes** (`assistant-verify-security`).
- **Ollama is reachable** on `127.0.0.1:11434`.
- Core memory store, retrieval, conflict resolution, task router, extractor, orchestrator, and CLI are functional.

Known cleanup items (Milestone 0):

- Docker build context mismatch (compose at root, Dockerfile under `/assistant/`).
- Ruff lint failures (line-too-long, N999 module-name warnings).
- Duplicate `_get_conflict_row` method in `store.py`.
- One spurious `@pytest.mark.asyncio` on a non-async test.

---

## 4. Cognitive Subsystems

| Subsystem | Responsibility | Primary Module(s) |
|---|---|---|
| **Semantic Memory** | Long-term world model: frames, slots, associations, confidence, priority, sources. | `memory/store.py`, `memory/models.py` |
| **Episodic Memory** | Per-user conversation history linked to touched frames. | `memory/store.py` |
| **Attention / Retrieval** | Rank memory by relevance, confidence, priority, recency. | `memory/retrieval.py` |
| **Reasoner** | Decide whether memory, LLM inference, or web search should answer the query. | `pipeline/reasoner.py` |
| **Response** | Generate grounded responses with citations. | `pipeline/llm_client.py`, `pipeline/orchestrator.py` |
| **Learning** | Extract facts from conversations and search results; refresh embeddings. | `pipeline/extractor.py` |
| **Truth Maintenance** | Detect contradictions, resolve by confidence/priority/source, accept user overrides. | `memory/confidence.py` |
| **Controller** | Orchestrate the full cognitive loop. | `pipeline/orchestrator.py` |

---

## 5. Architecture Decisions

- **Keep and evolve the existing codebase.** Refactor and extend; do not rewrite.
- **Local-first, privacy-first.** Ollama on `127.0.0.1:11434`, SearXNG on `127.0.0.1:8080`, no cloud APIs, no telemetry, no phone-home.
- **Multi-user from the start.** Shared household frames plus per-user private frames. Episodic memory is per-user.
- **Pluggable local models.** Chat, utility, and embedding models selected via `.env`. Any Ollama-compatible model works.

---

## 6. Memory Model

### Core entities

- **Frame** — entity, concept, event, or household object. Has `name`, `type`, `confidence`, `priority`, `owner_user_id` (nullable for shared).
- **Slot** — key/value attribute on a frame. Has `confidence`, `priority`, `source_type`, `source_url`.
- **Association** — typed relation between two frames. Has `confidence`, `priority`.
- **Episode** — a conversation turn, per-user, per-session, with links to frames touched.
- **Conflict + Slot History** — audit trail of every change and contradiction.

### Priority vs confidence

- **Confidence** is the agent's estimate of how *true* a fact is. New = 0.5; repeat bumps it. Existing formula: `1 - (1 - conf) * 0.7`, capped at 0.99.
- **Priority** is the agent's estimate of how *important* a fact is. Default 0.3; "remember this" boosts it; "remember this forever" sets it to 1.0 and marks the fact essential.
- Priority affects retrieval ranking and what gets surfaced. Confidence affects whether a fact is believed.

### Source tracking

Each slot records where it came from: `source_type` (`user`, `search`, `inference`, `manual_override`), `source_url`, `source_episode_id`, and a `source_reliability` score. Search facts start at 0.5; user-confirmed facts reach 0.9.

### User scoping

- `frames.owner_user_id = NULL` → shared household frame.
- `frames.owner_user_id = <user_id>` → private frame visible only to that user.
- Retrieval returns shared frames plus the current user's private frames.

---

## 7. Reasoning & Search

### Reasoner

The reasoner classifies the user turn and produces a plan. It does not need to be elaborate in the MVP:

- **Question about what the agent knows** → answer from memory.
- **Question about the world, current events, or external info** → try memory; if coverage is low, search.
- **Correction** → route to truth maintenance.
- **"Remember this"** → store with elevated priority.
- **Social / conversational** → light LLM response, no memory operation.

A simple `Plan` dataclass is enough: `intent`, `use_memory`, `use_search`, `use_llm`, `memory_focus`, `search_queries`. The "memory coverage" score can start as a weighted sum of `similarity × confidence × priority`; refine later.

### Search

Search is the primary way the agent learns about the world beyond what the user has told it. It is also the most privacy-sensitive subsystem, so the choice of search backend matters.

**Backend options (ranked by privacy fit):**

| Backend | Privacy | Quality | Cost | Setup |
|---|---|---|---|---|
| **SearXNG** (self-hosted) | Best — all traffic stays on-device | Good (aggregates Google/Bing/DDG/Brave) | Free | One Docker container |
| **Brave Search API** | Good — Brave doesn't track, but queries leave the device | Excellent | Free tier: 2,000 queries/month, 1/sec | API key in `.env` |
| **DuckDuckGo Instant Answer** | Good but very limited — returns structured data, not general web results | Poor for general queries | Free | None |
| **Google / Bing / Tavily / SerpAPI** | Poor — cloud, tracks, violates the project's privacy posture | Excellent | Paid | API key |

**Recommendation: SearXNG as the primary backend**, with the code structured so a second backend (e.g. Brave) can be added without changing the reasoner. This is what the current code already assumes.

DuckDuckGo is **not** a real general-web search API — it has no API-key search endpoint. The Instant Answer API only returns structured data (calculations, definitions, Wikipedia summaries). Community scraping libraries (e.g. `duckduckgo-search`) violate DuckDuckGo's ToS and break unpredictably. Avoid it for an agent that needs to learn reliably.

**Search-trigger rules:**

- Trigger only when needed: time-sensitive queries, explicit search verbs, or low memory coverage on an external-looking question.
- Do **not** trigger for introspective recall or when high-confidence memory already answers.
- Use the utility model to extract facts from snippets and store them as frames with `source_type=search` and `source_url`.
- If the backend is down or returns nothing, say so — never fabricate.
- Treat search-derived facts as provisional (initial confidence 0.5, source reliability 0.5). Corroboration from multiple independent sources bumps both.

---

## 8. Error Correction

- **Automatic:** when a new slot value differs from the existing one, create a conflict. Resolve by source reliability, then confidence, then priority, then recency. The losing value moves to `slot_history`.
- **User correction:** users can correct via chat or CLI. User-stated facts are treated as high-reliability and override automatic resolutions.
- **Essential facts** (`priority=1.0`) are protected unless the user revokes them.

Decay and automated garbage collection are **future** — the MVP supports soft-delete but does not run GC.

---

## 9. Multi-User

- `users` table: `id`, `name`, `created_at`. Names unique within the household.
- Episodes are per-user. Frames are shared by default; private frames carry `owner_user_id`.
- Shared household frames cover "our Wi-Fi", "the dog's vet", etc. Private frames cover "my password hint", "my health notes".
- The MVP assumes the household trusts itself; no per-user trust levels.

---

## 10. Model Strategy

```bash
CHAT_MODEL=qwen2.5:7b
UTILITY_MODEL=qwen2.5:3b
EMBEDDING_MODEL=nomic-embed-text
```

Recommendations are starting points, not commitments. Swap in `.env`; do not hard-code. Run `ollama list` to see what's already pulled and `ollama pull <model>` to add more.

### Default roles

| Role | Recommended | Notes |
|---|---|---|
| **Chat** | `qwen2.5:7b` or `llama3.1:8b` | Good instruction following; 8–16 GB VRAM. |
| **Utility** | `qwen2.5:3b` | Fast, sufficient for JSON extraction. |
| **Embeddings** | `nomic-embed-text` | 768-dim, high quality. |

### Alternatives worth trying

| Role | Option | Why |
|---|---|---|
| **Structured-output chat** | `qwen2.5-coder:7b` or `qwen3-coder:30b` | Better at JSON / schema generation than general chat models. |
| **Stronger chat** | `qwen2.5:14b`, `qwen2.5:32b`, `phi4:14b` | Better reasoning; needs more VRAM/RAM. |
| **Efficient chat (newer gen)** | `qwen3:8b`, `gemma3:4b`, `gemma3:12b` | Newer than qwen2.5; better quality per parameter. |
| **Reasoning** | `deepseek-r1:7b` (already pulled) | Good for multi-step reasoning; slower than non-thinking models. |
| **Very low resource** | `qwen2.5:1.5b`, `llama3.2:1b`, `gemma3:1b` | CPU-only machines. |

### On deepseek-r1:7b

It is still the current DeepSeek reasoning model. There is no `deepseek-r2` yet, so there is nothing to "update" to. Keep it. If you want more reasoning capability and have the VRAM, consider `deepseek-r1:14b` (newer size in the same family). For a smaller, faster option, `deepseek-r1:1.5b` exists but loses most of the reasoning quality.

**Note on thinking models:** `deepseek-r1:*` and `qwen3:*` (with `:thinking` tag) emit reasoning tokens before their answer. This can double token usage and latency. They are excellent for the reasoner (which does multi-step planning) but wasteful for simple utility extraction — keep them on the chat role, not the utility role.

### Privacy note

**Avoid** any model with `:cloud` in its name (e.g. `kimi-k3:cloud`, `gpt-oss:cloud`). These route data off-device and violate the privacy posture, even if Ollama hosts them.

### When JSON extraction is unreliable

Switch the utility role to a coder-tuned or instruction-tuned variant (`qwen2.5-coder:3b` or `qwen3:4b`) before changing application code. The current code already retries on parse failure, so most issues are model quality, not code.

---

## 11. Docker

Docker is the preferred deployment because it abstracts Python dependencies and sqlite-vec setup.

- Host port mapping: `127.0.0.1:8000:8000`.
- Inside the container uvicorn binds `0.0.0.0` (required for container networking); document the exception in `SECURITY.md`.
- **SearXNG** is the search backend. Add it to compose as a service on `127.0.0.1:8080` (also bound to localhost inside the container). The backend container reaches it via the Docker network at `http://searxng:8080` (or whatever service name you choose); the host-side address stays `127.0.0.1:8080`.
- `docker compose build && docker compose up` should just work for the backend. SearXNG is opt-in (start with `docker compose --profile search up` or similar) so users who do not need search do not pay its cost.

Milestone 0 fixes the Dockerfile so the image can import `assistant.backend.main` correctly. Milestone 3 wires SearXNG into compose and the search pipeline.

---

## 12. Implementation Roadmap

Five milestones. Each one ships something usable on its own.

### Milestone 0 — Hygiene (quick)

- Fix Docker build context (workspace-root Dockerfile, correct COPY paths).
- Fix ruff lint failures and remove the duplicate method.
- Remove the spurious `@pytest.mark.asyncio` warning.
- `pytest` and `ruff` stay green.

### Milestone 1 — Richer Memory

- Schema: add `priority`, `owner_user_id`, `source_type`, `source_url`, `source_reliability` to frames/slots/associations.
- Priority math: "remember this" boosts; "remember this forever" sets to 1.0 + essential; "forget this" soft-deletes.
- User-scoped frames: retrieval filters by user.
- Tests for priority, source tracking, user scoping.

### Milestone 2 — Reasoner & Memory-First Retrieval

- New `pipeline/reasoner.py` with a simple `Plan` and intent classification.
- Retrieval ranks by `similarity × confidence × priority` (add recency later).
- System prompts instruct the LLM to ground answers in memory and cite frames.
- Tests: memory-sufficient vs memory-insufficient paths.

### Milestone 3 — Web Search That Learns

- Add SearXNG as a service in `docker-compose.yml` (profile: `search`) on `127.0.0.1:8080`.
- Abstract the search backend behind a small interface so a second provider (e.g. Brave Search API) can be swapped in without changing the reasoner.
- Replace the keyword heuristic with reasoner-driven search.
- Extract facts from snippets into frames with `source_type=search` and `source_url`.
- Corroboration: same fact from multiple independent sources bumps confidence.
- Cite search sources in responses.
- If SearXNG is down or returns nothing, the agent says so — never fabricates.
- Tests: search-then-learn-then-recall, no-search-when-memory-sufficient, search-failure-handled.

### Milestone 4 — Self-Correction & Multi-User Polish

- Contradiction detection across slots and sources; resolution by source reliability > confidence > priority > recency.
- User override commands and chat-based correction parsing.
- Multi-user validation: cross-user isolation, shared household frames.
- Smoke tests: learn → recall, false fact → correct → recall truth, current event → search → learn → recall.

---

## 13. Testing Strategy

- Keep the existing 177 tests green at all times.
- Add unit tests for each new module.
- Add e2e tests for: learn-then-recall, contradiction-then-resolve, search-then-learn-then-recall, user-correction, multi-user isolation.
- `assistant-verify-security` runs as part of Milestone 0.
- `pytest assistant/tests/` and `ruff check .` are the gate before considering a milestone done.

---

## 14. Long-Term Goals

Ideas that are *not* in the MVP but are worth keeping in mind. Not commitments — signposts.

- **Working memory** as a distinct module (recent turns, active frames).
- **Belief revision** with formal AGM-style guarantees.
- **Source reliability** with domain allowlists and cross-source consensus.
- **Garbage collection** with Ebbinghaus-style priority decay.
- **Embedding model migration** with a versioning column and background re-embed.
- **Per-user trust levels** for child-safe use.
- **Honest refusal** when memory, search, and LLM are all unavailable.
- **Reranking** with a cross-encoder for high-precision recall.
- **Voice / audio I/O** via local Whisper.
- **Web UI** for memory inspection.
- **Local multi-host sync** (if the user ever wants to run it on multiple devices).

---

## 15. Considerations Appendix

Context that informed the design but is not a specification.

### Historical & scientific lineage

- Minsky's frames (1975) — the frame/slot/association model is directly inspired.
- ACT-R (Anderson, 1990s) — declarative vs procedural memory; activation and spreading.
- Spreading activation — the graph-walk decay mirrors this.
- Ebbinghaus forgetting curve — informs future priority decay.
- MYCIN certainty factors (Shortliffe, 1970s) — combining evidence from multiple sources.
- AGM belief revision (1985) — formal axioms for rational belief update.
- Retrieval-Augmented Generation (Lewis et al., 2020) — our architecture is RAG over a structured, mutable knowledge base.
- Self-RAG (Asai et al., 2023) — the reasoner's memory-coverage score is a lightweight self-evaluation.
- Local-first software (Ink & Switch, 2019) — privacy posture.

### Edge cases & failure modes (awareness, not a checklist)

The MVP should not crash on these. Many can be handled as they come up.

- **Perception:** empty input, ambiguous intent, implicit correction without a value.
- **Memory store:** frame-name collisions, slot-value size, circular associations, embedding dimension changes.
- **Retrieval:** empty database, very long queries, `sqlite-vec` not loaded (fall back to brute-force cosine).
- **Reasoner:** all resources unavailable; memory and search disagree.
- **Search:** SearXNG down, no results, dead URLs, prompt-injection attempts in results.
- **Extraction:** malformed JSON from the LLM, hallucinated facts.
- **Truth maintenance:** user self-contradiction, two users disagreeing on a shared fact.
- **Multi-user:** cross-user data leakage, attribution of corrections.
- **Safety:** secrets stored, prompt injection, the LLM emitting code.
- **Operational:** Ollama down, SQLite locked, schema version mismatch, memory leaks.

These are not gating tests. Address them when they become real problems.

---

## 16. Next Step

Begin **Milestone 0**: fix the Docker build context, clean up lint warnings, remove the duplicate method, drop the spurious pytest warning, and confirm `pytest assistant/tests/` and `ruff check .` stay green. The existing 177 passing tests and passing security checks mean this is hygiene work, not a rescue mission.
