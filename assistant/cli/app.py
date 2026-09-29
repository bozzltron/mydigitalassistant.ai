import argparse
import asyncio
import os
import sys
from datetime import datetime
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from assistant.backend.config import settings

console = Console()
# Get backend URL from environment or use default
BACKEND_URL_ENV = os.environ.get("ASSISTANT_BACKEND")
if BACKEND_URL_ENV:
    DEFAULT_BACKEND = BACKEND_URL_ENV
else:
    DEFAULT_BACKEND = f"http://{settings.backend_host}:{settings.backend_port}"


class BackendClient:
    """Thin HTTP client for the FastAPI backend."""

    def __init__(self, base_url: str = DEFAULT_BACKEND):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=120.0)

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def health(self) -> dict:
        r = self.client.get("/health")
        r.raise_for_status()
        return r.json()

    def create_user(self, name: str) -> dict:
        r = self.client.post("/users", params={"name": name})
        r.raise_for_status()
        return r.json()

    def list_users(self) -> list[dict]:
        r = self.client.get("/users")
        r.raise_for_status()
        return r.json()

    def list_frames(self, type: str | None = None) -> list[dict]:
        r = self.client.get("/memory/frames", params={"type": type} if type else {})
        r.raise_for_status()
        return r.json()

    def get_frame(self, frame_id: int) -> dict:
        r = self.client.get(f"/memory/frames/{frame_id}")
        r.raise_for_status()
        return r.json()

    def get_frame_by_name(self, name: str) -> dict | None:
        r = self.client.get(f"/memory/frames/by-name/{name}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_frame_slots(self, frame_id: int) -> list[dict]:
        r = self.client.get(f"/memory/frames/{frame_id}/slots")
        r.raise_for_status()
        return r.json()

    def forget_frame(self, frame_id: int) -> dict:
        r = self.client.post(f"/memory/frames/{frame_id}/forget")
        r.raise_for_status()
        return r.json()

    def forget_slot(self, slot_id: int) -> dict:
        r = self.client.post(f"/memory/slots/{slot_id}/forget")
        r.raise_for_status()
        return r.json()

    def list_conflicts(self, status: str | None = None) -> list[dict]:
        r = self.client.get("/memory/conflicts", params={"status": status} if status else {})
        r.raise_for_status()
        return r.json()

    def resolve_conflict(self, conflict_id: int, value: str) -> dict:
        r = self.client.post(
            f"/memory/conflicts/{conflict_id}/resolve", params={"value": value}
        )
        r.raise_for_status()
        return r.json()

    def chat(
        self, user_id: int, message: str, session_id: str | None = None
    ) -> dict:
        r = self.client.post(
            "/chat",
            json={"user_id": user_id, "message": message, "session_id": session_id},
        )
        r.raise_for_status()
        return r.json()

    def get_user_episodes(self, user_id: int, limit: int = 50) -> list[dict]:
        r = self.client.get(f"/users/{user_id}/episodes", params={"limit": limit})
        r.raise_for_status()
        return r.json()

    def db_backup(self) -> dict:
        r = self.client.post("/db/backup")
        r.raise_for_status()
        return r.json()

    def db_restore(self, backup_filename: str) -> dict:
        r = self.client.post("/db/restore", params={"backup_filename": backup_filename})
        r.raise_for_status()
        return r.json()

    def list_backups(self) -> dict:
        r = self.client.get("/db/backups")
        r.raise_for_status()
        return r.json()


def _memory_used(response: dict) -> bool:
    """Return True if the response contains meaningful memory context."""
    context = response.get("memory_context") or ""
    return bool(context) and "(no memory frames yet)" not in context


def cmd_chat(args: argparse.Namespace, client: BackendClient) -> None:
    """Interactive chat REPL."""
    user_id = args.user

    if user_id is None:
        users = client.list_users()
        if not users:
            console.print(
                "[red]No users exist. Create one with: assistant users add <name>[/red]"
            )
            sys.exit(1)
        user_id = users[0]["id"]
        console.print(
            f"[dim]Using user: {users[0]['name']} (id={user_id}). Use -u to specify.[/dim]"
        )

    session_id: str | None = None
    console.print(
        Panel(
            f"[bold]Cognitive Digital Assistant[/bold]\n"
            f"User ID: {user_id} | Backend: {client.base_url}\n"
            f"Type 'exit' or Ctrl+D to quit.",
            border_style="blue",
        )
    )

    while True:
        try:
            message = Prompt.ask("\n[bold cyan]You[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        text = message.strip()
        if text.lower() in {"exit", "quit", "/exit", "/quit"}:
            console.print("[dim]Goodbye.[/dim]")
            break
        if not text:
            continue

        console.print(f"[bold yellow]{text}[/bold yellow]")

        try:
            response = client.chat(user_id=user_id, message=message, session_id=session_id)
        except httpx.HTTPError as e:
            console.print(f"[red]Error: {e}[/red]")
            continue

        session_id = response["session_id"]

        console.print(f"[bold green]Assistant[/bold green]: {response['response']}")

        if _memory_used(response):
            console.print(
                f"  [dim italic]↳ recalled memory ({response['task_type']})[/dim italic]"
            )

        if args.trace:
            console.print(
                Panel(
                    f"[bold]Task type:[/bold] {response['task_type']}\n"
                    f"[bold]Session:[/bold] {response['session_id']}\n\n"
                    f"[bold]Memory context:[/bold]\n{response['memory_context']}",
                    title="Trace",
                    border_style="yellow",
                )
            )


def cmd_memory_list(args: argparse.Namespace, client: BackendClient) -> None:
    """List all frames in memory."""
    frames = client.list_frames(type=args.type)
    if not frames:
        console.print("[dim]No frames in memory yet.[/dim]")
        return

    table = Table(title="Memory Frames")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Conf", justify="right")
    table.add_column("Pri", justify="right")
    table.add_column("Ess", justify="center")
    table.add_column("Updated")

    for f in frames:
        conf = f["confidence"]
        pri = f["priority"]
        ess = f["essential"]
        conf_color = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "red"
        pri_color = "green" if pri > 0 else "dim"
        ess_str = "[yellow]★[/yellow]" if ess else ""
        updated = f["updated_at"][:19] if f["updated_at"] else ""
        table.add_row(
            str(f["id"]),
            f["name"],
            f["type"],
            f"[{conf_color}]{conf:.2f}[/{conf_color}]",
            f"[{pri_color}]{pri:.2f}[/{pri_color}]",
            ess_str,
            updated,
        )
    console.print(table)


def cmd_memory_show(args: argparse.Namespace, client: BackendClient) -> None:
    """Show a frame's slots, associations, and history."""
    frame = None
    try:
        frame_id = int(args.frame)
        frame = client.get_frame(frame_id)
    except ValueError:
        frame = client.get_frame_by_name(args.frame)

    if not frame:
        console.print(f"[red]No frame found: {args.frame}[/red]")
        sys.exit(1)

    slots = client.get_frame_slots(frame["id"])

    updated = frame["updated_at"][:19] if frame["updated_at"] else "never"
    console.print(
        Panel(
            f"[bold]{frame['name']}[/bold] ({frame['type']})\n"
            f"ID: {frame['id']} | Confidence: {frame['confidence']:.2f} | "
            f"Updated: {updated}",
            title="Frame",
        )
    )

    if slots:
        slot_table = Table(title="Slots")
        slot_table.add_column("ID", style="dim")
        slot_table.add_column("Key", style="bold")
        slot_table.add_column("Value")
        slot_table.add_column("Conf", justify="right")
        slot_table.add_column("Priority", justify="right")
        slot_table.add_column("Source", style="dim")
        slot_table.add_column("URL", style="dim")
        for s in slots:
            conf = s["confidence"]
            pri = s["priority"]
            conf_color = "green" if conf >= 0.7 else "yellow" if conf >= 0.4 else "red"
            pri_color = "green" if pri > 0 else "dim"
            source = s.get("source_type", "") or ""
            url = s.get("source_url", "") or ""
            reliability = s.get("source_reliability")
            rel_str = f" (rel: {reliability:.2f})" if reliability else ""
            slot_table.add_row(
                f"[dim]{s['id']}[/dim]",
                s["key"],
                s["value"],
                f"[{conf_color}]{conf:.2f}[/{conf_color}]",
                f"[{pri_color}]{pri:.2f}[/{pri_color}]",
                f"[dim]{source}{rel_str}[/dim]",
                f"[dim]{url}[/dim]" if url else "[dim]-[/dim]",
            )
        console.print(slot_table)
    else:
        console.print("[dim]No slots.[/dim]")


def cmd_memory_conflicts(args: argparse.Namespace, client: BackendClient) -> None:
    """List conflicts."""
    conflicts = client.list_conflicts(status=args.status)
    if not conflicts:
        console.print("[dim]No conflicts.[/dim]")
        return

    table = Table(title="Conflicts")
    table.add_column("ID", style="cyan")
    table.add_column("Frame", style="bold")
    table.add_column("Slot")
    table.add_column("Existing")
    table.add_column("New")
    table.add_column("Status")
    table.add_column("Created")

    status_colors = {
        "pending": "yellow",
        "auto_resolved": "green",
        "manual_override": "blue",
    }
    for c in conflicts:
        status_color = status_colors.get(c["status"], "white")
        created = c["created_at"][:19] if c["created_at"] else ""
        table.add_row(
            str(c["id"]),
            str(c["frame_id"]),
            c["slot_key"],
            str(c.get("existing_value", ""))[:30],
            str(c.get("new_value", ""))[:30],
            f"[{status_color}]{c['status']}[/{status_color}]",
            created,
        )
    console.print(table)


def cmd_memory_resolve(args: argparse.Namespace, client: BackendClient) -> None:
    """Manually resolve a conflict."""
    try:
        result = client.resolve_conflict(args.conflict_id, args.value)
        console.print(f"[green]Conflict {args.conflict_id} resolved.[/green]")
        console.print(f"  Slot value: {result['slot']['value']}")
    except httpx.HTTPError as e:
        console.print(f"[red]Failed to resolve: {e}[/red]")
        sys.exit(1)


def cmd_memory_forget(args: argparse.Namespace, client: BackendClient) -> None:
    """Soft-delete a frame by name or ID."""
    try:
        result = client.forget_frame(args.frame)
        console.print(f"[green]Forgotten: {result['frame']['name']} (priority set to 0).[/green]")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Frame not found: {args.frame}[/red]")
        elif e.response.status_code == 409:
            console.print(f"[yellow]Cannot forget essential frame: {args.frame}[/yellow]")
        else:
            console.print(f"[red]Failed: {e}[/red]")
        sys.exit(1)


def cmd_memory_forget_slot(args: argparse.Namespace, client: BackendClient) -> None:
    """Soft-delete a slot by ID."""
    try:
        result = client.forget_slot(args.slot_id)
        console.print(
            f"[green]Slot forgotten: {result['slot']['key']} = {result['slot']['value']}[/green]"
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            console.print(f"[red]Slot not found: {args.slot_id}[/red]")
        elif e.response.status_code == 409:
            console.print("[yellow]Cannot forget essential slot.[/yellow]")
        else:
            console.print(f"[red]Failed: {e}[/red]")
        sys.exit(1)


def cmd_users_add(args: argparse.Namespace, client: BackendClient) -> None:
    """Add a new user."""
    try:
        user = client.create_user(args.name)
        console.print(f"[green]User created: {user['name']} (id={user['id']})[/green]")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            console.print(f"[yellow]User '{args.name}' already exists.[/yellow]")
        else:
            console.print(f"[red]Failed: {e}[/red]")
            sys.exit(1)


def cmd_users_list(args: argparse.Namespace, client: BackendClient) -> None:
    """List all users."""
    users = client.list_users()
    if not users:
        console.print("[dim]No users. Create one with: assistant users add <name>[/dim]")
        return

    table = Table(title="Household Members")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Created")
    for u in users:
        created = u["created_at"][:19] if u["created_at"] else ""
        table.add_row(str(u["id"]), u["name"], created)
    console.print(table)


def cmd_db_backup(args: argparse.Namespace, client: BackendClient) -> None:
    """Create a backup of the database.

    By default produces an AES-256-GCM encrypted JSON bundle (requires DB_KEY).
    Use --plain for an unencrypted SQLite copy via the backend API.
    """
    if args.plain:
        try:
            result = client.db_backup()
            console.print("[green]Backup created (unencrypted)[/green]")
            console.print(f"  Filename: {result['backup_filename']}")
            console.print(f"  Path (in container): {result['backup_path']}")
            console.print(f"  Size: {result['backup_size_bytes']:,} bytes")
            console.print("[dim]To retrieve the backup file:[/dim]")
            console.print(f"  docker cp assistant-backend:{result['backup_path']} ./")
        except httpx.HTTPError as e:
            console.print(f"[red]Backup failed: {e}[/red]")
            sys.exit(1)
        return

    try:
        import asyncio

        from assistant.backend.memory.backup import create_encrypted_backup

        dest = args.output or f"assistant-backup-{datetime.now():%Y%m%d-%H%M%S}.enc.json"
        result = asyncio.run(create_encrypted_backup(dest))
        console.print("[green]Encrypted backup created[/green]")
        console.print(f"  Path: {result['backup_path']}")
        db_size = result["db_size_bytes"]
        bk_size = result["backup_size_bytes"]
        console.print(f"  Size: {bk_size:,} bytes (from {db_size:,} byte DB)")
        console.print(f"  Key ID: {result['key_id']}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        console.print(
            "[yellow]Set DB_KEY in .env to create encrypted backups, or use --plain.[/yellow]"
        )
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        sys.exit(1)


def cmd_db_restore(args: argparse.Namespace, client: BackendClient) -> None:
    """Restore from a backup file via the backend API."""
    try:
        backups = client.list_backups().get("backups", [])
        matching = [b for b in backups if b["filename"] == args.file]
        if not matching:
            console.print(f"[red]Backup not found: {args.file}[/red]")
            console.print("[yellow]Available backups:[/yellow]")
            for b in backups:
                console.print(f"  {b['filename']} ({b['size_bytes']:,} bytes)")
            sys.exit(1)

        if not args.yes:
            console.print(f"[yellow]This will overwrite the current DB with: {args.file}[/yellow]")
            confirm = Prompt.ask("Continue?", choices=["y", "n"], default="n")
            if confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                return

        result = client.db_restore(args.file)
        console.print(f"[green]Restored from {result['restored_from']}[/green]")
        console.print("[yellow]Restart the backend for changes to take effect.[/yellow]")
    except httpx.HTTPError as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        sys.exit(1)


def cmd_db_backup_encrypted(args: argparse.Namespace, client: BackendClient) -> None:
    """Create an AES-256-GCM encrypted backup of the database (requires DB_KEY)."""
    try:
        import asyncio

        from assistant.backend.memory.backup import create_encrypted_backup

        dest = args.output or f"assistant-backup-{datetime.now():%Y%m%d-%H%M%S}.enc.json"
        result = asyncio.run(create_encrypted_backup(dest))
        console.print(f"[green]Encrypted backup created: {dest}[/green]")
        db_size = result["db_size_bytes"]
        bk_size = result["backup_size_bytes"]
        console.print(f"  Size: {bk_size:,} bytes (from {db_size:,} byte DB)")
        console.print(f"  Key ID: {result['key_id']}")
        console.print("[dim]Store this file securely. To restore:[/dim]")
        console.print(f"  assistant db restore-encrypted {dest}")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[yellow]Set DB_KEY in .env to enable encrypted backups.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        sys.exit(1)


def cmd_db_restore_encrypted(args: argparse.Namespace, client: BackendClient) -> None:
    """Restore an encrypted backup (requires DB_KEY matching the backup)."""
    try:
        import asyncio

        from assistant.backend.memory.backup import restore_encrypted_backup

        if not args.yes:
            console.print(
                "[yellow]This will overwrite the current database.[/yellow]\n"
                "The old database will be moved to <db_path>.pre-restore before restore."
            )
            confirm = Prompt.ask("Continue?", choices=["y", "n"], default="n")
            if confirm != "y":
                console.print("[dim]Cancelled.[/dim]")
                return

        result = asyncio.run(restore_encrypted_backup(args.file))
        console.print(f"[green]Restored from: {result['restored_from']}[/green]")
        console.print(f"  Tables restored: {result['tables_restored']}")
        console.print("[yellow]Restart the backend for changes to take effect.[/yellow]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        sys.exit(1)


def cmd_db_migrate_encrypted(args: argparse.Namespace, client: BackendClient) -> None:
    """Migrate an unencrypted DB to encrypted (requires DB_KEY set in .env)."""
    try:
        import asyncio

        from assistant.backend.memory.backup import migrate_to_encrypted

        console.print(
            f"[yellow]Migrating {args.file} to encrypted format...[/yellow]\n"
            "The current assistant.db will be moved to assistant.db.unencrypted."
        )
        result = asyncio.run(migrate_to_encrypted(args.file))
        console.print("[green]Migration complete![/green]")
        console.print(f"  New encrypted DB: {result['new_encrypted_db']}")
        console.print(f"  Old DB moved to: {result['old_db_moved_to']}")
        console.print(f"  Tables migrated: {result['tables_migrated']}")
        console.print("[yellow]Restart the backend to use the new encrypted DB.[/yellow]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        console.print("[yellow]Set DB_KEY in .env first, then run again.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Migration failed: {e}[/red]")
        sys.exit(1)


def cmd_db_list(args: argparse.Namespace, client: BackendClient) -> None:
    """List available backups."""
    try:
        result = client.list_backups()
        backups = result.get("backups", [])
        if not backups:
            console.print("[dim]No backups yet. Run: assistant db backup[/dim]")
            return

        table = Table(title="Available Backups")
        table.add_column("Filename", style="bold")
        table.add_column("Size", justify="right")
        table.add_column("Created")
        for b in backups:
            table.add_row(
                b["filename"],
                f"{b['size_bytes']:,} bytes",
                b["created_at"][:19],
             )
        console.print(table)
    except httpx.HTTPError as e:
        console.print(f"[red]Failed to list backups: {e}[/red]")
        sys.exit(1)


def cmd_db_upgrade(args: argparse.Namespace, client: BackendClient) -> None:
    """Upgrade database schema."""
    try:
        from assistant.backend.config import settings
        from assistant.backend.db.schema import init_db
        
        console.print(f"[bold]Upgrading schema at {settings.database_path}...[/bold]")
        asyncio.run(init_db(settings.database_path))
        console.print("[green]✓ Schema upgraded successfully[/green]")
    except Exception as e:
        console.print(f"[red]Upgrade failed: {e}[/red]")
        sys.exit(1)


def cmd_db_migrate(args: argparse.Namespace, client: BackendClient) -> None:
     """Migrate embeddings to vec_f32 format."""
     try:
        import json

        from assistant.backend.config import settings
        
        console.print("[bold]Migrating embeddings to vec_f32...[/bold]")
        
        async def migrate():
            import aiosqlite
            import sqlite_vec
            
            db = await aiosqlite.connect(settings.database_path)
            await db.enable_load_extension(True)
            
            try:
                await db.load_extension(sqlite_vec.loadable_path())
                console.print("[green]✓ sqlite-vec loaded[/green]")
            except Exception as e:
                console.print(f"[red]✗ sqlite-vec not available: {e}[/red]")
                await db.close()
                raise
            
            async with db.execute("SELECT COUNT(*) FROM frame_embeddings") as cursor:
                count = (await cursor.fetchone())[0]
            
            if count == 0:
                console.print("[yellow]No embeddings to migrate[/yellow]")
                await db.close()
                return
            
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
            
            console.print(f"[green]✓ Migrated {migrated}/{count} embeddings to vec_f32[/green]")
        
        asyncio.run(migrate())
     except Exception as e:
        console.print(f"[red]Migration failed: {e}[/red]")
        sys.exit(1)


def cmd_db_consolidate(args: argparse.Namespace, client: BackendClient) -> None:
    """Run duplicate-frame consolidation against the local brain."""
    from assistant.cli.db import main as db_main

    asyncio.run(db_main("consolidate", execute=args.execute))


def cmd_db_embed_episodes(args: argparse.Namespace, client: BackendClient) -> None:
    """Backfill episode embeddings for semantic conversation recall."""
    from assistant.cli.db import main as db_main

    asyncio.run(db_main("embed-episodes", embed_cap=args.cap))


def cmd_db_backfill_embeddings(args: argparse.Namespace, client: BackendClient) -> None:
    """Generate and store embeddings for all existing frames."""
    try:
        from assistant.backend.config import settings
        from assistant.backend.memory.store import MemoryStore
        from assistant.backend.pipeline.llm_client import OllamaClient

        async def backfill():
            store = MemoryStore(settings.database_path)
            llm_client = OllamaClient(
                base_url=settings.ollama_url,
                chat_model=settings.chat_model,
                utility_model=settings.utility_model,
                embedding_model=settings.embedding_model,
                coder_model=settings.coder_model,
                verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
                chat_num_ctx=settings.chat_num_ctx,
                utility_num_ctx=settings.utility_num_ctx,
            )
            try:
                frames = await store.list_frames()
                if not frames:
                    console.print("[yellow]No frames found to embed[/yellow]")
                    return
                frame_ids = [f.id for f in frames if f.id is not None]
                console.print(f"[bold]Embedding {len(frame_ids)} frames...[/bold]")

                async def get_embedding(text: str) -> list[float]:
                    resp = await llm_client.embed(text)
                    return resp.embedding

                await store.embed_frames(
                    frame_ids, get_embedding, settings.embedding_model
                )
                console.print(f"[green]✓ Embedded {len(frame_ids)} frames[/green]")
            finally:
                await llm_client.close()

        asyncio.run(backfill())
    except Exception as e:
        console.print(f"[red]Backfill failed: {e}[/red]")
        sys.exit(1)


def cmd_db_status(args: argparse.Namespace, client: BackendClient) -> None:
     """Show database status."""
     try:
        from pathlib import Path

        from assistant.backend.config import settings
        
        db_path = Path(settings.database_path)
        
        table = Table(title="Database Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        
        if db_path.exists():
            table.add_row("Path", str(db_path))
            table.add_row("Size", f"{db_path.stat().st_size:,} bytes")
        else:
            table.add_row("Status", "Not found")
            return
        
         # Check if vec extension is available
        import aiosqlite
        
        async def check_vec():
            import sqlite_vec
            db = await aiosqlite.connect(db_path)
            try:
                await db.enable_load_extension(True)
                await db.load_extension(sqlite_vec.loadable_path())
                await db.close()
                return True
            except Exception:
                await db.close()
                return False
        
        if asyncio.run(check_vec()):
            table.add_row("Vector Search", "[green]Available[/green]")
        else:
            table.add_row("Vector Search", "[yellow]Not available[/yellow]")
        
        console.print(table)
     except Exception as e:
        console.print(f"[red]Failed to check status: {e}[/red]")
        sys.exit(1)


def cmd_db_reembed(args: argparse.Namespace, client: BackendClient) -> None:
    """Re-embed all frames with a new embedding model."""
    try:
        from assistant.backend.memory.metadata import (
            METADATA_KEY_EMBEDDING_MODEL,
            set_metadata,
        )
        from assistant.backend.memory.store import MemoryStore
        from assistant.backend.pipeline.llm_client import OllamaClient

        model = args.model or settings.embedding_model
        console.print(f"[bold]Re-embedding all frames with model [cyan]{model}[/cyan]...[/bold]")

        store = MemoryStore(settings.database_path)
        llm_client = OllamaClient(
            base_url=settings.ollama_url,
            embedding_model=model,
            verify_tls=settings.ollama_tls_cert if settings.ollama_tls_cert else True,
            chat_num_ctx=settings.chat_num_ctx,
            utility_num_ctx=settings.utility_num_ctx,
        )

        all_frames = asyncio.run(store.list_frames())
        frame_ids = [f.id for f in all_frames if f.id is not None]

        if not frame_ids:
            console.print("  No frames to re-embed.")
            asyncio.run(set_metadata(settings.database_path, METADATA_KEY_EMBEDDING_MODEL, model))
            console.print(f"[green]✓ Metadata updated: embedding_model = {model}[/green]")
            return

        total = len(frame_ids)
        batch_size = 100
        embedded = 0

        # embed_fn must be (text) -> list[float]; llm_client.embed returns an
        # EmbeddingResponse, which json.dumps rejects. See cli/db.py reembed_db.
        async def embed_text(text: str) -> list[float]:
            resp = await llm_client.embed(text)
            return resp.embedding

        for i in range(0, total, batch_size):
            batch = frame_ids[i:i + batch_size]
            try:
                embedded += asyncio.run(store.embed_frames(batch, embed_text, model))
                console.print(f"  [{embedded}/{total}] Embedded batch {i // batch_size + 1}")
            except Exception as e:
                console.print(f"[red]Error embedding batch {i // batch_size + 1}: {e}[/red]")

        if embedded < total:
            console.print(
                f"[red]Only {embedded}/{total} frames were embedded; metadata left on the "
                f"previous model so the startup audit keeps reporting the gap.[/red]"
            )
            return

        asyncio.run(set_metadata(settings.database_path, METADATA_KEY_EMBEDDING_MODEL, model))
        console.print(
            f"[green]✓ Re-embedded {embedded}/{total} frames."
            f" Metadata updated: embedding_model = {model}[/green]"
        )

        # Episodes: top up any that lack a vector under the new model. After a
        # model swap every old-model episode lacks a vector, so this re-embeds
        # the full episode set (keeps "related past conversations" searchable).
        # NOTE: embed_missing_episodes expects embed_fn -> list[float]; OllamaClient.embed
        # returns an EmbeddingResponse, so unwrap `.embedding` (regression: passing
        # the raw response made json.dumps fail and every episode get skipped silently).
        try:
            async def _embed_episode(text: str) -> list[float]:
                resp = await llm_client.embed(text)
                return resp.embedding

            episode_count = asyncio.run(store.embed_missing_episodes(_embed_episode, model))
            console.print(f"[green]✓ Embedded {episode_count} episodes with {model}[/green]")
        except Exception as e:
            console.print(f"[yellow]Episode re-embed skipped: {e}[/yellow]")
    except Exception as e:
        console.print(f"[red]Re-embed failed: {e}[/red]")
        sys.exit(1)


def cmd_status(args: argparse.Namespace, client: BackendClient) -> None:
    """Check backend status."""
    try:
        health = client.health()
        models = health.get("models", {})
        think = "supported" if health.get("thinking_supported") else "not supported"
        console.print(
            Panel(
                f"[green]Backend OK[/green]\n"
                f"  URL: {client.base_url}\n"
                f"  Ollama: {'reachable' if health['ollama_reachable'] else 'NOT REACHABLE'}\n"
                f"  Chat model: {models.get('chat', health.get('chat_model', '?'))}\n"
                f"  Utility model: {models.get('utility', health.get('utility_model', '?'))}\n"
                f"  Embedding model: {models.get('embedding', '?')}\n"
                f"  Coder model: {models.get('coder', '?')}\n"
                f"  Max model: {models.get('max', health.get('max_model', '?'))}\n"
                f"  Thinking mode: {think}",
                title="Status",
            )
        )
    except httpx.HTTPError as e:
        console.print(f"[red]Backend not reachable at {client.base_url}: {e}[/red]")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="assistant", description="Cognitive digital assistant CLI"
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("ASSISTANT_BACKEND", DEFAULT_BACKEND),
        help=f"Backend URL (default: {DEFAULT_BACKEND})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # chat
    p_chat = subparsers.add_parser("chat", help="Interactive chat with the assistant")
    p_chat.add_argument("-u", "--user", type=int, help="User ID (default: first user)")
    p_chat.add_argument(
        "--trace", action="store_true", help="Show memory context + task type per turn"
    )
    p_chat.set_defaults(func=cmd_chat)

    # memory
    p_memory = subparsers.add_parser("memory", help="Memory introspection")
    mem_sub = p_memory.add_subparsers(dest="memory_command", required=True)

    p_mem_list = mem_sub.add_parser("list", help="List all frames")
    p_mem_list.add_argument("--type", help="Filter by type (entity|concept|event|household)")
    p_mem_list.set_defaults(func=cmd_memory_list)

    p_mem_show = mem_sub.add_parser("show", help="Show a frame's details")
    p_mem_show.add_argument("frame", help="Frame name or ID")
    p_mem_show.set_defaults(func=cmd_memory_show)

    p_mem_conflicts = mem_sub.add_parser("conflicts", help="List conflicts")
    p_mem_conflicts.add_argument(
        "--status", choices=["pending", "auto_resolved", "manual_override"]
    )
    p_mem_conflicts.set_defaults(func=cmd_memory_conflicts)

    p_mem_resolve = mem_sub.add_parser("resolve", help="Manually resolve a conflict")
    p_mem_resolve.add_argument("conflict_id", type=int)
    p_mem_resolve.add_argument("value", help="The value to set")
    p_mem_resolve.set_defaults(func=cmd_memory_resolve)

    p_mem_forget = mem_sub.add_parser("forget", help="Soft-delete a frame (sets priority to 0)")
    p_mem_forget.add_argument("frame", help="Frame name or ID")
    p_mem_forget.set_defaults(func=cmd_memory_forget)

    p_mem_forget_slot = mem_sub.add_parser(
        "forget-slot", help="Soft-delete a slot (sets priority to 0)"
    )
    p_mem_forget_slot.add_argument("slot_id", type=int, help="Slot ID to forget")
    p_mem_forget_slot.set_defaults(func=cmd_memory_forget_slot)

    # users
    p_users = subparsers.add_parser("users", help="User management")
    users_sub = p_users.add_subparsers(dest="users_command", required=True)

    p_users_add = users_sub.add_parser("add", help="Add a new user")
    p_users_add.add_argument("name")
    p_users_add.set_defaults(func=cmd_users_add)

    p_users_list = users_sub.add_parser("list", help="List all users")
    p_users_list.set_defaults(func=cmd_users_list)

    # db
    p_db = subparsers.add_parser("db", help="Database maintenance")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)

    p_db_upgrade = db_sub.add_parser("upgrade", help="Upgrade schema to latest version")
    p_db_upgrade.set_defaults(func=cmd_db_upgrade)

    p_db_migrate = db_sub.add_parser("migrate", help="Migrate embeddings to vec_f32")
    p_db_migrate.set_defaults(func=cmd_db_migrate)

    p_db_backup = db_sub.add_parser(
        "backup",
        help="Backup the DB to an encrypted bundle (default) or plain copy (--plain)",
    )
    p_db_backup.add_argument("-o", "--output", help="Output file path")
    p_db_backup.add_argument(
        "--plain", action="store_true",
        help="Create an unencrypted SQLite copy instead of an encrypted bundle",
    )
    p_db_backup.set_defaults(func=cmd_db_backup)

    p_db_restore = db_sub.add_parser("restore", help="Restore DB from a file (DESTRUCTIVE)")
    p_db_restore.add_argument("file", help="Backup file to restore from")
    p_db_restore.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_db_restore.set_defaults(func=cmd_db_restore)

    p_db_list = db_sub.add_parser("list", help="List available backup files")
    p_db_list.set_defaults(func=cmd_db_list)

    p_db_status = db_sub.add_parser("status", help="Show database status")
    p_db_status.set_defaults(func=cmd_db_status)

    p_db_embed = db_sub.add_parser(
        "backfill-embeddings", help="Generate embeddings for all existing frames"
    )
    p_db_embed.set_defaults(func=cmd_db_backfill_embeddings)

    p_db_consolidate = db_sub.add_parser(
        "consolidate",
        help="Merge duplicate memory frames (dry-run by default, --execute to apply)",
    )
    p_db_consolidate.add_argument(
        "--execute", action="store_true", help="Apply the planned merges"
    )
    p_db_consolidate.set_defaults(func=cmd_db_consolidate)

    p_db_embed_eps = db_sub.add_parser(
        "embed-episodes",
        help="Backfill semantic embeddings for stored conversations",
    )
    p_db_embed_eps.add_argument(
        "--cap", type=int, default=None, help="Max episodes to embed this run"
    )
    p_db_embed_eps.set_defaults(func=cmd_db_embed_episodes)

    p_db_migrate_enc = db_sub.add_parser(
        "migrate-encrypted",
        help="Migrate an unencrypted DB to encrypted (requires DB_KEY set in .env)",
    )
    p_db_migrate_enc.add_argument(
        "file",
        help="Path to the existing unencrypted SQLite database file",
    )
    p_db_migrate_enc.set_defaults(func=cmd_db_migrate_encrypted)

    p_db_backup_enc = db_sub.add_parser(
        "backup-encrypted", help="Create AES-256-GCM encrypted backup (requires DB_KEY)"
    )
    p_db_backup_enc.add_argument(
        "-o", "--output",
        help="Output file path (default: assistant-backup-TIMESTAMP.enc.json)"
    )
    p_db_backup_enc.set_defaults(func=cmd_db_backup_encrypted)

    p_db_restore_enc = db_sub.add_parser(
        "restore-encrypted", help="Restore from AES-256-GCM encrypted backup (requires DB_KEY)"
    )
    p_db_restore_enc.add_argument("file", help="Path to encrypted backup file")
    p_db_restore_enc.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_db_restore_enc.set_defaults(func=cmd_db_restore_encrypted)

    p_db_reembed = db_sub.add_parser(
        "reembed", help="Re-embed all frames with a new embedding model"
    )
    p_db_reembed.add_argument(
        "--model", type=str, help="Target embedding model (default: from settings)"
    )
    p_db_reembed.set_defaults(func=cmd_db_reembed)

    # status
    p_status = subparsers.add_parser("status", help="Check backend status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()

    with BackendClient(args.backend) as client:
        args.func(args, client)


if __name__ == "__main__":
    main()
