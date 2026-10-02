"""Regression: `read_file` reads a document through the extractor, not as bytes.

The bug (F3 in `plans/2026-10-02-manual-test-round-2.md`): upload ran
`extract_file_content` and stored the file, but `execute_read_file` called
`read_sandbox_file`, which does `path.read_text(encoding="utf-8",
errors="replace")`. For any container format — PDF, .docx, .xlsx, .pptx, .rtf,
.odt — that yields binary noise, and for a large one it hit the 1 MB read cap
first. A 6.7 MB press kit extracted to 9,213 clean characters at upload and was
unreadable at read.

The synthetic PDF used elsewhere in the suite (`_make_pdf`) is a single
uncompressed content stream, which is why this class of bug survived it. The
fixture here is a **real** PDF produced by a PDF writer: compressed object
streams, a real font, positioned glyphs. It is the shape of file a user actually
uploads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "real_report.pdf"


def _store_pdf(tmp_path: Path) -> Path:
    """Place the real PDF at a sandbox-relative path under /app/data."""
    from assistant.backend.pipeline import filesystem

    root = filesystem.SANDBOX_ROOT
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "phase2_real_report.pdf"
    dest.write_bytes(FIXTURE_PDF.read_bytes())
    return dest


@pytest.fixture(autouse=True)
def _cleanup_sandbox_file():
    yield
    from assistant.backend.pipeline import filesystem

    for name in ("phase2_real_report.pdf", "phase2_note.txt"):
        try:
            (filesystem.SANDBOX_ROOT / name).unlink()
        except FileNotFoundError:
            pass


class TestReadFileUsesTheExtractor:
    @pytest.mark.asyncio
    async def test_a_real_pdf_reads_as_text(self, store):
        """The regression: this returned binary noise before the fix."""
        _store_pdf(Path("/tmp"))

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_real_report.pdf"}, user_id="1", session_id="s"
        )

        assert result.success, f"read_file failed: {result.error}"
        content = result.data["content"]
        # Real words from the document, not PDF syntax.
        assert "Quarterly Report Q3" in content
        assert "Operating margin" in content
        # And none of the signatures of a raw-bytes read.
        assert "%PDF" not in content
        assert "endstream" not in content
        assert "FlateDecode" not in content

    @pytest.mark.asyncio
    async def test_a_plain_text_file_is_read_verbatim(self, store):
        """The extractor is not used where a direct read is correct."""
        from assistant.backend.pipeline import filesystem

        filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        (filesystem.SANDBOX_ROOT / "phase2_note.txt").write_text(
            "line one\nline two\n", encoding="utf-8"
        )

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_note.txt"}, user_id="1", session_id="s"
        )

        assert result.success
        assert result.data["content"] == "line one\nline two\n"

    @pytest.mark.asyncio
    async def test_a_file_with_no_text_layer_says_so(self, store):
        """An image-only PDF reports why, rather than an empty success.

        Empty content reads to the model as "the file is empty", which is a lie
        about a scanned document. It must be told the text could not be
        extracted. Built with pypdf so the file is genuinely valid — a
        hand-written stub is a parse error, which is a different case
        (see the test below).
        """
        from pypdf import PdfWriter

        from assistant.backend.pipeline import filesystem

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)  # a page, no text, no fonts
        from io import BytesIO

        buf = BytesIO()
        writer.write(buf)

        filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        (filesystem.SANDBOX_ROOT / "phase2_real_report.pdf").write_bytes(buf.getvalue())

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_real_report.pdf"}, user_id="1", session_id="s"
        )

        assert not result.success
        assert "no extractable text" in result.error or "scanned" in result.error

    @pytest.mark.asyncio
    async def test_a_corrupt_document_is_not_reported_as_missing(self, store):
        """A file that exists but cannot be parsed says so.

        Collapsing every read failure into "File not found" sends the model
        hunting for a file it already has, which is worse than saying the file is
        unreadable.
        """
        from assistant.backend.pipeline import filesystem

        filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        (filesystem.SANDBOX_ROOT / "phase2_real_report.pdf").write_bytes(
            b"%PDF-1.4\nthis is not a valid pdf body\n%%EOF\n"
        )

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_real_report.pdf"}, user_id="1", session_id="s"
        )

        assert not result.success
        assert "File not found" not in result.error
        assert "Could not read" in result.error


class TestSandboxSafetySurvives:
    """Removing the size limits must not weaken path safety.

    The size check lived in the same `validate_path_safety` function as the
    symlink-escape guard, and the read path now catches more exceptions to
    distinguish "missing" from "unreadable". Either could have swallowed the
    traversal rejection; both are pinned here.
    """

    @pytest.mark.asyncio
    async def test_traversal_is_still_rejected(self, store):
        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "../../../etc/passwd"}, user_id="1", session_id="s"
        )

        assert not result.success
        assert "escapes sandbox" in result.error, (
            "a traversal path was not reported as a sandbox escape"
        )

    def test_the_symlink_guard_is_still_defined(self):
        from assistant.backend.pipeline.filesystem import (
            SymlinkEscapeError,
            validate_path_safety,
        )

        assert callable(validate_path_safety)
        assert issubclass(SymlinkEscapeError, Exception)


class TestNoSizeLimits:
    @pytest.mark.asyncio
    async def test_a_document_larger_than_one_megabyte_reads(self, store):
        """The 1 MB read cap is gone; a large text file reads in full.

        The file is stored whole. Only what is *returned to the model* is
        bounded, and the bound is the context window, not a file policy.
        """
        from assistant.backend.pipeline import filesystem

        filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        big = "x" * 1_500_000 + "\nEND OF FILE\n"
        (filesystem.SANDBOX_ROOT / "phase2_note.txt").write_text(big, encoding="utf-8")

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_note.txt"}, user_id="1", session_id="s"
        )

        assert result.success, f"a >1MB file was refused: {result.error}"
        # The true size is reported, even though the model sees a bounded slice.
        assert result.data["total_chars"] == len(big)

    @pytest.mark.asyncio
    async def test_the_model_bound_is_marked_honestly(self, store):
        """A truncated read tells the model it saw a fragment, and how big the whole is."""
        from assistant.backend.pipeline import filesystem

        filesystem.SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        big = "y" * 200_000
        (filesystem.SANDBOX_ROOT / "phase2_note.txt").write_text(big, encoding="utf-8")

        from assistant.backend.pipeline.tool_executor import execute_read_file

        result = await execute_read_file(
            {"path": "phase2_note.txt"}, user_id="1", session_id="s"
        )

        assert result.success
        content = result.data["content"]
        assert "truncated" in content
        assert f"{len(big):,}" in content
