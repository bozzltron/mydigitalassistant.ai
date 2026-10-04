"""Regression: a chat attachment is referenced by frame, and its bytes are never text.

The input bar read every attached file with `FileReader.readAsText` and the
backend re-encoded it with `text.encode("utf-8")` before storing. That is
correct for a `.txt` and corrupting for everything else: a PDF decoded as UTF-8
is not a PDF, so the file that reached memory could not be extracted. The fix is
that the client uploads the file through the multipart endpoint first (bytes as
bytes) and the chat request carries a **frame reference**, not content.

These tests pin the reference path, the ownership check, and the legacy
text-only path that old clients still use.
"""

from __future__ import annotations

import pytest

from assistant.backend.main import _build_enhanced_message, _process_attached_files


async def _upload(store, name: str, ext: str, data: bytes, user_id: int):
    from assistant.backend.main import upload_file_to_memory

    return await upload_file_to_memory(
        filename=name, content=data, ext=ext, store=store, user_id=user_id
    )


class TestFrameReferenceIsNotReUploaded:
    @pytest.mark.asyncio
    async def test_a_frame_reference_resolves_to_the_stored_file(self, store):
        user = await store.create_user("alice")
        up = await _upload(store, "notes.txt", "txt", b"hello there", user.id)

        uploaded, contents = await _process_attached_files(
            [{"name": "notes.txt", "ext": "txt", "frame_id": up["frame_id"]}],
            store,
            user.id,
        )

        assert len(uploaded) == 1
        assert uploaded[0]["frame_id"] == up["frame_id"]
        assert uploaded[0]["frame_name"] == up["frame_name"]
        assert uploaded[0]["file_name"] == "notes.txt"
        assert contents[0]["name"] == "notes.txt"

    @pytest.mark.asyncio
    async def test_a_frame_name_reference_also_resolves(self, store):
        user = await store.create_user("alice")
        up = await _upload(store, "report.pdf", "pdf", b"%PDF-1.4 stub", user.id)

        uploaded, _ = await _process_attached_files(
            [{"name": "report.pdf", "ext": "pdf", "frame_name": up["frame_name"]}],
            store,
            user.id,
        )

        assert len(uploaded) == 1
        assert uploaded[0]["frame_id"] == up["frame_id"]

    @pytest.mark.asyncio
    async def test_a_reference_does_not_create_a_second_file(self, store):
        """Referencing must not re-upload: one file, one frame."""
        user = await store.create_user("alice")
        up = await _upload(store, "once.txt", "txt", b"body", user.id)

        await _process_attached_files(
            [{"name": "once.txt", "ext": "txt", "frame_id": up["frame_id"]}],
            store,
            user.id,
        )

        frames = [
            f for f in await store.list_frames("entity") if f.name == up["frame_name"]
        ]
        assert len(frames) == 1, "referencing an uploaded file created a duplicate frame"

    @pytest.mark.asyncio
    async def test_another_users_file_is_refused(self, store):
        """A frame reference must not become a way to read someone else's file."""
        owner = await store.create_user("alice")
        other = await store.create_user("bob")
        up = await _upload(store, "private.txt", "txt", b"secret", owner.id)

        uploaded, contents = await _process_attached_files(
            [{"name": "private.txt", "ext": "txt", "frame_id": up["frame_id"]}],
            store,
            other.id,
        )

        assert uploaded == []
        assert contents == []

    @pytest.mark.asyncio
    async def test_an_unknown_reference_is_skipped_not_fatal(self, store):
        user = await store.create_user("alice")

        uploaded, contents = await _process_attached_files(
            [{"name": "ghost.txt", "ext": "txt", "frame_id": 999_999}],
            store,
            user.id,
        )

        assert uploaded == []
        assert contents == []


class TestLegacyInlineTextStillWorks:
    @pytest.mark.asyncio
    async def test_a_text_attachment_without_a_frame_is_uploaded(self, store):
        user = await store.create_user("alice")

        uploaded, contents = await _process_attached_files(
            [{"name": "pasted.txt", "ext": "txt", "text": "some pasted text"}],
            store,
            user.id,
        )

        assert len(uploaded) == 1
        assert contents[0]["name"] == "pasted.txt"

    @pytest.mark.asyncio
    async def test_an_unsupported_legacy_type_is_skipped(self, store):
        user = await store.create_user("alice")

        uploaded, contents = await _process_attached_files(
            [{"name": "old.doc", "ext": "doc", "text": "binary-ish"}],
            store,
            user.id,
        )

        assert uploaded == []
        assert contents == []


class TestEnhancedMessageNamesTheFile:
    def test_the_summary_points_at_read_file_by_name(self):
        contents = [{"name": "report.pdf", "ext": "pdf", "preview": "Quarterly..."}]
        uploaded = [{"frame_id": 42, "frame_name": "file_report.pdf"}]

        message = _build_enhanced_message("what does this say?", contents, uploaded)

        assert "report.pdf" in message
        assert 'read_file(frame_name="file_report.pdf")' in message
        # The id is deliberately absent: `read_file` resolves by name, and an id
        # in the prompt is what the model echoed back to the user (Phase 1).
        assert "42" not in message
        assert "frame_id" not in message

    def test_no_attachments_leaves_the_message_untouched(self):
        assert _build_enhanced_message("hello", [], []) == "hello"
