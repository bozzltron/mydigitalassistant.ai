# Security & Privacy

## Guarantee

This assistant is **local-first**. It never sends your conversations, prompts, or any
personal memory to external servers. All inference happens locally via Ollama.

Web search defaults to a local SearXNG instance. An optional Brave Search API backend
is available with explicit opt-in (`BRAVE_ENABLED=true` + `BRAVE_API_KEY`). When Brave
is enabled, sanitized query text and your IP address are sent to Brave's servers; see
[Brave Search privacy policy](https://brave.com/privacy/search/) for details.

## What's enforced

- FastAPI binds to `127.0.0.1` only — never accessible from the network
- All HTTP calls to Ollama at `http://127.0.0.1:11434`
- Web search defaults to local SearXNG at `http://127.0.0.1:8080` (configurable in `.env`)
- Optional Brave Search API is the only permitted cloud backend, and only with explicit opt-in
- No telemetry, analytics, or phone-home code
- No cloud LLM APIs (OpenAI, Anthropic, Google, etc.)
- SQLite DB stored locally; backups via `assistant db backup`
- Secrets live in `.env` (gitignored); `.env.example` documents safe defaults

## Threat model

This is a **single-household deployment**, not a multi-tenant SaaS.

### Protected against

- Data leakage to tech giants (no cloud LLM APIs; only optional Brave Search)
- Network exposure (localhost-only binding)
- Telemetry / tracking (no analytics code)
- Accidental secret commits (`.env` is gitignored, checked in CI)
- Misconfigured Ollama endpoints (defaults and verification require localhost)
- Misconfigured search endpoints (SearXNG must be localhost; Brave requires explicit enablement)

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

- No forbidden external API URLs in source code (OpenAI, Anthropic, Google, etc.)
- Brave Search API, if present, is gated by an explicit enablement flag
- Ollama URL defaults to localhost
- SearXNG URL defaults to localhost (if search is enabled)
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

# Confirm SearXNG is configured for localhost (if search is enabled)
grep -R "SEARXNG_URL" .env* backend/ cli/ || true

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
