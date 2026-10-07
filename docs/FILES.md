# Files System: Implementation, Caveats & Contracts

How uploaded files, tool-created files, and their memory frames work — and the
sharp edges discovered while building it. Read this before touching file
handling code.

## Two paths, one memory shape

A file enters memory two ways, and they must produce the **same** data state:

1. **Upload** — `/files/upload`, or a chat attachment (`upload_file_to_memory`).
2. **Agent-authored** — the `write_file` tool.

Both funnel through `apply_file_to_memory` (`pipeline/files.py`), which owns the
frame, the `file_*` slots, the `entity_*` slots, and CSV row frames. Before this
shared step existed the two diverged: upload extracted entities and row frames,
the tool wrote neither, so identical bytes produced two different memories
(measured in `assistant/experiments/file_write_parity/`). **Do not add a third
file writer that builds its own frame** — call `apply_file_to_memory`.

## Supported formats

**Uploadable + writable (13):** `txt`, `md`, `csv`, `json`, `ics`, `pdf`, `docx`,
`odt`, `xlsx`, `xls`, `ods`, `pptx`, `odp`. The single source of truth is
`SUPPORTED_UPLOAD_EXTS` (`pipeline/files.py`), mirrored by the frontend
(`frontend/src/utils/uploadFormats.ts`) and pinned by a test that parses the
backend file.

**Dropped from upload, still readable:** `rtf`, `eml`, `tsv`, `html`, `xml`. These
are no longer *accepted* for new uploads, but their extractors stay in
`extract_file_content` because files already on disk must remain readable.
Removing the extractors would orphan existing files. The upload list and the read
set are deliberately different.

**Never supported:** `.doc`, `.ppt` (no offline pure-Python reader).

## Writing (agent-authored files)

`write_file` renders the agent's text into **real bytes** for the file's
extension via `render_file_bytes` (`pipeline/files.py`), then writes with
`write_sandbox_bytes` (atomic, traversal-checked). The writer table is
`FILE_WRITERS`; every `SUPPORTED_UPLOAD_EXTS` entry has one.

- Text-ish (`txt`, `md`, `csv`) are the content verbatim; `json` is wrapped so it
  is valid JSON if the agent passed prose.
- `ics` builds real VEVENTs, from one of two shapes. A complete iCalendar document
  (`BEGIN:VCALENDAR…`) is parsed and re-serialized by the `icalendar` library and
  written as-is — the model writes ICS directly, and it carries fields no
  hand-rolled convention can (description, status, timezone). Otherwise plain prose
  becomes a single event titled with its first line and dated today, because
  prose-as-literal-bytes extracts to `''`. The library formats both shapes; nothing
  hand-builds ICS text.
- Binary formats use their encoder library (`python-docx`, `openpyxl`, `xlwt`,
  `odfpy`, `python-pptx`, `reportlab`).

**Refusal rule.** `write_file` refuses an extension with no writer
(`ToolResult(success=False, error="cannot write .x: no writer …")`). It must
never write the string with a document extension: that produced files Word calls
corrupt (a `.docx` containing `b'name,rol'`), which is the defect the parity
experiment exists to prevent. **Do not add a fallback that writes text under a
binary extension.**

## Editing (agent changes to an existing file)

`edit_file` is a surgical text replace: read the file, replace text, write it
back. It is **whitespace-tolerant** (exact match first, then any run of
whitespace matches any other) but never fuzzy beyond that — matching the wrong
region silently is worse than failing.

**Text only.** A binary document (`.docx`, `.pdf`, `.xlsx`, `.pptx`,
`.odt`/`.ods`/`.odp`, `.rtf`) is refused with a redirect. `read_file` *extracts*
a document to text, but the bytes on disk are not that text (a `.docx` is a zip),
so an exact match can never be found. Editing a document means **read → rewrite
with `write_file`**, which re-renders real bytes and re-runs extraction so memory
stays in step. This asymmetry — read extracts, edit replaces bytes — is why the
refusal exists; without it the model gets a misleading "old_text not found" and
dead-ends.

**A failed match is recoverable.** The error names the file size and the closest
region, so the model can correct its `old_text` rather than hitting a wall. Do
not replace this with a bare "not found".

## Renaming

`rename_file(path, new_name)` moves the disk file and its `file_<name>` frame
together, so the old name stops resolving and the new one works immediately. It
keeps the extension fixed — renaming a `.txt` to `.docx` would leave text bytes
under a document name — and refuses to overwrite an existing file rather than
silently clobber it. A bare `new_name` keeps the file in its folder; include a
slash to move it. It is a first-class operation on purpose: the model should not
have to compose read+write+delete, which can lose the file if it fails midway.

## The two names of every file

Every uploaded file has **two** names, and conflating them is the #1 source of
file bugs:

| What | Example | Where it lives |
|------|---------|----------------|
| **Frame name** | `file_subscribers_active.csv` | SQLite `frames.name` (UNIQUE) |
| **Disk name** (`file_safe_name` slot) | `subscribers_active.csv` | `/app/data/` in the container |

- **Disk name = the user's exact file name**, sanitized only where the
  filesystem forces it (spaces / unsafe characters become `_`). No timestamp,
  no numeric prefix.
- **Frame name = `file_` + disk name.** It is readable on purpose: the Files UI
  and the brain graph show it, and `frames.name` is UNIQUE so re-uploads of the
  same name *merge* into the existing frame.
- The `file_name` slot stores the display name (same as disk name in the
  current design; keep them in sync).

### Why this bites (the logged bug)
The model reads uploaded files via `read_file`. It frequently quotes the
**frame name** (`file_subscribers_active.csv`) as the `path` argument. A literal
sandbox lookup of that path fails — the disk file has no `file_` prefix.

Past conversations also compound this: old episodes and summaries quote names
that no longer exist verbatim (renames, the pre-name-preservation `upload_<ts>_<name>`
disk naming), and the model recalls those too.

**`execute_read_file` therefore resolves a `path` in this order:**
1. literal sandbox path (`subscribers_active.csv`)
2. as an upload frame name: `file_<basename>` → its `file_safe_name` slot
3. with a leading `file_` prefix stripped (covers historical `upload_…` disk names)
4. a **unique fuzzy match** against the user's uploaded files (same extension
   plus exact/suffix name containment or ≥2 shared non-numeric tokens), so names
   quoted in old conversations still resolve. Only a *unique best match* is
   accepted — never a guess.

On total failure the error names the available files so the model can
self-correct with `list_files()` instead of retrying blindly.

## What the model is told about files

`format_memory_context` (in `retrieval.py`) renders file frames specially:

- Metadata slots are shown (`file_name`, `file_size`, `file_ext`, `row_count`,
  `columns`, …).
- File frames get an explicit pointer:
  `read full contents: read_file(frame_name="file_…") or read_file(path="…")`

The system prompt (both functional and introspective branches in
`llm_client.py`) reinforces: a memory preview is a hint, never the full contents.

**Memory holds what a file *is*, never what it *contains*.** The bytes live in the
sandbox and are read verbatim. Content slots (`file_content`, `file_content_preview`)
are **refused at write time** by `upsert_slot`, which raises
`FileContentInMemoryError` — not merely excluded from the prompt. That distinction
matters: exclusion at render time left a stale copy in the database, and `read_file`
had a fallback that served it when a file was missing, so the model answered from a
truncated preview believing it had read the file. The refusal is at the store because
every writer funnels through it, so a new call site cannot reintroduce the copy.

## Memory shape of an upload

One frame per uploaded file (`frames.name = file_<safe>`, `source_type=file_upload`):

- `file_name` / `file_ext` / `file_size` / `file_safe_name`
- CSV extras when the file is a CSV: `row_count`, `columns`, plus up to
  `CSV_MAX_ROW_FRAMES` (default 100) `csv_row_<n>` child frames for small-CSV
  recall/edit. Row data beyond the cap stays on disk, read via `read_file`.
- Entity slots from key extraction are capped at `FILE_MAX_ENTITY_SLOTS` (50).

No content slot is stored. The upload *response* still carries a `content_preview`
field so the UI can show what was received, but it is never written to memory.

Large files are never fully embedded; the frame embeds metadata only.

## Re-upload / merge semantics

- `frames.name` is UNIQUE → uploading a file whose name already exists **merges
  into the existing frame** and overwrites the single disk copy.
- CSV row children are rebuilt on merge (old `part_of` children pruned first).

## Delete semantics

- Uploaded files are **hard-deleted** as a cluster: `DELETE /files/{frame_id}` /
  `delete_file` tool remove the disk copy *and* the frame *and* the `file_*`
  slots *and* associated row frames / associations.
- `forget_frame` (priority → 0) is an explicit memory forget, not file deletion.
- Schedule, facts, episodes, and unrelated memory survive a file delete.

## Files page (UI)

- `GET /files/list` carries `file_name`, `file_ext`, and `file_size` (from slots)
  alongside the frame. `file_ext` picks the row icon, `file_size` is the Size
  column. Without them every size rendered as `NaN` — it was simply never in
  the response. The table is Name (icon + filename), Size, Date, Actions; there
  is no Type column, because the extension is already in the filename.
- `GET /files/{frame_id}/download` serves the **original bytes** with
  `Content-Disposition: attachment`. Download must not go through
  `GET /files/{frame_id}/content`: that route decodes as UTF-8 with
  `errors="replace"`, so a binary document (PDF, docx) comes back as garbage.
- The viewer previews only text extensions (`FileViewer.TEXT_EXTS`); anything
  else shows "No preview" with a download link rather than decoding bytes as
  text. `.md`/`.markdown` render as sanitized HTML (`marked` + `DOMPurify`).
- Deleting goes through a confirmation modal — the row button opens it, the
  modal's confirm calls `DELETE /files/{frame_id}`.

## Sandbox rules

- All ops route through `pipeline/filesystem.py`: `SANDBOX_ROOT = /app/data`,
  path-traversal and symlink-escape checks, `sandbox_max_glob_results`.
- **No file-size limits.** Upload, read, and write are uncapped: this is a local,
  disk-backed project, so the user's disk is the limit. The deleted
  `sandbox_max_file_size` / `sandbox_max_read_size` knobs are gone, as is
  `SizeLimitError`. Do not reintroduce a cap as a "safety" measure — it was a
  policy on the user's own files, and it is why a 6.7 MB PDF that uploaded fine
  could not then be read.
- `read_file` returns document text through `extract_file_content` (the same
  extractor upload uses), not raw bytes: reading a PDF as UTF-8 yields garbage.
  The rule is **extract whenever an extractor exists**; only formats with none
  (`txt`, `md`, `log`, `yaml`, `yml`) are read directly. That includes
  `html`/`xml`/`eml`, which are text but whose extractors strip markup or parse the
  message — reading those raw would hand the model `<h1>Title</h1>` or MIME
  boundaries instead of the content. **`ics` is the exception**: its extractor
  summarises the calendar (and caps at five events) for memory, but a summary
  cannot be edited back into a calendar, so `read_file` returns the raw document
  (see `READ_RAW_EXTS` in `tool_executor`).
- The only bound is the **model's context window** on what `read_file` returns: a
  single result is capped at a fraction of the window (`_read_char_limit`), so one
  read cannot fill it. A capped read carries an **actionable** marker — the line
  range, the true total, and the exact `read_file(path=…, offset=…)` call to read
  on — so the model knows it saw a fragment and how to get the rest.
  `read_file(path, offset=, limit=)` pages by line. The file on disk is never
  truncated.
- Jobs, backups (`.db`, `.assistant-brain`) and the search index also live in
  `/app/data`; `list_sandbox_files("**/*")` sees everything, which is why
  `list_files` enriches from frames (only `file_upload` / `file_create` frames
  with metadata are reported).

## Ownership

File frames are owner-scoped (`owner_user_id`). `read_file` / `list_files`
filter by `list_frames(owner_user_id=user_id)`, and read/delete check ownership
before touching the disk copy. Note: `owner_user_id=None` means "not scoped" —
old index frames like `uploaded_files` can carry stale references (see below).

## Known sharp edges

- **Stale names in history.** Old episodes/summaries and the old `uploaded_files`
  index frame can quote pre-rename names. Resolution step 4 turns those into
  correct reads; consider cleaning the index frame's `file_list` slot when a
  file is renamed or deleted.
- **`read_file` result size.** The tool result is re-sent into the model's
  context, so `_bounded_for_model` caps what the model *sees* per turn at
  `_read_char_limit()` — a fraction of `CHAT_NUM_CTX`, not a flat number — with an
  actionable marker naming the line range and the next page. This is a
  context-window bound, not a file-size limit: the file is stored and read whole,
  and nothing is refused at any size.
- **Embedding model drift.** Frames may carry embeddings under a different
  `embedding_model` than the runtime default (the current default is
  `qwen3-embedding:0.6b`, 1024-dim; older data may still carry
  `nomic-embed-text`, 768-dim). Never re-embed a frame under a new model
  without confirming the vector dimension matches the store (`sqlite-vec`
  searches dimension-sensitively).
- **`fetch_url` / sandbox files.** Tool-created files (`write_file`) get
  `file_create` frames; they resolve the same way as uploads.

## Regression tests

`assistant/tests/test_tools.py`:
- `test_read_file_resolves_frame_name_given_as_path` — frame name passed as path.
- `test_read_file_resolves_stale_frame_name_from_old_conversation` — the logged
  stale-name bug (`file_upload_2026…_subscribers_active.csv` → current frame).
- `test_read_file_miss_reports_available_files` — failure names existing files.

`assistant/tests/test_retrieval.py`:
- `test_format_memory_context_file_frame_points_to_read_file` — previews never
  leak into the prompt; file frames carry a `read_file` pointer.

`assistant/tests/test_files.py`: exact-name storage, re-upload overwrite,
entity cap, frame-name readability, delete cascade, row-frame cap.

`assistant/tests/test_ics_writer.py`: a model-written `.ics` document is used
as-is (timed + all-day, timezone preserved), the prose fallback, and the
`BEGIN:VCALENDAR`-in-a-DESCRIPTION regression.

`assistant/tests/test_file_rename.py`: rename moves the disk file and its frame
together, and refuses an overwrite or an extension change.