"""File processing pipeline for the cognitive assistant.

Handles extraction of content from user-supplied business files: text and
structured formats (.txt, .csv, .tsv, .json, .xml, .html, .ics, .eml) plus binary
documents (.pdf, .docx, .xlsx, .pptx, .xls, .rtf, .odt/.ods/.odp) — and integrates
with the memory system.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Formats the upload paths accept. Reading is handled by `extract_file_content`;
# this is the single source of truth for the three call sites in `main.py`.
# Legacy Word/PowerPoint binaries (.doc/.ppt) are deliberately absent: there is
# no good offline pure-Python reader, so the user is told to re-save as .docx/.pdf.
# Legacy Excel (.xls) is supported, via xlrd.
SUPPORTED_UPLOAD_EXTS = frozenset(
    {
        "txt", "csv", "tsv", "json", "xml", "html", "ics", "eml",
        "pdf", "docx", "xlsx", "pptx", "xls", "rtf", "odt", "ods", "odp",
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
