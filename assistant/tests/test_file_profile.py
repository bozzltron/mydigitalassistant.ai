"""The deterministic file profile: what a file is, computed not inferred.

Phase 5 of plans/2026-10-07-large-file-context.md. The model kept hand-rolling
this ("~92% radio, ~8% venue") and getting it wrong; a profile is arithmetic over
the file, stored on the frame so the answer is a memory read, not a file read.
"""

from __future__ import annotations

import json

import pytest

from assistant.backend.pipeline.file_profile import (
    build_profile,
    profile_summary_lines,
)


def test_csv_profile_has_rows_columns_distinct_and_categoricals():
    csv = "email,name,state\n" + "\n".join(
        f"a{i}@x.com,Name {i},{'CA' if i % 4 == 0 else 'TX'}" for i in range(40)
    )
    profile = build_profile("csv", csv.encode())
    assert profile["kind"] == "csv"
    assert profile["rows"] == 40
    assert profile["columns"] == ["email", "name", "state"]
    assert profile["distinct"]["email"] == 40
    assert profile["distinct"]["state"] == 2
    # The categorical distribution is the "~92% radio" answer, computed.
    assert profile["categorical"]["state"] == {"TX": 30, "CA": 10}


def test_high_cardinality_column_is_not_categorical():
    csv = "id\n" + "\n".join(str(i) for i in range(100))
    profile = build_profile("csv", csv.encode())
    assert "id" not in profile.get("categorical", {})


def test_json_profile_reports_keys_and_sizes():
    profile = build_profile("json", b'{"a": [1, 2, 3], "b": {"x": 1}}')
    assert profile["kind"] == "json"
    assert profile["keys"] == ["a", "b"]
    assert profile["sizes"] == {"a": 3, "b": 1}


def test_json_array_profile_reports_items_and_item_keys():
    profile = build_profile("json", b'[{"a": 1}, {"a": 2}]')
    assert profile["items"] == 2
    assert profile["item_keys"] == ["a"]


def test_markdown_profile_reports_headings():
    profile = build_profile("md", b"# Title\n\ntext\n\n## Section\ntext\n")
    assert profile["kind"] == "markdown"
    assert profile["headings"] == ["# Title", "## Section"]


def test_plain_text_profile_reports_shape():
    profile = build_profile("txt", b"one two\nthree four\n")
    assert profile["kind"] == "txt"
    assert profile["lines"] == 3
    assert profile["words"] == 4


def test_tsv_profile_is_labelled_tsv():
    profile = build_profile("tsv", b"name\tstate\nAda\tTX\nBob\tCA\n")
    assert profile["kind"] == "tsv"
    assert profile["columns"] == ["name", "state"]


def test_blank_categorical_values_are_labelled():
    """A blank cell is a gap, so the distribution names it rather than showing ''."""
    lines = profile_summary_lines(build_profile("csv", b"state\nTX\n\nTX\n"))
    assert any("(blank) 1" in line for line in lines)


def test_broken_json_degrades_to_a_shape_not_an_error():
    profile = build_profile("json", b"{not json")
    assert profile["kind"] == "json"
    assert "lines" in profile


def test_profile_lines_are_compact_and_readable():
    lines = profile_summary_lines(build_profile("csv", b"state\nTX\nTX\nCA\n"))
    assert lines and lines[0].startswith("  profile: csv")
    assert any("state: TX 2, CA 1" in line for line in lines)


def test_no_profile_renders_nothing():
    assert profile_summary_lines(None) == []
    assert profile_summary_lines({}) == []


def test_a_non_dict_profile_renders_nothing():
    """A hand-edited slot could hold any JSON; the renderer must not raise."""
    assert profile_summary_lines(["not", "a", "dict"]) == []


@pytest.mark.asyncio
async def test_a_file_frame_carries_its_profile(store):
    """The write path stores the profile, so the prompt can carry the shape."""
    from assistant.backend.pipeline.files import apply_file_to_memory

    user = await store.create_user("test_user")
    csv = "email,state\n" + "\n".join(f"a{i}@x.com,TX" for i in range(5))
    summary = await apply_file_to_memory(
        store,
        frame_name="file_phase5.csv",
        safe_filename="phase5.csv",
        ext="csv",
        content_bytes=csv.encode(),
        user_id=user.id or 1,
        source_type="file_create",
        source_reliability=0.8,
    )

    slots = {
        s.key: s.value
        for s in await store.get_slots_for_frame(summary["frame_id"])
    }
    assert "file_profile" in slots
    profile = json.loads(slots["file_profile"])
    assert profile["rows"] == 5
    assert profile["categorical"]["state"] == {"TX": 5}


def test_format_memory_context_renders_the_profile_not_raw_json():
    from assistant.backend.memory.models import Frame, Slot
    from assistant.backend.memory.retrieval import (
        MemoryContext,
        RetrievedFrame,
        format_memory_context,
    )

    frame = Frame(
        id=1,
        name="file_phase5.csv",
        type="entity",
        confidence=0.8,
        source_type="file_upload",
    )
    slots = [
        Slot(
            id=1, frame_id=1, key="file_safe_name",
            value="phase5.csv", confidence=0.9,
        ),
        Slot(
            id=2, frame_id=1, key="file_profile",
            value=json.dumps(
                {"kind": "csv", "rows": 40, "columns": ["state"],
                 "categorical": {"state": {"TX": 30, "CA": 10}}}
            ),
            confidence=0.9,
        ),
    ]
    rf = RetrievedFrame(
        frame=frame, slots=slots, associations=[], relevance=0.9, source="direct_match"
    )
    formatted = format_memory_context(
        MemoryContext(
            query="phase5", retrieved_frames=[rf], recent_episodes=[], formatted=""
        )
    )

    assert "profile: csv" in formatted
    assert "state: TX 30, CA 10" in formatted
    assert '"kind"' not in formatted  # the raw JSON slot is not dumped
