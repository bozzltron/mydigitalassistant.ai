"""CLI for database operations."""

import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db

console = Console()


async def upgrade_db():
    """Upgrade database schema to latest version."""
    db_path = settings.database_path
    
    console.print(f"📦 Upgrading database at [cyan]{db_path}[/cyan]")
    
    try:
        await init_db(db_path)
        console.print("✓ Database schema updated")
        return True
    except Exception as e:
        console.print(f"✗ Failed to upgrade: {e}")
        return False


async def migrate_embeddings():
    """Migrate frame embeddings from JSON to vec_f32."""
    db_path = settings.database_path
    
    console.print(f"⚡ Migrating frame embeddings to sqlite-vec at [cyan]{db_path}[/cyan]")
    
    try:
        import json

        import aiosqlite
        import sqlite_vec
        
        db = await aiosqlite.connect(db_path)
        await db.enable_load_extension(True)
        
        try:
            await db.load_extension(sqlite_vec.loadable_path())
            console.print("✓ sqlite-vec extension loaded")
        except Exception as e:
            console.print(f"✗ sqlite-vec not available: {e}")
            await db.close()
            return False
        
        # Count existing embeddings
        async with db.execute("SELECT COUNT(*) FROM frame_embeddings") as cursor:
            count = (await cursor.fetchone())[0]
        
        if count == 0:
            console.print("✓ No embeddings to migrate")
            await db.close()
            return True
        
        # Migrate
        async with db.execute("SELECT frame_id, embedding FROM frame_embeddings") as cursor:
            rows = await cursor.fetchall()
        
        migrated = 0
        for frame_id, embedding_json in rows:
            try:
                embedding = json.loads(embedding_json)
                await db.execute(
                     "UPDATE frame_embeddings SET embedding = vec_f32(?) WHERE frame_id = ?",
                     (json.dumps(embedding), frame_id),
                 )
                migrated += 1
            except (json.JSONDecodeError, TypeError):
                pass
        
        await db.commit()
        await db.close()
        
        console.print(f"✓ Migrated {migrated}/{count} embeddings to vec_f32")
        return True
        
    except Exception as e:
        console.print(f"✗ Migration failed: {e}")
        return False


async def backup_db():
    """Create a raw file copy of the database (encryption-preserving)."""
    import shutil
    from datetime import UTC, datetime

    db_path = Path(settings.database_path)
    if not db_path.exists():
        console.print("✗ No database found")
        return False

    from assistant.backend.db.sqlcipher import open_checked_db

    async with open_checked_db(str(db_path)) as adb:
        await adb.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = Path(f"backup-{timestamp}.db")

    shutil.copy2(db_path, backup_path)
    console.print(f"✓ Backup created: [green]{backup_path}[/green]")
    return True


async def export_db(export_path: Path | None = None):
    """Export database to SQL dump for migration to encrypted brain."""
    import gzip
    from datetime import UTC, datetime

    # This writes a plaintext SQL dump with `sqlite3.connect`, which cannot read a
    # SQLCipher database: with DB_KEY set the command fails with "file is not a
    # database", which reads like corruption. Point at the supported path instead.
    if settings.db_key:
        console.print(
            "✗ The brain is encrypted, so a plaintext SQL dump cannot read it.\n"
            "  Use [cyan]assistant db export-portable[/cyan] — the supported backup."
        )
        return False

    db_path = Path(settings.database_path)
    if not db_path.exists():
        console.print("✗ No database found")
        return False

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    export_path = export_path or Path(f"brain-export-{timestamp}.sql.gz")

    console.print(f"Exporting [cyan]{db_path}[/cyan] to [green]{export_path}[/green]...")

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")

    with gzip.open(export_path, "wt") as f:
        for line in conn.iterdump():
            f.write(line + "\n")

    conn.close()
    console.print(f"✓ Exported to [green]{export_path}[/green]")
    return True


def is_inside_sandbox(path: str | Path) -> bool:
    """Whether a destination would land in the agent's writable file sandbox.

    The sandbox is where the file tools operate: the agent can list, read and
    **delete** anything in it. A brain export is the protection artifact — the
    thing you restore from when a write goes wrong — so it must not live somewhere
    the agent can remove. Measured: the first verified export was written to
    `/app/data/brain-exports/`, readable and deletable by `delete_file`.
    """
    from assistant.backend.pipeline.filesystem import SANDBOX_ROOT

    try:
        Path(path).resolve().relative_to(SANDBOX_ROOT)
        return True
    except ValueError:
        return False


async def export_portable(export_path: Path | None = None):
    """Export the brain as a portable, self-contained `.assistant-brain` file.

    This is the protection step before any major memory work: a merged snapshot
    (write-ahead log included), encrypted with DB_KEY, renameable and restorable
    on any instance with the same key. `restore-portable --new-db` proves it is
    restorable without touching the live brain.
    """
    from datetime import UTC, datetime

    from assistant.backend.memory.backup import export_portable_brain

    db_path = Path(settings.database_path)
    if not db_path.exists():
        console.print("✗ No database found")
        return False

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    export_path = Path(export_path) if export_path else Path(f"brain-{timestamp}.assistant-brain")

    if is_inside_sandbox(export_path):
        from assistant.backend.pipeline.filesystem import SANDBOX_ROOT

        console.print(
            f"✗ Refusing to write the export inside the agent's sandbox "
            f"([yellow]{SANDBOX_ROOT}[/yellow]): the file tools can delete it. "
            "Use a path outside it, e.g. /app/brain/exports."
        )
        return False

    console.print(f"Exporting portable brain [cyan]{db_path}[/cyan] → [green]{export_path}[/green]")
    try:
        result = await export_portable_brain(export_path, db_path=str(db_path))
    except Exception as exc:
        console.print(f"✗ Portable export failed: [red]{exc}[/red]")
        return False

    console.print("✓ Portable brain exported")
    table = Table(show_header=False)
    table.add_row("Path", str(result["path"]))
    table.add_row("Version", str(result["version"]))
    table.add_row("Key id", str(result["key_id"]))
    table.add_row("Frames", f"{result['frame_count']:,}")
    table.add_row("Slots", f"{result['slot_count']:,}")
    table.add_row("Associations", f"{result['association_count']:,}")
    table.add_row("Size", f"{result['file_size_bytes']:,} bytes")
    console.print(table)
    console.print(
        "Verify it before trusting it: "
        f"[cyan]assistant db restore-portable {export_path} --new-db /tmp/verify-brain.db[/cyan]"
    )
    return True


async def restore_portable(
    src_path: Path,
    new_db_path: Path | None = None,
):
    """Restore a portable brain, or prove one is restorable into a scratch DB.

    With `--new-db`, the live brain is not touched: the file is restored to that
    path only. Without it, the current database is replaced (a
    `.pre-portable-restore` copy is made first).
    """
    from assistant.backend.memory.backup import restore_portable_brain

    src_path = Path(src_path)
    if not src_path.exists():
        console.print(f"✗ Portable brain not found: [red]{src_path}[/red]")
        return False

    target = Path(new_db_path) if new_db_path else Path(settings.database_path)
    console.print(f"Restoring [cyan]{src_path}[/cyan] → [green]{target}[/green]")
    try:
        result = await restore_portable_brain(src_path, db_path=str(target))
    except Exception as exc:
        console.print(f"✗ Restore failed: [red]{exc}[/red]")
        return False

    console.print(f"✓ Restored to [green]{target}[/green]")
    for key in ("frame_count", "slot_count", "association_count"):
        if key in result:
            console.print(f"  {key}: {result[key]:,}")
    return True


async def import_db(export_path: Path, new_db_path: Path | None = None):
    """Import SQL dump into a new (optionally encrypted) database.

    Use this to migrate from an unencrypted brain to an encrypted one:
      1. Set DB_KEY in .env
      2. Run: assistant db import brain-export-xxx.sql.gz --new-db /app/data/assistant.db
    """
    from assistant.backend.db.schema import init_db

    export_path = Path(export_path)
    if not export_path.exists():
        console.print(f"✗ Export file not found: [red]{export_path}[/red]")
        return False

    new_db_path = new_db_path or Path(settings.database_path)
    console.print(
        f"Importing [cyan]{export_path}[/cyan] into [green]{new_db_path}[/green]"
        + (" (encrypted)" if settings.db_key else " (unencrypted)")
    )

    import gzip
    import sqlite3

    if new_db_path.exists():
        console.print("[yellow]WARNING: new database exists and will be overwritten.[/yellow]")
        new_db_path.unlink()

    temp_conn = sqlite3.connect(str(new_db_path))
    temp_conn.execute("PRAGMA foreign_keys = ON")
    temp_conn.commit()
    temp_conn.close()

    await init_db(str(new_db_path))

    imported = 0
    with gzip.open(export_path, "rt") as f:
        sql_commands = f.read()

    conn = sqlite3.connect(str(new_db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(sql_commands)
    conn.commit()
    conn.close()

    imported += 1
    console.print(f"✓ Imported to [green]{new_db_path}[/green]")
    return True


async def status():
    """Show database status."""
    db_path = Path(settings.database_path)

    table = Table(title="Database Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    if db_path.exists():
        table.add_row("Path", str(db_path))
        table.add_row("Size", f"{db_path.stat().st_size:,} bytes")
    else:
        table.add_row("Status", "Not found")
        return False

    # Check if vec extension is available
    try:
        import aiosqlite
        db = await aiosqlite.connect(db_path)
        await db.enable_load_extension(True)
        try:
            await db.load_extension("vec0")
            table.add_row("Vector Search", "Available")
            await db.close()
        except Exception:
            table.add_row("Vector Search", "Not available")
            await db.close()
    except Exception:
        table.add_row("Vector Search", "Error")

    console.print(table)
    return True


async def consolidate_db(execute: bool = False):
    """Merge duplicate frames in memory (Phase 9B).

    Defaults to dry-run: prints the merge plan without writing. Pass
    --execute to apply it.
    """
    from assistant.backend.memory.consolidate import run_consolidation

    db_path = settings.database_path
    mode = "[bold green]EXECUTE[/bold green]" if execute else "[bold yellow]DRY RUN[/bold yellow]"
    console.print(f"Running consolidation in {mode} mode on [cyan]{db_path}[/cyan]...")

    def _make_embed_fn():
        """Embedding callable built from settings (container-aware base_url)."""
        from assistant.backend.pipeline.llm_client import OllamaClient

        client = OllamaClient(
            base_url=settings.ollama_url,
            embedding_model=settings.embedding_model,
            timeout=settings.ollama_timeout,
            keep_alive=settings.ollama_keep_alive,
        )

        async def _embed(text):
            """Single string or batch, matching Orchestrator.embed_fn.

            Consolidation batches frame names for the frames that have no stored
            vector; a single-string-only callable made that batch raise, so the
            missing vectors were never filled and Pass 3 silently skipped them.
            """
            resp = await client.embed(text)
            if isinstance(text, list):
                return [r.embedding for r in resp]
            return resp.embedding

        return _embed

    try:
        # The scheduler snapshots before applying; this path did not. A manual
        # `--execute` is exactly where a large batch is most tempting, so it gets
        # the same protection.
        if execute:
            from datetime import UTC, datetime

            from assistant.backend.memory.backup import create_encrypted_backup

            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            dest = Path(db_path).parent / f"assistant-pre-consolidate-{stamp}.db"
            await create_encrypted_backup(dest, db_path=db_path)
            console.print(f"✓ Snapshot before merging: [green]{dest}[/green]")

        try:
            embed_fn = _make_embed_fn()
            await embed_fn("consolidation probe")
        except Exception as e:
            embed_fn = None
            console.print(
                f"[yellow]Embedding model unreachable ({e}) — "
                "normalized-name matching only.[/yellow]"
            )
        # The same bound the unattended scheduler uses. Without it, `--execute`
        # applied the whole plan (70 merges on the live brain) in one go — the
        # circuit breaker protected the automatic path and not the manual one,
        # which is where a large batch is most tempting.
        report = await run_consolidation(
            db_path,
            dry_run=not execute,
            embed_fn=embed_fn,
            max_merges=settings.consolidation_max_merges_per_run if execute else None,
        )
        if report.planned_merges:
            table = Table(title="Duplicate frame merges")
            table.add_column("Survivor")
            table.add_column("Merges away")
            for merge in report.planned_merges:
                table.add_row(
                    f"#{merge.survivor_id} {merge.survivor_name}",
                    f"#{merge.loser_id} {merge.loser_name}",
                )
            console.print(table)
        else:
            console.print("No duplicate frames found.")
        console.print(f"  {report.summary()}")
        if not execute:
            console.print("[yellow]Dry run — no changes written. Use --execute to apply.[/yellow]")
        return True
    except Exception as e:
        console.print(f"[red]Consolidation failed: {e}[/red]")
        return False


async def reembed_db(target_model: str | None = None):
    """Re-embed all frames with a new embedding model.

    This updates the stored embeddings to use a different embedding model.
    Old embeddings for other models are retained until this completes successfully.
    """
    from assistant.backend.memory.metadata import (
        METADATA_KEY_EMBEDDING_MODEL,
        set_metadata,
    )
    from assistant.backend.memory.store import MemoryStore
    from assistant.backend.pipeline.llm_client import OllamaClient

    db_path = settings.database_path
    model = target_model or settings.embedding_model

    console.print(f"🔄 Re-embedding all frames with model [cyan]{model}[/cyan]...")

    try:
        store = MemoryStore(db_path)
        llm_client = OllamaClient(
            base_url=settings.ollama_url,
            embedding_model=model,
            verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
        )

        all_frames = await store.list_frames()
        frame_ids = [f.id for f in all_frames if f.id is not None]

        if not frame_ids:
            console.print("  No frames to re-embed.")
            await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, model)
            console.print(f"✓ Metadata updated: embedding_model = {model}")
            return True

        total = len(frame_ids)
        batch_size = 100
        embedded = 0

        # embed_fn must be (text) -> list[float]. Passing llm_client.embed straight
        # through hands back an EmbeddingResponse, which json.dumps rejects -- every
        # frame then failed inside embed_frames and the count came back zero, so
        # this command reported success having re-embedded nothing.
        async def embed_text(text: str) -> list[float]:
            resp = await llm_client.embed(text)
            return resp.embedding

        for i in range(0, total, batch_size):
            batch = frame_ids[i:i + batch_size]
            try:
                embedded += await store.embed_frames(batch, embed_text, model)
                console.print(f"  [{embedded}/{total}] Embedded batch {i // batch_size + 1}")
            except Exception as e:
                console.print(f"[red]Error embedding batch {i // batch_size + 1}: {e}[/red]")

        if embedded < total:
            console.print(
                f"[red]Only {embedded}/{total} frames were embedded. The metadata key is "
                f"NOT being advanced -- leave it on the old model so the audit still "
                f"reports the gap.[/red]"
            )
            return False

        await set_metadata(db_path, METADATA_KEY_EMBEDDING_MODEL, model)
        msg = f"Re-embedded {embedded}/{total} frames. Metadata updated: embedding_model = {model}"
        console.print(f"✓ {msg}")
        return True
    except Exception as e:
        console.print(f"[red]Re-embed failed: {e}[/red]")
        return False


async def main(
    command: str,
    dry_run: bool = False,
    execute: bool = False,
    embed_cap: int | None = None,
    reembed_model: str | None = None,
    export_path: Path | None = None,
    import_path: Path | None = None,
    new_db_path: Path | None = None,
):
    """Main CLI entry point."""
    if command == "upgrade":
        success = await upgrade_db()
    elif command == "migrate":
        success = await migrate_embeddings()
    elif command == "backup":
        success = await backup_db()
    elif command == "export":
        success = await export_db(export_path)
    elif command == "export-portable":
        success = await export_portable(export_path)
    elif command == "restore-portable":
        if not import_path:
            console.print("[red]restore-portable requires a file path[/red]")
            return False
        success = await restore_portable(import_path, new_db_path)
    elif command == "import":
        if not import_path:
            console.print("[red]--import <file> is required[/red]")
            return False
        success = await import_db(import_path, new_db_path)
    elif command == "status":
        success = await status()
    elif command == "consolidate":
        success = await consolidate_db(execute=execute)
    elif command == "embed-episodes":
        success = await embed_episodes_db(cap=embed_cap)
    elif command == "reembed":
        success = await reembed_db(target_model=reembed_model)
    else:
        console.print(f"Unknown command: [red]{command}[/red]")
        return False

    return success


def main_entry():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: assistant db <command> [--dry-run] [--execute] [--model <model>]")
        print(
            "Commands: upgrade, migrate, backup, export, import, "
            "status, consolidate, reembed"
        )
        sys.exit(1)

    command = sys.argv[1]
    dry_run = "--dry-run" in sys.argv
    execute = "--execute" in sys.argv
    reembed_model = None
    export_path = None
    import_path = None
    new_db_path = None
    for j, arg in enumerate(sys.argv):
        if arg == "--model" and j + 1 < len(sys.argv):
            reembed_model = sys.argv[j + 1]
        elif arg == "--export" and j + 1 < len(sys.argv):
            export_path = Path(sys.argv[j + 1])
        elif arg == "--import" and j + 1 < len(sys.argv):
            import_path = Path(sys.argv[j + 1])
        elif arg == "--new-db" and j + 1 < len(sys.argv):
            new_db_path = Path(sys.argv[j + 1])

    if not asyncio.run(
        main(
            command,
            dry_run=dry_run,
            execute=execute,
            reembed_model=reembed_model,
            export_path=export_path,
            import_path=import_path,
            new_db_path=new_db_path,
        )
    ):
        sys.exit(1)


if __name__ == "__main__":
    main_entry()


async def embed_episodes_db(cap: int | None = None):
    """Backfill embeddings for conversation turns missing vectors.

    Semantic episode recall needs every stored turn indexed. New turns are
    embedded at write time; this command archives the backlog (e.g. after
    upgrading an existing brain).
    """
    from assistant.backend.config import settings
    from assistant.backend.db.schema import init_db
    from assistant.backend.memory.store import MemoryStore

    await init_db(settings.database_path)
    store = MemoryStore(settings.database_path)

    from assistant.backend.pipeline.llm_client import OllamaClient

    client = OllamaClient(
        base_url=settings.ollama_url,
        embedding_model=settings.embedding_model,
        timeout=settings.ollama_timeout,
    )

    async def embed(text: str) -> list[float]:
        resp = await client.embed(text)
        return resp.embedding

    done = await store.embed_missing_episodes(
        embed, embedding_model=settings.embedding_model, cap=cap
    )
    console.print(f"✓ Embedded [green]{done}[/green] episodes")
    return True
