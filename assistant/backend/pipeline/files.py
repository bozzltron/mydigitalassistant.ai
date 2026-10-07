"""File processing pipeline for the cognitive assistant.

Extracts content from user-supplied files (text, structured, and binary
document/spreadsheet/presentation formats) and renders agent-authored files back
into real bytes — and integrates with the memory system.

Read/extract supports a superset of the uploadable formats: formats dropped from
the upload allowlist remain readable for files already on disk. See
`SUPPORTED_UPLOAD_EXTS` and `FILE_WRITERS` below, and docs/FILES.md.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from assistant.backend.pipeline.file_profile import build_profile

logger = logging.getLogger(__name__)

# Formats the upload paths accept, and the formats the agent may write. Reading
# is handled by `extract_file_content`, which supports MORE than this set on
# purpose: files in the dropped formats (rtf/eml/tsv/html/xml) that uploads already
# put on disk must still be readable, so their extractors stay even though new
# uploads of them are refused. See docs/FILES.md and
# assistant/experiments/file_write_parity/.
#
# Legacy Word/PowerPoint binaries (.doc/.ppt) are absent: no good offline
# pure-Python reader, so the user is told to re-save as .docx/.pdf. Legacy Excel
# (.xls) is supported, via xlrd (read) and xlwt (write).
#
# The frontend mirrors this list (frontend/src/utils/uploadFormats.ts); a test
# parses this file so the two cannot drift.
SUPPORTED_UPLOAD_EXTS = frozenset(
    {
        # Text
        "txt", "md", "csv", "json", "ics",
        # Documents
        "pdf", "docx", "odt",
        # Spreadsheets
        "xlsx", "xls", "ods",
        # Presentations
        "pptx", "odp",
    }
)


@dataclass
class FileExtractionResult:
    """Result of file content extraction."""
    text: str  # Plain text extracted from the file
    key_entities: list[str]  # Key entities/concepts found
    open_questions: list[str]  # Potential open questions from the content
    structure_info: dict  # Information about the file structure
    row_data: list[dict] | None = None  # Parsed CSV rows: [{col: value}, ...]


def extract_text_from_txt(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .txt file."""
    try:
        plain_text = content.decode("utf-8", errors="replace")
        return plain_text, [], []
    except Exception:
        return "", [], []


def extract_text_from_csv(content: bytes) -> tuple[str, list[str], list[str], list[dict] | None]:
    """Extract text from a .csv file.

    Returns: (plain_text, key_entities, open_questions, row_data)
    row_data: List of dicts, one per row {column: value}, or None if not a CSV
    """
    import csv
    from io import StringIO

    try:
        text = content.decode("utf-8", errors="replace")
        lines = [line for line in text.strip().split("\n") if line.strip()]

        if not lines:
            return "", [], [], None

        # Parse using csv module for proper handling of quoted fields
        reader = csv.reader(StringIO(text))
        all_rows = list(reader)

        if not all_rows:
            return "", [], [], None

        headers = [h.strip() for h in all_rows[0]]
        row_data = []

        key_entities = []
        open_questions = []

        for row_vals in all_rows[1:]:
            # Pad row if fewer columns than headers
            row_vals = list(row_vals) + [""] * (len(headers) - len(row_vals))
            row = {headers[i]: row_vals[i].strip() for i in range(len(headers))}
            row_data.append(row)

            # Extract entities
            for header, value in row.items():
                if value and value not in ("NA", "N/A", "", "null", "None"):
                    entity_key = f"{header}_{value}"
                    if entity_key not in key_entities:
                        key_entities.append(entity_key)

        open_questions = []
        if row_data:
            open_questions.append(f"Analyze {len(row_data)} rows for patterns and insights")
            # Detect potential ID columns
            for h in headers:
                if h.lower() in ('id', 'uuid', 'key', 'pk'):
                    open_questions.append(f"Column '{h}' appears to be an identifier")

        # Plain text representation
        plain_text = "\n".join(",".join(r) for r in all_rows)
        return plain_text, key_entities, open_questions, row_data

    except Exception as e:
        logger.warning(f"CSV extraction failed: {e}")
        return "", [], [], None


def extract_text_from_json(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .json file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        data = json.loads(content.decode("utf-8", errors="replace"))

        key_entities = []
        open_questions = []

        # Extract key entities from JSON structure
        def extract_entities(obj, prefix=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    new_prefix = f"{prefix}.{key}" if prefix else key
                    if isinstance(value, (str, int, float)) and value:
                        entity_key = f"{prefix}_{value}"
                        if entity_key not in key_entities:
                            key_entities.append(entity_key)
                    elif isinstance(value, (dict, list)):
                        extract_entities(value, new_prefix)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    extract_entities(item, f"{prefix}[{i}]")

        extract_entities(data)

        # Open questions (simple heuristics)
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], str) and len(data[key]) > 500:
                    open_questions.append(f"Analyze detailed content for key: {key}")

        # Plain text representation
        plain_text = json.dumps(data, indent=2)[:5000]
        return plain_text, key_entities, open_questions
    except Exception:
        return "", [], []


def extract_text_from_xml(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .xml file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        # defusedxml, not the stdlib: these are user-supplied files, and the
        # stdlib parser expands entities (XXE / billion laughs) by default.
        from defusedxml.ElementTree import fromstring, tostring

        tree = fromstring(content.decode("utf-8", errors="replace"))

        key_entities = []
        open_questions = []

        # Extract tag names and attributes as entities
        def extract_entities_from_element(element, depth=0):
            tag = element.tag
            attrib = element.attrib

            # Add tag name as entity
            if tag:
                entity_key = f"tag_{tag}"
                if entity_key not in key_entities:
                    key_entities.append(entity_key)

            # Add attributes as entities
            for key, value in attrib.items():
                entity_key = f"attr_{key}_{value}"
                if entity_key not in key_entities:
                    key_entities.append(entity_key)

            # Recurse into children
            for child in element:
                extract_entities_from_element(child, depth + 1)

        extract_entities_from_element(tree)

        # Plain text - extract all text content
        plain_text = tostring(tree, encoding="unicode", method="text")

        # Open questions
        if tree is not None:
            open_questions.append("Review XML structure and extract meaningful data")

        return plain_text, key_entities, open_questions
    except Exception:
        return "", [], []


def extract_text_from_html(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .html file.

    Returns: (plain_text, key_entities, open_questions)
    """
    try:
        from html.parser import HTMLParser

        class HTMLTextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._text: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag in ("br", "hr", "p", "div", "li"):
                    self._text.append("\n")
                elif tag in ("a",):
                    self._text.append(" [LINK] ")

            def handle_data(self, data):
                if data.strip():
                    self._text.append(data.strip())

        extractor = HTMLTextExtractor()
        extractor.feed(content.decode("utf-8", errors="replace"))
        plain_text = " ".join(extractor._text)

        key_entities = []
        open_questions = ["Review HTML content for links, forms, and structured data"]

        return plain_text, key_entities, open_questions
    except Exception:
        return "", [], []


def extract_text_from_ics(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .ics (iCalendar) file via the `icalendar` parser.

    Replaces a hand-rolled regex that missed line folding, escaped separators,
    and TZID/date handling.
    """
    try:
        from icalendar import Calendar

        cal = Calendar.from_ical(content)
        summaries: list[str] = []
        key_entities: list[str] = []
        open_questions: list[str] = []

        for component in cal.walk("VEVENT"):
            summary = str(component.get("SUMMARY", "Untitled event"))
            when = ""
            dtstart = component.get("DTSTART")
            dtend = component.get("DTEND")
            if dtstart is not None:
                when += f" on {dtstart.dt}"
            if dtend is not None:
                when += f" to {dtend.dt}"
            summaries.append(f"Event: {summary}{when}")
            if summary and summary != "Untitled event":
                key_entities.append(f"event_{summary[:50]}")
            open_questions.append(f"Review event: {summary[:50]}")

        plain_text = (
            "\n\n".join(summaries[:5]) if summaries else "No iCalendar events found"
        )
        return plain_text, key_entities, open_questions
    except Exception as e:
        logger.warning(f"iCalendar extraction failed: {e}")
        return "", [], []


# A long document is not worth reading past its first pages for the assistant's
# purposes, and an unbounded parse is a way to stall a request. Cap the pages.
PDF_MAX_PAGES = 50


def extract_text_from_pdf(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .pdf file (the first `PDF_MAX_PAGES` pages)."""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        pages = list(reader.pages)[:PDF_MAX_PAGES]
        text = "\n\n".join((page.extract_text() or "").strip() for page in pages).strip()
        return text, [], ["Review the document for key points"]
    except Exception as e:
        logger.warning(f"PDF extraction failed: {e}")
        return "", [], []


def extract_text_from_docx(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .docx file (paragraphs and table cells)."""
    try:
        from io import BytesIO

        from docx import Document

        doc = Document(BytesIO(content))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts), [], ["Review the document for key points"]
    except Exception as e:
        logger.warning(f"DOCX extraction failed: {e}")
        return "", [], []


def extract_text_from_xlsx(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .xlsx workbook, sheet by sheet."""
    try:
        from io import BytesIO

        from openpyxl import load_workbook

        wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
        lines: list[str] = []
        for sheet in wb.worksheets:
            lines.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                lines.append(" | ".join("" if v is None else str(v) for v in row))
        wb.close()
        return "\n".join(lines), [], ["Review the workbook for key figures"]
    except Exception as e:
        logger.warning(f"XLSX extraction failed: {e}")
        return "", [], []


def extract_text_from_pptx(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .pptx deck, slide by slide."""
    try:
        from io import BytesIO

        from pptx import Presentation

        prs = Presentation(BytesIO(content))
        parts: list[str] = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [
                shape.text_frame.text.strip()
                for shape in slide.shapes
                if shape.has_text_frame and shape.text_frame.text.strip()
            ]
            if texts:
                parts.append(f"# Slide {i}\n" + "\n".join(texts))
        return "\n\n".join(parts), [], ["Review the deck for key points"]
    except Exception as e:
        logger.warning(f"PPTX extraction failed: {e}")
        return "", [], []


def extract_text_from_rtf(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a .rtf (Rich Text Format) file."""
    try:
        from striprtf.striprtf import rtf_to_text

        text = rtf_to_text(content.decode("utf-8", errors="replace"))
        return text, [], ["Review the document for key points"]
    except Exception as e:
        logger.warning(f"RTF extraction failed: {e}")
        return "", [], []


def extract_text_from_odf(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from an OpenDocument file (.odt/.ods/.odp) via odfpy."""
    try:
        from io import BytesIO

        from odf import teletype, text
        from odf.opendocument import load

        doc = load(BytesIO(content))
        parts: list[str] = []
        for para in doc.getElementsByType(text.P):
            value = teletype.extractText(para).strip()
            if value:
                parts.append(value)
        return "\n".join(parts), [], ["Review the document for key points"]
    except Exception as e:
        logger.warning(f"ODF extraction failed: {e}")
        return "", [], []


def extract_text_from_xls(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a legacy .xls (Excel 97-2003) workbook via xlrd."""
    try:
        import xlrd

        wb = xlrd.open_workbook(file_contents=content)
        lines: list[str] = []
        for sheet in wb.sheets():
            lines.append(f"# {sheet.name}")
            for row_idx in range(sheet.nrows):
                lines.append(" | ".join(str(v) for v in sheet.row_values(row_idx)))
        return "\n".join(lines), [], ["Review the workbook for key figures"]
    except Exception as e:
        logger.warning(f"XLS extraction failed: {e}")
        return "", [], []


def extract_text_from_eml(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract headers and the plain-text body from a .eml (RFC 822) message."""
    try:
        import email
        from email import policy

        msg = email.message_from_bytes(content, policy=policy.default)
        parts: list[str] = []
        for header in ("From", "To", "Cc", "Subject", "Date"):
            if msg[header]:
                parts.append(f"{header}: {msg[header]}")
        body = msg.get_body(preferencelist=("plain",))
        if body is not None:
            parts.append(body.get_content())
        return "\n".join(parts), [], ["Review the message for key points"]
    except Exception as e:
        logger.warning(f"EML extraction failed: {e}")
        return "", [], []


def extract_text_from_tsv(content: bytes) -> tuple[str, list[str], list[str]]:
    """Extract text from a tab-separated values (.tsv) file."""
    import csv
    from io import StringIO

    try:
        reader = csv.reader(
            StringIO(content.decode("utf-8", errors="replace")), delimiter="\t"
        )
        lines = [" | ".join(row) for row in reader if row]
        return "\n".join(lines), [], ["Review the table for patterns"]
    except Exception as e:
        logger.warning(f"TSV extraction failed: {e}")
        return "", [], []


async def extract_file_content(
    file_path: str,
    ext: str,
    content: bytes,
) -> FileExtractionResult:
    """Extract content from a file based on its extension.

    Args:
        file_path: Path to the file
        ext: File extension (lowercase)
        content: Raw file bytes

    Returns:
        FileExtractionResult with extracted text, entities, and questions
    """
    # Route to appropriate extractor
    extractors = {
        "txt": extract_text_from_txt,
        "csv": extract_text_from_csv,
        "tsv": extract_text_from_tsv,
        "json": extract_text_from_json,
        "xml": extract_text_from_xml,
        "html": extract_text_from_html,
        "ics": extract_text_from_ics,
        "eml": extract_text_from_eml,
        "pdf": extract_text_from_pdf,
        "docx": extract_text_from_docx,
        "xlsx": extract_text_from_xlsx,
        "pptx": extract_text_from_pptx,
        "xls": extract_text_from_xls,
        "rtf": extract_text_from_rtf,
        "odt": extract_text_from_odf,
        "ods": extract_text_from_odf,
        "odp": extract_text_from_odf,
    }

    extractor = extractors.get(ext)
    if not extractor:
        # Unknown extension: best-effort plain text. Legacy binaries (.doc/.xls/
        # .ppt) never reach here — the upload allowlist rejects them — so this is
        # a plain-text fallback, not a way to read binary documents.
        plain_text = content.decode("utf-8", errors="replace")
        key_entities = []
        open_questions = ["Review file content"]
        row_data = None
    else:
        # Parsing is CPU-bound and synchronous; a large document must not block
        # the event loop, so run it off-thread.
        result = await asyncio.to_thread(extractor, content)
        if len(result) == 4:
            plain_text, key_entities, open_questions, row_data = result
        else:
            plain_text, key_entities, open_questions = result
            row_data = None

    # Structure info
    structure_info = {
        "file_type": ext,
        "file_size": len(content),
        "content_length": len(plain_text),
    }

    return FileExtractionResult(
        text=plain_text,
        key_entities=key_entities,
        open_questions=open_questions,
        structure_info=structure_info,
        row_data=row_data,
    )


# ---------------------------------------------------------------------------
# Writing agent-authored files
# ---------------------------------------------------------------------------
#
# The read side dispatches on the extension (`extract_file_content`); so does the
# write side. Choosing a serializer for a file type is not a reasoning task — the
# experiment showed the model cannot do it (it writes the string, and the file is
# unopenable). The model still decides WHICH format the user wants; only the
# encoding is deterministic here.
#
# Every format in SUPPORTED_UPLOAD_EXTS must have a writer, or `write_file`
# refuses the extension rather than writing a fake file. `csv`/`json`/`txt`/`md`
# are the content verbatim; `ics` needs a real VEVENT (prose-as-text extracts to
# ''); the rest need their binary encoder.


def _render_text(content: str) -> bytes:
    return content.encode("utf-8")


def _render_json(content: str) -> bytes:
    """Render prose as valid JSON.

    Verified in the experiment: writing prose as literal `.json` bytes extracts to
    '' because the parser rejects it — the same failure as `.ics`. Wrap the content
    so the file is real JSON. If the content already parses as JSON, keep it
    verbatim (the agent may have produced a JSON document deliberately).
    """
    import json

    try:
        json.loads(content)
        parsed = content
    except (json.JSONDecodeError, ValueError):
        parsed = json.dumps({"content": content}, indent=2)
    return parsed.encode("utf-8")


def _as_icalendar_document(content: str) -> bytes | None:
    """Normalize `content` if it is already a complete iCalendar document.

    The model knows ICS and frequently writes one directly. Wrapping that in a
    VEVENT buries the real events inside a `DESCRIPTION`: a live write produced a
    single event titled "BEGIN:VCALENDAR" with the actual calendar escaped inside
    it, so reading the file back yielded the wrapper, not the events.

    Returns normalized bytes when `content` parses as a VCALENDAR with at least
    one VEVENT, else None so the caller can fall back. The parse is the check --
    a malformed document (or prose that merely mentions the marker) is rejected.
    """
    from icalendar import Calendar

    if "BEGIN:VCALENDAR" not in content.upper():
        return None
    try:
        cal = Calendar.from_ical(content)
    except Exception:
        return None
    if not cal.walk("VEVENT"):
        return None
    return cal.to_ical()


def _render_ics(content: str) -> bytes:
    """Render content as an iCalendar file, in one of two shapes.

    **A complete iCalendar document** (contains ``BEGIN:VCALENDAR``) -- parsed and
    re-serialized by the `icalendar` library, then written as-is. This is the
    intended input: the model writes ICS directly, and it carries fields no
    hand-rolled convention can (description, status, duration, timezone).

    **Prose** (no document) -- the fallback: a single event titled with the first
    non-empty line, dated today, the whole text as its description. Kept because a
    `.ics` must be a real VEVENT -- prose-as-literal-bytes extracts to '' (the
    parser rejects a bare line). See `file_write_parity`.

    The library does the formatting in both shapes; nothing here builds ICS text.
    """
    import datetime

    from icalendar import Calendar, Event

    from assistant.backend.timeutil import local_tz

    # A document the model already wrote is used as-is. Checked first: without
    # this a raw VCALENDAR fell through to the prose path and got wrapped as one
    # event titled "BEGIN:VCALENDAR", trapping the real events in a DESCRIPTION.
    document = _as_icalendar_document(content)
    if document is not None:
        return document

    summary = next(
        (ln.strip() for ln in content.splitlines() if ln.strip()), "Note"
    )
    cal = Calendar()
    cal.add("prodid", "-//mydigitalassistant//file_write//EN")
    cal.add("version", "2.0")
    event = Event()
    event.add("summary", summary)
    # The user's date, not the container's (which is UTC and can be a day ahead
    # for a negative-offset zone late in the evening).
    event.add("dtstart", datetime.datetime.now(local_tz()).date())
    event.add("description", content)
    cal.add_component(event)
    return cal.to_ical()


def _render_docx(content: str) -> bytes:
    from io import BytesIO

    from docx import Document

    doc = Document()
    for line in content.splitlines():
        doc.add_paragraph(line)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_xlsx(content: str) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in _parse_rows(content):
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_xls(content: str) -> bytes:
    from io import BytesIO

    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    for r, row in enumerate(_parse_rows(content)):
        for c, value in enumerate(row):
            ws.write(r, c, value)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_ods(content: str) -> bytes:
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    for row in _parse_rows(content):
        tr = TableRow()
        for value in row:
            cell = TableCell()
            cell.addElement(P(text=str(value)))
            tr.addElement(cell)
        table.addElement(tr)
    doc.spreadsheet.addElement(table)
    return _odf_bytes(doc)


def _render_odt(content: str) -> bytes:
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    for line in content.splitlines():
        doc.text.addElement(P(text=line))
    return _odf_bytes(doc)


def _render_odp(content: str) -> bytes:
    from odf.draw import Frame, Page, TextBox
    from odf.opendocument import OpenDocumentPresentation
    from odf.text import P

    doc = OpenDocumentPresentation()
    page = Page(name="Slide 1", masterpagename="Default")
    # Text lives in a text-box inside a frame; draw:page forbids a bare text:p.
    frame = Frame(width="24cm", height="18cm", x="2cm", y="1cm")
    box = TextBox()
    box.addElement(P(text=content.splitlines()[0] if content.splitlines() else ""))
    frame.addElement(box)
    page.addElement(frame)
    doc.presentation.addElement(page)
    return _odf_bytes(doc)


def _odf_bytes(doc) -> bytes:
    """Serialize an odfpy document to bytes without a filesystem round-trip."""
    from io import BytesIO

    buf = BytesIO()
    doc.write(buf)
    return buf.getvalue()


def _render_pptx(content: str) -> bytes:
    from io import BytesIO

    from pptx import Presentation

    prs = Presentation()
    lines = [ln for ln in content.splitlines() if ln.strip()] or [""]
    # Title slide, then one bullet slide with the body if there is more.
    title_slide = prs.slides.add_slide(prs.slide_layouts[1])
    title_slide.shapes.title.text = lines[0]
    if len(lines) > 1:
        title_slide.placeholders[1].text = "\n".join(lines[1:])
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _render_pdf(content: str) -> bytes:
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    flow = []
    for line in content.splitlines():
        # Escape XML, then render: reportlab Paragraph parses a mini-markup.
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        flow.append(Paragraph(safe or "&nbsp;", styles["BodyText"]))
        flow.append(Spacer(1, 0.08 * inch))
    doc.build(flow)
    return buf.getvalue()


# Text-ish formats that survive a direct encode.
_TEXT_WRITERS = {
    "txt": _render_text,
    "md": _render_text,
    "csv": _render_text,
    "json": _render_json,
}

# Format -> serializer. Every SUPPORTED_UPLOAD_EXTS ext must appear here or
# write_file refuses it. Kept as one table so "can we write it" and "list it as
# writable" cannot diverge.
FILE_WRITERS = {
    **_TEXT_WRITERS,
    "ics": _render_ics,
    "docx": _render_docx,
    "xlsx": _render_xlsx,
    "xls": _render_xls,
    "ods": _render_ods,
    "odt": _render_odt,
    "odp": _render_odp,
    "pptx": _render_pptx,
    "pdf": _render_pdf,
}


def _parse_rows(content: str) -> list[list[str]]:
    """Parse CSV/TSV-ish content into rows for spreadsheet writers.

    Falls back to one row per line if the content is not comma-separated, so a
    prose body still produces a valid (single-column) sheet rather than failing.
    """
    import csv
    from io import StringIO

    text = content.strip("\n")
    if not text:
        return []
    if "," in text.splitlines()[0]:
        return [row for row in csv.reader(StringIO(text))]
    return [[ln] for ln in content.splitlines()]


def render_file_bytes(ext: str, content: str) -> tuple[bytes | None, str | None]:
    """Render agent-authored text into real bytes for `ext`.

    Returns ``(data, None)`` on success, or ``(None, reason)`` when there is no
    writer for the extension — the caller must refuse rather than write the
    string, which is how a `.docx` came to be a text file Word calls corrupt
    (assistant/experiments/file_write_parity/).
    """
    ext = (ext or "").lower().lstrip(".")
    writer = FILE_WRITERS.get(ext)
    if writer is None:
        writable = ", ".join(sorted(FILE_WRITERS))
        return None, f"cannot write .{ext}: no writer. Writable formats: {writable}"
    try:
        return writer(content), None
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as a tool error
        logger.warning("Rendering .%s failed: %s", ext, exc)
        return None, f"failed to render .{ext}: {exc}"


# ---------------------------------------------------------------------------
# Memory shape of a file (shared by upload and write_file)
# ---------------------------------------------------------------------------


async def apply_file_to_memory(
    store,
    *,
    frame_name: str,
    safe_filename: str,
    ext: str,
    content_bytes: bytes,
    user_id: int,
    source_type: str,
    source_reliability: float,
    display_name: str | None = None,
) -> dict:
    """Record a file's identity in memory. THE single path for upload and write.

    Both the upload endpoint and the ``write_file`` tool funnel through here, so
    the two cannot drift: identical bytes produce an identical frame, entity
    slots, and CSV row frames. The file_write_parity experiment found them
    diverging (upload wrote entity_* slots and row frames; the tool wrote neither)
    purely because this shared step did not exist.

    Callers write the bytes to disk first; this does not touch the filesystem.
    Memory holds what the file IS, never what it CONTAINS (see docs/FILES.md) —
    no content slot is written here.
    """
    from assistant.backend.config import settings

    display_name = display_name or safe_filename
    extraction_result = await extract_file_content(
        safe_filename, ext, content_bytes
    )

    existing_frame = await store.get_frame_by_name(frame_name)
    if existing_frame is None:
        frame = await store.create_frame(
            frame_name,
            "entity",
            source_type=source_type,
            owner_user_id=user_id,
            source_reliability=source_reliability,
        )
    else:
        frame = existing_frame
        # Same-name re-write merges into the existing frame (frame names are
        # UNIQUE); rebuild its CSV row frames so rows do not accumulate.
        stale_rows = [
            a.to_frame_id
            for a in await store.get_all_associations_for_frame(frame.id)
            if a.relation_type == "part_of"
        ]
        if stale_rows:
            await store.prune_frames(stale_rows)

    await store.upsert_slot(
        frame_id=frame.id, key="file_name", value=display_name,
        source_type=source_type, source_reliability=source_reliability,
    )
    await store.upsert_slot(
        frame_id=frame.id, key="file_size", value=str(len(content_bytes)),
        source_type=source_type, source_reliability=source_reliability,
    )
    await store.upsert_slot(
        frame_id=frame.id, key="file_ext", value=ext,
        source_type=source_type, source_reliability=source_reliability,
    )
    # The sandbox-relative path (for an upload, the flattened on-disk name —
    # uploads have no subdirectories). read_file step 2 maps a frame name to this
    # value, and list_files reports it, so a nested tool-written file
    # ('notes/x.txt') must keep its path here, not just its basename.
    await store.upsert_slot(
        frame_id=frame.id, key="file_safe_name", value=safe_filename,
        source_type=source_type, source_reliability=source_reliability,
    )

    # The deterministic profile: what the file IS, computed rather than inferred.
    # One JSON slot, so the memory context can answer "what is this file and where
    # are the gaps?" without reading the bytes. It holds shape, not content (no
    # cell values beyond a bounded categorical distribution), which is why it is
    # allowed where `file_content` is refused.
    #
    # CPU-bound like the extractor above, so it runs off-thread for the same
    # reason: a large file must not block the event loop.
    profile = await asyncio.to_thread(build_profile, ext, content_bytes)
    await store.upsert_slot(
        frame_id=frame.id, key="file_profile", value=json.dumps(profile),
        source_type=source_type, source_reliability=source_reliability,
    )

    entity_cap = settings.file_max_entity_slots
    for entity in extraction_result.key_entities[:entity_cap]:
        await store.upsert_slot(
            frame_id=frame.id, key=f"entity_{entity}", value=entity,
            source_type=source_type, source_reliability=source_reliability,
        )

    row_frame_ids: list[int] = []
    row_count = 0
    if ext == "csv" and extraction_result.row_data:
        row_count = len(extraction_result.row_data)
        await store.upsert_slot(
            frame_id=frame.id, key="row_count", value=str(row_count),
            source_type=source_type, source_reliability=source_reliability,
        )
        await store.upsert_slot(
            frame_id=frame.id, key="columns",
            value=json.dumps(list(extraction_result.row_data[0].keys())),
            source_type=source_type, source_reliability=source_reliability,
        )
        row_frame_cap = settings.csv_max_row_frames
        if row_count > row_frame_cap:
            # Past the cap: keep the first `cap` rows as frames for recall and
            # leave the rest on disk for read_file, so memory cannot explode
            # per-row. (The tool path used to create NONE in this case, while
            # upload created `cap` — a silent divergence; upload's behaviour is
            # the documented one and both paths now share it.)
            logger.info(
                "CSV %s has %d rows; creating first %d row frames only "
                "(CSV_MAX_ROW_FRAMES=%d)",
                safe_filename, row_count, row_frame_cap, row_frame_cap,
            )
        base = safe_filename.rsplit(".", 1)[0].rsplit("/", 1)[-1]
        for i, row in enumerate(extraction_result.row_data[:row_frame_cap], 1):
            row_frame = await store.create_frame(
                f"file_{base}.csv_row_{i}", "record",
                source_type="csv_row", owner_user_id=user_id,
            )
            row_frame_ids.append(row_frame.id)
            for col, val in row.items():
                slot_key = re.sub(r"[^a-zA-Z0-9_]", "_", col.lower().strip())
                slot_key = re.sub(r"_+", "_", slot_key).strip("_")
                if not slot_key:
                    # Row ordinal, not column index: two empty-named columns
                    # must not collide. The tool path used the column index.
                    slot_key = f"col_{i}"
                if val is None or not str(val).strip():
                    continue
                await store.upsert_slot(
                    frame_id=row_frame.id, key=slot_key, value=str(val),
                    source_type="csv_row", source_reliability=source_reliability,
                )
            await store.create_association(frame.id, row_frame.id, "part_of")

    content_text = extraction_result.text
    return {
        "status": "ok",
        "file_name": display_name,
        "file_size": len(content_bytes),
        "file_ext": ext,
        "content_preview": content_text[:200] + ("..." if len(content_text) > 200 else ""),
        "key_entities": extraction_result.key_entities[:entity_cap],
        "open_questions": extraction_result.open_questions,
        "frame_name": frame_name,
        "frame_id": frame.id,
        "parent_frame_id": frame.id,
        "row_count": row_count,
        "row_frame_ids": row_frame_ids,
    }
