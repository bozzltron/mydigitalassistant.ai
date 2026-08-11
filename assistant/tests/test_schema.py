from assistant.backend.db.schema import init_db


async def test_init_db_creates_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)

    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r[0] for r in rows}
        expected = {
            "users",
            "frames",
            "slots",
            "slot_history",
            "associations",
            "episodes",
            "conflicts",
            "sqlite_sequence",
        }
        assert expected.issubset(tables)


async def test_init_db_idempotent(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    await init_db(db_path)

    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='frames'"
        )
        assert len(rows) == 1
