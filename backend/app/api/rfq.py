"""Эндпоинты RFQ: предпросмотр, создание, чтение, сводный список.

Видимость по ролям (раздел 4 UI/UX-плана): закупщик видит свои запросы,
руководитель/администратор/аудитор — все. Права проверяются на сервере.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import RfqAiSetting, RfqAnalogCandidate, Substance, User
from app.models.communication import Communication
from app.models.enums import (
    CommDirection,
    DispatchStatus,
    EscalationStatus,
    UserRole,
)
from app.models.escalation import Escalation
from app.models.manager import Manager
from app.models.quotation import Quotation
from app.models.recipient import RfqRecipient
from app.models.rfq_supplier import RfqSupplierLink
from app.models.search_trace import SearchRun
from app.models.rfq import RFQ
from app.models.rfq_batch import RfqBatch
from app.schemas.rfq import (
    RFQCreate,
    RFQListItem,
    RFQMessageDraftUpdate,
    RFQRead,
    RFQTranslationRead,
)
from app.services.communication_testing import (
    CommunicationTestError,
    translate_preview_text,
)
from app.services.analog_candidates import MAX_CANDIDATES as MAX_ANALOGS
from app.services.analog_candidates import suggest_analogs
from app.services.analog_service import (
    confirm_analogs,
    stored_candidates,
    store_suggestion,
)
from app.services.incoterms import SUPPORTED_INCOTERMS
from app.services.rfq_batch_service import (
    MAX_BATCH_ITEMS,
    create_rfq_batch,
    existing_batch_result,
)
from app.services.rfq_import import (
    MAX_FILE_BYTES,
    TEMPLATE_FORMATS,
    TEMPLATE_MEDIA_TYPES,
    RfqImportError,
    build_template_csv,
    build_template_xlsx,
    parse_import_file,
    parse_import_row,
    template_reference,
)
from app.services.rfq_builder import (
    RFQInput,
    UnsupportedIncotermError,
    build_rfq,
)
from app.services.rfq_progress import (
    RfqProgress,
    as_utc,
    rfq_next_action,
    rfq_stage,
    waiting_days,
)
from app.services.rfq_service import (
    archive_rfq,
    create_rfq,
    search_run_payload,
    render_rfq_text,
    update_rfq_message_draft,
)
from app.services.search_trace import create_search_run

router = APIRouter(prefix="/rfq", tags=["rfq"])

# Значение RfqSupplierLink.status для компании, снятой закупщиком вручную.
# Дублирует константу из app.api.suppliers: импорт оттуда завёл бы цикл
# между двумя роутерами.
LINK_EXCLUDED = "excluded"


@dataclass
class _Dialogue:
    """Свёртка переписки заявки по компаниям.

    Складывается из пар «последнее входящее / последнее исходящее» на одну
    компанию. Компания считается ответившей, если от неё пришло хоть одно
    сообщение, и ждущей нас, если её сообщение новее нашего.
    """

    replied: int = 0
    awaiting: int = 0
    last_inbound: datetime | None = None
    last_outbound: datetime | None = None

    def add(self, inbound: datetime | None, outbound: datetime | None) -> None:
        if inbound is not None:
            self.replied += 1
            if outbound is None or inbound > outbound:
                self.awaiting += 1
            if self.last_inbound is None or inbound > self.last_inbound:
                self.last_inbound = inbound
        if outbound is not None and (
            self.last_outbound is None or outbound > self.last_outbound
        ):
            self.last_outbound = outbound

# Роли, видящие все запросы (остальные — только свои).
_SEE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN, UserRole.AUDITOR}
_DELETE_ALL_ROLES = {UserRole.HEAD, UserRole.ADMIN}


def _can_see(user: User, rfq: RFQ) -> bool:
    if user.role in _SEE_ALL_ROLES:
        return True
    return rfq.owner_id is None or rfq.owner_id == user.id


def _can_delete(user: User, rfq: RFQ) -> bool:
    return user.role in _DELETE_ALL_ROLES or rfq.owner_id == user.id


def _merge_names(*groups: list[str] | None) -> list[str]:
    """Объединяет списки названий без повторов, сохраняя порядок."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group or []:
            name = raw.strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                merged.append(name)
    return merged


class RFQGenerateRequest(BaseModel):
    cas: str | None = Field(default=None, examples=["50-78-2"])
    name: str = Field(..., examples=["Acetylsalicylic acid"])
    identification_method: str = "cas"
    analog_reference: str | None = None
    analog_variations: list[str] = Field(default_factory=list)
    specification: str | None = None
    incoterms: list[str] = Field(..., examples=[list(SUPPORTED_INCOTERMS)])
    purity: str | None = None
    application: str | None = None
    volume: str | None = None
    target_price: float | None = None
    currency: str = "USD"


@router.get("/import/reference")
def import_reference(user: User = Depends(get_current_user)) -> dict:
    """Описание колонок и примеры заполнения для окна загрузки списка.

    Отдаётся сервером по той же причине, по которой сервер собирает и сам
    образец: правила разбора живут здесь, и текст, повторённый на экране
    руками, разошёлся бы с ними при первой же правке разбора.
    """
    return template_reference()


@router.get("/import/template")
def import_template(
    fmt: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
    user: User = Depends(get_current_user),
) -> Response:
    """Отдаёт образец файла со списком позиций.

    Перечислить колонки в подсказке мало: закупщик видит названия, но не
    видит, как записать два базиса поставки в одной ячейке и что писать
    в «Чистота». Заполненный образец показывает это строками.

    Данные в образце демонстрационные и одинаковые для всех: ничего из
    списка закупщика сюда не попадает.
    """
    if fmt not in TEMPLATE_FORMATS:  # pragma: no cover - отсечено pattern
        raise HTTPException(status_code=422, detail="Формат: xlsx или csv.")
    payload = build_template_xlsx() if fmt == "xlsx" else build_template_csv()
    return Response(
        content=payload,
        media_type=TEMPLATE_MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": (
                f'attachment; filename="chemsource-rfq-template.{fmt}"'
            ),
            # Образец меняется вместе с правилами разбора: старая копия
            # из кеша браузера разошлась бы с тем, что читает загрузка.
            "Cache-Control": "no-store",
        },
    )


@router.post("/import/preview")
async def preview_import(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    """Разбирает XLSX/CSV со списком позиций и возвращает предпросмотр.

    Ничего не создаёт и ничего не сохраняет: ни запросов, ни самого файла.
    Список сырья — коммерческая тайна закупщика, а решения о сроке хранения
    загруженного файла ещё нет. Создание запросов — отдельное действие
    закупщика уже по разобранным строкам.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    payload = await file.read()
    if len(payload) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Файл больше {MAX_FILE_BYTES // (1024 * 1024)} МБ."
            ),
        )
    try:
        preview = parse_import_file(file.filename or "", payload)
    except RfqImportError as exc:
        # Содержимое файла в ответ не попадает — только причина отказа.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return preview.to_dict()


class ImportRowRecheck(BaseModel):
    """Строка предпросмотра после правки закупщиком."""

    row: int = Field(default=0, ge=0)
    raw: dict[str, str] = Field(default_factory=dict)


@router.post("/import/row")
def recheck_import_row(
    data: ImportRowRecheck,
    user: User = Depends(get_current_user),
) -> dict:
    """Проверяет исправленную строку тем же разбором, что и файл."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    return parse_import_row(data.row, data.raw).to_dict()


class BatchItemIn(BaseModel):
    """Строка списка. Значения сырые: их проверяет разбор каждой строки.

    Схема запроса намеренно не валидирует поля позиции. Если бы валидировала,
    одна негодная строка отвергала бы весь список ещё на разборе тела — а
    закупщику нужны 49 созданных запросов и один понятный отказ.
    """

    row: int = Field(default=0, ge=0)
    values: dict = Field(default_factory=dict)


class BatchCreate(BaseModel):
    # Ключ придумывает клиент и повторяет при повторной отправке.
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    source_name: str | None = Field(default=None, max_length=255)
    # Условия закупки, общие для списка: базисы и страны. В файле таких
    # колонок обычно нет, а без них запрос не создаётся.
    defaults: dict = Field(default_factory=dict)
    items: list[BatchItemIn] = Field(..., min_length=1, max_length=MAX_BATCH_ITEMS)


@router.post("/batch", status_code=201)
def create_batch(
    data: BatchCreate,
    verify: bool = Query(
        default=False,
        description="Подтверждать номера в PubChem (медленно на большом списке)",
    ),
    start_search: bool = Query(
        default=True, description="Ставить поиск по каждой позиции в очередь"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Создаёт пакет запросов по списку позиций.

    Каждая строка отвечает за себя: отказ одной не отменяет остальные, и
    итог возвращается по каждой. Повтор с тем же ключом идемпотентности
    возвращает уже созданный пакет, а не заводит второй.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")

    result = create_rfq_batch(
        db,
        owner_id=user.id,
        idempotency_key=data.idempotency_key,
        source_name=data.source_name,
        items=[(item.row, item.values) for item in data.items],
        defaults=data.defaults,
        verify=verify,
        start_search=start_search,
    )
    return result.to_dict()


@router.get("/batch/{batch_id}")
def read_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Сводка пакета: позиции, их статусы и число поставленных поисков.

    Видимость пакета не расширяет права на запросы: закупщик, открывший
    чужой пакет, не получает через него чужие карточки.
    """
    batch = db.get(RfqBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Пакет не найден")
    if user.role not in _SEE_ALL_ROLES and batch.owner_id not in (None, user.id):
        # 404, а не 403: существование чужого пакета — тоже сведения.
        raise HTTPException(status_code=404, detail="Пакет не найден")

    rows = db.scalars(
        select(RFQ)
        .where(RFQ.batch_id == batch.id, RFQ.deleted_at.is_(None))
        .order_by(RFQ.id)
    ).all()
    visible = [rfq for rfq in rows if _can_see(user, rfq)]

    run_counts = dict(
        db.execute(
            select(SearchRun.rfq_id, func.count(SearchRun.id))
            .where(SearchRun.rfq_id.in_([rfq.id for rfq in visible] or [0]))
            .group_by(SearchRun.rfq_id)
        ).all()
    )
    return {
        "batch_id": batch.id,
        "source_name": batch.source_name,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "owner_id": batch.owner_id,
        "total": len(visible),
        "hidden": len(rows) - len(visible),
        "items": [
            {
                "rfq_id": rfq.id,
                "name": rfq.name,
                "cas": rfq.cas,
                "status": rfq.status.value,
                "volume": rfq.volume,
                "search_runs": run_counts.get(rfq.id, 0),
            }
            for rfq in visible
        ],
    }


@router.post("/preview")
def preview_rfq(
    req: RFQGenerateRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """Генерирует RFQ без сохранения (для предпросмотра в UI)."""
    try:
        return build_rfq(
            RFQInput(
                cas=req.cas,
                name=req.name,
                identification_method=req.identification_method,
                analog_reference=req.analog_reference,
                analog_variations=req.analog_variations,
                specification=req.specification,
                incoterms=req.incoterms,
                purity=req.purity,
                application=req.application,
                volume=req.volume,
                target_price=req.target_price,
                currency=req.currency,
            )
        )
    except UnsupportedIncotermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=RFQRead, status_code=201)
def create(
    data: RFQCreate,
    verify: bool = Query(default=True, description="Верифицировать CAS через PubChem"),
    start_search: bool = Query(
        default=False,
        description="Сразу поставить поиск поставщиков в очередь",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    """Создаёт RFQ: верификация CAS, генерация текста, сохранение."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    selected_substance = None
    if data.substance_id is not None:
        selected_substance = db.get(Substance, data.substance_id)
        if selected_substance is None:
            raise HTTPException(status_code=422, detail="Вещество не найдено")
        data = data.model_copy(
            update={
                "cas": selected_substance.cas,
                "name": selected_substance.preferred_name,
            }
        )
    try:
        rfq = create_rfq(db, data, verify=verify, owner_id=user.id)
    except UnsupportedIncotermError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if selected_substance is None and rfq.substance_id is not None:
        selected_substance = db.get(Substance, rfq.substance_id)
    if data.additional_instructions:
        db.add(
            RfqAiSetting(
                rfq_id=rfq.id,
                additional_instructions=data.additional_instructions.strip(),
            )
        )
    # Запрос на аналог сам к поставщикам не идёт. Сначала подбираются
    # вещества-заменители, закупщик отмечает подходящие, и поиск компаний
    # стартует уже по ним — отдельным запросом на каждое. Поставить поиск
    # здесь значило бы искать поставщиков вещества, которое закупщик
    # закупать не собирался.
    if start_search and rfq.identification_method != "analog":
        for country in data.search_countries:
            create_search_run(
                db,
                owner_id=user.id,
                rfq_id=rfq.id,
                input_payload=search_run_payload(
                    rfq,
                    country=country,
                    substance=selected_substance,
                    additional_instructions=data.additional_instructions,
                ),
                mode="queued_search",
                status="queued",
            )
    db.commit()
    db.refresh(rfq)
    return _to_read(rfq)


class AnalogConfirm(BaseModel):
    """Выбор закупщика: по каким аналогам заводить запросы."""

    candidate_ids: list[int] = Field(default_factory=list, max_length=MAX_ANALOGS)


def _analog_rfq(db: Session, rfq_id: int, user: User) -> RFQ:
    """Запрос, к которому относится подбор, с проверкой прав."""
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or rfq.deleted_at is not None or not _can_see(user, rfq):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return rfq


def _candidate_dict(candidate: RfqAnalogCandidate) -> dict:
    return {
        "id": candidate.id,
        "name": candidate.name,
        "cas": candidate.cas,
        "cas_confirmed": candidate.cas_confirmed,
        "reason": candidate.reason,
        "quote": candidate.quote,
        "source_url": candidate.source_url,
        "selected": candidate.selected,
        "created_rfq_id": candidate.created_rfq_id,
    }


def _analogs_payload(db: Session, rfq: RFQ) -> dict:
    return {
        "rfq_id": rfq.id,
        "name": rfq.name,
        # Разделяет «ещё не подбирали» и «подбирали, ничего не нашли»:
        # без отметки второе выглядит как несработавшая кнопка.
        "suggested_at": rfq.analog_suggested_at.isoformat()
        if rfq.analog_suggested_at
        else None,
        "warnings": list(rfq.analog_warnings or []),
        "candidates": [
            _candidate_dict(item) for item in stored_candidates(db, rfq.id)
        ],
    }


@router.get("/{rfq_id}/analogs")
def read_analogs(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Подобранные аналоги и отметки закупщика."""
    return _analogs_payload(db, _analog_rfq(db, rfq_id, user))


@router.post("/{rfq_id}/analogs/suggest")
def suggest_rfq_analogs(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Подбирает вещества, которыми можно заменить позицию.

    Ничего не создаёт: возвращает кандидатов с цитатой и ссылкой, из
    которых выбирает закупщик. Повторный подбор заменяет прежний список,
    но не трогает аналоги, по которым запросы уже заведены.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = _analog_rfq(db, rfq_id, user)

    suggestion = suggest_analogs(
        rfq.name,
        cas=rfq.cas,
        specification=rfq.specification,
    )
    store_suggestion(db, rfq, suggestion)
    db.commit()
    db.refresh(rfq)
    return _analogs_payload(db, rfq)


@router.post("/{rfq_id}/analogs/confirm")
def confirm_rfq_analogs(
    rfq_id: int,
    data: AnalogConfirm,
    start_search: bool = Query(
        default=True, description="Ставить поиск поставщиков по каждому аналогу"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Заводит запрос на каждый выбранный аналог и ставит поиски.

    Повтор с тем же выбором не заводит второй набор: ключ идемпотентности
    считается по запросу и составу выбора.
    """
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = _analog_rfq(db, rfq_id, user)

    result = confirm_analogs(
        db,
        rfq,
        candidate_ids=data.candidate_ids,
        owner_id=user.id,
        start_search=start_search,
    )
    db.commit()
    db.refresh(rfq)
    return {
        "analogs": _analogs_payload(db, rfq),
        # None означает, что по всему выбранному запросы уже были заведены:
        # менялись только отметки, нового пакета не появилось.
        "batch": result.to_dict() if result is not None else None,
    }


@router.get("/{rfq_id}", response_model=RFQRead)
def get(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    rfq = db.get(RFQ, rfq_id, options=[joinedload(RFQ.owner)])
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    return _to_read(rfq)


@router.put("/{rfq_id}/message-draft", response_model=RFQRead)
def update_message_draft(
    rfq_id: int,
    data: RFQMessageDraftUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQRead:
    """Сохраняет ручной текст первого RFQ или возвращает исходный шаблон."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(
        RFQ,
        rfq_id,
        options=[joinedload(RFQ.owner), joinedload(RFQ.substance)],
    )
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    update_rfq_message_draft(
        db,
        rfq,
        subject=data.subject,
        body=data.body,
    )
    return _to_read(rfq)


@router.post("/{rfq_id}/translation", response_model=RFQTranslationRead)
def translate_rfq(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RFQTranslationRead:
    """Переводит сохранённый RFQ для просмотра, ничего не изменяя и не отправляя."""
    rfq = db.get(RFQ, rfq_id, options=[joinedload(RFQ.owner)])
    if (
        rfq is None
        or rfq.deleted_at is not None
        or not _can_see(user, rfq)
    ):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    subject, body = render_rfq_text(rfq)
    try:
        translation = translate_preview_text(f"Subject: {subject}\n\n{body}")
    except CommunicationTestError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RFQTranslationRead(translation_ru=translation)


@router.delete("/{rfq_id}", status_code=204, response_class=Response)
def delete_rfq(
    rfq_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Archive a request without destroying its audit history."""
    if user.role == UserRole.AUDITOR:
        raise HTTPException(status_code=403, detail="Аудитор — только чтение")
    rfq = db.get(RFQ, rfq_id)
    if rfq is None or not _can_delete(user, rfq):
        raise HTTPException(status_code=404, detail="Запрос не найден")
    archive_rfq(db, rfq, actor_id=user.id)
    return Response(status_code=204)


@router.get("", response_model=list[RFQListItem])
def list_rfqs(
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RFQListItem]:
    """Сводный список RFQ с числом котировок, полнотой и эскалациями."""
    stmt = (
        select(RFQ)
        .where(RFQ.deleted_at.is_(None))
        .options(joinedload(RFQ.owner))
        .order_by(RFQ.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if user.role not in _SEE_ALL_ROLES:
        stmt = stmt.where(
            (RFQ.owner_id == user.id) | (RFQ.owner_id.is_(None))
        )
    rfqs = list(db.scalars(stmt).all())
    ids = [r.id for r in rfqs]
    if not ids:
        return []

    # Агрегаты одной выборкой: котировки (всего/полные) и открытые эскалации.
    quote_rows = db.execute(
        select(
            Quotation.rfq_id,
            func.count(Quotation.id),
            func.sum(case((Quotation.is_complete.is_(True), 1), else_=0)),
        ).where(Quotation.rfq_id.in_(ids)).group_by(Quotation.rfq_id)
    ).all()
    quotes = {rfq_id: (total or 0, int(complete or 0)) for rfq_id, total, complete in quote_rows}

    # Знаменатель охвата: скольким поставщикам RFQ действительно ушёл. Один
    # поставщик может стоять в двух каналах — считаем компании, не отправки.
    # Заодно берём момент первой отправки (дата заведения заявки ничего не
    # говорит о ходе работ) и число упавших отправок.
    recipient_rows = db.execute(
        select(
            RfqRecipient.rfq_id,
            func.count(func.distinct(RfqRecipient.supplier_id)),
            func.min(RfqRecipient.created_at),
            func.sum(
                case((RfqRecipient.status == DispatchStatus.ERROR, 1), else_=0)
            ),
        )
        .where(
            RfqRecipient.rfq_id.in_(ids),
            RfqRecipient.status != DispatchStatus.QUEUED,
        )
        .group_by(RfqRecipient.rfq_id)
    ).all()
    recipients = {
        rfq_id: (int(total or 0), dispatched_at, int(errors or 0))
        for rfq_id, total, dispatched_at, errors in recipient_rows
    }

    # Найденные компании до рассылки: без них «разослать» предлагать нечему.
    link_rows = db.execute(
        select(RfqSupplierLink.rfq_id, func.count(RfqSupplierLink.id))
        .where(
            RfqSupplierLink.rfq_id.in_(ids),
            RfqSupplierLink.status != LINK_EXCLUDED,
        )
        .group_by(RfqSupplierLink.rfq_id)
    ).all()
    found = {rfq_id: int(total or 0) for rfq_id, total in link_rows}

    # Состояние переписки по каждой компании: когда она написала последний
    # раз и когда последний раз писали мы. Из пары выводится и «ответили»,
    # и «ждут нашего ответа» — счётчик котировок ни того, ни другого не
    # даёт: одна компания присылает несколько котировок, а вопрос без
    # цены не создаёт ни одной.
    moment = func.coalesce(Communication.message_at, Communication.created_at)
    conversation_rows = db.execute(
        select(
            Communication.rfq_id,
            Manager.supplier_id,
            func.max(
                case(
                    (Communication.direction == CommDirection.INBOUND, moment),
                    else_=None,
                )
            ),
            func.max(
                case(
                    (Communication.direction == CommDirection.OUTBOUND, moment),
                    else_=None,
                )
            ),
        )
        .join(Manager, Manager.id == Communication.manager_id)
        .where(Communication.rfq_id.in_(ids))
        .group_by(Communication.rfq_id, Manager.supplier_id)
    ).all()

    dialogues: dict[int, _Dialogue] = {}
    for rfq_id, _supplier_id, last_in, last_out in conversation_rows:
        state = dialogues.setdefault(rfq_id, _Dialogue())
        state.add(as_utc(last_in), as_utc(last_out))

    esc_rows = db.execute(
        select(Escalation.rfq_id, Escalation.reason)
        .where(
            Escalation.rfq_id.in_(ids),
            Escalation.status != EscalationStatus.RESOLVED,
        )
        .distinct()
    ).all()
    escalations: dict[int, list[str]] = {}
    for rfq_id, reason in esc_rows:
        value = reason.value if hasattr(reason, "value") else str(reason)
        escalations.setdefault(rfq_id, []).append(value)

    now = datetime.now(timezone.utc)
    items: list[RFQListItem] = []
    for r in rfqs:
        total, complete = quotes.get(r.id, (0, 0))
        n_recipients, dispatched_at, n_errors = recipients.get(r.id, (0, None, 0))
        dialogue = dialogues.get(r.id, _Dialogue())
        reasons = escalations.get(r.id, [])

        progress = RfqProgress(
            status=r.status,
            verified=r.verified,
            n_suppliers_found=found.get(r.id, 0),
            n_recipients=n_recipients,
            n_dispatch_errors=n_errors,
            n_suppliers_replied=dialogue.replied,
            n_awaiting_our_reply=dialogue.awaiting,
            n_open_escalations=len(reasons),
            n_quotations=total,
            completeness_pct=round(100 * complete / total) if total else 0,
            dispatched_at=as_utc(dispatched_at),
            last_inbound_at=dialogue.last_inbound,
            last_outbound_at=dialogue.last_outbound,
        )

        item = RFQListItem.model_validate(r)
        item.owner_name = r.owner.full_name if r.owner else None
        item.n_quotations = progress.n_quotations
        item.n_complete = complete
        item.completeness_pct = progress.completeness_pct
        item.n_recipients = progress.n_recipients
        item.has_open_escalation = bool(reasons)

        item.stage = rfq_stage(progress)
        item.next_action = rfq_next_action(progress, now=now)
        item.n_suppliers_found = progress.n_suppliers_found
        item.n_suppliers_replied = progress.n_suppliers_replied
        item.n_silent = progress.n_silent
        item.n_awaiting_our_reply = progress.n_awaiting_our_reply
        item.n_dispatch_errors = progress.n_dispatch_errors
        item.escalation_reasons = reasons
        item.dispatched_at = progress.dispatched_at
        item.last_inbound_at = progress.last_inbound_at
        item.last_outbound_at = progress.last_outbound_at
        item.waiting_days = waiting_days(progress, now=now)
        items.append(item)
    return items


def _to_read(rfq: RFQ) -> RFQRead:
    """Сериализует RFQ + добавляет сгенерированный текст письма."""
    read = RFQRead.model_validate(rfq)
    subject, body = render_rfq_text(rfq)
    read.rfq_subject = subject
    read.rfq_body = body
    read.rfq_is_customized = bool(
        rfq.rfq_subject_override and rfq.rfq_body_override
    )
    read.owner_name = rfq.owner.full_name if rfq.owner else None
    read.substance_preferred_name = (
        rfq.substance.preferred_name if rfq.substance else None
    )
    read.substance_review_status = (
        rfq.substance.review_status if rfq.substance else None
    )
    return read
