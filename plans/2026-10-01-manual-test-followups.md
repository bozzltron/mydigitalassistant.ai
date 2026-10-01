---
date: 2026-10-01
status: active
estimated_hours: 17
---

# Plan F — Manual-test follow-ups

## Objective

Clear the queue from the 2026-10-01 manual test round: one confirmed backend bug, a set
of media-presentation fixes, a persistence gap, an infra split, and file-format read
support. Each item below was traced to a code location before it was scheduled; nothing
here is "sounds broken, look into it."

## Measured state (evidence, not inference)

| Item | Where it lives | What is actually wrong |
|---|---|---|
| `embed_frames ... EmbeddingResponse is not JSON serializable` | `scheduler/summarizer.py:232,255` | Passes `self.llm_client.embed` (returns `EmbeddingResponse`) where `embed_frames` wants `list[float]`. Every other call site wraps (`get_embedding`/`embed_text`/`embed_fn`). `OllamaClient.embed_one` already returns the bare vector. |
| Dev/prod collide | `docker-compose.yml`, `docker-compose.prod.yml` | Neither sets `name:`, so both resolve to the same Compose project, the same `assistant-data` volume, and the same `/app/data/assistant.db` — and both declare `container_name: assistant-backend`, so they cannot run at once (observed: `docker compose up` fails with a name conflict while the other stack is up). Prod also defaulted `EMBEDDING_MODEL=nomic-embed-text` against a `qwen3-embedding:0.6b` brain. |
| Media lost on refresh | `main.py:1250` | `GET /chat/session/{id}/messages` returns only `role, content, timestamp`. `search_info` is never persisted. The frontend already reads `s.search_info` on restore (`state/chat.ts`), so the contract is half-built. |
| Video query heroes an image | `utils/media.ts:174` | `searchMedia` pushes images before videos; `getHeroMedia` returns `media[0]`. |
| Many video iframes | `MessageContent.tsx:67` | `getExtraVideos` renders a full `MediaCard` (iframe embed) per video. |
| Grainy inline images | `MediaGrid.tsx:159`, `MediaCard.tsx` | Tiles render `item.thumbnail`; full-res is available as `fullUrl` and already proxied via `imageSrc`. |
| Binary uploads unreadable | `pipeline/files.py:314-327`, `main.py:502,643,2357` | No PDF/OOXML extractor; binary bytes fall through to `content.decode("utf-8", errors="replace")` → garbage. All three upload allowlists (chat, stream, `/files/upload`) reject them outright. |
| Input bar has no top shadow | `layout.css:17,59` | Header has a downward `box-shadow`; `#input-row` has only `border-top`. |
| External links open directly | `MessageContent.tsx:15`, `MediaGrid.tsx:173,272`, `MediaCard.tsx:126`, `FrameDetail.tsx:129` | All `target="_blank"` with no confirmation. |

## Relationship to other plans

Independent of Plan E (file support). Plan E is about the agent's *own* artifacts
(write/read/list consistency); this plan is about *reading files the user supplies*
(PDF/OOXML). They touch the same subsystem (`pipeline/files.py`) but different paths and
neither blocks the other — sequence them to avoid edit collisions, not for a dependency.

## Phases

### Phase 1 — Bug + trivial UI (~1.5 h)

The two-minute fix and the two one-liners, shipped first and separately.

1. **Summarizer embedding (R1).** `self.llm_client.embed` → `self.llm_client.embed_one`
   at both call sites.
2. **Input bar shadow (R2).** Mirror the header's shadow upward on `#input-row` so the
   transcript reads as scrolling under the bar at both ends.
3. **High-res inline images (R3).** The hero already renders `fullUrl ?? url`; the grainy
   images are the grid tiles (`MediaGrid.tsx:159`), which render `thumbnail`. Switch tiles to
   `fullUrl ?? url` and keep a per-tile thumbnail fallback on `onError` — the image proxy
   caps a fetch at 5 MB (`main.py:829`) and returns 413 above it, so a full-res fetch can
   legitimately fail. Keep `loading="lazy"`.

**Acceptance:** a summarizer run stores an embedding (no `stored 0 of 1` warning); the
input bar shows a top shadow that matches the header's bottom shadow; inline images render
at source resolution and still fall back to the thumbnail when a host blocks hotlinking.

### Phase 2 — Media presentation (~3.5 h)

1. **Grid tiles lose the overlay (R4).** The response hero keeps its caption overlay — it
   has the room. The smaller grid tiles do not: remove the hover `.grid-item-overlay`
   (title + source) from `MediaGrid`, leaving the image; details appear in the lightbox
   modal. Keep the hero caption and the lightbox caption. Preserve the tile `alt` text.
2. **Video-intent hero is the video (R5).** When the query matches `VIDEO_INTENT`, push
   videos before images in `searchMedia` so `getHeroMedia` returns the first video.
3. **One embed, the rest thumbnails (R6).** Hero video stays an iframe; the remaining
   videos render as thumbnail tiles with a play badge. Clicking a thumbnail swaps it into
   the single embedded player (never more than one live iframe).
4. **One formatting rule for dynamic content (R7).** Document and enforce a single order in
   `MessageContent`: the hero block (video gallery — player plus thumbnails — when the query
   asked for video, else the lead image) → body → image grid → sources, with consistent
   spacing from tokens.

**Acceptance:** a video query heroes the video; a multi-video response has exactly one
iframe and thumbnails for the rest; grid tiles show no hover overlay while the hero keeps
its caption; the ordering is pinned by a component test.

### Phase 3 — Persistence & link consent (~5 h)

1. **Persist the media payload (R8).** New `search_info TEXT` column on `episodes`
   (migration in `db/schema.py`, following `_migrate_add_reasoning_trace`). Store a
   **trimmed** payload — `backend`, `query`, `results[{url,title,thumbnail,image}]`,
   `video_results[{video_id,title,channel_title,thumbnail_url,url}]`, capped (default 20).
   Drop `snippet`/`engine` (the UI renders only `title`/`url`/`thumbnail`/`image`, and
   snippets are the large part). `create_episode` gains a `search_info` param; the
   orchestrator passes it for assistant turns where a search ran; `SessionMessage` gains
   `search_info: dict | None` (the frontend `SessionMessage` type gains it too —
   `state/chat.ts:95` already maps it).
2. **Confirm before opening external links (R9).** One delegated click handler for
   `http(s)` anchors with `target="_blank"` (markdown links render via `innerHTML`, so a
   handler on the container catches them). Show a confirm modal naming the hostname;
   open on confirm, do nothing on cancel. Reuse `ui/Modal.tsx`. Relative/same-origin links
   are exempt. Apply to `MessageContent`, `MediaGrid`, `MediaCard`, `FrameDetail`; this also
   covers the `**Sources:**` footer, whose links are markdown-rendered into the body.

**Acceptance:** reloading a session restores the images and video thumbnails from that
turn; clicking an external link asks first and opens a new tab only on confirm.

### Phase 4 — Infra & file support (~7 h)

1. **Separate the environments (R10).** Give each compose file its own `name:` — prod stays
   `mydigitalassistantai` (so it keeps the existing volume and brain), dev becomes
   `mydigitalassistantai-dev` with its own volume and network. Rename dev's shared
   containers (`assistant-backend-dev`, `assistant-cli-dev`) so both stacks can run at once.
   Dev sets `DATABASE_PATH=/app/data/assistant.dev.db`; seed its volume from a fresh
   `/db/backup` snapshot (a consistent copy, unlike a raw copy of a live WAL DB) owned by
   uid 1000. Prod keeps `assistant.db`. Bring `docker-compose.prod.yml` back in sync with
   dev (missing `CHAT_NUM_CTX`, `TOOLS_*`, `MAX_*`, `MATH_*`) and align its embedding model.
2. **PDF + modern Office read support (R11).** Add `pypdf`, `python-docx`, `openpyxl`,
   `python-pptx` (all pure-Python, offline — no new network calls). New extractors in
   `pipeline/files.py`; extend the dispatch map and all three allowlists
   (`main.py:502,643,2357`). Extraction is CPU-bound and synchronous inside an async handler
   (`main.py:2152`), so run it via `asyncio.to_thread` and cap PDF pages so one large
   document cannot stall the event loop. Legacy `.doc/.xls/.ppt` report "unsupported —
   re-save as .docx/.pdf" rather than decoding as UTF-8. Parse failures degrade to a clear
   message, never garbage.
3. **Run the daily tasks by hand (R12).** Exercise `POST /tasks/run-now/{task_name}` for
   each task (and `POST /tasks/run-due`), watch the logs, and confirm: task executes,
   `last_run` updates, the output summary stores, and no embedding warnings appear.

**Acceptance:** dev and prod run against different DB files with one key; a PDF, .docx,
.xlsx and .pptx each extract readable text; a `.doc` reports unsupported; each daily task
runs manually and lands a clean result.

## What this plan does not do

- **Does not change the upload/parse allowlist beyond read support.** No writing of Office
  formats.
- **Does not add a file-manager feature** — the Files page is out of scope.
- **Does not rework the lightbox into a video player** unless R6's swap-into-hero proves
  insufficient.
- **Does not touch Plan E's journal work.**

## Requirements

**Functional**

| # | Requirement | Phase |
|---|---|---|
| R1 | Summarizer embeds summary frames via a `list[float]`-returning callable; no `EmbeddingResponse` reaches `embed_frames`. | 1 |
| R2 | `#input-row` carries an upward shadow matching the header's downward shadow. | 1 |
| R3 | Inline hero and grid images use full resolution, with thumbnail fallback on error. | 1 |
| R4 | Grid tiles render the image only (no hover overlay); the hero keeps its caption overlay, and details appear in the lightbox modal. | 2 |
| R5 | A video-intent query heroes the first video, not the first image. | 2 |
| R6 | At most one video is embedded; the rest render as thumbnails. | 2 |
| R7 | Dynamic content follows one documented order and spacing rule. | 2 |
| R8 | Search/media payload is persisted on the assistant episode and returned on session reload. | 3 |
| R9 | External links confirm (naming the host) before opening a new tab. | 3 |
| R10 | Dev and prod are separate Compose projects (distinct container names and DBs) under one encryption key; prod compose env is current. | 4 |
| R11 | PDF, .docx, .xlsx, .pptx extract readable text; legacy binaries report unsupported. | 4 |
| R12 | Each daily task runs manually and produces a clean, stored result. | 4 |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | No new external calls, telemetry, or cloud APIs. All new deps are offline and import-safe. |
| N2 | No inline styles; shadows/spacing from tokens or component CSS (AGENTS CSS rules). |
| N3 | New-tab links keep `rel="noopener noreferrer"`. |
| N4 | Every fix gets a regression test that fails before it and passes after. |
| N5 | Clean ship: removed markup leaves no orphaned CSS or dead helpers. |
| N6 | High-res tiles stay lazy-loaded so first paint is not blocked. |
| N7 | The confirm modal is keyboard-accessible and focus-managed. |
| N8 | A full-res image that exceeds the proxy cap (5 MB, `main.py:829`) falls back to the thumbnail, not a broken tile. |
| N9 | File extraction runs off the event loop and is bounded, so a large document cannot stall other requests. |

## Technical approach

**R1.** `OllamaClient.embed_one(text) -> list[float]` already exists precisely for this
shape. Optionally harden `store_frame_embeddings` to unwrap an `EmbeddingResponse` as a
safety net; the call-site fix plus a summarizer regression test is the primary change.

**R8.** Trim at serialization time, not at render time: the stored payload is a projection
of `SearchInfo`, so the episodes row stays small and the privacy surface is unchanged
(URLs only, no page bodies — `snippet` is dropped). The migration is additive; old episodes
return `search_info = None` and render text-only, which is today's behaviour.

**R9.** A shared `externalLink` helper owns the confirm-then-open logic; components keep
plain `<a target="_blank" rel="noopener noreferrer">` markup and the delegated handler
intercepts. This keeps markdown-rendered links (which cannot carry a per-link handler)
covered by the same rule.

**R10.** The dev brain is seeded from a fresh `/db/backup` snapshot rather than a raw copy of
the live file: the DB is in WAL mode, so a plain `cp` can miss recent writes, while the
backup endpoint uses SQLite's backup API and produces a consistent encrypted copy that the
same `DB_KEY` opens. The copied file is chowned to uid 1000, matching the volume the backend
writes to.

**R11.** Each extractor is a small pure function `(bytes) -> (text, entities, questions)`
matching the existing signatures, so the dispatch map and `FileExtractionResult` are
unchanged. Libraries are imported lazily inside the extractor so a missing package
degrades that one format instead of breaking import of the module.

## Dependencies and ordering

- **Phase 1 is independent** and ships first (the bug is live and cheap to fix).
- **Phase 2 depends on nothing** but should land before Phase 3 so the persisted payload
  matches the final rendering rules.
- **Phase 3 R8 depends on Phase 2** only for payload shape.
- **Phase 4 R10 is independent**; **R11 shares `pipeline/files.py` with Plan E** — sequence
  them to avoid concurrent edits.
- Within Phase 4: R10 → R11 → R12.

## Test strategy

- `test_summarizer_embedding.py` — summary frame is created/updated with a stored
  embedding (fails on the `embed` vs `embed_one` bug).
- `test_media_ordering.py` (frontend) — video-intent hero is a video; exactly one embed;
  thumbnails for the rest.
- `test_session_media_restore.py` — a search turn round-trips `search_info` through
  `create_episode` → `GET /chat/session/{id}/messages`.
- `test_external_link_consent.tsx` — clicking an external link prompts; cancel does not
  navigate; confirm opens a new tab; relative links are exempt.
- `test_file_extractors.py` — pdf/docx/xlsx/pptx fixtures extract text; legacy `.doc`
  reports unsupported; a corrupt file degrades gracefully; a PDF over the page cap is
  truncated cleanly rather than hanging.
- Migration test — old episodes read back `search_info = None`.

Gates: `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py`,
then the full backend suite, then `ruff check .`; frontend `npm run check`.

## Principle alignment

| Principle | How |
|---|---|
| Stability: no regressions | Every item carries a regression test; R1's test fails on the current bug. |
| Clean ship | Hero caption removal drops its CSS; no orphaned helpers. |
| Visual feedback | Confirm modal and shadows are deliberate, token-based, brief. |
| Safety & Privacy | New-tab confirmation; payload stores URLs only, no page bodies; no telemetry; new deps offline. |
| No templated responses | Untouched — R8 persists what the model already produced. |
| Data driven | R12 observes real task runs rather than assuming healthy. |

## Rollback

Phase 1–2 are code-only; `git revert`. R8's migration is additive and backward-compatible
(old rows read `NULL`), so reverting the code leaves the column inert. R10 is a file copy —
revert by pointing dev back at `assistant.db`. R11 is additive dependencies; reverting the
dispatch leaves the allowlist entries harmless.
