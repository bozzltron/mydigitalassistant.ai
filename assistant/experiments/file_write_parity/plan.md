# Experiment: Do Agent-Written and Uploaded Files Produce the Same Data State?

## Status

**PRE-REGISTERED. No data collected yet. No result may be written until this
document is committed.**

## Agreed target format set (decided before the run)

The user has fixed the format set this work targets:

- **Drop from uploads:** `rtf`, `eml`, `tsv`, `html`, `xml`.
- **Add as official:** `md` (currently readable and writable but absent from
  `SUPPORTED_UPLOAD_EXTS` — a list disagreement this run reports).
- **Keep:** `txt`, `csv`, `json`, `ics`, `pdf`, `docx`, `odt`, `xlsx`, `xls`,
  `ods`, `pptx`, `odp`.

Final set (13): `txt`, `md`, `csv`, `json`, `ics`, `pdf`, `docx`, `odt`, `xlsx`,
`xls`, `ods`, `pptx`, `odp`.

**Write is required for all of them if a writer exists.** Known writer situation
before the run (to be confirmed by measurement, not assumed):

| Format | Offline writer | Status in image |
|---|---|---|
| `txt`, `md`, `csv`, `json` | plain text | present |
| `docx` | `python-docx` | present |
| `xlsx` | `openpyxl` | present |
| `xls` | `xlwt` | **present (already installed)** |
| `odt`/`ods`/`odp` | `odfpy` | present |
| `pptx` | `python-pptx` | present (creation support TBC) |
| `ics` | `icalendar` | present |
| `pdf` | `reportlab` | **absent — 1.9 MB pure-Python, would be added** |

`reportlab` is the only prospective new dependency: pure-Python, no system
binaries, no network, in the same family as the readers already committed to
(the pyproject note "All offline, pure-Python, no system binaries"). The
alternative (`weasyprint`/`pdfkit`) pulls a browser engine and violates that
stance.

## Background

A user works with the agent, then asks it to save the result as a file — "write
that up as a docx", "save this as markdown", "make me a CSV of those rows". The
agent has a `write_file` tool. Uploading the same file by hand goes through
`upload_file_to_memory` in `main.py`.

These are supposed to be the same data state. Reading the code, they are not,
and the divergence is large enough that the user-visible promise ("the agent
saved a file, I can find and read it like anything else") may be false for most
formats.

The two paths as they stand:

| Step | Upload (`upload_file_to_memory`) | Tool (`execute_write_file`) |
|---|---|---|
| Write bytes | raw `open(...,"wb")` | `write_sandbox_file` → `write_text(utf-8)` |
| Content extraction | `extract_file_content(path, ext, bytes)` | **none** |
| Entity slots | `entity_*`, capped `FILE_MAX_ENTITY_SLOTS` | **none** |
| Frame `file_safe_name` | basename | possibly-nested relative path |
| CSV row-frame key fallback | `col_{i}` (row ordinal) | `col_{col_idx}` (column ordinal) |
| CSV row-frame name | `file_{base}_row_{n}` | `file_{name}_row_{n}` |
| Frame `source_type` / reliability | `file_upload` / 0.7 | `file_create` / 0.8 |

## Question

**For each supported format, is the data state created by `write_file` the same
as the data state created by an upload of identical content — and for binary
document formats, can `write_file` produce a *valid* file at all?**

Two distinct failures are in scope and must not be conflated:

1. **Parity failure** — the file is valid, but memory is thinner (no extraction,
   no entities) or shaped differently (slot values, row frames).
2. **Fidelity failure** — the file itself is not a real document. `write_file`
   writes UTF-8 text; an agent asked for a `.docx`/`.pdf`/`.xlsx` has no encoder
   and would write the literal string with a document extension. A file that
   cannot be opened by the application that owns its extension is a worse
   outcome than not writing it.

Fidelity is the more serious of the two. Parity is a memory-shape problem;
fidelity is a broken artefact the user will try to open in Word.

## Hypotheses

- **H1 (fidelity, text).** For formats written as plain text by nature (`txt`,
  `md`, `csv`, `json`, `html`, `xml`), `write_file` output re-extracts to
  itself: `extract_file_content(write_file(content)) == content` (modulo
  whitespace normalization). If this fails, even the text path is lossy.
- **H2 (fidelity, binary).** For `docx`, `pdf`, `xlsx`, `pptx`, `odt`, writing
  the content as a string produces a file that the owning library **cannot
  open** (`python-docx`/`pypdf`/`openpyxl` raise). This is the expected negative
  and is the primary falsifiable claim.
- **H3 (parity).** For a format that *is* written correctly, the memory frame
  from `write_file` carries the same slots as an upload of the same bytes —
  specifically `entity_*` slots and, for CSV, row frames with identical slot keys.
- **H4 (round-trip).** For a truly generated binary document (using the format's
  real writer library), the agent can `read_file` it back and recover the text it
  intended to write. This tests the *fix direction*, not the current code: it
  establishes whether parity is achievable for each binary format. Formats with
  no writer in the image (`pdf` without adding `reportlab`) are reported as
  "no writer" rather than as a failure.

## Method

`experiment.py`, run against a **scratch database** (never the live brain) and a
scratch sandbox root, so no real file or frame is touched. For each format in
`SUPPORTED_UPLOAD_EXTS` plus `md`:

1. Build a small deterministic content payload (a short document with a heading,
   a table row, and two entities).
2. **Upload arm:** write the bytes to disk, run `extract_file_content`, then
   build the frame/slots exactly as `upload_file_to_memory` does.
3. **Tool arm:** call `execute_write_file` with the same intended content.
4. **Fidelity probe:** attempt to open/re-extract the tool-arm file with the
   format's real library (`python-docx`, `pypdf`, `openpyxl`, `odfpy`, …) and
   record success/failure + the recovered text.
5. **Parity diff:** compare the two frames' slot keys/values and, for CSV, the
   row-frame slot keys.
6. **H4 arm:** where a writer library exists (`python-docx`, `openpyxl`,
   `reportlab` if present), generate a real file, then `extract_file_content` it
   back and compare recovered text to intended text.

No LLM calls. This is a code-path measurement, not a reasoning measurement, so it
is deterministic and cheap to re-run.

## Addendum (2026-10-05): H5, the Files-UI view

**Added after the first run, before measuring it.** The original experiment
compared frames the way the *database* sees them. A live test then showed that
`/files/list` — what the user actually sees in the Files page — filtered to
`source_type == "file_upload"` and so omitted every agent-written file
(`file_create`). A file could therefore satisfy H3 (identical frame) and still be
invisible to the user. That is a coverage gap in the original design: "same data
state" was measured at the wrong surface.

- **H5 (UI visibility).** An agent-written file appears in the response of
  `GET /files/list` for its owner, the same as an uploaded one. Falsified if the
  written file is absent from that response while present on disk and in memory.

This is pre-registered before the H5 measurement is taken; it is *not* claimed to
have been part of the original pre-registration, and `result.md` marks it as an
addendum.

### H5 method

In the same scratch harness, after writing a file via `execute_write_file`,
build a FastAPI `TestClient` with `get_store` overridden to the scratch store and
call `GET /files/list?user_id=<owner>`. Record whether the written file's
`file_name` appears. Compare against an uploaded file in the same store.

### Variables

- **Independent:** format (17 upload exts + `md`), write path (upload vs tool).
- **Dependent:** file validity (bool + owning-library error), re-extracted text
  equality with intent, frame slot-key set, CSV row-frame slot-key set,
  `file_safe_name` value.
- **Controls:** identical content payload, identical scratch store, identical
  extractor, no network, no LLM.

## What counts as evidence

- H2 is decided by **library open success**, not by file size or extension. A
  `.docx` that `python-docx` cannot open is a fidelity failure, full stop.
- H3 requires **exact slot-key set equality**, not "overlapping". A missing
  `entity_*` slot is a divergence, because that is exactly the recall path the
  upload arm enables and the tool arm does not.
- H4 decides whether a fix is *possible* (a writer library exists and round-trips)
  or *impossible* (no offline pure-Python writer for that format — in which case
  the honest design is to refuse or downgrade, not to write a fake file).

**Pre-committed bar:** if fewer than half of `SUPPORTED_UPLOAD_EXTS` round-trip
through `write_file` with both valid output and equal frame state, the current
two-path design has not delivered the promise and the unification work is
justified. If ≥90% already round-trip, the divergence is narrower than reading
the code suggests and I will say so.

## Falsification conditions

1. *H2 fails — `write_file` produces documents the owning library opens* → the
   fidelity concern is unfounded; drop it and report parity only. (This would
   mean a writer I have not seen; I will show the file.)
2. *H3 holds for every format* → extraction parity is a non-issue and the fix is
   smaller than assumed.
3. *H1 fails on plain text* → the text path is lossy independently of binaries,
   which raises severity and changes the recommendation.
4. *No offline writer exists for a format* → parity for that format is not
   achievable by building a writer; the recommendation must become "refuse with a
   clear message" rather than "generate anyway".

## Safety

- Runs against a **scratch** DB and a **scratch** sandbox root (`EXP_SANDBOX`),
  never `/app/data` and never the live / prod brain. The experiment container
  mounts no live volume.
- `preflight.py` is not applicable in its brain-reading form (this experiment
  does not open the live brain), but the same principle is enforced: the DB path
  must be under the scratch root, asserted at startup, and the run aborts if
  `EXP_SANDBOX` is unset or points at `/app/data`.
- Every file the experiment writes is written under `EXP_SANDBOX` and removed at
  the end; the script prints the paths it created so non-leakage is checkable.
- No LLM, no network, no external calls.

## Known threats, stated now rather than after seeing results

1. **Version drift in the writing libraries.** `python-docx`/`openpyxl` change
   their generated XML across versions; H4 round-trip success depends on the
   pinned version in the image. The version strings will be recorded with the
   results.
2. **`execute_write_file` calls module-global `_store`.** The harness must wire
   that global to the scratch store or the tool silently skips memory work
   (`if _store is not None`). A mis-wired harness would report "no parity gaps"
   for the wrong reason, so the harness asserts `_store is not None` before the
   tool arm runs.
3. **Round-trip equality is text-level, not semantic.** A generated PDF whose
   extracted text differs only in whitespace/ligatures counts as a pass here;
   the experiment does not judge whether the agent wrote *good* content.
4. **This measures the code paths, not the user experience.** It can show that
   `write_file` on a `.docx` produces an unopenable file; it cannot show how
   often the agent actually attempts that. Frequency needs real transcripts and
   is out of scope.
5. **`md` is a special case.** It is readable (`PLAIN_TEXT_EXTS`) but absent
   from `SUPPORTED_UPLOAD_EXTS`, so the two lists disagree about it today. The
   experiment reports it explicitly rather than folding it into "text".
