"""Regression: the store refuses blank records, not just the extraction path.

Plan B added blank-record guards on the *extraction* paths — blank frame names, keys,
and values are dropped before they reach a write. That was not enough, and the gap was
found while verifying the release notes against the live brain:

```
blank slot values: 65
```

All 65 were written **that day**, after the extraction guard landed. They came from CSV
row ingestion, which writes one slot per column:

```python
for col, val in row.items():
    ...
    await store.upsert_slot(frame_id=row_frame.id, key=slot_key, value=str(val), ...)
```

An empty cell is `""`, which `str()` happily produces. 100 subscriber rows with two
empty columns each is 65 blank slots — a CSV of empty cells becomes a set of facts that
say nothing.

So the guard belongs where every writer funnels, not on the paths that were thought of.
Same reasoning as the file-content refusal in the same function.

An empty CSV cell is an **absent** fact. Not writing a slot for it is the accurate
representation, not a loss.
"""

from __future__ import annotations

import pytest


class TestStoreRefusesBlankValues:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["", " ", "\t", "\n", "   "])
    async def test_a_blank_value_is_refused(self, store, value):
        frame = await store.create_frame("thing", "entity")
        with pytest.raises(ValueError, match="non-blank value"):
            await store.upsert_slot(frame_id=frame.id, key="city", value=value)

    @pytest.mark.asyncio
    async def test_none_is_refused(self, store):
        frame = await store.create_frame("thing", "entity")
        with pytest.raises(ValueError, match="non-blank value"):
            await store.upsert_slot(frame_id=frame.id, key="city", value=None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["", " ", "\t"])
    async def test_a_blank_key_is_refused(self, store, key):
        frame = await store.create_frame("thing", "entity")
        with pytest.raises(ValueError, match="non-blank key"):
            await store.upsert_slot(frame_id=frame.id, key=key, value="Austin")

    @pytest.mark.asyncio
    async def test_real_values_are_unaffected(self, store):
        """The guard must not block ordinary facts, including falsy-looking ones."""
        frame = await store.create_frame("thing", "entity")
        for key, value in (
            ("city", "Austin"),
            ("count", "0"),          # a legitimate value that reads as falsy
            ("note", "n/a"),         # placeholder text, still a value
            ("flag", "false"),
        ):
            slot, _ = await store.upsert_slot(frame_id=frame.id, key=key, value=value)
            assert slot.value == value

    @pytest.mark.asyncio
    async def test_nothing_was_written_when_refused(self, store):
        frame = await store.create_frame("thing", "entity")
        with pytest.raises(ValueError):
            await store.upsert_slot(frame_id=frame.id, key="city", value="  ")
        assert await store.get_slots_for_frame(frame.id) == []


class TestCsvRowIngestionSkipsEmptyCells:
    """The writer that produced the 65: one slot per column, empty columns included."""

    @pytest.mark.asyncio
    async def test_empty_cells_do_not_become_slots(self, store):
        """Mirrors main.py's row loop: a row with empty columns yields slots only for
        the cells that carry something."""
        user = await store.create_user("alice")
        frame = await store.create_frame(
            "file_row_1", "record", owner_user_id=user.id, source_type="csv_row"
        )
        row = {
            "Email": "someone@example.com",
            "Name": "Someone",
            "Location": "",       # empty cell
            "Groups": None,       # missing cell
        }

        for col, val in row.items():
            if val is None or not str(val).strip():
                continue
            await store.upsert_slot(
                frame_id=frame.id,
                key=col.lower(),
                value=str(val),
                source_type="csv_row",
            )

        slots = {s.key: s.value for s in await store.get_slots_for_frame(frame.id)}
        assert slots == {"email": "someone@example.com", "name": "Someone"}
