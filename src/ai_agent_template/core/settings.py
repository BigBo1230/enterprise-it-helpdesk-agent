from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_AGENT_",
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    service_name: str = "enterprise-it-helpdesk-agent"
    log_level: str = "INFO"
    cors_allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")
    bailian_api_key: SecretStr | None = Field(default=None, alias="BAILIAN_API_KEY")
    bailian_base_url: str | None = None
    model_provider: Literal["openai", "groq", "bailian"] = "groq"
    model_name: str = "llama-3.1-8b-instant"
    model_temperature: float = 0
    model_timeout_seconds: float = 30
    model_max_retries: int = 2
    ticket_db_path: str = "/data/tickets.sqlite3"
    ticket_backend: Literal["local", "jira_mock", "github_mock"] = "local"
    jira_base_url: str = "http://jira-mock:8090"
    jira_api_token: SecretStr | None = None
    jira_project_key: str = "HELP"
    jira_timeout_seconds: float = 5
    github_base_url: str = "http://github-mock:8091"
    github_api_token: SecretStr | None = None
    github_owner: str = "demo-org"
    github_repo: str = "it-helpdesk"
    github_timeout_seconds: float = 5
    slack_notifications_enabled: bool = True
    slack_mock_base_url: str = "http://slack-mock:8092"
    slack_mock_api_token: SecretStr | None = None
    slack_mock_channel: str = "it-helpdesk"
    slack_mock_timeout_seconds: float = 5

    rag_enabled: bool = True
    rag_collection_name: str = "agent_knowledge"
    rag_top_k: int = 4
    rag_voice_top_k: int = 4
    rag_score_threshold: float | None = None
    rag_chunk_size: int = 1_000
    rag_chunk_overlap: int = 150
    rag_search_multiplier: int = 3
    rag_embedding_provider: Literal["fastembed"] = "fastembed"
    rag_embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    rag_embedding_cache_path: str = "/data/fastembed_cache"
    rag_embedding_dimensions: int = 384
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_storage_path: str = ".qdrant"

    auth_api_keys: list[str] = Field(default_factory=list)
    tenant_api_keys: dict[str, str] = Field(default_factory=dict)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    request_id_header: str = "x-request-id"
    request_max_body_bytes: int = 5 * 1024 * 1024

    agent_checkpoint_backend: Literal["memory", "none"] = "memory"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
