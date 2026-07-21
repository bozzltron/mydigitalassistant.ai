.PHONY: help build up down logs restart clean dev prod m4-setup m4

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Open WebUI commands (recommended)
open-webui: ## Start Open WebUI with Whisper (STT), Edge TTS (TTS), and SearXNG (Web Search)
	@docker-compose -f docker-compose.open-webui.yml up -d
	@echo "⏳ Waiting for container to start..."
	@sleep 8
	@echo "📦 Installing faster-whisper (required for voice input)..."
	@docker exec open-webui pip install --no-cache-dir faster-whisper >/dev/null 2>&1 && \
		echo "✅ faster-whisper installed successfully" || \
		echo "⚠️  Whisper installation failed - try: make open-webui-install-whisper"
	@echo "✅ Open WebUI is ready at http://localhost:8888"

open-webui-logs: ## View Open WebUI logs
	@docker-compose -f docker-compose.open-webui.yml logs -f

open-webui-stop: ## Stop Open WebUI
	@docker-compose -f docker-compose.open-webui.yml down

open-webui-restart: ## Restart Open WebUI
	@docker-compose -f docker-compose.open-webui.yml restart
	@echo "⏳ Waiting for container to start..."
	@sleep 5
	@echo "📦 Reinstalling faster-whisper (lost on restart)..."
	@docker exec open-webui pip install --no-cache-dir faster-whisper >/dev/null 2>&1 || echo "⚠️  Whisper installation may have failed"
	@echo "✅ Open WebUI restarted with faster-whisper"

open-webui-install-whisper: ## Install Whisper in Open WebUI container (required for voice input)
	@echo "📦 Installing faster-whisper in Open WebUI container..."
	@docker exec open-webui pip install --no-cache-dir faster-whisper || \
		(docker-compose -f docker-compose.open-webui.yml up -d && \
		 sleep 5 && \
		 docker exec open-webui pip install --no-cache-dir faster-whisper)
	@echo "🔄 Restarting container to load Whisper..."
	@docker-compose -f docker-compose.open-webui.yml restart
	@echo "✅ faster-whisper installed and container restarted. Voice input should now work!"

open-webui-export: ## Export Open WebUI data to tar.gz file
	@docker run --rm -v open-webui-data:/data -v $(PWD):/backup \
		alpine tar czf /backup/open-webui-data-$$(date +%Y%m%d-%H%M%S).tar.gz -C /data .
	@echo "✅ Export created: open-webui-data-$$(date +%Y%m%d-%H%M%S).tar.gz"

open-webui-import: ## Import Open WebUI data from tar.gz file (usage: make open-webui-import FILE=backup.tar.gz)
	@if [ -z "$(FILE)" ]; then \
		echo "❌ Error: Specify FILE=backup.tar.gz"; \
		echo "Usage: make open-webui-import FILE=open-webui-data-20240101.tar.gz"; \
		exit 1; \
	fi
	@docker run --rm -v open-webui-data:/data -v $(PWD):/backup \
		alpine sh -c "cd /data && rm -rf * && tar xzf /backup/$(FILE)"
	@echo "✅ Import complete. Restart Open WebUI: make open-webui-restart"

open-webui-reset: ## Reset Open WebUI database (deletes all data - use if locked out)
	@echo "⚠️  WARNING: This will delete ALL Open WebUI data (conversations, users, settings)"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read
	@docker-compose -f docker-compose.open-webui.yml down
	@docker volume rm open-webui-data 2>/dev/null || true
	@echo "✅ Database reset. Starting fresh..."
	@docker-compose -f docker-compose.open-webui.yml up -d
	@echo "✅ Open WebUI restarted. Go to http://localhost:8888 to create a new account"

open-webui-reset-password: ## Reset Open WebUI password (usage: make open-webui-reset-password EMAIL=your@email.com PASSWORD='newpassword')
	@if [ -z "$(EMAIL)" ] || [ -z "$(PASSWORD)" ]; then \
		echo "❌ Error: Specify EMAIL and PASSWORD"; \
		echo "Usage: make open-webui-reset-password EMAIL=admin@example.com PASSWORD='mypassword'"; \
		echo "Note: Use single quotes around password if it contains special characters"; \
		exit 1; \
	fi
	@docker exec open-webui python3 -c "import bcrypt, sqlite3; \
		hash = bcrypt.hashpw(b'$(PASSWORD)', bcrypt.gensalt()).decode(); \
		conn = sqlite3.connect('/app/backend/data/webui.db'); \
		conn.execute('UPDATE auth SET password=? WHERE email=?', (hash, '$(EMAIL)')); \
		conn.commit(); \
		conn.close(); \
		print('Password updated successfully')"
	@echo "✅ Password reset complete. Login at http://localhost:8888"

open-webui-query: ## Query Open WebUI database (usage: make open-webui-query QUERY="SELECT * FROM auth")
	@if [ -z "$(QUERY)" ]; then \
		echo "❌ Error: Specify QUERY"; \
		echo "Usage: make open-webui-query QUERY=\"SELECT email FROM auth\""; \
		echo ""; \
		echo "Common queries:"; \
		echo "  - List users: QUERY=\"SELECT email, role FROM auth\""; \
		echo "  - Count conversations: QUERY=\"SELECT COUNT(*) FROM conversations\""; \
		echo "  - List tables: QUERY=\"SELECT name FROM sqlite_master WHERE type='table'\""; \
		exit 1; \
	fi
	@docker exec open-webui python3 -c "import sqlite3; \
		conn = sqlite3.connect('/app/backend/data/webui.db'); \
		conn.row_factory = sqlite3.Row; \
		cursor = conn.execute('$(QUERY)'); \
		rows = cursor.fetchall(); \
		if rows: \
			headers = [desc[0] for desc in cursor.description]; \
			print(' | '.join(headers)); \
			print('-' * (len(' | '.join(headers)))); \
			for row in rows: \
				print(' | '.join(str(row[col]) for col in headers)); \
		else: \
			print('(no results)'); \
		conn.close()"

# Legacy commands (for archived custom implementation)
archive-build: ## Build archived custom implementation
	@cd archive && docker-compose build

archive-up: ## Start archived custom implementation
	@cd archive && docker-compose up -d

archive-down: ## Stop archived custom implementation
	@cd archive && docker-compose down

test: ## Run tests (when implemented)
	@echo "Tests not yet implemented"

m4-setup: ## Setup M4 optimizations (Mac only)
	@./setup-m4.sh

m4: ## Start with M4 optimizations (Mac only)
	@docker-compose -f docker-compose.m4.yml up -d

# Model management commands
ollama-models: ## List available models
	@echo "📦 Available Models:"
	@echo ""
	@echo "Recommended:"
	@echo "  qwen2.5:7b     - Excellent reasoning, multilingual (8GB RAM)"
	@echo "  llama3.2        - Fast, efficient (4GB RAM)"
	@echo ""
	@echo "Other options:"
	@echo "  gemma:7b        - Open-source, various applications (8GB RAM)"
	@echo ""
	@echo "Your installed models:"
	@ollama list 2>/dev/null || echo "  (Ollama not running or not installed)"
	@echo ""
	@echo "Quick download commands:"
	@echo "  make ollama-pull-qwen      # Download Qwen2.5"
	@echo "  make ollama-pull-mistral   # Download Mistral"
	@echo "  make ollama-pull-gemma     # Download Gemma"
	@echo "  make ollama-pull-deepseek  # Download DeepSeek"

ollama-pull-qwen: ## Download recommended Qwen2.5 model
	@echo "📥 Downloading qwen2.5:7b (this may take a few minutes)..."
	@ollama pull qwen2.5:7b

ollama-pull-llama: ## Download Llama3.2 model
	@echo "📥 Downloading llama3.2 (this may take a few minutes)..."
	@ollama pull llama3.2

ollama-pull-gemma: ## Download Gemma model
	@echo "📥 Downloading gemma:7b (this may take a few minutes)..."
	@ollama pull gemma:7b

ollama-status: ## Check Ollama status and installed models
	@echo "🔍 Checking Ollama status..."
	@echo ""
	@if command -v ollama >/dev/null 2>&1; then \
		echo "✅ Ollama is installed"; \
		echo ""; \
		echo "Installed models:"; \
		ollama list 2>/dev/null || echo "  (No models installed or Ollama not running)"; \
		echo ""; \
		echo "Ollama service:"; \
		curl -s http://localhost:11434/api/tags >/dev/null 2>&1 && echo "  ✅ Running on http://localhost:11434" || echo "  ❌ Not running (start with: ollama serve)"; \
	else \
		echo "❌ Ollama is not installed"; \
		echo "   Download from: https://ollama.ai/download"; \
	fi

