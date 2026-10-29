"""The brain's on-disk format must match the configured key.

Live incident 2026-10-29: after moving to a second machine, the backend died at startup with

    ERROR CORE sqlcipher_page_cipher: hmac check failed for pgno=1
    ERROR CORE sqlite3Codec: error decrypting page 1 data: 1

The database was **plain SQLite** — created on an earlier run where `DB_KEY` was empty —
and the new `.env` set a key. Neither message says "your key and your database disagree",
so the failure reads as corruption.

Two properties are pinned here:

1. A mismatch is refused at open time, with a message that names the actual problem and
   both ways out.
2. Encryption that is requested but cannot be applied **raises**. It must never fall back
   to plain SQLite: that writes a brain the configuration believes is encrypted, which is
   worse than failing.
"""

import sqlite3
from pathlib import Path

import pytest

from assistant.backend.db import sqlcipher

PLAIN_HEADER = b"SQLite format 3\x00"


def _plain_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("create table t (x)")
    conn.commit()
    conn.close()
    return path


def _encrypted_db(path: Path, key: str) -> Path:
    sqlcipher3 = pytest.importorskip("sqlcipher3")
    conn = sqlcipher3.connect(str(path))
    conn.execute(f"PRAGMA key = '{key}'")
    conn.execute("create table t (x)")
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The per-process memo must not leak between tests."""
    sqlcipher._encryption_checked.clear()
    yield
    sqlcipher._encryption_checked.clear()


def test_the_header_is_the_detection(tmp_path):
    """Belt and braces: the discriminator is the file header, not a heuristic."""
    plain = _plain_db(tmp_path / "brain.db").read_bytes()
    assert plain[:16] == PLAIN_HEADER
    assert sqlcipher._looks_encrypted(tmp_path / "brain.db") is False


def test_unencrypted_brain_with_a_key_is_refused(tmp_path):
    db = _plain_db(tmp_path / "brain.db")
    with pytest.raises(RuntimeError) as exc:
        sqlcipher.connect(db, encrypted=True)
    message = str(exc.value)
    assert "NOT encrypted" in message
    # Names the raw error the user actually saw, so the report is findable.
    assert "hmac check failed" in message
    assert "docs/SECURITY.md" in message


def test_encrypted_brain_without_a_key_is_refused(tmp_path):
    db = _encrypted_db(tmp_path / "brain.db", "some-key")
    with pytest.raises(RuntimeError) as exc:
        sqlcipher.connect(db, encrypted=False)
    assert "ENCRYPTED" in str(exc.value)


def test_matching_plain_opens(tmp_path):
    db = _plain_db(tmp_path / "brain.db")
    conn = sqlcipher.connect(db, encrypted=False)
    try:
        assert conn.execute("select count(*) from t").fetchone() == (0,)
    finally:
        conn.close()


def test_matching_encrypted_opens(tmp_path):
    from assistant.backend.config import settings

    if not settings.db_key:
        pytest.skip("plain-mode run: DB_KEY is not set, so there is no matching key")
    db = _encrypted_db(tmp_path / "brain.db", settings.db_key)
    conn = sqlcipher.connect(db, encrypted=True)
    try:
        assert conn.execute("select count(*) from t").fetchone() == (0,)
    finally:
        conn.close()


def test_a_new_database_is_not_judged(tmp_path):
    """Nothing on disk yet: SQLCipher writes its salt on first use."""
    sqlcipher._ensure_encryption_matches(tmp_path / "absent.db", encrypted=True)
    sqlcipher._ensure_encryption_matches(tmp_path / "absent.db", encrypted=False)


def test_an_empty_file_is_not_judged(tmp_path):
    empty = tmp_path / "empty.db"
    empty.touch()
    sqlcipher._ensure_encryption_matches(empty, encrypted=True)


def test_no_silent_plaintext_fallback(tmp_path, monkeypatch):
    """Encryption requested and unavailable must raise, not write plaintext."""

    def boom(*args, **kwargs):
        raise RuntimeError("sqlcipher3 is not installed")

    monkeypatch.setattr(sqlcipher, "connect_encrypted", boom)
    with pytest.raises(RuntimeError, match="sqlcipher3 is not installed"):
        sqlcipher.connect(tmp_path / "brain.db", encrypted=True)


async def test_no_silent_plaintext_fallback_async(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("sqlcipher3 is not installed")

    monkeypatch.setattr(sqlcipher, "aiosqlite_connect_encrypted", boom)
    with pytest.raises(RuntimeError, match="sqlcipher3 is not installed"):
        await sqlcipher.aiosqlite_connect(tmp_path / "brain.db", encrypted=True)
