# Deployment Modes

## Development Mode (Default)

**Command:** `docker compose up`

**Architecture:**
- `assistant-backend` - Python FastAPI server (port 8000 internal)
- `solid-dev-server` - Vite dev server (port 5173, hot reloading)
- `caddy` - TLS reverse proxy (port 8443 published to 127.0.0.1:8443)
- `searxng` - Local search engine

**Frontend Serving:**
- Caddy routes API requests (`/chat*`, `/users*`, `/memory*`, etc.) → `assistant-backend:8000`
- All other requests → `solid-dev-server:5173` (Vite dev server)
- Frontend code mounted as volume for live editing
- Hot module replacement (HMR) enabled

**Use Case:** Active development with instant feedback

---

## Production Mode

**Command:** `docker compose -f docker-compose.prod.yml up`

**Architecture:**
- `assistant-backend` - Python FastAPI server + **built frontend assets**
- `caddy` - TLS reverse proxy (port 8444 published to 127.0.0.1:8444)
- `searxng` - Local search engine
- **No solid-dev-server**

**Frontend Serving:**
- Caddy routes API requests → `assistant-backend:8000`
- All other requests → `assistant-backend:8000` (serves static files from `/app/assistant/backend/static`)
- Frontend assets baked into container image at build time
- Single container serves both API and frontend

**Use Case:** Production deployment, CI/CD, demos

The `Dockerfile` `frontend-builder` stage runs a real `npm run build` (output to
`../assistant/backend/static` via `frontend/vite.config.ts`), so prod ships the
compiled SPA rather than a placeholder.

---

## Quick Reference

| Aspect | Dev Mode | Prod Mode |
|--------|----------|-----------|
| Compose file | `docker-compose.yml` | `docker-compose.prod.yml` |
| Frontend server | Vite dev server (separate container) | FastAPI static files (same container) |
| Hot reload | ✅ Yes | ❌ No |
| Port published | 8443 (Caddy) | 8444 (Caddy) |
| API endpoint | `https://localhost:8443` | `https://localhost:8444` |
| Frontend edits | Instant | Requires rebuild |

---

## ⚠️ Backup Policy

**Always run a backup before any database operation.** See [Backup System](BACKUP_SYSTEM.md) for details.

```bash
# Safe pattern for ANY db operation
assistant db backup                    # 1. Backup (encrypted by default)
assistant db <operation>               # 2. Run operation
# If fail:
assistant db restore-encrypted assistant-backup-<timestamp>.enc.json -y
```

**Default is encrypted (AES-256-GCM).** Requires `DB_KEY` in `.env`.
- `assistant db backup` → encrypted JSON bundle (default)
- `assistant db backup --plain` → raw SQLite copy
- Auto backups: `/app/data/backups/` (pre-consolidation, every 12h)