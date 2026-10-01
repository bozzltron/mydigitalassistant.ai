"""Read support for user-supplied documents (Phase 4 R11).

Each extractor runs against a document generated in-memory by the same library
family, so the test carries no binary fixtures. Legacy binaries (.doc/.xls/.ppt)
have no good offline reader and are excluded from the upload allowlist.
"""

import pytest

from assistant.backend.pipeline.files import (
    SUPPORTED_UPLOAD_EXTS,
    extract_file_content,
    extract_text_from_docx,
    extract_text_from_pdf,
    extract_text_from_pptx,
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


@pytest.mark.asyncio
async def test_extract_file_content_routes_new_formats(tmp_path):
    cases = [
        ("pdf", _make_pdf("routed pdf"), "routed pdf"),
        ("docx", _make_docx("routed docx"), "routed docx"),
        ("xlsx", _make_xlsx("routed xlsx"), "routed xlsx"),
        ("pptx", _make_pptx("routed pptx"), "routed pptx"),
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


def test_legacy_binaries_are_not_accepted():
    for ext in ("doc", "xls", "ppt"):
        assert ext not in SUPPORTED_UPLOAD_EXTS
    for ext in ("pdf", "docx", "xlsx", "pptx"):
        assert ext in SUPPORTED_UPLOAD_EXTS
