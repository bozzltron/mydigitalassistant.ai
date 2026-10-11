# Upgrading a machine

**Short version: `git pull`, rebuild, done. Nothing to migrate, and the brain volume is
never touched.**

```bash
git pull
docker compose up -d --build                    # dev  → https://localhost:8443
docker compose -f docker-compose.prod.yml up -d --build   # prod → https://localhost:8444
```

## Why there is no migration

- **The schema has no migration runner and no `ALTER TABLE`.** Every table is
  `CREATE TABLE IF NOT EXISTS`, and `init_db` runs at startup — a **no-op on an existing
  brain**. A release that changes a table shape would need a real migration; none so far
  has, and this is the place to say so when one does.
- **The volume holds the brain and is not modified by an upgrade.** `docker compose up`
  replaces the container image, not the named volume.
- **No new models to pull.** Roles are added behind env vars and models are opt-in; a
  release that *adds* a required model must say so here (see the fleet in
  `docs/MODEL_SELECTION.md`).

## What a release can require of you

| kind of change | what you do |
|---|---|
| code only | `git pull` + rebuild |
| new/changed Compose env | rebuild — `.env` is forwarded wholesale (`env_file`) |
| **new required model** | `ollama pull <model>` (must be listed here) |
| **schema change** | a migration step (must be listed here; none exists yet) |
| new `.env` setting | optional — every setting has a default; `.env.example` documents it |

## Verify the upgrade

```bash
curl -sk https://localhost:8443/health | python3 -m json.tool   # all nine roles
```

Then the manual smoke test in `docs/TESTING.md`. **Remember prod points at the real
brain** — use a throwaway session and files you don't mind touching.

## Optional backfills (a brain write — back up first)

Two fixes apply only to **future** writes. Neither is required; both are repairs.

1. **`source_type` provenance.** The conversational path used to record no `source_type`,
   so facts the user stated sit in a NULL bucket (~950 frames per brain). New facts are
   labelled `"user"`. To repair the history:

   ```bash
   assistant db backup                 # ALWAYS first
   # then, in the container, UPDATE frames/slots SET source_type='user' WHERE source_type IS NULL
   ```

   Nothing depends on it; it makes the user's share measurable.

2. **Stale `updated_at`.** A slot write never bumped its frame, so ~99% of frames carry
   `updated_at == created_at`, and the Files list shows an old date until a file is next
   written. A **file-only** backfill from disk mtime is possible; other frames have no
   source of truth, so leave them.

## Per-machine `.env` (nothing required)

`MATH_MODEL` now **defaults** to `qwen3.5:4b` with `MATH_NUM_CTX=4096` — the warm utility
runner, so compute adds no model load. A machine whose `.env` still names a model no
longer in the fleet should delete those two lines and inherit the default; the old value
still *works*, but it reloads the runner on every compute call.

Also harmless but dead if present: `CONFLICT_AUTO_RESOLVE` (nothing reads it) and
`DATABASE_PATH` (both Compose files set it per environment).

## Rollback

Code rolls back with `git checkout <previous-tag>` + rebuild. A **brain** rolls back with
`assistant db restore`. The two are independent — an upgrade never rewrites the volume, so
a code rollback does not need a brain rollback.
