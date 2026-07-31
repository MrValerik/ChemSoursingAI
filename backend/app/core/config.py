"""Конфигурация приложения. 12-factor: все параметры — из окружения (.env).

Смена среды (dev/prod) и провайдеров (LLM, БД, каналы) выполняется через
переменные окружения без правок кода.
"""

from functools import lru_cache
from typing import Literal

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
    # Отдельный ключ шифрования для паролей и токенов, сохранённых через UI.
    # Если пуст, используется AUTH_SECRET_KEY; в production задайте отдельное
    # случайное значение и храните его только в окружении.
    integration_encryption_key: str = Field(default="")

    # --- Очередь ---
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- LLM (OpenAI-совместимый эндпоинт) ---
    # В локальной разработке backend занимает :8000, поэтому LLM использует :8080.
    # Docker Compose переопределяет адрес на host.docker.internal:8000.
    llm_base_url: str = Field(default="http://127.0.0.1:8080/v1")
    llm_model: str = Field(default="Qwen_Qwen3.5-27B-Q4_K_M")
    llm_api_key: str = Field(default="not-needed-for-local")
    llm_timeout_s: int = Field(default=600)

    # --- Email-коннектор (IMAP/SMTP) ---
    # demo сохраняет безопасное поведение без внешней отправки; live включает SMTP.
    email_delivery_mode: Literal["demo", "live"] = Field(default="demo")
    email_from: str = Field(default="")
    email_from_name: str = Field(default="ChemSource AI")
    email_timeout_s: int = Field(default=30)
    auto_followup_mode: Literal["off", "draft", "send"] = Field(default="draft")
    imap_host: str = Field(default="")
    imap_port: int = Field(default=993)
    imap_user: str = Field(default="")
    imap_password: str = Field(default="")
    imap_use_ssl: bool = Field(default=True)
    imap_folder: str = Field(default="INBOX")
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=465)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_ssl: bool = Field(default=True)
    smtp_starttls: bool = Field(default=False)

    # --- WhatsApp Cloud API — этап интеграций ---
    whatsapp_token: str = Field(default="")
    whatsapp_phone_id: str = Field(default="")
    whatsapp_api_base_url: str = Field(default="https://graph.facebook.com")
    whatsapp_api_version: str = Field(default="v23.0")
    whatsapp_timeout_s: int = Field(default=30)

    # --- PubChem ---
    pubchem_base_url: str = Field(
        default="https://pubchem.ncbi.nlm.nih.gov/rest/pug"
    )

    # --- Бюджеты одного этапа поиска поставщиков ---
    # Исчерпание бюджета завершает этап безопасным частичным результатом
    # со stop reason в трассировке, а не ошибкой.
    search_max_queries: int = Field(default=12)
    search_max_page_fetches: int = Field(default=30)
    search_max_llm_calls: int = Field(default=40)
    search_max_runtime_s: int = Field(default=2700)

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
