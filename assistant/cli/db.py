"""CLI for database operations."""

import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from assistant.backend.config import settings
from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore

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
    """Migrate frame embeddings from JSON to vec_float32."""
    db_path = settings.database_path
    
    console.print(f"⚡ Migrating frame embeddings to sqlite-vec at [cyan]{db_path}[/cyan]")
    
    try:
        import json
        import aiosqlite
        
        db = await aiosqlite.connect(db_path)
        await db.enable_load_extension(True)
        
        try:
            await db.load_extension("vec0")
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
                     "UPDATE frame_embeddings SET embedding = vec_float32(?) WHERE frame_id = ?",
                     (json.dumps(embedding), frame_id),
                 )
                migrated += 1
            except (json.JSONDecodeError, TypeError):
                pass
        
        await db.commit()
        await db.close()
        
        console.print(f"✓ Migrated {migrated}/{count} embeddings to vec_float32")
        return True
        
    except Exception as e:
        console.print(f"✗ Migration failed: {e}")
        return False


async def backup_db():
    """Create a backup of the database."""
    import shutil
    from datetime import datetime
    
    db_path = Path(settings.database_path)
    if not db_path.exists():
        console.print("✗ No database found")
        return False
    
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(f"backup-{timestamp}.db")
    
    shutil.copy2(db_path, backup_path)
    console.print(f"✓ Backup created: [green]{backup_path}[/green]")
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


async def main(command: str):
    """Main CLI entry point."""
    if command == "upgrade":
        success = await upgrade_db()
    elif command == "migrate":
        success = await migrate_embeddings()
    elif command == "backup":
        success = await backup_db()
    elif command == "status":
        success = await status()
    else:
        console.print(f"Unknown command: [red]{command}[/red]")
        return False
    
    return success


def main_entry():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: assistant db <command>")
        print("Commands: upgrade, migrate, backup, status")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if not asyncio.run(main(command)):
        sys.exit(1)


if __name__ == "__main__":
    main_entry()
