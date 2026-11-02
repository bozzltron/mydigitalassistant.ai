# Security & Privacy

## Guarantee

This assistant is **local by default**. It never sends your conversations, prompts, or
personal memory to external servers, and all inference happens locally via Ollama.

There is exactly one opt-in exception, and it is off unless you turn it on: the **Brave
Search API** (`BRAVE_ENABLED=true` plus `BRAVE_API_KEY`). When it is enabled, the
sanitized query text and your IP address are sent to Brave's servers. See
`docs/SECURITY.md` for the consent behaviour and Brave's privacy policy.

## What's enforced

- Bare-metal runs: FastAPI binds to `127.0.0.1` only — never accessible from the network
- Docker runs: the backend container publishes **no** port and is reachable only on the
  internal `appnet` bridge. Every port that *is* published is bound to `127.0.0.1`, never
  to a LAN-facing address. What each project publishes:
  - dev (`docker-compose.yml`): `8443` Caddy (TLS), `8080` SearXNG, `5174` the Vite dev
    server — which also proxies API requests to the backend, so it is a way in, dev only
  - prod (`docker-compose.prod.yml`): `8444` Caddy (TLS), `8081` SearXNG

  `assistant/tests/test_published_ports.py` fails the build if any compose file publishes
  a port without a loopback host IP.
- All HTTP calls limited to Ollama, SearXNG and (only when enabled) the Brave Search API —
  local endpoints by default; see `.env.example`
- Search is always-on by design and defaults to local SearXNG; the Brave backend is the
  only permitted non-local one and requires explicit opt-in
- No telemetry, analytics, or phone-home code
- No cloud LLM APIs (OpenAI, Anthropic, Google, etc.)
- SQLite DB stored locally; backups via `assistant db backup`
- Secrets live in `.env` (gitignored); `.env.example` documents safe defaults

## Threat model

This is a **single-household deployment**, not a multi-tenant SaaS.

### Protected against

- Data leakage to tech giants (no cloud LLM APIs; the only external call is optional Brave Search)
- Network exposure (every published port is loopback-bound; the backend publishes none)
- Telemetry / tracking (no analytics code)
- Accidental secret commits (`.env` is gitignored, checked in CI)
- Misconfigured Ollama endpoints (defaults require localhost)
- Misconfigured search endpoints (SearXNG must be localhost; Brave needs explicit enablement)
- Search results stored in memory without explicit selection

### Not protected against (out of scope for hobby project)

- A compromised local process with root access
- Physical access to the machine
- Side-channel attacks on the LLM model weights
- Malicious users already on the local machine
- Attacks on Ollama itself or its downloaded models

## For kids

The assistant supports multi-user profiles. Each user has private episodic memory.
Household-shared knowledge is visible to all users. Parents can review memory state
via `assistant memory list` and `assistant memory show <frame>`.

## Verification

Run the security verification script before each release:

```bash
assistant-verify-security
```

Or directly:

```bash
python scripts/verify_security.py
```

This checks:

- No external API URLs in source code (OpenAI, Anthropic, Google, etc.)
- The Brave Search API, if referenced, is gated by an explicit enablement flag
- Ollama URL defaults to localhost
- SearXNG search tool binds to localhost only (not 0.0.0.0)
- No telemetry/analytics code
- No insecure bind addresses (`0.0.0.0`)
- `.env` is gitignored
- `.env.example` exists
- No `.env` file is committed
- Ollama is reachable on localhost (informational)

The script exits `0` if all pass, `1` if any check fails.

### Manual review commands

```bash
# Confirm no cloud API endpoints in backend/cli source
grep -R "api.openai.com\|api.anthropic.com\|googleapis.com" backend/ cli/ || true

# Confirm FastAPI is not bound to 0.0.0.0
grep -R "0.0.0.0" backend/ cli/ || true

# Confirm .env is gitignored
grep "\.env" .gitignore ../.gitignore

# Confirm only .env.example is present, not .env
ls -la .env*
```

## Reporting

This is a hobby project. If you find a security issue:

1. Do not open a public issue if the vulnerability could expose user data.
2. Open a private discussion or email the maintainer if available.
3. Include the affected file, reproduction steps, and whether it breaks any
   guarantee in the "What's enforced" section above.
4. If the issue is in an installed dependency, report it upstream after
   confirming the assistant uses it.
