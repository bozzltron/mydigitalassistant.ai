# Cognitive Digital Assistant

A privacy-first digital assistant that **remembers, learns, and error-corrects**.

Unlike a typical chatbot that just queries an LLM, this assistant maintains a structured
**world model** that grows from every conversation. It extracts concepts into frame/slot
memory, builds associations, reconciles contradictions, and recalls relevant context in
future interactions. Inspired by cognitive architecture concepts from HCI (Minsky frames,
ACT-R/SOAR's functional/introspective task distinction).

## Features

- **Structured memory** — frames, slots, associations, episodic memory, confidence scores
- **Error correction** — contradictions are auto-resolved with full audit trail
- **Multi-user** — per-user private episodic memory + shared household knowledge
- **Local-first** — Ollama for inference, local embeddings, SQLite for storage
- **Web search** — privacy-first retrieval via local SearXNG instance
- **Optional Brave Search API** — higher-quality results with explicit opt-in
- **Scheduled tasks** — daily list runs automatically; tasks and their outputs are memory
- **No cloud LLM calls** — never leaks conversations to tech giants; safe for kids

## Quick Start

### Option 1: Using Docker (Recommended)
```bash
# Build the image
docker build -t assistant .

# Start all services
docker compose up

# Chat via CLI
assistant chat
```

### Option 2: Manual Setup
```bash
# 1. Install Ollama and pull models
ollama pull qwen2.5:7b
ollama pull qwen2.5:3b
ollama pull nomic-embed-text

# Tip: keep models warm between turns (see .env.example)
#   OLLAMA_KEEP_ALIVE=-1  OLLAMA_MAX_LOADED_MODELS=2

# 2. Install the assistant
pip install -e ./assistant

# 3. Start the backend
uvicorn assistant.backend.main:app --host 127.0.0.1 --port 8000

# 4. Chat via CLI
assistant chat
```

## Architecture

See `/assistant/AGENTS.md` for the cognitive architecture and memory model details.

## Security

See `SECURITY.md` for the threat model and data-flow details.
All inference is local via Ollama. Web search defaults to local SearXNG.
An optional Brave Search API backend is available with explicit opt-in;
no other cloud providers are used.

## License

MIT
