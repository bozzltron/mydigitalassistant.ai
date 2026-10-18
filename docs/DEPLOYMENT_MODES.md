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

## Page routes and API paths

**A page route and an API path must not share a path.** This is a rule about how the SPA
is served, and breaking it fails in a way that looks like a caching problem.

The backend serves the SPA for **any path it has no exact route for** (`spa_catch_all`,
`main.py`). So:

- If an API route exists at `/foo`, a browser navigating to `/foo` gets the API's
  response — JSON, or a 422 — never the page.
- If no API route exists at `/foo`, the browser gets the SPA, whether or not `/foo` is a
  page the router knows about.

Two failure modes follow, and both have happened here:

| Symptom | Cause |
|---|---|
| The page shows JSON | an API handler sits at the bare page path — `GET /tasks` beat the Tasks page |
| The page loads the **old** app in dev, with no HMR | the dev Caddy `@api` matcher is too coarse — `/files*` swallowed the `/files` page and served the **built** bundle from the backend instead of Vite |

The second is the nastier one: the page looks like it works, so it reads as a stale
browser or a bad rebuild rather than a routing rule.

**Rules**

1. **Wildcards are `/prefix/*`, never `/prefix*`** in the dev `Caddyfile`'s `@api`
   matcher, and **proxy keys end in `/`** in `vite.config.ts`. The backend's own
   `spa_catch_all` already uses the trailing-slash form for exactly this reason
   (`/files/`, `/brain/`, `/tasks/`).
2. **A page route must not sit at an API path.** `/map` (API) with `/maps` (page) is the
   worked example; `/tasks/list` (API) with `/tasks` (page) is the second. If a page
   wants a name an API already owns, move one of them — do not rely on a method or an
   `Accept` header to tell them apart, because the dev matcher matches on path alone.
3. **Every route under an API prefix lives under it.** `POST /tasks` would not collide
   with a page navigation, but it *would* be missed by `/tasks/*` in the dev matcher and
   sent to the SPA. One rule for the whole prefix beats remembering which bare paths are
   safe.

Prod needs no matcher — Caddy sends everything to the backend and `spa_catch_all` makes
the same decision there.

**There are three matchers, and they have to agree.** This is the part that costs time:
the same split is decided in three places, and fixing one leaves the others wrong.

| Where | Covers |
|---|---|
| `Caddyfile` `@api` (dev) | what Caddy sends to the backend rather than the dev server |
| `frontend/vite.config.ts` `proxy` | what the **dev server** forwards when a request reaches it directly. Vite matches a proxy key by **prefix**, so a bare `'/files'` swallows the `/files` page |
| `spa_catch_all`'s `api_prefixes` (`main.py`) | what the backend serves as a page vs an API route — the only one that runs in **prod** |

`/brain` is absent from the Vite proxy, which is why it was the one page that behaved
while `/files` and `/tasks` did not: Caddy had been fixed and the Vite proxy was still
prefix-matching the bare paths. **When you add a page route, check all three.**

**Checking it**

```bash
# A page in dev must come from Vite, not the built bundle:
curl -sk https://localhost:8443/files | grep -o '@vite/client'   # present = Vite, absent = the stale bundle
# And it must not be JSON:
curl -sk https://localhost:8443/tasks | head -c 40
```

---

## Quick Reference

| Aspect | Dev Mode | Prod Mode |
|--------|----------|-----------|
| Compose file | `docker-compose.yml` | `docker-compose.prod.yml` |
| Frontend server | Vite dev server (separate container) | FastAPI static files (same container) |
| Hot reload | ✅ Yes | ❌ No |
| Port published | 8443 (Caddy) | 8444 (Caddy) |
| API endpoint | `https://localhost:8443` | `https://localhost:8444` |

**The endpoint is `localhost`, and the IP will not work.** The site address in `Caddyfile` is
`https://localhost` and the certificate is `CN=localhost`, so a request to
`https://127.0.0.1:8443` carries an SNI of `127.0.0.1`, matches no site, and the connection is
dropped. `curl` reports `000` and no error — indistinguishable from a container that is down,
while `docker compose ps` says healthy. Only the *published bind* is `127.0.0.1` (and it must
stay loopback-only); the address to open and to curl is `localhost`.
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