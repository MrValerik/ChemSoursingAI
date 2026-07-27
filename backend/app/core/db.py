"""Слой данных (L7): engine, фабрика сессий и FastAPI-зависимость.

Engine создаётся из DSN в конфиге. Для PostgreSQL используется psycopg3.
В dev/тестах допускается SQLite (DATABASE_URL=sqlite:///./dev.db).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models import Base

_settings = get_settings()

# SQLite требует особый флаг для многопоточного доступа FastAPI.
_connect_args = (
    {"check_same_thread": False}
    if _settings.sqlalchemy_dsn.startswith("sqlite")
    else {}
)
_pool_options = (
    {"poolclass": NullPool}
    if _settings.sqlalchemy_dsn.startswith("sqlite")
    else {}
)

engine = create_engine(
    _settings.sqlalchemy_dsn,
    pool_pre_ping=True,
    connect_args=_connect_args,
    future=True,
    **_pool_options,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Создаёт таблицы по метаданным моделей.

    Для прода предпочтительны миграции (Alembic); это удобно для dev/демо
    и первичного развёртывания.
    """
    Base.metadata.create_all(bind=engine)
    _apply_light_migrations()


def _apply_light_migrations() -> None:
    """Дописывает недостающие колонки в существующие таблицы (dev/демо).

    create_all не изменяет уже созданные таблицы, поэтому новые поля
    добавляем точечно. В проде это заменят миграции Alembic.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "rfqs" in tables:
        cols = {c["name"] for c in inspector.get_columns("rfqs")}
        if "owner_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE rfqs ADD COLUMN owner_id INTEGER"))
    if "suppliers" in tables:
        cols = {c["name"] for c in inspector.get_columns("suppliers")}
        if "source" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE suppliers ADD COLUMN source VARCHAR(255)"))
    if "communications" in tables:
        cols = {c["name"] for c in inspector.get_columns("communications")}
        additions = {
            "subject": "VARCHAR(998)",
            "from_address": "VARCHAR(320)",
            "to_address": "VARCHAR(320)",
            "status": "VARCHAR(32)",
        }
        with engine.begin() as conn:
            for name, sql_type in additions.items():
                if name not in cols:
                    conn.execute(
                        text(
                            f"ALTER TABLE communications ADD COLUMN {name} {sql_type}"
                        )
                    )
    if "search_attempts" in tables:
        cols = {c["name"] for c in inspector.get_columns("search_attempts")}
        if "results_payload" not in cols:
            json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE search_attempts "
                        f"ADD COLUMN results_payload {json_type}"
                    )
                )


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос с гарантированным закрытием."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
