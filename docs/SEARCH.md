# Search — architecture and contracts

How the assistant identifies a search target, retrieves it, learns from it, and
renders its imagery. Search is one of the project's **two sources** (the user and
the internet); this file is the map so the plumbing is maintained, not
rediscovered. Read it before changing anything on the search path.

## The pipeline

```
user turn
  → Router        task_router.route → classify_with_llm (utility model)
                  → task_type, wants_search, search_query
  → Reasoner      reasoner.classify_intent (heuristic, no LLM) → plan.search_needed
  → Veto / force  storage-style turns veto search; force_search (scheduled) overrides;
                  user-supplied content de-prioritises search
  → QUERY         classification.search_query  OR  distill_search_query(message)
                  (skip the search entirely when the distiller declines)
  → Backend       WebSearchTool → SearXNGBackend (default) | BraveBackend (opt-in)
                  web results + videos (+ Brave image index)
  → Filter        filter_relevant (embedding-distance gate)
  → Extract       snippets (+ Brave full-page fetch) → slots/associations → memory
  → Prompt        results + "Facts you just stored this turn" → system prompt
  → Media         search_info.image_results → media.ts → the message hero
```

Each stage's home:

| Stage | File |
|---|---|
| Router | `pipeline/task_router.py` (`classify_with_llm`, `ClassificationResult`) |
| Reasoner | `pipeline/reasoner.py` (`classify_intent`, `Plan.search_needed`) |
| Query selection + orchestration | `pipeline/orchestrator.py` (`_run_turn`, "7b. Execute search") |
| Query distillation / sanitising | `pipeline/search.py` (`distill_search_query`, `sanitize_query`) |
| Backends | `pipeline/search.py` (`SearXNGBackend`, `BraveBackend`, `WebSearchTool`) |
| Relevance gate | `pipeline/search.py` (`filter_relevant`) |
| Extraction | `pipeline/extractor.py` (`extract_facts_from_search`, `extract_facts_from_document`) |
| Media selection | `frontend/src/utils/media.ts` (`searchMedia`, `getHeroMedia`) |
| Image proxy / OG preview | `backend/main.py` (`/image-proxy`, `/og-preview`) |

## 1. The query is distilled, never the raw message

**This is the rule that makes search find the target.** The query that leaves is:

```python
query = classification.search_query or await distill_search_query(request.message, llm)
if not query:
    plan.search_needed = False   # no concrete external target: do not search
```

- The **router** emits `search_query` when it wants search — a short keyword query.
- When the router produced none (forced/scheduled search, `skip_route`, a vetoed
  router), **`distill_search_query`** turns the request into a focused query with
  the utility model, **or returns `None`** when there is no concrete external
  target ("prices for these items", "findings first") — and the turn does not
  search at all.
- **Do not fall back to `sanitize_query(request.message)`.** `sanitize_query` only
  strips conversational filler; it cannot turn an instruction into a target. That
  fallback is how the engine came to search the assistant's *own task scripts*
  ("Monitor and alert for Mozilla release dates…") and return "how to search"
  results. `sanitize_query` is still applied to whatever leaves (filler, length
  cap) — it is the last step, not the fallback.

Distiller prompt contract (`_DISTILL_PROMPT` in `search.py`): one query, concrete
subject, instruction words dropped, `NONE` when there is no concrete target.

## 2. When search runs (and when it does not)

`plan.search_needed` is set by the reasoner (`task_type == "search"`, or memory
sufficiency `NONE` on an info-seeking query) and forced for scheduled tasks
(`force_search`, because a standing task must not answer from yesterday's frames).
It is **suppressed** by:

- the **router veto** for storage-style turns (`wants_search is False`),
- **user-supplied content** — a turn that transforms content the user pasted
  answers from that content; search must not become the subject.

**Known sharp edge (measured, `assistant/experiments/search_trigger/`):** the
router's `wants_search` is a model judgment. On unambiguous external queries it is
reliable — must-search recall **8/8 (100%)**, must-not specificity **7/7 (100%)**.
The gap is **borderline** queries ("explain recent research…", "best X for
beginners"): the router judges them general knowledge and vetoes search, even
where a current source would answer better. The trigger is not systematically
suppressed; the router under-searches borderline queries.

## 3. Backends

| | Default | Opt-in |
|---|---|---|
| Web | **SearXNG** (`SEARCH_BASE_URL`, local) | **Brave** (`BRAVE_ENABLED` + `BRAVE_API_KEY`) |
| Images | — | **Brave image index** (`search_images`) |
| Videos | SearXNG video results | Brave `videos` section (free) |

`WebSearchTool` picks the backend from config; `SearchBackend` is the ABC. A new
backend implements `search()`; `search_images()` defaults to `[]` so a backend
without an image index needs no change.

**Brave is the only permitted non-local backend** (it does not profile users or
sell query data). A **sensitive query is gated by consent** *before* it is sent
(`classify_query_sensitivity` → `consent_required`); the search does not execute
until the user consents. This is a hard privacy rule — see the root `AGENTS.md`.

## 4. The relevance gate

`filter_relevant` drops results whose embedding sits below the threshold
(`SEARCH_MIN_RELEVANCE` 0.30 for SearXNG, `BRAVE_SEARCH_MIN_RELEVANCE` 0.20 for
Brave). It is **graceful by design**: if the embedder fails, all results pass, so a
dead embedder never blinds search. Brave extracts more aggressively (larger budget,
lower threshold, optional full-page fetch) because its index is cleaner.

## 5. Learning from the target (extraction and corroboration)

Search-derived facts enter memory only when they are accurate and useful:

1. **Extract from snippets first** (`extract_facts_from_search`), then **fetch the
   top Brave pages** and extract from full content (`extract_facts_from_document`).
2. **Corroborate** — a fact from multiple independent domains gets a
   source-reliability bonus; high-stakes facts require ≥2 domains or are flagged.
3. **Dedup** against conversational slots by `(frame_name, value)`.
4. **Conflict and audit** — the confidence ladder auto-resolves; `slot_history` and
   the `conflicts` table preserve both sides.
5. **Per-slot provenance** — every slot carries `source_urls` and `source_domains`.

## 6. Imagery

Search is the **only** source of imagery; nothing is rendered from memory. Two
sources feed the hero, in order:

1. **Brave image index** (`image_results`) — images that match the *query*, with a
   reliable ~500px Brave-CDN copy (`thumbnail.src`) and the source image
   (`properties.url`). Preferred.
2. **Web results' og:image** — the page author's social-share image, ~200px
   preview and often site chrome; logo/junk-filtered.

`media.ts`: `searchMedia` builds the list (image index first), `getHeroMedia`
**prefers a distinct full-resolution image** (a ~200px preview upscaled to the
message width is blurry). A preview-only hero gets `is-low-res` and renders near
its natural size rather than being stretched.

Measured (`assistant/experiments/image_quality/`): web-result previews are always
~200px; ~76% of web results carry a distinct full image at 1200–2048px; only ~9%
of full images are hotlink-blocked. Brave image thumbnails are 500px and reliable.

## 7. Config (`.env`)

`SEARCH_BASE_URL`, `SEARCH_TIMEOUT`, `SEARCH_LANGUAGE`, `SEARCH_SAFESEARCH`,
`SEARCH_MIN_RELEVANCE`; `BRAVE_ENABLED`, `BRAVE_API_KEY`,
`BRAVE_SEARCH_MIN_RELEVANCE`. See `.env.example`.

## 8. Measuring (do this before changing the trigger or the gate)

- **Search-trigger rate** — does a query that should search actually search?
  Measured once (`assistant/experiments/search_trigger/`): **100% recall on
  unambiguous must-search**, 100% specificity on must-not-search. **Still open:**
  the **borderline** class (research / recommendation queries), where the router
  under-searches — pre-register a separate probe before biasing it toward search.
- **Corroboration** — from live searches, the share of extracted slots with ≥2
  independent `source_domains`; single-source facts are claims, not knowledge.
- The query that actually left is logged: `Reasoner triggered search for: …`, and
  `Search extraction: N slots, M assocs`. Read these before and after a change.

## Related

- `docs/FETCHING.md` — `fetch_url` and robots.txt policy.
- Root `AGENTS.md` (Web search) and `assistant/AGENTS.md` (Search Learning).
- `docs/MODEL_SELECTION.md` — the model behind extraction.
