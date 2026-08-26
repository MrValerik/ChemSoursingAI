"""Пакетное создание запросов по списку позиций.

Пакет заводит несколько независимых RFQ одним действием. Независимых —
значит у каждого свой поиск, свои поставщики и своя котировка. Позиции
связывает только то, что закупщик принёс их одним списком.

Две вещи здесь важнее скорости.

Первое: ошибка одной позиции не отменяет остальные. Список на 50 веществ
с одной кривой строкой обязан дать 49 запросов и один понятный отказ, а не
пустой результат. Поэтому каждая позиция заворачивается в свою точку
сохранения, а разбор входных данных идёт построчно — иначе одна негодная
строка отвергла бы весь запрос ещё на разборе тела.

Второе: повтор подтверждения не создаёт второй пакет. Повтор — это не
только дважды нажатая кнопка: чаще это обрыв ответа, после которого
закупщик не знает, создалось что-нибудь или нет, и нажимает снова.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt import RfqAiSetting
from app.models.rfq import RFQ
from app.models.rfq_batch import RfqBatch
from app.schemas.rfq import RFQCreate
from app.services.rfq_builder import UnsupportedIncotermError
from app.services.rfq_service import create_rfq, search_run_payload
from app.services.search_trace import create_search_run

# Верхняя граница пакета. Совпадает с ограничением разбора файла: список
# закупки — это десятки строк, а не выгрузка из учётной системы.
MAX_BATCH_ITEMS = 200


@dataclass
class BatchItemResult:
    """Итог по одной строке списка. Каждая строка отвечает за себя."""

    row: int
    name: str
    rfq_id: int | None = None
    search_runs: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "row": self.row,
            "name": self.name,
            "rfq_id": self.rfq_id,
            "search_runs": self.search_runs,
            "error": self.error,
        }


@dataclass
class BatchResult:
    batch: RfqBatch
    created: bool
    results: list[BatchItemResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        created = [item for item in self.results if item.rfq_id is not None]
        return {
            "batch_id": self.batch.id,
            # Отличает свежесозданный пакет от возвращённого по ключу: без
            # этого повтор выглядит как новое создание.
            "created": self.created,
            "source_name": self.batch.source_name,
            "created_at": self.batch.created_at.isoformat()
            if self.batch.created_at
            else None,
            "total": len(self.results),
            "created_count": len(created),
            "failed_count": len(self.results) - len(created),
            "search_runs": sum(item.search_runs for item in created),
            "results": [item.to_dict() for item in self.results],
        }


def _row_name(values: dict) -> str:
    name = values.get("name")
    return str(name).strip() if isinstance(name, str) and name.strip() else "—"


def _first_validation_message(error: ValidationError) -> str:
    """Первая причина отказа человеческим языком.

    Показывается одна: список из пяти сообщений про одну строку закупщик
    не читает, а правит он всё равно по первой.
    """
    for item in error.errors():
        message = str(item.get("msg", "")).replace("Value error, ", "").strip()
        if message:
            location = ".".join(str(part) for part in item.get("loc", ()))
            return f"{location}: {message}" if location else message
    return "Строка не прошла проверку."


def find_batch(db: Session, *, owner_id: int, idempotency_key: str) -> RfqBatch | None:
    return db.scalar(
        select(RfqBatch).where(
            RfqBatch.owner_id == owner_id,
            RfqBatch.idempotency_key == idempotency_key,
        )
    )


def existing_batch_result(db: Session, batch: RfqBatch) -> BatchResult:
    """Итог по уже созданному пакету — ответ на повторную отправку."""
    rows = db.scalars(
        select(RFQ).where(RFQ.batch_id == batch.id).order_by(RFQ.id)
    ).all()
    return BatchResult(
        batch=batch,
        created=False,
        results=[
            BatchItemResult(row=index, name=rfq.name, rfq_id=rfq.id)
            for index, rfq in enumerate(rows, start=1)
        ],
    )


def create_rfq_batch(
    db: Session,
    *,
    owner_id: int,
    idempotency_key: str,
    items: list[tuple[int, dict]],
    defaults: dict | None = None,
    source_name: str | None = None,
    verify: bool = False,
    start_search: bool = True,
) -> BatchResult:
    """Создаёт пакет и по одному запросу на каждую валидную строку.

    `defaults` — условия закупки, общие для всего списка: базисы поставки и
    страны поиска. В файле закупщика таких колонок обычно нет, а без них
    запрос не создаётся, и без умолчаний весь список отвергался бы целиком.
    Значение самой строки всегда сильнее умолчания: если в файле базис
    указан, применяется он.

    `verify=False` по умолчанию: подтверждение номера в PubChem — сетевое
    обращение на каждую позицию, и полсотни таких подряд превратили бы
    создание пакета в многоминутное ожидание. Формат и контрольная сумма
    номера при этом уже проверены детерминированно при разборе файла, а
    подтверждение справочником остаётся доступным в карточке запроса.
    """
    existing = find_batch(db, owner_id=owner_id, idempotency_key=idempotency_key)
    if existing is not None:
        return existing_batch_result(db, existing)

    batch = RfqBatch(
        owner_id=owner_id,
        idempotency_key=idempotency_key,
        source_name=source_name,
    )
    db.add(batch)
    db.flush()

    base = {
        key: value
        for key, value in (defaults or {}).items()
        if value not in (None, "", [], {})
    }

    results: list[BatchItemResult] = []
    for row_number, values in items:
        name = _row_name(values)
        # Строка сильнее умолчания: указанное в файле не перебивается.
        merged = {
            **base,
            **{k: v for k, v in values.items() if v not in (None, "", [], {})},
        }
        savepoint = db.begin_nested()
        try:
            data = RFQCreate(**merged)
        except ValidationError as exc:
            savepoint.rollback()
            results.append(
                BatchItemResult(
                    row=row_number, name=name, error=_first_validation_message(exc)
                )
            )
            continue
        except TypeError as exc:
            savepoint.rollback()
            results.append(
                BatchItemResult(row=row_number, name=name, error=str(exc))
            )
            continue

        try:
            rfq = create_rfq(
                db, data, verify=verify, owner_id=owner_id, commit=False
            )
            rfq.batch_id = batch.id
            db.flush()

            if data.additional_instructions:
                db.add(
                    RfqAiSetting(
                        rfq_id=rfq.id,
                        additional_instructions=data.additional_instructions.strip(),
                    )
                )

            runs = 0
            if start_search:
                # Отдельный прогон на каждую страну: у него свой correlation
                # ID, свой статус и своя ошибка. Один прогон на позицию не
                # дал бы отличить, где именно поиск встал.
                for country in data.search_countries:
                    create_search_run(
                        db,
                        owner_id=owner_id,
                        rfq_id=rfq.id,
                        input_payload=search_run_payload(
                            rfq,
                            country=country,
                            additional_instructions=data.additional_instructions,
                        ),
                        mode="queued_search",
                        status="queued",
                    )
                    runs += 1

            savepoint.commit()
            results.append(
                BatchItemResult(
                    row=row_number, name=name, rfq_id=rfq.id, search_runs=runs
                )
            )
        except UnsupportedIncotermError as exc:
            savepoint.rollback()
            results.append(
                BatchItemResult(row=row_number, name=name, error=str(exc))
            )
        except Exception as exc:  # noqa: BLE001 - строка отвечает за себя
            # Неожиданная ошибка обязана остаться внутри своей строки:
            # иначе одна позиция уносит весь список закупщика.
            savepoint.rollback()
            results.append(
                BatchItemResult(
                    row=row_number,
                    name=name,
                    error=f"Строку не удалось создать: {exc}",
                )
            )

    db.commit()
    db.refresh(batch)
    return BatchResult(batch=batch, created=True, results=results)
