---
date: 2026-10-09
status: active
estimated_hours: 16
---

# Files page: rename, edit, sort, PDFs — and a trace panel that works

## Objective

The Files page and the chat trace panel are the two places a user inspects what
the agent knows. Today the trace panel is a **placeholder that shows nothing**,
and the Files page is **read-only**: no rename, no edit, no sort, and PDFs
download instead of opening. This makes the user wait on the agent for operations
the browser can do directly — and the agent is slow at them (editing a file takes
several chat turns of read → anchor → retry).

Give the user direct control: rename a file (and keep memory true to the new
name), edit a text file in place, sort by recency, open a PDF in a tab. And make
the trace panel show the turn's actual memory context, citations, and search.

## Fact-check (verified against the code, 2026-10-09)

Everything below was read, not assumed. Corrections to earlier drafts are noted.

1. **The trace panel is a non-functional placeholder.** `ChatPage.tsx:396–426`
   renders literal `-` into plain DOM ids (`#trace-task-type`, `#trace-memory`,
   `#trace-citations`, `#trace-search-section`). **Nothing in the Solid app reads
   or writes those ids** — the population logic exists only in the legacy
   `module_script.js:500–524` and was never ported. So it "shows no data" for two
   independent reasons, and fixing one is not enough:
   - **No binding at all** (break #1).
   - `memory_context` and `citations` never reach the message meta: the live merge
     (`state/chat.ts:219–231`) copies only `task_type`, extraction summaries,
     `search_info`, `prompt_tokens`, `context_window`; and the history loader
     (`services/api.ts:121–131`) uses `SessionMessageSchema`, which declares only
     `role, content, timestamp, search_info` — **Zod `.object()` strips the rest**
     (break #2). `task_type` survives live but is stripped on history.
2. **The panel is in-flow, not docked.** `#trace-panel` is a flex sibling of
   `#chat-area` inside `#main` (`layout.css:27–39, 199–211`), so it pushes the
   chat column instead of overlaying. The Settings panel is the pattern to copy:
   `position:fixed; top:0; right:-300px; width:300px; height:100vh;
   transition:right` + `.open { right:0 }` (`components.css:307–324`), toggled by a
   top-bar button (`TopBar.tsx:365–373`).
3. **The file table is unsorted.** `FileGrid.tsx` renders `files()` in the order
   `/files/list` returns, which is `list_frames(... ORDER BY id)`
   (`store.py:542`) — **oldest first**. No sort control exists.
4. **PDFs download, not render.** `download_file` (`main.py:2590–2623`) returns
   `FileResponse(..., filename=…, media_type="application/octet-stream")`, i.e.
   `Content-Disposition: attachment`. The viewer (`FileViewer.tsx`) shows
   "No preview for this file type" for anything outside `TEXT_EXTS`/`MARKDOWN_EXTS`.
5. **There is no rename endpoint and no save/edit endpoint.** The only mutating
   file routes are `POST /files/upload` and `DELETE /files/{frame_id}`. Rename
   exists only as the agent tool `execute_rename_file` (`tool_executor.py:1944`),
   coupled to the module-global `_store`.
6. **Rename writes the wrong `file_name`.** `execute_rename_file` sets **both**
   `file_name` and `file_safe_name` to `new_rel` (the sandbox-relative path,
   `tool_executor.py:2002–2007`). But `apply_file_to_memory` defines
   `file_name` = **display name** and `file_safe_name` = **path**
   (`files.py:890–909`, `display_name = display_name or safe_filename`). For a
   nested file (`notes/x.txt`) a rename sets `file_name = "notes/x.txt"`; for an
   upload it **loses the original display name**. This is the "memory has the
   wrong file name" the requirement points at.
7. **Rename leaves CSV row frames named after the old base.** Children are
   `file_<base>.csv_row_<n>` (`files.py:957`); a rename moves the parent frame but
   not the children's names.
8. **The `uploaded_files` / `file_list` index frame does not exist.**
   `docs/FILES.md:312–319` describes it as carrying stale references; a repo-wide
   search finds no writer and no reader (only the doc and one experiment fixture).
   The doc is stale — do not implement against it. (Fix the doc in this work.)
9. **The agent-edit friction is structural, not a bug.** Editing via chat is
   `read_file` (bounded, paged) → construct an exact `old_text` anchor → `edit_file`,
   which refuses ambiguous anchors and retries on whitespace differences
   (`tool_executor.py:1714–1906`). Each retry is a turn. The prod logs no longer
   carry the evidence (the container was rebuilt; `docker logs` holds 162 lines,
   none a file edit), so the claim is grounded in the code path, not the log.

## Requirements (acceptance criteria)

### R1 — Trace panel
- R1.1 The panel is **fixed and right-docked**, sliding in like Settings, and
  overlays the chat column rather than pushing it.
- R1.2 It has a **top-bar toggle** (like the Settings gear) and keeps the existing
  Settings checkbox and close button.
- R1.3 It shows the **last assistant turn's** `task_type`, `memory_context`,
  `citations`, and — when the turn searched — the search query/backend.
- R1.4 The data is present for a **live turn**. **Correction (found while
  implementing):** the backend does not persist `task_type`/`memory_context`/
  `citations` per episode and `SessionMessage` (`main.py:1270–1276`) returns only
  `role/content/timestamp/search_info`, so a reloaded history cannot show them
  without an episode column (and persisting the full memory context per turn is a
  DB-size tradeoff). Deferred: the panel shows the persisted search info on reload
  and a clear "captured for the current turn" note otherwise, never `-`.

### R2 — File rename
- R2.1 A **copy file name** button copies the display name and confirms with a toast.
- R2.2 A **rename** button opens a modal; Save renames the file.
- R2.3 The extension is **fixed** (the modal edits the base name; a changed
  extension is refused).
- R2.4 Renaming **updates memory**: the `file_<name>` frame moves, `file_name`
  becomes the new **display name** (not the path), `file_safe_name` becomes the new
  path, and CSV row frames are renamed to the new base.
- R2.5 The grid refreshes and shows the new name; the viewer updates.

### R3 — File editing
- R3.1 **Text files only** (`TEXT_EXTS` ∪ `MARKDOWN_EXTS`); binary documents offer
  no edit.
- R3.2 An **Edit** button opens an edit view (textarea, monospace) seeded with the
  file's current content.
- R3.3 **Save** writes the file and returns to the read view showing the saved
  content; **Cancel** returns without writing.
- R3.4 Saving refreshes memory through `apply_file_to_memory` (the one write path),
  so `file_profile` and CSV rows stay true.

### R4 — Sort by last updated
- R4.1 The table is sorted by `updated_at` **descending** (most recently updated
  first); files with no `updated_at` sort last.

### R5 — PDFs
- R5.1 A PDF opens in a **new tab**, rendered by the browser.
- R5.2 The viewer shows an **Open in new tab** action for PDFs instead of
  "No preview".

### R6 — Agent edit friction (reduce, measure)
- R6.1 Document the multi-turn cost with the mechanism (read → anchor → retry).
- R6.2 Make at least one concrete improvement that shortens the loop, or record
  why none is warranted. (Candidate: the read view already carries line numbers, so
  the tool description should steer the model to **line-addressed** edits, which do
  not need a byte-exact anchor.)

## Phases

| # | Phase | Surface |
|---|---|---|
| 1 | Trace panel: dock right + wire the data (live + history) | frontend |
| 2 | File table: sort by `updated_at` desc | frontend |
| 3 | PDFs: inline serving + "Open in new tab" | backend + frontend |
| 4 | Rename: core extraction, endpoint, memory correctness, UI | backend + frontend |
| 5 | Edit: save endpoint (text only), edit view | backend + frontend |
| 6 | Agent edit friction: steer to line-addressed edits | backend |
| 7 | Holistic review | — |

### Phase 1 — Trace panel

- **Dock it.** Move `#trace-panel` out of the flex row: `position:fixed; top:0;
  right:-Wpx; width:W; height:100vh; transition:right` + `.open`, mirroring
  `#settings-panel`. Keep the existing `settings.traceVisible` store value as the
  open state so the Settings checkbox keeps working.
- **Add a top-bar toggle** button next to the gear, wired to the same signal.
- **Wire the data.** In `ChatPage.tsx`, a `createMemo` over `messages()` selects the
  last assistant message's `meta`; bind `task_type`, `memory_context`,
  `citations`, and `search_info` into the panel. Reveal the search section only
  when `search_info` is present (the current `hidden` is hardcoded).
- **Carry the fields.** Add `memory_context` and `citations` to the live merge
  (`state/chat.ts`) and to the `StreamEvent` type so the panel can bind them.
  (History persistence is deferred — see R1.4.)
- Empty state: a clear "No turn yet" / "captured for the current turn" rather
  than `-`.

### Phase 2 — Sort

`FileGrid.tsx`: a `createMemo` sorting by `updated_at` desc, `undefined` last.
Backend stays `ORDER BY id`; sorting is a view concern.

### Phase 3 — PDFs

- Backend: serve inline. Add `inline: bool = False` to `download_file`; when true,
  omit `filename=` (no attachment disposition) and set the MIME type from the
  extension (`mimetypes.guess_type`, `application/pdf` for `.pdf`).
- Frontend: `FileViewer` renders an **Open in new tab** action for `.pdf` (opens
  `/files/{id}/download?inline=true`), and the grid opens a PDF in a new tab on
  click instead of selecting it.

### Phase 4 — Rename

- **Extract the core** from `execute_rename_file` into `files.py`
  (`rename_file(store, *, path, new_name, display_name=None, user_id)`), so the
  tool and the endpoint share one implementation (no duplicate rename logic). The
  tool handler becomes a thin wrapper mapping exceptions to `ToolResult`.
- **Correct the memory writes:** `file_name` = new **display name**
  (basename of `new_name` unless `display_name` is given); `file_safe_name` = the
  new relative path. **Rename CSV row children** to the new base. Keep the
  extension fixed and the clash check before disk.
- Backend endpoint: `PATCH /files/{frame_id}` body `{new_name}` → 200 with the
  updated metadata; 404 unknown frame; 409 clash; 400 extension change.
- Frontend: `renameFile` in `api.ts`; a rename **modal** (reuse `Modal.tsx`, base
  input + fixed extension suffix); a **copy name** button (`navigator.clipboard` +
  toast); on success dispatch `files-changed` and re-select the frame.

### Phase 5 — Edit

- Backend: `PUT /files/{frame_id}/content` body `{content}` → writes bytes
  (overwrite) and calls `apply_file_to_memory` with the frame's existing name/ext
  and the stored display name. **Refuse binary** (`BINARY_DOCUMENT_EXTS`) with 415.
  404 unknown frame. This is the **one write path** — do not build a second writer.
- Frontend: `FileViewer` gets an **Edit** button for text files → textarea edit
  view; **Save** calls the endpoint then returns to the read view (reload);
  **Cancel** returns. A dirty-state guard on Cancel.

### Phase 6 — Agent edit friction

- Update the `edit_file` tool description to prefer the **line-addressed** mode
  after a read (the read already shows `[lines a–b of N]`), since it needs no
  exact-text anchor and so does not fail on whitespace or ambiguity.
- Record the mechanism and the change in `docs/FILES.md`; the log evidence is gone
  (rotated), so state that.

### Phase 7 — Holistic review

- Re-read the whole diff against the design principles below; verify dev + prod.

## Design-principles alignment

- **Clean ship.** Extract, do not duplicate: one rename core, one memory write path
  (`apply_file_to_memory`). No dead code; `ruff --select=F401,F811`.
- **Model-first.** R6 steers the *model* to a better edit mode rather than adding
  scripted retry logic.
- **Stability / no regressions.** Each phase ships a regression test (below).
- **Safety & privacy.** No new network calls; files stay local. Rename/edit are
  local disk + DB operations. No cloud, no telemetry.
- **Speed.** Rename/edit are single round trips; no new work on the chat hot path.
  The trace panel adds no prompt cost (it reads meta, not the model).
- **Visual feedback.** Reuse the existing 250 ms panel transition, toasts, and
  modal patterns; no inline styles (tokens + CSS classes only).
- **Docs.** `docs/FILES.md` is the home for the file contracts; fix its stale
  `uploaded_files` claim.

## Test strategy

- **R1:** frontend — a memo returns the last assistant meta; the panel renders
  `task_type`/`memory_context`/`citations`/search when present; a history message
  (post-schema-fix) keeps them. Backend — none (no change).
- **R2:** backend — `rename_file` sets `file_name` to the display name and
  `file_safe_name` to the path (nested case), renames CSV row children, refuses
  clash/extension change; `PATCH` endpoint 200/404/409/400. Frontend — copy name,
  rename modal save, grid refresh.
- **R3:** backend — `PUT` writes and refreshes memory (`file_profile` changes),
  refuses binary (415), 404. Frontend — edit view seeds content, save returns and
  shows new content, cancel does not write.
- **R4:** frontend — files sort by `updated_at` desc; `undefined` last.
- **R5:** backend — `inline=true` serves `application/pdf` with no attachment
  disposition; default still downloads. Frontend — the PDF action targets a new tab.
- **R6:** backend — the tool description contains the line-addressed guidance.
- **Regression (the point):** the existing `test_file_rename.py` must keep passing
  (its top-level assertions are unchanged); add the nested-file `file_name` case
  that today is wrong.

## Rollback

Each phase is independent and revertible on its own: Phase 1 is frontend-only;
Phase 2 is one memo; Phase 3 is one query param + one button; Phase 4 extracts a
function (the tool keeps working through the wrapper); Phase 5 adds one endpoint;
Phase 6 edits a string. No migration, no schema change, no data rewrite.
