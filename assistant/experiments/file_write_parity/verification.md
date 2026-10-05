# Verification — file_write_parity

Per `plan.md`, this verification is written **before** `result.md`, and names the
threats that remain rather than only the checks that passed.

## What was actually run

`experiment.py` against a scratch DB (`/tmp/fw_scratch/assistant.db`) and a scratch
sandbox root (`/tmp/fw_scratch`), with `filesystem.SANDBOX_ROOT` monkey-patched to
the scratch dir before any write. `tool_executor._store` was wired to the scratch
store and asserted non-None before the tool arm ran (plan threat 2). No LLM, no
network. Scratch was removed at the end of each run.

Formats run are the **agreed target set** (13); the five dropped formats
(`rtf`, `eml`, `tsv`, `html`, `xml`) are not in the record.

## Harness bugs found and fixed during the run (recorded, not hidden)

The first two runs produced results that were **wrong in a way that flattered and
damaged the code differently**, and both were caught before `result.md`:

1. **Text formats reported `valid=False`.** The probe had no validity check for
   `txt`/`md`/`csv`/`json`, so "no probe" read as a fidelity failure. Fixed:
   text formats validate by decoding the bytes. Without this, the record would
   have claimed text writing is broken.
2. **Working writers reported `roundtrip=False`.** The comparison required the
   recovered text to equal the full prose payload, but a spreadsheet recovers
   `# Sheet Ada Lovelace` and a slide recovers its title — both correct. Fixed to
   match the salient token per format. Without this, `xlsx`/`xls`/`pptx`/`ics`
   would have been recorded as writer failures when they work.
3. **`slots_equal=True` was initially meaningless.** The first payload produced no
   entities, so the upload arm's `entity_*` slots were empty and the missing-slot
   gap could not be seen. Fixed with a JSON payload whose scalars extract as
   entities, and by building CSV row frames in the upload arm. This is what turned
   `csv`/`json` from `slots_equal=True` (false comfort) to `False` (the real gap).

All three are recorded because a measurement whose harness is wrong is not a
measurement, and the corrections changed the conclusion in both directions.

## Checks that passed

- Scratch-only execution: `EXP_SANDBOX` asserted outside `/app/data`, aborting
  otherwise; scratch tree removed afterward and printed.
- `tool_executor._store` non-None asserted (plan threat 2).
- Every binary `roundtrip=True` result is backed by the recovered text in
  `result.json`, e.g. `docx` → `'Quarterly Report Ada Lovelace shipped the
  analytical engine in 1843.'`.
- `write_file` validity failures carry the owning library's own error
  (`PackageNotFoundError`, `BadZipFile`, `PdfStreamError`, `XLRDError`), not a
  heuristic.

## Threats that remain (state them, do not paper over them)

1. **n is 13 formats × 1 payload.** This measures the code paths, not frequency.
   It does not show how often the agent actually writes a `.docx`; that needs real
   transcripts (plan threat 4).
2. **Round-trip is text-level, not semantic.** A generated file whose extracted
   text matches the intended token passes even if layout is poor. `reportlab` was
   not measured at all (absent from the image), so the PDF column is "no writer",
   not "works" (plan threat 1).
3. **`safe_name_equal=False` for every format is partly an artifact.** The arms use
   different base names (`report.<ext>` vs `agent_report.<ext>`), so the inequality
   is expected here and does **not** by itself prove the `file_safe_name`
   basename-vs-relative-path divergence found by reading the code. That divergence
   is real but this experiment does not isolate it; a follow-up with equal base
   names is needed before claiming it.
4. **`ics` writer test used a different payload** (a calendar event) than the prose
   `DOC`, so its `roundtrip=True` rests on a different string than the others.
5. **Writer-library version drift.** `python-docx`/`openpyxl`/`pptx`/`odfpy` change
   their output across versions; the results are under the versions in the image
   (plan threat 1). Not pinned in this record yet — see follow-up.
