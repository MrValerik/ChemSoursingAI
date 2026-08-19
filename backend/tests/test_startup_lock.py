"""Подготовку схемы проходит по одному процессу за раз.

19 августа стенд не поднялся после холодной загрузки. Стек стартует шесть
процессов разом — backend, четыре поисковых воркера и почтовый, — и
каждый на старте правит схему и заполняет справочники. Двое сошлись на
одном и том же `UPDATE rfqs SET substance_id = ...`, PostgreSQL увидел
взаимную блокировку и снял одного. Снятым оказался backend: исключение
прилетело в lifespan, контейнер упал, compose объявил его нездоровым и не
стал поднимать фронтенд — то есть nginx, который держит 80-й порт. Машина
работала, сайт молчал.

Блокировка рекомендательная: на SQLite её нет и быть не должно — там
процесс всегда один, — а на PostgreSQL второй ждёт, пока первый закончит.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_startup_lock.db")

import pytest

from app.core import db
from app.core.db import _one_process_at_a_time, engine, init_db


def test_the_lock_steps_aside_where_there_is_nothing_to_guard():
    """SQLite: замок не мешает и ничего не требует."""
    with _one_process_at_a_time():
        pass

    assert engine.dialect.name == "sqlite"


def test_preparing_the_schema_twice_changes_nothing():
    """Второй процесс застаёт всё сделанным и не падает."""
    init_db()
    init_db()


def test_on_postgres_it_takes_and_returns_the_lock(monkeypatch):
    """На PostgreSQL замок берётся до работы и отдаётся после неё.

    Проверяем именно наш код, а не движок: подставляем соединение,
    записывающее запросы, и смотрим, что и в каком порядке ушло в базу.
    """
    executed: list[tuple[str, object]] = []

    class FakeConnection:
        def execute(self, statement, params=None):
            executed.append((str(statement), params))

        def commit(self):
            executed.append(("commit", None))

        def close(self):
            executed.append(("close", None))

    class FakeDialect:
        name = "postgresql"

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(db, "engine", FakeEngine())

    with db._one_process_at_a_time():
        executed.append(("работа", None))

    statements = [statement for statement, _ in executed]
    assert "pg_advisory_lock" in statements[0]
    unlock_at = next(
        i for i, text in enumerate(statements) if "pg_advisory_unlock" in text
    )
    # Замок берётся до работы и отпускается после неё, а соединение
    # закрывается последним.
    assert statements.index("работа") > 0
    assert unlock_at > statements.index("работа")
    assert statements[-1] == "close"


def test_the_lock_is_released_even_if_preparing_fails(monkeypatch):
    """Иначе упавший процесс запер бы стек целиком и навсегда."""
    executed: list[str] = []

    class FakeConnection:
        def execute(self, statement, params=None):
            executed.append(str(statement))

        def commit(self):
            pass

        def close(self):
            pass

    class FakeDialect:
        name = "postgresql"

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(db, "engine", FakeEngine())

    with pytest.raises(RuntimeError):
        with db._one_process_at_a_time():
            raise RuntimeError("схема не поддалась")

    assert any("pg_advisory_unlock" in statement for statement in executed)
