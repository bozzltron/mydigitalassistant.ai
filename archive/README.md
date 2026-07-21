# My Digital Assistant

A privacy-focused, local AI assistant with voice input/output, web search, and multiple AI models - all running locally.

## ⚡ Quick Start

```bash
# 1. Install Docker and Ollama
# 2. Download a model: ollama pull qwen2.5:7b
# 3. Start everything:
make open-webui
# 4. Open http://localhost:8888 and create your account
```

## 🎯 Features

- ✅ **100% Local**: All processing on your machine
- ✅ **Voice Input/Output**: Speech-to-text and text-to-speech (local, no API keys)
- ✅ **Web Search**: Privacy-respecting search (SearXNG with privacy-focused engines only)
- ✅ **Email Support** (optional): Receive and respond to emails via AI
- ✅ **Multiple Models**: Qwen2.5, Llama, Mistral, and more

## 📋 Prerequisites

- **Docker Desktop**: [Download](https://www.docker.com/products/docker-desktop/)
- **Ollama**: [Download](https://ollama.ai/download)
- **Make** (optional): Pre-installed on macOS, install via package manager on Linux/Windows
- **System**: 8GB+ RAM, 10GB free space

## 🚀 Setup

### 1. Install Ollama
```bash
# macOS/Windows: Download from ollama.ai
# Linux:
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Download a Model
```bash
ollama pull qwen2.5:7b  # Recommended: excellent balance
# Or: ollama pull llama3.2  # Faster, smaller
```

### 3. Start Services
```bash
make open-webui
# Wait 1-2 minutes, then open http://localhost:8888
```

### 4. Create Account
- First user automatically becomes admin
- **Important**: After creating account, set `ENABLE_SIGNUP=false` in `docker-compose.open-webui.yml` and restart

## ⚙️ Configuration

### Voice Output (TTS)
1. Settings → Audio → Text-to-Speech
2. Engine: `OpenAI`
3. API Base URL: `http://edge-tts:5050/v1` ⚠️ **Use service name, not localhost**
4. API Key: `your_api_key_here` (any placeholder)
5. TTS Model: `tts-1`
6. TTS Voice: `en-GB-LibbyNeural` (UK female) or choose another

### Web Search
1. Settings → Web Search
2. Enable Web Search: `ON` (or `OFF` to disable by default)
3. Engine: `searxng`
4. Searxng Query URL: `http://searxng:8080/search` ⚠️ **Use service name, not localhost**

**💡 Tip**: To prevent unnecessary searches (e.g., when summarizing conversations):
- **Option 1**: Disable web search by default, enable manually when needed
- **Option 2**: Use **AutoFeature Selector** (see Best Practices below) to intelligently enable features
- **Option 3**: Configure tools per model in Settings → Models → [Model Name] → Tools

**Voice Input**: Already configured! Click microphone icon in chat.

## 🎨 Models

**Recommended**: `qwen2.5:7b` - Excellent reasoning, multilingual (119 languages)

**Other options**:
- `llama3.2` - Fast, good for general tasks (3B, needs 4GB RAM)
- `gemma:7b` - Open-source model, suitable for various applications (7B, needs 8GB RAM) ✅ **Downloaded**

### Download Models (Two Ways)

#### Option 1: Using Open WebUI (Easiest)
1. Open http://localhost:8888
2. Click the **model selector** (top of chat interface)
3. Click **"Show more models"** or **"Download models"**
4. Search for a model (e.g., "qwen2.5")
5. Click **Download** - Open WebUI will download it automatically!

#### Option 2: Using Command Line

```bash
# List available models and check status
make ollama-models

# Download recommended model
make ollama-pull-qwen

# Or download others
make ollama-pull-llama
make ollama-pull-gemma

# Check Ollama status
make ollama-status
```

Or manually: `ollama pull <model-name>`

## 💡 Best Practices for Personal Assistant

### Model Switching & Context

**⚠️ Important**: Switching models mid-conversation will lose context. Each model maintains its own session state.

**Solutions**:
1. **Use the same model** throughout a conversation for best results
2. **Enable Adaptive Memory** (Settings → Features → Adaptive Memory):
   - Helps models retain relevant conversation details over time
   - Improves context retention across sessions
   - Makes model switching less disruptive
3. **Manual context transfer**: If you must switch models, summarize the conversation and provide it to the new model

### Web Search Control

**Problem**: Web search activates even when unnecessary (e.g., summarizing conversations).

**Solutions**:
1. **Disable by default**: Settings → Web Search → Enable Web Search: `OFF`
   - Enable manually when needed using the search toggle in chat
2. **AutoFeature Selector**: Install from Open WebUI Hub to intelligently enable features
   - Analyzes your input to determine if web search is needed
   - Automatically enables/disables features based on context
3. **Per-model configuration**: Settings → Models → [Model Name] → Tools
   - Configure which tools (web search, etc.) are available per model
   - Some models can have web search disabled entirely

### Recommended Settings for Personal Assistant

1. **Enable Adaptive Memory**: Improves long-term context retention
2. **Configure web search selectively**: Disable by default, enable when needed
3. **Use consistent models**: Stick with one model (e.g., `qwen2.5:7b`) for most tasks
4. **Browser integration**: Set up Open WebUI as a custom search engine for quick access
5. **Custom tools**: Install tools from Open WebUI Hub that match your needs

### Quick Tips

- **Start new conversations** when switching models to avoid context confusion
- **Use model-specific chats**: Create separate chats for different models if you frequently switch
- **Leverage Adaptive Memory**: It learns your preferences and conversation patterns over time
- **Disable unused features**: Turn off tools you don't need to reduce unnecessary processing

## 🐛 Troubleshooting

**Can't access http://localhost:8888:**
- Check containers: `docker ps`
- View logs: `make open-webui-logs`
- Wait 1-2 minutes for startup

**Voice input not working:**
- Install Whisper: `make open-webui-install-whisper`
- Restart: `make open-webui-restart`

**TTS not working:**
- API URL must be `http://edge-tts:5050/v1` (not localhost)
- Check Edge TTS is running: `docker ps | grep edge-tts`

**Web search not working:**
- URL must be `http://searxng:8080/search` (not localhost)
- Check SearXNG is running: `docker ps | grep searxng`

**DuckDuckGo CAPTCHA errors:**
- Normal behavior - SearXNG automatically uses other engines (Brave, Startpage)
- Not a problem, system continues working

## 💾 Data Management

```bash
# Export data
make open-webui-export

# Import data
make open-webui-import FILE=backup.tar.gz

# Reset password
make open-webui-reset-password EMAIL=your@email.com PASSWORD='newpass'

# Reset everything (deletes all data)
make open-webui-reset
```

## 🛠️ Commands

```bash
# Service management
make open-webui              # Start services
make open-webui-logs         # View logs
make open-webui-stop         # Stop services
make open-webui-restart      # Restart services
make open-webui-install-whisper  # Install/reinstall Whisper

# Model management
make ollama-models           # List available models
make ollama-pull-qwen        # Download Qwen2.5 (recommended)
make ollama-status           # Check Ollama status

# Help
make help                    # Show all commands
```

## 🔒 Privacy & Security

- **100% Local**: All processing on your machine
- **No Tracking**: No analytics or telemetry
- **Privacy-Focused Search**: Only privacy-respecting engines enabled (Startpage, Brave, DuckDuckGo, Qwant)
- **Self-Hosted**: Whisper, TTS, and web search all run locally

**📋 Security Assessment**: See [SECURITY_ASSESSMENT.md](SECURITY_ASSESSMENT.md) for detailed security and privacy analysis of all models.

## 📚 Resources

- **Ollama**: https://ollama.ai/
- **Open WebUI**: https://open-webui.com/
- **Documentation**: https://docs.openwebui.com/
- **Adaptive Memory**: https://open-webui.com/open-webui-adaptive-memory/
- **Tools & Plugins**: https://docs.openwebui.com/features/plugin/tools/
- **Browser Integration**: https://docs.openwebui.com/tutorials/integrations/browser-search-engine/

## 📝 License

MIT License - see [LICENSE](LICENSE) file for details.

---

**Built with ❤️ for privacy and local-first computing**
