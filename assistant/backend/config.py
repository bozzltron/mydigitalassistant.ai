from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3.5:9b"
    utility_model: str = "qwen3.5:4b"
    embedding_model: str = "qwen3-embedding:0.6b"
    # Reserved role (Phase 6 M5). Empty = use chat_model for codegen.
    coder_model: str = ""

    # Math/computation model (Phase: Math Model Integration). Empty = math
    # computation disabled (compute tool is dropped from the tool loop).
    # NOTE: a large model here (e.g. qwen3-coder:30b) competes with chat +
    # utility + embedding for host RAM/VRAM and must not be left resident on
    # the hot path — the default is deliberately OFF. Opt in with MATH_MODEL
    # and keep math_num_ctx small so it loads alongside the always-warm set.
    math_model: str = ""
    math_num_ctx: int = 16384
    math_keep_alive: str = "10m"

    # Thinking-mode plumbing (Phase 6 M4 wires the escalation policy).
    # chat_think_default is the fast-path default; escalations override per call.
    chat_think_default: bool = False
    # Cap applies to thinking AND answer combined (Ollama has no separate
    # think budget). 1024 truncated long answers mid-sentence once the model
    # spent its budget reasoning; 4096 leaves room for both.
    think_num_predict_cap: int = 4096
    # Native tool-calling on the chat model (Phase 6 M5). The web_search tool
    # additionally depends on SearXNG availability.
    tools_enabled: bool = True

    # File sandbox settings
    sandbox_max_file_size: int = 10_000_000      # 10MB max write
    sandbox_max_read_size: int = 1_000_000       # 1MB max read
    sandbox_max_glob_results: int = 1000         # max glob results

    # CSV uploads: cap per-row memory frames. Files with more rows than this
    # keep only row_count/columns metadata on the file frame; row data stays on
    # disk and is read via read_file. Prevents a large CSV from exploding into
    # thousands of frames/slots (regression: a 698-row upload created 698 row
    # frames + ~5.5k slots — ~60% of the brain — exempt from GC decay at 0.5).
    csv_max_row_frames: int = 100

    # File uploads: cap entity_* slots stored on the file frame. The CSV/JSON
    # extractors emit one entry per unique cell/value, so a large file would
    # otherwise dump hundreds-to-thousands of entity slots onto the parent frame
    # (e.g. 1,140 on a 692-row CSV) — heavy, redundant (content lives on disk
    # via read_file), and large enough to break embedding (text over nomic's
    # context => Ollama 500). Small files (few unique values) store all entities.
    file_max_entity_slots: int = 50

    # Context windows per call class (Phase 6 plan §4.4). Without these,
    # Ollama defaults to 32K context on large-RAM hosts and allocates a
    # proportionally huge KV cache on every request.
    chat_num_ctx: int = 8192
    utility_num_ctx: int = 4096

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    database_path: str = "./assistant.db"

    # Encryption key for SQLCipher. If empty, DB is stored unencrypted.
    # The key is passed to SQLCipher via "PRAGMA key" on each connection.
    db_key: str = ""

    # Web search settings (always enabled - core requirement)
    search_base_url: str = "http://127.0.0.1:8080"    # SearXNG default
    search_timeout: float = 90.0
    # Result-quality controls (search hardening). safesearch follows
    # SearXNG's 0=off..2=strict scale; min_relevance is the cosine similarity
    # between query and title+snippet below which results are dropped.
    search_language: str = "en"
    search_safesearch: int = 1
    search_min_relevance: float = 0.30

    # Optional cloud search backend. Brave is the only permitted non-local search
    # provider because it does not profile users or sell query data. Brave is
    # disabled by default; set BRAVE_ENABLED=true AND provide BRAVE_API_KEY to use it.
    brave_enabled: bool = False
    brave_api_key: str = ""  # required when brave_enabled is true
    brave_search_min_relevance: float = 0.20

    # Ollama request timeout in seconds. Local 27B-class models with large
    # context prefills (tool loops) can legitimately take minutes; the default
    # is generous on purpose.
    ollama_timeout: float = 600.0

    # How long Ollama keeps a model in memory after the last request.
    # Reloading a 27B model takes tens of seconds, so short defaults make
    # every turn feel slow. "-1" keeps models loaded forever (Option A
    # residency: chat + utility stay warm so alternating roles cost ~0).
    # Pair with host Ollama env OLLAMA_MAX_LOADED_MODELS>=2.
    ollama_keep_alive: str = "-1"

    # Dedicated tools model for fast function calling (Phase: Performance)
    # qwen3.5:9b matches the chat model so the whole turn (tool calls + final
    # answer) runs on the same brain with hot KV cache reuse.
    tools_model: str = "qwen3.5:9b"
    tools_num_ctx: int = 4096
    tools_keep_alive: str = "-1"

    # Max-intelligence escalation tier (Phase 6 M6). The largest local model the
    # host can serve well (qwen3.8:27b on a 48GB Mac). Loaded on demand with a
    # short keep_alive — never resident next to the warm chat/utility/embedding
    # set. Empty = escalation falls back to thinking-mode on the chat model.
    max_model: str = ""
    max_num_ctx: int = 16384
    max_keep_alive: str = "10m"

    # Streaming responses (SSE) - enabled by default
    streaming_enabled: bool = True

    # Retrieval: max vector distance for direct candidate frames
    # (sqlite-vec cosine, 0-2). Lower = stricter similarity.
    retrieval_min_distance: float = 0.7
    # Max past-conversation turns injected per retrieval cycle (semantic
    # episode recall). Owner-scoped, current session excluded. Disabled (0)
    # is opt-out; the default of 5 matches .env.example.
    retrieval_episode_limit: int = 5

    # System prompt budget (context window protection)
    # Limits total prompt chars before LLM call; truncates least-relevant first
    max_system_prompt_chars: int = 12000
    max_search_results_in_prompt: int = 3
    # Peak measured in assistant/experiments/frame_budget: recall rises to 0.697
    # at 10 frames and falls to 0.636 at 20 and 40, and max_system_prompt_chars
    # starts truncating memory at ~8-10 frames (73% of generations at 40). This
    # was 5 while retrieval returned only top_k_direct=3 frames, so it never
    # bound; now that the graph walk supplies candidates it caps both halves of
    # the context independently, so 5 would discard half the walk.
    max_frames_in_prompt: int = 10
    max_episodes_in_prompt: int = 10
    max_episode_digest_chars: int = 240

    # Canonical frame resolution (Phase 9A): max embedding distance at which a
    # near-duplicate name reuses an existing frame instead of creating one.
    # Conservative by design — consolidation (Phase 9B) loosens with evidence.
    canonical_name_distance: float = 0.10

    # Consolidation merge pass (Phase 9B): embedding gate for the offline
    # dedup job. Calibrated against the live corpus: true duplicates sit at
    # cosine distance < 0.17, unrelated same-type pairs at 0.26+ (p01).
    # 0.15 keeps precision high; shared-title evidence catches the rest.
    consolidation_name_distance: float = 0.15
    # Twice-daily memory consolidation ("dreaming") in the scheduler loop:
    # merge duplicate frames + strengthen episode-backed associations.
    # 0 disables the timer entirely.
    consolidation_interval_hours: int = 12
    # Circuit breaker for unattended runs: if a pass plans more merges than
    # this, it writes nothing and logs for manual review instead.
    consolidation_max_merges_per_run: int = 10

    # TLS verification (defense-in-depth for local services)
    # Set to path of CA cert if Ollama/SearXNG use self-signed TLS
    ollama_tls_cert: str = ""

    # Voice transcription (optional - requires faster-whisper)
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_download_root: str = "/app/.cache/whisper"  # model cache dir
    whisper_language: str = "en"  # ISO code; "" = auto-detect

    # Fallback display name until the user sets the assistant's identity name.
    assistant_name: str = "Cognitive Assistant"

    # Background summarization (episodic -> semantic compression)
    summarization_enabled: bool = True
    summarization_interval_hours: int = 24
    summarization_min_turns: int = 10
    summarization_max_sessions_per_run: int = 5
    summarization_max_chars: int = 4000
    summarization_timeout_seconds: int = 60

    # Working memory (LRU cache for retrieval bias)
    working_memory_max_size: int = 50   # max entries in working memory
    working_memory_boost: float = 1.5  # relevance multiplier for working memory frames

    # Scheduled tasks (runs as background task inside the backend)
    scheduler_enabled: bool = True

    # The daily list: one wake-up time for all user tasks. Tasks repeat daily
    # until the user stops them, or run once at the next tick.
    daily_tasks_time: str = "09:00"  # HH:MM, 24h
    daily_tasks_tz: str = ""  # IANA zone; empty = TZ env or host-local


settings = Settings()
