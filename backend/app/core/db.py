"""Слой данных (L7): engine, фабрика сессий и FastAPI-зависимость.

Engine создаётся из DSN в конфиге. Для PostgreSQL используется psycopg3.
В dev/тестах допускается SQLite (DATABASE_URL=sqlite:///./dev.db).
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Base

_settings = get_settings()

# SQLite требует особый флаг для многопоточного доступа FastAPI.
_connect_args = (
    {"check_same_thread": False}
    if _settings.sqlalchemy_dsn.startswith("sqlite")
    else {}
)

engine = create_engine(
    _settings.sqlalchemy_dsn,
    pool_pre_ping=True,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Создаёт таблицы по метаданным моделей.

    Для прода предпочтительны миграции (Alembic); это удобно для dev/демо
    и первичного развёртывания.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос с гарантированным закрытием."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
