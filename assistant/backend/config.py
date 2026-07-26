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

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000

    database_path: str = "./assistant.db"

    conflict_auto_resolve: bool = True

    # Web search settings (always enabled - core requirement)
    search_base_url: str = "http://127.0.0.1:8080"    # SearXNG default
    search_timeout: float = 30.0


settings = Settings()
