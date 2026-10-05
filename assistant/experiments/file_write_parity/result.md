# Result: Do Agent-Written and Uploaded Files Produce the Same Data State?

**Pre-registration:** `plan.md` (committed before data).
**Verification:** `verification.md` (written before this file).
**Raw run:** `result.json`. Writer library versions: `pypdf==6.19.0`,
`python-docx==1.2.0`, `openpyxl==3.1.5`, `python-pptx==1.0.2`, `odfpy==1.4.1`,
`xlrd==2.0.2`, `xlwt==1.3.0`, `icalendar==7.3.0`. `reportlab` absent.

## Answer

**No — not today.** `write_file` produces a valid file for text formats only. For
every binary document format it writes the literal UTF-8 string with a document
extension, and the owning library refuses to open it. The memory state also
diverges: an upload records extracted entities and CSV row structure; a tool write
records metadata only.

The important second half: **the fix is achievable for 12 of the 13 formats with
libraries already in the image.** Only `pdf` needs a new dependency
(`reportlab`, 1.9 MB, pure-Python).

## H2 — fidelity, binary format (CONFIRMED, the primary result)

`write_file` on a binary format produces a file the owning library cannot read:

| Format | Owning library error on the written file |
|---|---|
| `docx` | `PackageNotFoundError: File is not a zip file` |
| `xlsx` | `BadZipFile: File is not a zip file` |
| `pptx` | `PackageNotFoundError: File is not a zip file` |
| `odt` / `ods` / `odp` | `BadZipFile: File is not a zip file` |
| `xls` | `XLRDError: Unsupported format... found b'name,rol'` |
| `pdf` | `PdfStreamError: Stream has ended unexpectedly` |

The `xls` error is the clearest evidence of the mechanism: the reader found
`b'name,rol'` — the literal CSV text — where a BOF record should be. `write_file`
wrote the string; the extension is a costume.

**This is the user-facing defect.** A user asking "save that as a docx" gets a
file that Word will report as corrupt. That is worse than refusing.

## H4 — the fix direction works (POSITIVE)

Every binary format with a writer library in the image round-trips: a real writer
produces a file our own `extract_file_content` reads back correctly.

| Format | Writer present | Round-trip |
|---|---|---|
| `docx` | `python-docx` | ✅ `'Quarterly Report Ada Lovelace shipped...'` |
| `odt`/`ods`/`odp` | `odfpy` | ✅ |
| `xlsx` | `openpyxl` | ✅ `'# Sheet Ada Lovelace'` |
| `xls` | `xlwt` | ✅ `'# S Ada Lovelace'` |
| `pptx` | `python-pptx` | ✅ `'# Slide 1 Quarterly Report'` |
| `ics` | `icalendar` | ✅ `'Event: Trip to Rainier'` |
| `pdf` | **none** | ⚠️ blocked on `reportlab` |

So parity is **achievable without new dependencies for every format except pdf.**
That is the decisive input to the build: the work is "call a writer library that
is already installed", not "invent an encoder".

## H3 — parity (CONFIRMED for csv/json, even elsewhere)

- **`txt`, `md`, `ics`**: slot-key sets equal. Text rounds trips and the two paths
  agree.
- **`csv`**: **not equal.** Upload stores four `entity_*` slots and imports row
  frames; the tool path stores the row frames but no entities.
- **`json`**: **not equal.** Upload stores `entity__Ada Lovelace` and
  `entity__analytical engine`; the tool path stores none.

The mechanism: `upload_file_to_memory` runs `extract_file_content` and writes
`entity_*` slots; `execute_write_file` never calls the extractor. The JSON case is
the clean illustration — identical bytes, two different memories.

## H1 — text round-trip (holds)

`txt`, `md`, `csv`, `json` written by the tool read back as themselves.

## Against the pre-committed bar

> If fewer than half of `SUPPORTED_UPLOAD_EXTS` round-trip with both valid output
> and equal frame state, the two-path design has not delivered.

Of the 13 target formats: **5 valid on write** (text) + **7 valid only via a
writer not yet used** (binaries) + **1 blocked** (pdf). On current code, **4 of 13
(text+json+ics) fully satisfy "valid output and equal frame state"**, and only
**`txt`/`md`/`ics` satisfy both** without the entity gap. **The bar is not met.**
The unification work is justified — as the plan said it would be if this held.

## What this changes

1. **`write_file` must route through a format-aware writer**, not `write_text`. The
   writers exist for 12 formats; `pdf` needs `reportlab`.
2. **`write_file` must run the same extraction as upload** (entities, CSV rows) so
   the memory state matches.
3. **The format set becomes official and equal on both sides:** the agreed 13,
   with `md` added and `rtf`/`eml`/`tsv`/`html`/`xml` removed from
   `SUPPORTED_UPLOAD_EXTS` and the frontend mirror.
4. **The agent must not be able to write a binary extension it cannot encode** —
   either use the writer, or refuse with a message. Silence plus a fake file is
   the outcome this experiment exists to kill.

## What this does NOT establish

- **Frequency.** It does not show how often the agent attempts a binary write.
- **PDF quality.** `reportlab` was never measured; the pdf column is "no writer",
  not "works". Its round-trip must be measured before pdf authoring is claimed.
- **The `file_safe_name` basename-vs-relative-path divergence.** `safe_name_equal`
  is `False` for every format, but the arms use different base names, so this run
  does not isolate it (verification threat 3). It is a real divergence found by
  reading the code and needs a targeted test, not a claim from this data.
