"""Read support for user-supplied business documents (Phase 4 R11).

Each extractor runs against a document generated in-memory by the same library
family, so the test carries no binary fixtures. Legacy Word/PowerPoint binaries
(.doc/.ppt) have no good offline reader and are excluded from the allowlist;
legacy Excel (.xls) is supported via xlrd.
"""

import pytest

from assistant.backend.pipeline.files import (
    SUPPORTED_UPLOAD_EXTS,
    extract_file_content,
    extract_text_from_docx,
    extract_text_from_eml,
    extract_text_from_ics,
    extract_text_from_odf,
    extract_text_from_pdf,
    extract_text_from_pptx,
    extract_text_from_rtf,
    extract_text_from_tsv,
    extract_text_from_xls,
    extract_text_from_xlsx,
)


def _make_pdf(text: str) -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_docx(text: str) -> bytes:
    from io import BytesIO

    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_xlsx(cell: str) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    wb.active["A1"] = cell
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_pptx(text: str) -> bytes:
    from io import BytesIO

    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    slide.shapes.title.text = text
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _make_ics(summary: str) -> bytes:
    from datetime import date

    from icalendar import Calendar, Event

    cal = Calendar()
    event = Event()
    event.add("summary", summary)
    event.add("dtstart", date(2026, 10, 1))
    cal.add_component(event)
    return cal.to_ical()


def _make_rtf(text: str) -> bytes:
    return rb"{\rtf1\ansi\deff0 " + text.encode() + rb"}"


def _make_odt(text: str) -> bytes:
    from io import BytesIO

    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    doc.text.addElement(P(text=text))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_xls(cell: str) -> bytes:
    from io import BytesIO

    import xlwt

    wb = xlwt.Workbook()
    wb.add_sheet("Sheet1").write(0, 0, cell)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_eml(subject: str, body: str) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    return msg.as_bytes()


def _make_tsv() -> bytes:
    return b"name\tqty\nwidget\t3\n"


def test_pdf_extracts_text():
    text, _, _ = extract_text_from_pdf(_make_pdf("Hello PDF"))
    assert "Hello PDF" in text


def test_docx_extracts_text():
    text, _, _ = extract_text_from_docx(_make_docx("Hello Word"))
    assert "Hello Word" in text


def test_xlsx_extracts_text():
    text, _, _ = extract_text_from_xlsx(_make_xlsx("Hello Excel"))
    assert "Hello Excel" in text


def test_pptx_extracts_text():
    text, _, _ = extract_text_from_pptx(_make_pptx("Hello Slides"))
    assert "Hello Slides" in text


def test_ics_extracts_event():
    text, entities, _ = extract_text_from_ics(_make_ics("Team sync"))
    assert "Team sync" in text
    assert any("Team sync" in e for e in entities)


def test_rtf_extracts_text():
    text, _, _ = extract_text_from_rtf(_make_rtf("Hello RTF"))
    assert "Hello RTF" in text


def test_odt_extracts_text():
    text, _, _ = extract_text_from_odf(_make_odt("Hello OpenDocument"))
    assert "Hello OpenDocument" in text


def test_xls_extracts_text():
    text, _, _ = extract_text_from_xls(_make_xls("Hello Legacy Excel"))
    assert "Hello Legacy Excel" in text


def test_eml_extracts_subject_and_body():
    text, _, _ = extract_text_from_eml(_make_eml("Quarterly report", "Numbers attached."))
    assert "Quarterly report" in text
    assert "Numbers attached." in text


def test_tsv_extracts_text():
    text, _, _ = extract_text_from_tsv(_make_tsv())
    assert "name | qty" in text
    assert "widget | 3" in text


@pytest.mark.asyncio
async def test_extract_file_content_routes_every_supported_format(tmp_path):
    cases = [
        ("pdf", _make_pdf("routed pdf"), "routed pdf"),
        ("docx", _make_docx("routed docx"), "routed docx"),
        ("xlsx", _make_xlsx("routed xlsx"), "routed xlsx"),
        ("pptx", _make_pptx("routed pptx"), "routed pptx"),
        ("ics", _make_ics("routed ics"), "routed ics"),
        ("rtf", _make_rtf("routed rtf"), "routed rtf"),
        ("odt", _make_odt("routed odt"), "routed odt"),
        ("xls", _make_xls("routed xls"), "routed xls"),
        ("eml", _make_eml("routed eml", "body"), "routed eml"),
        ("tsv", _make_tsv(), "widget"),
    ]
    for ext, data, needle in cases:
        result = await extract_file_content(str(tmp_path / f"f.{ext}"), ext, data)
        assert needle in result.text, f"{ext} did not round-trip"


@pytest.mark.asyncio
async def test_corrupt_binary_degrades_without_raising(tmp_path):
    # A file that claims to be a PDF but is not: extraction yields empty text
    # rather than raising or decoding binary to noise.
    result = await extract_file_content(str(tmp_path / "x.pdf"), "pdf", b"\x00\x01not a pdf")
    assert result.text == ""


@pytest.mark.asyncio
async def test_malformed_structured_files_degrade(tmp_path):
    # Regression: the JSON/XML error paths returned a bare "" (and HTML a bare
    # []), which crashed the dispatch unpacking instead of degrading.
    for ext, bad in [("xml", b"<not xml"), ("json", b"{not json")]:
        result = await extract_file_content(str(tmp_path / f"f.{ext}"), ext, bad)
        assert result.text == ""


def test_legacy_word_and_powerpoint_are_not_accepted():
    for ext in ("doc", "ppt"):
        assert ext not in SUPPORTED_UPLOAD_EXTS
    for ext in (
        "txt", "md", "csv", "json", "ics",
        "pdf", "docx", "odt", "xlsx", "xls", "ods", "pptx", "odp",
    ):
        assert ext in SUPPORTED_UPLOAD_EXTS


def test_dropped_formats_are_not_uploadable_but_still_readable():
    """rtf/eml/tsv/html/xml left the upload allowlist but stay readable.

    Files already on disk in those formats must still extract — deleting their
    extractors would orphan existing files. See docs/FILES.md.
    """
    for ext in ("rtf", "eml", "tsv", "html", "xml"):
        assert ext not in SUPPORTED_UPLOAD_EXTS


async def test_dropped_html_still_extracts():
    """The read path still handles a format no longer accepted for upload."""
    from assistant.backend.pipeline.files import extract_file_content

    html = b"<html><body><h1>Kept</h1><p>still readable</p></body></html>"
    result = await extract_file_content("legacy.html", "html", html)
    assert "Kept" in result.text
    assert "still readable" in result.text

