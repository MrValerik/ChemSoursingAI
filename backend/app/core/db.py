"""Слой данных (L7): engine, фабрика сессий и FastAPI-зависимость.

Engine создаётся из DSN в конфиге. Для PostgreSQL используется psycopg3.
В dev/тестах допускается SQLite (DATABASE_URL=sqlite:///./dev.db).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
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


# Ключ рекомендательной блокировки: число произвольное, важно лишь то,
# что оно одно на все процессы.
_STARTUP_LOCK_KEY = 4_073_120_519


@contextmanager
def _one_process_at_a_time() -> Iterator[None]:
    """Пропускает через подготовку схемы по одному процессу.

    Стек поднимает шесть процессов разом — backend, четыре поисковых
    воркера и почтовый, — и каждый на старте правит схему и заполняет
    справочники. 19 августа при холодной загрузке двое сошлись на одном и
    том же `UPDATE rfqs SET substance_id = ...`, PostgreSQL увидел
    взаимную блокировку и снял одного из них. Снятым оказался backend:
    исключение прилетело в lifespan, контейнер упал, compose объявил его
    нездоровым и не поднял фронтенд — то есть nginx, который и держит
    80-й порт. Сайт стоял тёмным при живой машине.

    Блокировка рекомендательная и уровня сессии: первый процесс делает
    работу, остальные ждут и застают всё уже сделанным. SQLite такого не
    умеет, да и незачем — там процесс всегда один.
    """
    if engine.dialect.name != "postgresql":
        yield
        return
    connection = engine.connect()
    try:
        connection.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": _STARTUP_LOCK_KEY}
        )
        # Держим саму блокировку, а не открытую транзакцию: она уровня
        # сессии и переживает коммит, а висящая транзакция мешала бы
        # остальным.
        connection.commit()
        yield
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _STARTUP_LOCK_KEY}
        )
        connection.commit()
        connection.close()


def init_db() -> None:
    """Создаёт таблицы по метаданным моделей.

    Для прода предпочтительны миграции (Alembic); это удобно для dev/демо
    и первичного развёртывания.
    """
    with _one_process_at_a_time():
        Base.metadata.create_all(bind=engine)
        _apply_light_migrations()


def _relax_sqlite_not_null(table: str, column: str) -> None:
    """Снимает устаревший NOT NULL в dev-базе SQLite.

    В PostgreSQL это делает миграция (ALTER COLUMN ... DROP NOT NULL), но
    SQLite менять ограничение колонки не умеет — только пересоздавать
    таблицу. Свежая dev-база берёт схему из моделей и уже корректна;
    проблема только у баз, созданных до того, как CAS стал необязательным.
    Без этого запрос по аналогу или спецификации падает на вставке, причём
    ошибкой уровня БД, которую в интерфейсе не объяснить.

    Схема пересоздаётся из метаданных модели, а не переписывается руками:
    так она не разъедется с определением таблицы.
    """
    from sqlalchemy import inspect, text

    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    columns = inspector.get_columns(table)
    target = next((c for c in columns if c["name"] == column), None)
    if target is None or target.get("nullable", True):
        return

    model_table = Base.metadata.tables.get(table)
    if model_table is None:
        return
    # Переносим только те колонки, что есть в обеих схемах: недостающие
    # добавит обычный проход по ALTER TABLE следом.
    shared = [c["name"] for c in columns if c["name"] in model_table.c]
    names = ", ".join(f'"{name}"' for name in shared)
    backup = f"{table}__old"
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text(f'DROP TABLE IF EXISTS "{backup}"'))
        conn.execute(text(f'ALTER TABLE "{table}" RENAME TO "{backup}"'))
        model_table.create(bind=conn)
        conn.execute(
            text(f'INSERT INTO "{table}" ({names}) SELECT {names} FROM "{backup}"')
        )
        conn.execute(text(f'DROP TABLE "{backup}"'))
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _apply_light_migrations() -> None:
    """Дописывает недостающие колонки в существующие таблицы (dev/демо).

    create_all не изменяет уже созданные таблицы, поэтому новые поля
    добавляем точечно. В проде это заменят миграции Alembic.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "communication_test_runs" in tables:
        cols = {
            c["name"]
            for c in inspector.get_columns("communication_test_runs")
        }
        with engine.begin() as conn:
            if "procurement_context" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "procurement_context TEXT NOT NULL DEFAULT ''"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE communication_test_runs "
                        "SET procurement_context = customer_message"
                    )
                )
            if "subject" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN subject "
                        "VARCHAR(998) NOT NULL DEFAULT 'Request for quotation'"
                    )
                )
            if "recipient_key" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "recipient_key VARCHAR(64)"
                    )
                )
            if "recipient_ciphertext" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "recipient_ciphertext TEXT"
                    )
                )
            if "simulation_mode" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "simulation_mode VARCHAR(32) NOT NULL DEFAULT 'buyer_ai'"
                    )
                )
            if "rfq_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "rfq_id INTEGER REFERENCES rfqs(id)"
                    )
                )
            if "quotation_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_runs ADD COLUMN "
                        "quotation_id INTEGER REFERENCES quotations(id)"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_communication_test_runs_rfq_id "
                    "ON communication_test_runs (rfq_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_communication_test_runs_quotation_id "
                    "ON communication_test_runs (quotation_id) "
                    "WHERE quotation_id IS NOT NULL"
                )
            )
    if "communication_test_messages" in tables:
        cols = {
            c["name"]
            for c in inspector.get_columns("communication_test_messages")
        }
        if "translation_ru" not in cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE communication_test_messages "
                        "ADD COLUMN translation_ru TEXT"
                    )
                )
    if "rfqs" in tables:
        _relax_sqlite_not_null("rfqs", "cas")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("rfqs")}
        json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
        with engine.begin() as conn:
            if "owner_id" not in cols:
                conn.execute(text("ALTER TABLE rfqs ADD COLUMN owner_id INTEGER"))
            if "search_countries" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE rfqs "
                        f"ADD COLUMN search_countries {json_type}"
                    )
                )
            if "supplier_target" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE rfqs ADD COLUMN supplier_target "
                        "INTEGER NOT NULL DEFAULT 5"
                    )
                )
            if "substance_id" not in cols:
                conn.execute(
                    text("ALTER TABLE rfqs ADD COLUMN substance_id INTEGER")
                )
            if "identification_method" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE rfqs ADD COLUMN identification_method "
                        "VARCHAR(16) NOT NULL DEFAULT 'cas'"
                    )
                )
            if "analog_reference" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE rfqs ADD COLUMN analog_reference VARCHAR(255)"
                    )
                )
            for column in ("analog_variations", "confirmed_synonyms",
                           "excluded_names", "field_sources"):
                if column not in cols:
                    conn.execute(
                        text(f"ALTER TABLE rfqs ADD COLUMN {column} {json_type}")
                    )
            if "specification" not in cols:
                conn.execute(
                    text("ALTER TABLE rfqs ADD COLUMN specification TEXT")
                )
            if "specialist_comment" not in cols:
                conn.execute(
                    text("ALTER TABLE rfqs ADD COLUMN specialist_comment TEXT")
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_rfqs_substance_id "
                    "ON rfqs (substance_id)"
                )
            )
            if "substances" in tables:
                empty_json = (
                    "CAST('[]' AS JSONB)"
                    if engine.dialect.name == "postgresql"
                    else "'[]'"
                )
                conn.execute(
                    text(
                        "INSERT INTO substances "
                        "(cas, preferred_name, synonyms, excluded_names, review_status) "
                        "SELECT r.cas, MIN(r.name), "
                        f"{empty_json}, {empty_json}, 'unreviewed' "
                        "FROM rfqs r "
                        "LEFT JOIN substances s ON s.cas = r.cas "
                        "WHERE r.cas IS NOT NULL AND s.id IS NULL "
                        "GROUP BY r.cas"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE rfqs SET substance_id = ("
                        "SELECT s.id FROM substances s WHERE s.cas = rfqs.cas"
                        ") WHERE substance_id IS NULL"
                    )
                )
    if "suppliers" in tables:
        cols = {c["name"] for c in inspector.get_columns("suppliers")}
        with engine.begin() as conn:
            if "source" not in cols:
                conn.execute(text("ALTER TABLE suppliers ADD COLUMN source VARCHAR(255)"))
            if "qualification_status" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE suppliers ADD COLUMN qualification_status "
                        "VARCHAR(32) NOT NULL DEFAULT 'candidate'"
                    )
                )
            if "evidence_score" not in cols:
                conn.execute(
                    text("ALTER TABLE suppliers ADD COLUMN evidence_score INTEGER")
                )
            if "last_checked_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE suppliers ADD COLUMN last_checked_at TIMESTAMP"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_suppliers_qualification_status "
                    "ON suppliers (qualification_status)"
                )
            )
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
    if "feedback_messages" in tables:
        cols = {c["name"] for c in inspector.get_columns("feedback_messages")}
        timestamp_type = (
            "TIMESTAMPTZ" if engine.dialect.name == "postgresql" else "TIMESTAMP"
        )
        with engine.begin() as conn:
            if "email_delivery_status" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE feedback_messages ADD COLUMN "
                        "email_delivery_status VARCHAR(32) NOT NULL "
                        "DEFAULT 'not_attempted'"
                    )
                )
            if "email_message_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE feedback_messages ADD COLUMN "
                        "email_message_id VARCHAR(998)"
                    )
                )
            if "email_delivery_attempted_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE feedback_messages ADD COLUMN "
                        f"email_delivery_attempted_at {timestamp_type}"
                    )
                )
    if "agent_runs" in tables:
        cols = {c["name"] for c in inspector.get_columns("agent_runs")}
        with engine.begin() as conn:
            for column in ("prompt_tokens", "completion_tokens"):
                if column not in cols:
                    conn.execute(
                        text(f"ALTER TABLE agent_runs ADD COLUMN {column} INTEGER")
                    )
    if "search_runs" in tables:
        cols = {c["name"] for c in inspector.get_columns("search_runs")}
        json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
        with engine.begin() as conn:
            if "result_payload" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        f"ADD COLUMN result_payload {json_type}"
                    )
                )
            if "rfq_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs ADD COLUMN rfq_id INTEGER "
                        "REFERENCES rfqs(id) ON DELETE SET NULL"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_search_runs_rfq_id "
                    "ON search_runs (rfq_id)"
                )
            )
            if "correlation_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        "ADD COLUMN correlation_id VARCHAR(36)"
                    )
                )
                # Тот же детерминированный legacy-формат, что в SQL-миграции
                # 20260729_add_search_execution_manifest.
                pad_expr = (
                    "LPAD(id::text, 12, '0')"
                    if engine.dialect.name == "postgresql"
                    else "SUBSTR('000000000000' || id, -12)"
                )
                conn.execute(
                    text(
                        "UPDATE search_runs SET correlation_id = "
                        f"'00000000-0000-0000-0000-' || {pad_expr} "
                        "WHERE correlation_id IS NULL"
                    )
                )
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "ix_search_runs_correlation_id "
                        "ON search_runs (correlation_id)"
                    )
                )
            if "graph_version" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs ADD COLUMN graph_version "
                        "VARCHAR(64) NOT NULL DEFAULT 'supplier-search.v1'"
                    )
                )
            # Аренда задач очереди; в проде это делает миграция
            # 20260801_add_search_run_lease.
            if "lease_owner" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        "ADD COLUMN lease_owner VARCHAR(128)"
                    )
                )
            if "lease_expires_at" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        "ADD COLUMN lease_expires_at TIMESTAMP"
                    )
                )
            if "lease_generation" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs ADD COLUMN lease_generation "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                )
            if "parent_search_run_id" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        "ADD COLUMN parent_search_run_id INTEGER "
                        "REFERENCES search_runs(id) ON DELETE SET NULL"
                    )
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS "
                        "ix_search_runs_parent_search_run_id "
                        "ON search_runs (parent_search_run_id)"
                    )
                )
            if "replay_mode" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE search_runs "
                        "ADD COLUMN replay_mode VARCHAR(32)"
                    )
                )
    if "agent_runs" in tables:
        cols = {c["name"] for c in inspector.get_columns("agent_runs")}
        json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
        with engine.begin() as conn:
            if "contract_version" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE agent_runs ADD COLUMN contract_version "
                        "VARCHAR(64) NOT NULL DEFAULT 'v1'"
                    )
                )
            for column in (
                "raw_output_payload",
                "parsed_output_payload",
                "validation_output_payload",
                "policy_output_payload",
                "events",
            ):
                if column not in cols:
                    conn.execute(
                        text(
                            f"ALTER TABLE agent_runs ADD COLUMN {column} "
                            f"{json_type}"
                        )
                    )


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос с гарантированным закрытием."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
