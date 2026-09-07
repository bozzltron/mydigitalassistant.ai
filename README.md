# MyDigitalAssistant.ai

A privacy-first cognitive digital assistant that remembers, learns, and error-corrects.

## Architecture Overview

This project uses:
- FastAPI backend with local Ollama inference 
- SolidJS frontend with Vite development server
- SearXNG for web search
- Docker Compose for orchestration  

## Core Services

### Built-in services (run by default)
1. `assistant-backend` - The main FastAPI backend serving the assistant and processing
2. `caddy` - Reverse proxy for HTTPS 
3. `searxng` - Local search engine

### Development services (added via Docker compose)
4. `solid-dev-server` - SolidJS/Vite frontend development server running on localhost:5173  

## Quickstart

1. Start all services:
   ```bash
   docker compose up -d
   ```

2. View logs to ensure services are healthy:
   ```bash
   docker compose logs -f
   ```

3. Access the assistant frontend at:
   - Dev mode (with hot-reloading): http://localhost:5173
   - Production mode via Caddy proxy: https://localhost:8443

## Development Workflow

### For Back-End / API Development
- Use Docker containers directly 
- Build changes with `docker compose build`

### For Front-End / UI Development
- The SolidJS dev server now runs on port 5173 for hot-reloading
- Direct Vite development server is available at http://localhost:5173  
- All services can be accessed through the Docker network as before

## Security & Privacy

This setup:
- Keeps all data local (no external cloud storage)
- Binds only to localhost interfaces  
- Uses Ollama on 127.0.0.1
- SearXNG search is also bound to localhost only
- No telemetry or analytics

## Configuration 

Environment variables are set in `.env`. Create a copy:
```bash
cp .env.example .env
```

## Troubleshooting

### Common Issues
- If frontend doesn't load after changes: 
  - Check Vite dev server at http://localhost:5173  
  - Restart `solid-dev-server` container: `docker compose restart solid-dev-server`
- Ensure all containers are running: `docker compose ps`