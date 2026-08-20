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
    reasoning_model: str = "qwen2.5:7b"
    embedding_model: str = "nomic-embed-text"

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

    # TLS verification (defense-in-depth for local services)
    # Set to path of CA cert if Ollama/SearXNG use self-signed TLS
    ollama_tls_cert: str = ""
    search_tls_cert: str = ""

    # Voice transcription (optional - requires faster-whisper)
    whisper_model: str = "base"
    whisper_device: str = "cpu"

    # Working memory (LRU cache for retrieval bias)
    working_memory_max_size: int = 50   # max entries in working memory
    working_memory_boost: float = 1.5  # relevance multiplier for working memory frames


settings = Settings()
