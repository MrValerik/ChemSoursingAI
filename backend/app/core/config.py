"""Конфигурация приложения. 12-factor: все параметры — из окружения (.env).

Смена среды (dev/prod) и провайдеров (LLM, БД, каналы) выполняется через
переменные окружения без правок кода.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Приложение ---
    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # --- База данных ---
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="chemsource")
    postgres_user: str = Field(default="chemsource")
    postgres_password: str = Field(default="change_me")
    database_url: str | None = Field(default=None)

    # --- Аутентификация ---
    auth_secret_key: str = Field(default="dev-secret-change-in-prod")
    access_token_expire_minutes: int = Field(default=480)

    # --- Очередь ---
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- LLM (OpenAI-совместимый эндпоинт) ---
    llm_base_url: str = Field(default="http://127.0.0.1:8080/v1")
    llm_model: str = Field(default="qwen3-8b")
    llm_api_key: str = Field(default="not-needed-for-local")
    llm_timeout_s: int = Field(default=120)

    # --- Email-коннектор (IMAP/SMTP) — этап интеграций ---
    imap_host: str = Field(default="")
    imap_port: int = Field(default=993)
    imap_user: str = Field(default="")
    imap_password: str = Field(default="")
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=465)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")

    # --- WhatsApp Cloud API — этап интеграций ---
    whatsapp_token: str = Field(default="")
    whatsapp_phone_id: str = Field(default="")

    # --- PubChem ---
    pubchem_base_url: str = Field(
        default="https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    )

    @property
    def sqlalchemy_dsn(self) -> str:
        """Готовый DSN для SQLAlchemy. database_url перекрывает поля POSTGRES_*."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Кэшированный синглтон настроек."""
    return Settings()
