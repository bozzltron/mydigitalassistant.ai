from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen2.5:7b"
    utility_model: str = "qwen2.5:3b"
    embedding_model: str = "nomic-embed-text"
    # Reserved role (Phase 6 M5). Empty = use chat_model for codegen.
    coder_model: str = ""

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

    conflict_auto_resolve: bool = True

    # Web search settings (always enabled - core requirement)
    search_base_url: str = "http://127.0.0.1:8080"    # SearXNG default
    search_timeout: float = 30.0
    # Result-quality controls (search hardening). safesearch follows
    # SearXNG's 0=off..2=strict scale; min_relevance is the cosine similarity
    # between query and title+snippet below which results are dropped.
    search_language: str = "en"
    search_safesearch: int = 1
    search_min_relevance: float = 0.30

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

    # Retrieval: max vector distance for direct candidate frames
    # (sqlite-vec cosine, 0-2). Lower = stricter similarity.
    retrieval_min_distance: float = 0.7

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
    search_tls_cert: str = ""

    # Voice transcription (optional - requires faster-whisper)
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_download_root: str = "/app/.cache/whisper"  # model cache dir
    whisper_language: str = "en"  # ISO code; "" = auto-detect

    # Fallback display name until the user sets the assistant's identity name.
    assistant_name: str = "Cognitive Assistant"

    # Working memory (LRU cache for retrieval bias)
    working_memory_max_size: int = 50   # max entries in working memory
    working_memory_boost: float = 1.5  # relevance multiplier for working memory frames

    # Scheduled tasks (runs as background task inside the backend)
    scheduler_enabled: bool = False

    # The daily list: one wake-up time for all user tasks. Tasks repeat daily
    # until the user stops them, or run once at the next tick.
    daily_tasks_time: str = "09:00"  # HH:MM, 24h
    daily_tasks_tz: str = ""  # IANA zone; empty = TZ env or host-local


settings = Settings()
