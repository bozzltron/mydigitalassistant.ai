"""Do agent-written and uploaded files produce the same data state?

Measures, per format: (a) whether `write_file` produces a file the owning library
can open, (b) whether the frame it creates carries the same slots an upload
creates, and (c) whether a real writer library can round-trip a document that
`extract_file_content` reads back intact.

See plan.md for the design, hypotheses, falsification conditions, and threats.

Runs against a SCRATCH database and SCRATCH sandbox only. Aborts unless
EXP_SANDBOX is set and is not the live sandbox. No LLM, no network.

Usage:
    EXP_SANDBOX=/tmp/fw_scratch EXP_DB=/tmp/fw_scratch/assistant.db \
        python -m assistant.experiments.file_write_parity.experiment
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- scratch-only guard, asserted before anything else -------------------------

LIVE_SANDBOX = Path("/app/data")
SCRATCH = os.environ.get("EXP_SANDBOX", "")


def _assert_scratch() -> Path:
    if not SCRATCH:
        print("ABORT: EXP_SANDBOX is not set; refusing to run against the live sandbox")
        sys.exit(2)
    scratch = Path(SCRATCH).resolve()
    if scratch == LIVE_SANDBOX.resolve() or LIVE_SANDBOX in scratch.parents:
        print(f"ABORT: EXP_SANDBOX={scratch} is inside the live sandbox {LIVE_SANDBOX}")
        sys.exit(2)
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


SCRATCH_DIR = _assert_scratch()
DB_PATH = os.environ.get("EXP_DB", str(SCRATCH_DIR / "assistant.db"))
OUT_DIR = Path(__file__).parent

# The filesystem module resolves all paths against SANDBOX_ROOT as a module
# constant. Monkey-patch it to the scratch root BEFORE any file is touched, so
# write_sandbox_file / read_sandbox_file / list_sandbox_files stay in scratch.
import assistant.backend.pipeline.filesystem as _fs  # noqa: E402

_fs.SANDBOX_ROOT = SCRATCH_DIR

from assistant.backend.config import settings  # noqa: E402
from assistant.backend.db.schema import init_db  # noqa: E402
from assistant.backend.memory.store import MemoryStore  # noqa: E402
from assistant.backend.pipeline import tool_executor  # noqa: E402
from assistant.backend.pipeline.files import (  # noqa: E402
    SUPPORTED_UPLOAD_EXTS,
    extract_file_content,
)
from assistant.backend.pipeline.tool_executor import PLAIN_TEXT_EXTS  # noqa: E402

USER_ID = 1

# Intended content: a heading, entity-bearing sentences, and CSV rows. The CSV
# has two columns with the SAME normalized name so the `col_` fallback collision
# (tool uses column index, upload uses row ordinal) is exercised.
DOC = "Quarterly Report\nAda Lovelace shipped the analytical engine in 1843.\n"
CSV_DOC = "name,role\nAda Lovelace,engineer\nGrace Hopper,admiral\n"
# A JSON payload carries entities the extractor surfaces (json extraction records
# scalar values as entities), so the upload arm's entity_* slots are non-empty and
# the missing-entity gap in the tool arm is actually measured.
JSON_DOC = '{"lead": "Ada Lovelace", "project": "analytical engine"}\n'

TEXT_EXTS = ["txt", "md", "csv", "json", "ics"]
BINARY_EXTS = ["docx", "pdf", "xlsx", "xls", "odt", "ods", "odp", "pptx"]

# The agreed target set (see plan.md). Formats dropped by decision (rtf, eml, tsv,
# html, xml) are not run at all, so the record describes the set we will ship,
# not the set we are removing.
TARGET_EXTS = [
    "txt", "md", "csv", "json", "ics",
    "pdf", "docx", "odt", "xlsx", "xls", "ods", "pptx", "odp",
]

# Owning library per binary format, for the fidelity probe. Returns recovered text
# or raises. Kept local so the probe does not depend on extract_file_content
# (which is the thing under test and would mask a fidelity failure).
PROBE_SOURCE = {
    "docx": "docx",
    "pdf": "pypdf",
    "xlsx": "openpyxl",
    "pptx": "pptx",
    "odt": "odfpy",
    "ods": "odfpy",
    "odp": "odfpy",
    "xls": "xlrd",
    "rtf": "striprtf",
}


@dataclass
class FormatResult:
    ext: str
    tool_file_valid: bool | None = None      # H2: owning library opened it
    tool_file_error: str = ""
    tool_recovered_text: str = ""
    upload_slot_keys: list[str] = field(default_factory=list)
    tool_slot_keys: list[str] = field(default_factory=list)
    slots_equal: bool | None = None          # H3
    upload_safe_name: str = ""
    tool_safe_name: str = ""
    safe_name_equal: bool | None = None
    roundtrip_ok: bool | None = None         # H4: real writer -> read back
    roundtrip_error: str = ""
    notes: str = ""


def _intended(ext: str) -> str:
    if ext == "json":
        return JSON_DOC
    return CSV_DOC if ext in ("csv", "tsv", "xls", "xlsx", "ods") else DOC


def _probe_owning_library(path: Path, ext: str) -> tuple[bool, str, str]:
    """Open `path` with the library that owns `ext`. Returns (ok, error, text)."""
    # Text formats have no owning binary library to fail against; validity is
    # "the bytes decode to the intended text", checked separately. Returning
    # False here would read as a fidelity failure when there is nothing to fail.
    if ext in PLAIN_TEXT_EXTS or ext in ("csv", "json", "ics"):
        try:
            return True, "", path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}", ""
    try:
        if ext == "docx":
            from docx import Document

            doc = Document(str(path))
            return True, "", "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        if ext == "pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            return True, "", text
        if ext == "xlsx":
            from openpyxl import load_workbook

            wb = load_workbook(str(path), read_only=True, data_only=True)
            lines = [
                " | ".join("" if v is None else str(v) for v in row)
                for sheet in wb.worksheets
                for row in sheet.iter_rows(values_only=True)
            ]
            wb.close()
            return True, "", "\n".join(lines)
        if ext == "pptx":
            from pptx import Presentation

            prs = Presentation(str(path))
            text = "\n".join(
                shape.text_frame.text
                for slide in prs.slides
                for shape in slide.shapes
                if shape.has_text_frame
            )
            return True, "", text
        if ext in ("odt", "ods", "odp"):
            from odf import teletype
            from odf import text as odftext
            from odf.opendocument import load

            doc = load(str(path))
            return True, "", "\n".join(
                teletype.extractText(p) for p in doc.getElementsByType(odftext.P)
            )
        if ext == "xls":
            import xlrd

            wb = xlrd.open_workbook(filename=str(path))
            return True, "", "\n".join(
                " | ".join(str(v) for v in sheet.row_values(r))
                for sheet in wb.sheets()
                for r in range(sheet.nrows)
            )
        if ext == "rtf":
            from striprtf.striprtf import rtf_to_text

            return True, "", rtf_to_text(path.read_text(errors="replace"))
        return False, "no probe for this extension", ""
    except Exception as exc:  # noqa: BLE001 - the error IS the measurement
        return False, f"{type(exc).__name__}: {exc}", ""


async def _upload_arm(store: MemoryStore, ext: str) -> tuple[list[str], str]:
    """Reproduce upload_file_to_memory's memory shape (frame + slots + rows)."""
    import re

    text_bytes = _intended(ext).encode()
    base = "report"
    safe_filename = f"{base}.{ext}"
    path = SCRATCH_DIR / safe_filename
    path.write_bytes(text_bytes)

    result = await extract_file_content(str(path), ext, text_bytes)

    frame_name = f"file_{base}.{ext}"
    frame = await store.create_frame(
        frame_name, "entity", source_type="file_upload",
        owner_user_id=USER_ID, source_reliability=0.7,
    )
    await store.upsert_slot(frame.id, "file_name", safe_filename)
    await store.upsert_slot(frame.id, "file_ext", ext)
    await store.upsert_slot(frame.id, "file_size", str(len(text_bytes)))
    await store.upsert_slot(frame.id, "file_safe_name", safe_filename)
    for entity in result.key_entities[: settings.file_max_entity_slots]:
        await store.upsert_slot(frame.id, f"entity_{entity}", entity)

    # CSV row frames, exactly as upload_file_to_memory builds them.
    if ext == "csv" and result.row_data:
        import json as _json

        row_count = len(result.row_data)
        await store.upsert_slot(frame.id, "row_count", str(row_count))
        await store.upsert_slot(
            frame.id, "columns", _json.dumps(list(result.row_data[0].keys()))
        )
        for i, row in enumerate(result.row_data[: settings.csv_max_row_frames], 1):
            row_frame = await store.create_frame(
                f"file_{base}_row_{i}", "record",
                source_type="csv_row", owner_user_id=USER_ID,
            )
            for col, val in row.items():
                slot_key = re.sub(r"[^a-zA-Z0-9_]", "_", col.lower().strip())
                slot_key = re.sub(r"_+", "_", slot_key).strip("_")
                if not slot_key:
                    slot_key = f"col_{i}"
                if val is None or not str(val).strip():
                    continue
                await store.upsert_slot(row_frame.id, slot_key, str(val))
            await store.create_association(frame.id, row_frame.id, "part_of")

    slots = await store.get_slots_for_frame(frame.id)
    keys = [s.key for s in slots]
    # Include row-frame keys so the CSV key divergence is in the comparison.
    for a in await store.get_all_associations_for_frame(frame.id):
        if a.relation_type == "part_of":
            child = await store.get_slots_for_frame(a.to_frame_id)
            keys.extend(f"row:{s.key}" for s in child)
    return sorted(keys), safe_filename


async def _tool_arm(store: MemoryStore, ext: str) -> tuple[bool, str, str, list[str], str]:
    """Run execute_write_file and report file validity, text, and frame slots."""
    # Assert the harness wired the global, or the tool would silently skip memory
    # work and report false parity (plan.md threat 2).
    if tool_executor._store is None:
        raise AssertionError("tool_executor._store is None: harness not wired")

    target = f"agent_report.{ext}"
    tool_executor._store = store
    result = await tool_executor.execute_write_file(
        {"path": target, "content": _intended(ext), "overwrite": True},
        user_id=str(USER_ID),
        session_id="exp",
    )
    if not result.success:
        return False, f"write_file failed: {result.error}", "", [], ""

    written = SCRATCH_DIR / target
    ok, err, text = _probe_owning_library(written, ext)

    frame = await store.get_frame_by_name(f"file_{target}")
    slot_keys: list[str] = []
    safe_name = ""
    if frame is not None:
        slots = await store.get_slots_for_frame(frame.id)
        slot_keys = [s.key for s in slots]
        safe_name = next((s.value for s in slots if s.key == "file_safe_name"), "")
        for a in await store.get_all_associations_for_frame(frame.id):
            if a.relation_type == "part_of":
                child = await store.get_slots_for_frame(a.to_frame_id)
                slot_keys.extend(f"row:{s.key}" for s in child)
        slot_keys = sorted(slot_keys)
    return ok, err, text, slot_keys, safe_name


async def _roundtrip_arm(ext: str) -> tuple[bool, str]:
    """H4: can a real writer make a file extract_file_content reads back?"""
    try:
        target = SCRATCH_DIR / f"roundtrip.{ext}"
        if ext == "docx":
            from docx import Document

            d = Document()
            d.add_paragraph(DOC)
            d.save(str(target))
        elif ext == "xlsx":
            from openpyxl import Workbook

            wb = Workbook()
            wb.active["A1"] = "Ada Lovelace"
            wb.save(str(target))
        elif ext == "xls":
            import xlwt

            wb = xlwt.Workbook()
            ws = wb.add_sheet("S")
            ws.write(0, 0, "Ada Lovelace")
            wb.save(str(target))
        elif ext in ("odt", "ods", "odp"):
            from odf.opendocument import OpenDocumentText
            from odf.text import P

            doc = OpenDocumentText()
            doc.text.addElement(P(text=DOC))
            doc.save(str(target))
        elif ext == "pptx":
            from pptx import Presentation

            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = DOC.splitlines()[0]
            prs.save(str(target))
        elif ext == "ics":
            from icalendar import Calendar, Event

            cal = Calendar()
            ev = Event()
            ev.add("summary", "Trip to Rainier")
            cal.add_component(ev)
            target.write_bytes(cal.to_ical())
        elif ext == "pdf":
            try:
                from reportlab.pdfgen import canvas
            except Exception:
                return False, "NO WRITER: reportlab absent (would be a new 1.9MB dep)"
            c = canvas.Canvas(str(target))
            c.drawString(72, 720, DOC.splitlines()[0])
            c.save()
        else:
            return False, "no round-trip writer implemented for this ext"

        result = await extract_file_content(str(target), ext, target.read_bytes())
        got = " ".join(result.text.split())
        # Compare on the salient token, not whole-document equality: a spreadsheet
        # recovers "Ada Lovelace" and a slide recovers its title, neither of which
        # equals the prose DOC verbatim. Requiring full equality reported working
        # writers as failures.
        if ext in ("xlsx", "xls"):
            probe_token = "Ada Lovelace"
        elif ext == "ics":
            probe_token = "Rainier"
        elif ext == "pptx":
            probe_token = "Quarterly Report"
        else:
            probe_token = None
        if probe_token is not None:
            ok = probe_token in result.text
        else:
            want = " ".join(DOC.split())
            ok = got in want or want in got
        return ok, f"recovered={got[:80]!r}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def run() -> None:
    await init_db(DB_PATH)
    store = MemoryStore(DB_PATH)
    tool_executor._store = store

    # A real owner row is required: create_frame sets owner_user_id, a FK to
    # users(id). Without it every frame write fails the constraint.
    await store.create_user("exp_user")

    formats = TARGET_EXTS
    results: list[FormatResult] = []

    for ext in formats:
        r = FormatResult(ext=ext)
        if ext == "md":
            r.notes = (
                "in agreed target set; NOT in SUPPORTED_UPLOAD_EXTS today "
                "(list disagreement to fix)"
                if ext not in SUPPORTED_UPLOAD_EXTS
                else ""
            )

        r.upload_slot_keys, r.upload_safe_name = await _upload_arm(store, ext)

        ok, err, text, slot_keys, safe_name = await _tool_arm(store, ext)
        r.tool_file_valid = ok
        r.tool_file_error = err
        r.tool_recovered_text = " ".join(text.split())[:120]
        r.tool_slot_keys = slot_keys
        r.tool_safe_name = safe_name
        r.slots_equal = r.upload_slot_keys == r.tool_slot_keys
        r.safe_name_equal = r.upload_safe_name == r.tool_safe_name

        if ext in ("docx", "xlsx", "xls", "odt", "ods", "odp", "pptx", "ics", "pdf"):
            rt_ok, rt_note = await _roundtrip_arm(ext)
            r.roundtrip_ok = rt_ok
            r.roundtrip_error = rt_note

        results.append(r)
        print(
            f"{ext:5s} valid={r.tool_file_valid!s:5s} slots_equal={r.slots_equal!s:5s} "
            f"safe_equal={r.safe_name_equal!s:5s}"
            + (f" roundtrip={r.roundtrip_ok}" if r.roundtrip_ok is not None else "")
            + (f"  err={r.tool_file_error[:50]}" if r.tool_file_error else "")
        )

    summary = {
        "formats": [asdict(r) for r in results],
        "supported_upload_exts": sorted(SUPPORTED_UPLOAD_EXTS),
        "text_exts": TEXT_EXTS,
        "binary_exts": BINARY_EXTS,
        "scratch": str(SCRATCH_DIR),
        "db": DB_PATH,
    }
    (OUT_DIR / "result.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {OUT_DIR / 'result.json'}")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    finally:
        # Leave nothing behind, and say so.
        if SCRATCH_DIR.exists():
            shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
            print(f"cleaned scratch: {SCRATCH_DIR}")
