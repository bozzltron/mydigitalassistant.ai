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
- **100% local** — Ollama for inference, local embeddings, SQLite for storage
- **Web search** — privacy-first retrieval via local SearXNG instance
- **No cloud calls** — never leaks data to tech giants; safe for kids

## Quick Start

```bash
# 1. Install Ollama and pull models
ollama pull qwen2.5:7b
ollama pull qwen2.5:3b
ollama pull nomic-embed-text

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

See `SECURITY.md` (in /assistant/) for the local-only guarantee and threat model.
All inference is local via Ollama. Web search only via local SearXNG instance.
No outbound network calls to cloud providers.

## License

MIT
