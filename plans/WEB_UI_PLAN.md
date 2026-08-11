# Web UI Implementation Plan

**Status:** Implemented

**Date:** 2026-08-11

---

## Overview

Added a local-only web UI to the cognitive assistant, served from the existing Dockerized FastAPI backend at `http://127.0.0.1:8000/chat-ui`. Supports rich markdown responses, text chat, and a hands-free voice conversation mode with local Whisper transcription.

---

## Design decisions

| Decision | Choice |
|----------|--------|
| UI path | `/chat-ui` |
| Markdown | Vendored `marked.min.js` |
| Voice STT | Local `faster-whisper` in backend container |
| Whisper model | `base` (configurable via `WHISPER_MODEL`) |
| Voice output | Browser `speechSynthesis` with user-selected local voice |
| Settings storage | `localStorage` (voice, TTS, trace visibility) |
| User selection | Auto-pick first user from `/users` |
| Trace panel | Visible by default |

---

## What was built

### Phase 1: Static web UI

**Files changed:**
- `assistant/backend/main.py` — added `StaticFiles` mount at `/static`, `GET /chat-ui` route, fixed `reasoning_model` in `OllamaClient` init and `/health` response
- `assistant/backend/static/chat.html` — new self-contained chat UI
- `assistant/backend/static/marked.min.js` — vendored markdown renderer

**Features:**
- Auto-selects first user from `/users`
- Persists `user_id` and `session_id` in `localStorage`
- Markdown-rendered chat history with code blocks, lists, links
- Trace panel visible by default (`task_type`, `memory_context`, `citations`)
- Text input with Enter-to-send; Shift+Enter for newlines
- Responsive dark-theme UI

### Phase 2: Voice conversation mode

**Files changed:**
- `assistant/backend/config.py` — added `whisper_model` and `whisper_device` settings
- `assistant/backend/pipeline/whisper.py` — new module for local Whisper transcription
- `assistant/backend/main.py` — added `POST /transcribe` endpoint

**Features:**
- "Voice" button in header and mic button in input row
- "Start voice chat" opens full-screen overlay with pulsing animation
- Browser `MediaRecorder` captures audio locally (no cloud)
- Audio blob sent to `POST /transcribe`; backend uses `faster-whisper` to transcribe
- Transcription auto-submitted to `/chat`
- Assistant response rendered and optionally read aloud via `speechSynthesis`
- After each response, recording auto-restarts for next turn (hands-free loop)
- "Stop" button ends voice mode
- Settings panel for TTS on/off and voice picker (local OS voices only)

### Phase 3: Docker

**Files changed:**
- `Dockerfile` — added `ffmpeg`, `faster-whisper` dependency; added `WHISPER_MODEL` and `WHISPER_DEVICE` env vars
- `docker-compose.yml` — added `REASONING_MODEL`, `WHISPER_MODEL`, `WHISPER_DEVICE` to environment

### Phase 4: Tests

**Files changed:**
- `assistant/tests/test_api.py` — added 3 new tests:
  - `test_health_includes_reasoning_model`
  - `test_chat_ui_returns_html`
  - `test_static_files_served`

**Results:** 235 tests pass, ruff clean.

### Phase 5: Documentation

**Files changed:**
- `plans/WEB_UI_PLAN.md` — this document

---

## Architecture

```
Browser ──► FastAPI (127.0.0.1:8000)
            ├── GET /chat-ui         → chat.html
            ├── GET /static/*        → static assets
            ├── POST /chat           → orchestrator
            ├── POST /transcribe     → faster-whisper
            └── GET /health
```

---

## Security notes

- All audio processing stays local in the backend container.
- No browser cloud STT services are used.
- TTS uses local OS voices via `speechSynthesis` (no cloud voices).
- FastAPI and static files bind to `127.0.0.1` only.
- Whisper model downloaded on first use to `~/.cache/whisper/`.

---

## Setup notes

1. **Voice requires a Whisper model.** The first time `/transcribe` is called, `faster-whisper` downloads the model automatically. This requires internet access for the initial download only.

2. **To upgrade Whisper model**, set `WHISPER_MODEL=small` in `.env` or `docker-compose.yml` and restart.

3. **FFmpeg** is required for audio processing. It is installed in the Docker image.

4. **No GPU?** Set `WHISPER_DEVICE=cpu` (default). GPU (CUDA) will automatically use GPU if available and `WHISPER_DEVICE=cuda` is set.

---

## Future enhancements

- Store voice/TTS preferences as memory slots so the agent knows user preferences
- Support voice preference changes via conversation ("use the British voice")
- Streaming audio chunks to reduce transcription latency
- Add conversation history scrollback in the UI
- Remember scroll position on reload
