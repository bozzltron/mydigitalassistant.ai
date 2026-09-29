# Files System: Implementation, Caveats & Contracts

How uploaded files, tool-created files, and their memory frames work — and the
sharp edges discovered while building it. Read this before touching file
handling code.

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
- **Content slots are never leaked into the prompt.** `file_content_preview`
  (200-char snapshot) and full `file_content` are *hints*, not the file. If the
  model sees a 200-char preview it answers from the snippet — "incomplete
  memories in the way of contents".
- Instead, file frames get an explicit pointer:
  `read full contents: read_file(frame_name="file_…") or read_file(path="…")`

The system prompt (both functional and introspective branches in
`llm_client.py`) reinforces: a memory preview is a hint, never the full contents.

## Memory shape of an upload

One frame per uploaded file (`frames.name = file_<safe>`, `source_type=file_upload`):

- `file_name` / `file_ext` / `file_size` / `file_safe_name`
- `file_content_preview` — first 200 chars (kept in the DB for the Files UI
  preview; excluded from the LLM prompt)
- CSV extras when the file is a CSV: `row_count`, `columns`, plus up to
  `CSV_MAX_ROW_FRAMES` (default 100) `csv_row_<n>` child frames for small-CSV
  recall/edit. Row data beyond the cap stays on disk, read via `read_file`.
- Entity slots from key extraction are capped at `FILE_MAX_ENTITY_SLOTS` (50).

Large files are never fully embedded; the frame embeds metadata only.

## Re-upload / merge semantics

- `frames.name` is UNIQUE → uploading a file whose name already exists **merges
  into the existing frame** and overwrites the single disk copy.
- CSV row children are rebuilt on merge (old `part_of` children pruned first).

## Delete semantics

- Uploaded files are **hard-deleted** as a cluster: `DELETE /files/{frame_id}` /
  `delete_file` tool remove the disk copy *and* the frame *and* the `file_*`
  slots *and* associated row frames / associations.
- `forget_frame` (priority → 0) is only for GC, not file deletion.
- Schedule, facts, episodes, and unrelated memory survive a file delete.

## Sandbox rules

- All ops route through `pipeline/filesystem.py`: `SANDBOX_ROOT = /app/data`,
  path-traversal and symlink-escape checks, `sandbox_max_file_size` (10 MB
  write), `sandbox_max_read_size` (1 MB read), `sandbox_max_glob_results`.
- `read_file` returns the full content to the model — keep reads under the
  context budget; that cap is the backstop.
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
  context. A 45 KB CSV is fine; near-1 MB reads are not. Keep the cap.
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