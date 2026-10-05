# Backup System

## Overview

The assistant has a layered backup system with multiple mechanisms for different use cases. **All critical DB operations should be preceded by a backup.**

## Backup Methods

### 1. API Endpoints (Runtime)

| Endpoint | Description | Format |
|----------|-------------|--------|
| `POST /db/backup` | Create raw SQLite copy in `/app/data/` | `.db` file |
| `POST /db/restore` | Restore from backup filename | `.db` file |
| `GET /db/backups` | List available backups | JSON list |

**Example:**
```bash
# Create backup
curl -X POST https://localhost:8443/db/backup

# List backups
curl https://localhost:8443/db/backups

# Restore (DESTRUCTIVE - overwrites current DB)
curl -X POST https://localhost:8443/db/restore -d '{"backup_filename": "backup-20260909-161930.db"}'
```

### 2. CLI Commands (Recommended for Ops)

#### Raw SQLite Copy (via API)
```bash
assistant db backup --plain
# Creates: backup-20260909-161930.db in container /app/data/
```

#### Encrypted JSON Bundle (Default - Requires DB_KEY)
```bash
assistant db backup
# Creates: assistant-backup-20260909-161930.enc.json (AES-256-GCM)
```
**⚠️ Default is encrypted.** Requires `DB_KEY` in `.env`. More portable than raw `.db` files.

#### Encrypted Backup/Restore (Explicit)
```bash
# Backup
assistant db backup-encrypted -o my-backup.enc.json

# Restore (DESTRUCTIVE)
assistant db restore-encrypted my-backup.enc.json -y
```

### 3. Automatic Backups (Scheduler)

The scheduler snapshots the brain every `BACKUP_INTERVAL_HOURS` (default 12), on
its own clock so backup frequency does not scale with how often merges happen.
A consolidation merge that runs **without** a fresh snapshot takes one first, so
a merge is never applied unprotected; a merge coinciding with the 12h snapshot
takes no extra copy.

- Location: `/app/data/backups/`
- Naming: `<db>-pre-<label>-<timestamp>-<uuid>.db` (`label` is `scheduled` for the
  12h snapshot, `consolidation` for a merge-time one)
- Pruned to a bounded ring per label

### 4. Pre-Operation Backups (Safety)

Automatically created before destructive operations:
- Portable brain import (removed) → `brain.db.pre-portable-restore`
- Encrypted restore → `<db_path>.pre-restore`
- Memory import → `brain-overwrite-{timestamp}.db`

## ⚠️ CRITICAL: Always Backup Before DB Operations

**Before running any of these, create a backup:**
- `assistant db migrate` / `assistant db reembed`
- `assistant db consolidate --execute`
- `assistant db restore-encrypted`
- Schema upgrades: `assistant db upgrade`
- Manual DB file manipulation
- Container recreation with volume changes

```bash
# Safe pattern
assistant db backup          # 1. Backup first
assistant db reembed         # 2. Then run operation
# If something goes wrong:
assistant db restore-encrypted assistant-backup-20260909-161930.enc.json -y
```

## Encryption

| Method | Encrypted? | Requires DB_KEY | Use Case |
|--------|------------|-----------------|----------|
| `assistant db backup` (default) | ✅ **Yes (AES-256-GCM)** | ✅ Yes | Daily ops, portable |
| `assistant db backup --plain` | ❌ No | ❌ No | Quick local copy |
| `POST /db/backup` (API) | ❌ No | ❌ No | Runtime automation |
| Pre-consolidation (auto) | ❌ No | ❌ No | Recovery points |
| `backup-encrypted` CLI | ✅ Yes (AES-256-GCM) | ✅ Yes | Explicit ops |

**Default is encrypted.** The `assistant db backup` command creates an AES-256-GCM encrypted JSON bundle by default. Use `--plain` flag for unencrypted raw SQLite copy.

## Key Management

- `DB_KEY` in `.env` → SHA-256 → AES-256-GCM key
- `key_id` = first 16 chars of SHA-256(DB_KEY)
- Backup metadata includes `key_id` for validation on restore
- **Never commit DB_KEY.** Backups created with different keys cannot be restored.

## File Locations

| Environment | Backup Directory |
|-------------|------------------|
| Dev (`docker compose`) | Volume `assistant-data` → `/app/data/` |
| Prod (`docker-compose.prod.yml`) | Volume `assistant-data` → `/app/data/` |
| Host (manual) | `./data/` (if mounted) |

## Recovery Scenarios

| Scenario | Recovery Method |
|----------|-----------------|
| Accidental data deletion | `assistant db restore-encrypted <latest>.enc.json -y` |
| Corrupted DB after migration | `assistant db restore-encrypted <pre-migration>.enc.json -y` |
| Schema upgrade failure | `assistant db restore-encrypted <pre-upgrade>.enc.json -y` |
| Container volume lost | Restore from off-host backup copy |
| Wrong DB_KEY on restore | Ensure `.env` DB_KEY matches backup's key_id |

## Quick Reference

```bash
# Daily ops - always encrypted by default
assistant db backup                    # Creates .enc.json
assistant db backup --plain            # Raw .db file

# Before risky operations
assistant db backup                    # 1. Backup
assistant db reembed                   # 2. Operation
# If fail:
assistant db restore-encrypted assistant-backup-<timestamp>.enc.json -y

# List available
assistant db list                      # Lists /db/backups (raw) + encrypted bundles
```