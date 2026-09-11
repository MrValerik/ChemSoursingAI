"""Запрос (RFQ): CAS, наименование, чистота, применение, объём,
ценовой ориентир, базисы, каналы, статус, ответственный."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import RFQStatus

if TYPE_CHECKING:
    from app.models.escalation import Escalation
    from app.models.quotation import Quotation
    from app.models.rfq_batch import RfqBatch
    from app.models.search_trace import SearchRun
    from app.models.substance import Substance
    from app.models.user import User


class RFQ(Base, TimestampMixin):
    __tablename__ = "rfqs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Способ идентификации предмета закупки: cas — точная молекула по
    # номеру, analog — «как вот это вещество», spec — назначение и
    # требования. Номер есть не у всего, что закупают: у смесей, рецептур
    # и промышленных продуктов его нет и не будет.
    identification_method: Mapped[str] = mapped_column(
        String(16), default="cas", index=True
    )

    # Входные параметры продукта. CAS необязателен — см. выше.
    cas: Mapped[str | None] = mapped_column(String(20), index=True, default=None)
    name: Mapped[str] = mapped_column(String(255))

    # Режим analog: запрос не идёт к поставщикам сам. Сначала система
    # подбирает вещества-аналоги с доказательствами, закупщик отмечает
    # подходящие, и уже на каждое отмеченное заводится свой запрос.
    #
    # Отметка о времени подбора нужна, чтобы отличить «ещё не подбирали» от
    # «подбирали и не нашли»: на экране это два разных ответа, и второй без
    # отметки выглядел бы как несработавшая кнопка.
    analog_suggested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Чем заменять нельзя: ограничение закупщика, которого нет ни на одной
    # странице-сравнении. Без него подбор предлагает формально похожее —
    # желатин вместо ксантана, потому что оба загустители.
    analog_constraints: Mapped[str | None] = mapped_column(Text, default=None)
    # Что не получилось при подборе: недоступная модель, заблокированный
    # источник, номер без подтверждения. Хранится вместе с результатом —
    # иначе после перезагрузки страницы пустой список выглядит как ошибка.
    analog_warnings: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Поля прежнего одноступенчатого режима. Новые запросы их не заполняют;
    # остаются ради карточек, созданных до перехода на двухступенчатый подбор.
    analog_reference: Mapped[str | None] = mapped_column(String(255), default=None)
    analog_variations: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Режим spec: требования свободным текстом (чистота и применение —
    # отдельные поля ниже).
    specification: Mapped[str | None] = mapped_column(Text, default=None)

    # Названия, отмеченные закупщиком как подходящие, и снятые им. Без
    # CAS-номера якорем поиска служит название, а оно неуникально: у
    # бетаина и его гидрохлорида названия соседние, вещества разные.
    # Снятые названия работают отрицательным фильтром в поиске.
    confirmed_synonyms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    excluded_names: Mapped[list[str] | None] = mapped_column(JSON, default=None)

    # Источник каждого поля: pubchem / ai_agent / human / catalog. Хранится
    # рядом со значением, иначе находка ИИ-агента через месяц неотличима
    # от справочных данных.
    field_sources: Mapped[dict | None] = mapped_column(JSON, default=None)
    purity: Mapped[str | None] = mapped_column(String(64))
    application: Mapped[str | None] = mapped_column(Text)
    volume: Mapped[str | None] = mapped_column(String(64))
    target_price: Mapped[float | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(3), default="USD")
    # Цена становится сравнимой только вместе с единицей и базисом поставки.
    # Поля необязательны для совместимости со старыми RFQ: такие ориентиры UI
    # честно помечает как несопоставимые, а не достраивает догадкой.
    target_price_unit: Mapped[str | None] = mapped_column(String(32), default=None)
    target_price_incoterm: Mapped[str | None] = mapped_column(
        String(24), default=None
    )

    # Комментарий закупщика к позиции (ТЗ, функция 1). Внутренняя заметка:
    # в письмо поставщику не попадает.
    specialist_comment: Mapped[str | None] = mapped_column(Text, default=None)

    # Базисы поставки (Incoterm) и каналы рассылки (Channel) — списки строк.
    incoterms: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    channels: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    search_countries: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    supplier_target: Mapped[int] = mapped_column(Integer, default=5)

    # Ручная версия первого RFQ. Оба поля либо заполнены вместе, либо остаются
    # пустыми — тогда предпросмотр и отправка используют единый шаблон.
    rfq_subject_override: Mapped[str | None] = mapped_column(
        String(500), default=None
    )
    rfq_body_override: Mapped[str | None] = mapped_column(Text, default=None)

    # Проверенная машинная английская версия единого шаблона. Исходные поля
    # остаются выше без изменений; hash не позволяет использовать устаревший
    # перевод после изменения любого внешнего поля.
    rfq_generated_subject_en: Mapped[str | None] = mapped_column(
        String(500), default=None
    )
    rfq_generated_body_en: Mapped[str | None] = mapped_column(Text, default=None)
    rfq_generated_source_hash: Mapped[str | None] = mapped_column(
        String(64), default=None
    )

    status: Mapped[RFQStatus] = mapped_column(
        SAEnum(RFQStatus), default=RFQStatus.DRAFT, index=True
    )

    # Данные верификации вещества (снимок ответа PubChem).
    verified: Mapped[bool] = mapped_column(default=False)
    verification: Mapped[dict | None] = mapped_column(JSON, default=None)
    substance_id: Mapped[int | None] = mapped_column(
        ForeignKey("substances.id"), index=True, default=None
    )
    substance: Mapped["Substance | None"] = relationship(back_populates="rfqs")

    # Ответственный закупщик (раздел 4 UI/UX-плана: данные принадлежат
    # запросу и его ответственному; роли расширяют видимость).
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, default=None
    )
    owner: Mapped["User | None"] = relationship(foreign_keys=[owner_id])

    # Пакет, которым позиция была заведена. Связь, а не объединение: запрос
    # остаётся независимым, у него свой поиск и своя котировка. Пакет нужен,
    # чтобы вернуться к сводке списка и увидеть соседние позиции.
    # ON DELETE SET NULL: удаление пакета не должно уносить запросы, по
    # которым уже идёт переписка.
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfq_batches.id", ondelete="SET NULL"), index=True, default=None
    )
    batch: Mapped["RfqBatch | None"] = relationship(back_populates="rfqs")

    # Мягкое удаление сохраняет историю поиска, переписку и котировки для аудита.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    deleted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )

    quotations: Mapped[list["Quotation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    escalations: Mapped[list["Escalation"]] = relationship(
        back_populates="rfq", cascade="all, delete-orphan"
    )
    search_runs: Mapped[list["SearchRun"]] = relationship(
        back_populates="rfq"
    )
    # Подобранные аналоги. Живут вместе с запросом: сами по себе, без
    # позиции, ради которой искали замену, они ничего не значат.
    analog_candidates: Mapped[list["RfqAnalogCandidate"]] = relationship(
        back_populates="rfq",
        cascade="all, delete-orphan",
        foreign_keys="RfqAnalogCandidate.rfq_id",
    )


class RfqAnalogCandidate(Base, TimestampMixin):
    """Вещество, предложенное на замену, и доказательство этого предложения.

    Модель здесь — интерпретатор веб-выдачи, а не источник фактов: у каждого
    кандидата хранится цитата и адрес страницы, откуда она взята. Название
    без цитаты закупщик проверить не может, а «аналог» ошибкой обходится
    дороже прочего: закупили не то — узнали через два месяца на производстве.

    Выбор остаётся за человеком. `selected` ставится закупщиком, и только
    после этого на кандидата заводится собственный запрос (`created_rfq_id`).
    """

    __tablename__ = "rfq_analog_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfq_id: Mapped[int] = mapped_column(
        ForeignKey("rfqs.id", ondelete="CASCADE"), index=True
    )
    rfq: Mapped["RFQ"] = relationship(
        back_populates="analog_candidates", foreign_keys=[rfq_id]
    )

    name: Mapped[str] = mapped_column(String(255))
    cas: Mapped[str | None] = mapped_column(String(20), default=None)
    # Номер прошёл контрольную сумму И дословно найден в источнике. Разница
    # показывается, а не усредняется: неподтверждённый номер в поиске опаснее
    # отсутствующего — он уводит поиск к другому веществу молча.
    cas_confirmed: Mapped[bool] = mapped_column(default=False)
    # Чем эта замена является: тот же класс, другая соль или форма, другая
    # марка того же продукта. Одним предложением, по-русски.
    reason: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str | None] = mapped_column(Text, default=None)
    source_url: Mapped[str | None] = mapped_column(String(500), default=None)

    selected: Mapped[bool] = mapped_column(default=False, index=True)
    # Запрос, заведённый по этому аналогу. ON DELETE SET NULL: удаление
    # заведённого запроса не должно уносить сам подбор — он объясняет,
    # почему этот запрос вообще появился.
    created_rfq_id: Mapped[int | None] = mapped_column(
        ForeignKey("rfqs.id", ondelete="SET NULL"), default=None, index=True
    )
