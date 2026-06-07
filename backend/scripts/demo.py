"""Демонстрация сквозного потока ChemSource AI на SQLite (без Postgres, LLM и сети).

Запуск:
    cd backend
    python scripts/demo.py

Показывает: создание RFQ → генерацию письма → извлечение котировок из ответов
поставщиков (на правилах) → контроль полноты → авто-эскалацию → сводную таблицу.
"""

from __future__ import annotations

import os
import sys

# Делаем пакет app импортируемым при запуске как `python scripts/demo.py`:
# добавляем каталог backend/ (родитель scripts/) в путь поиска модулей.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# БД — временный SQLite-файл; задаём ДО импорта app, чтобы engine поднялся на нём.
os.environ.setdefault("DATABASE_URL", "sqlite:///./demo.db")

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, engine, init_db  # noqa: E402
from app.extraction.pipeline import extract_quote  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.escalation import Escalation  # noqa: E402
from app.schemas.quotation import QuotationCreate  # noqa: E402
from app.schemas.rfq import RFQCreate  # noqa: E402
from app.services.quotation_service import build_summary, create_quotation  # noqa: E402
from app.services.rfq_service import create_rfq, render_rfq_text  # noqa: E402

SUPPLIER_REPLIES = [
    ("Shandong Haihua",
     "For Acetylsalicylic acid (CAS 50-78-2), USP grade, our best price is "
     "USD 12.50/kg CIP Moscow. MOQ 25 kg. We can provide CoA and TDS. "
     "Payment T/T in advance. Lead time 15 days."),
    ("Hubei Xinghuo",
     "Our quotation: 8.00 EUR per kg, FCA Shanghai. Minimum order quantity 1 ton. "
     "Purity 99.5% min. Certificate of analysis available. 30% deposit. Delivery within 3 weeks."),
    ("Jiangsu Chem",
     "Price RMB 90/kg EXW our factory. Technical data sheet available. MOQ: 100 kg. "
     "Industrial grade. Payment L/C at sight."),
    ("Tianjin Bohai",
     "This item requires custom synthesis, lead time 6-8 weeks. We will revert with "
     "price after feasibility check."),
    ("Anhui Supplier",
     "Sorry, currently out of stock. Usual price around USD 20/kg FOB Ningbo, "
     "MOQ 200 kg, CoA included."),
]


def main() -> None:
    # Чистый старт демо-БД.
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()

    print("=" * 70)
    print("ChemSource AI — демонстрация сквозного потока")
    print("=" * 70)

    # 1. RFQ.
    rfq = create_rfq(
        db,
        RFQCreate(
            cas="50-78-2",
            name="Acetylsalicylic acid",
            incoterms=["CIP", "FCA", "EXW"],
            channels=["email"],
            purity="USP",
            volume="500 kg",
        ),
        verify=False,  # offline: без обращения к PubChem
    )
    print(f"\n[1] RFQ #{rfq.id} создан | статус: {rfq.status.value}")

    # 2. Письмо RFQ.
    subject, body = render_rfq_text(rfq)
    print(f"\n[2] Сгенерированное письмо:\n    Subject: {subject}")
    print("    " + body.splitlines()[0])

    # 3. Извлечение котировок из ответов (на правилах).
    print(f"\n[3] Обработка {len(SUPPLIER_REPLIES)} ответов поставщиков...")
    for supplier, text in SUPPLIER_REPLIES:
        q = extract_quote(text, use_llm=False)
        create_quotation(
            db,
            QuotationCreate(
                rfq_id=rfq.id,
                price=q.price,
                currency=q.currency,
                incoterm=q.incoterm,
                moq=q.moq,
                grade=q.grade,
                payment_terms=q.payment_terms,
                lead_time=q.lead_time,
                has_coa=q.has_coa,
                has_tds=q.has_tds,
                field_confidence=q.field_confidence,
                source_text=text,
            ),
        )
        flag = "✓ полная" if q.price and q.incoterm and q.moq else "… неполная"
        print(f"    {supplier:18} -> {flag}")

    # 4. Сводная сравнительная таблица.
    summary = build_summary(db, rfq.id)
    print("\n[4] Сводная таблица (полные котировки — выше):")
    header = f"    {'price':>8} {'cur':>4} {'basis':>5} {'MOQ':>8} {'CoA':>4} {'TDS':>4} {'complete':>9}"
    print(header)
    print("    " + "-" * 50)
    for r in summary:
        price = f"{r.price:.2f}" if r.price is not None else "-"
        print(f"    {price:>8} {r.currency or '-':>4} {r.incoterm or '-':>5} "
              f"{(r.moq or '-'):>8} {('yes' if r.has_coa else 'no'):>4} "
              f"{('yes' if r.has_tds else 'no'):>4} {('yes' if r.is_complete else 'NO'):>9}")

    # 5. Эскалации.
    escalations = db.scalars(select(Escalation).where(Escalation.rfq_id == rfq.id)).all()
    print(f"\n[5] Эскалаций специалисту: {len(escalations)}")
    for e in escalations:
        print(f"    - причина: {e.reason.value} | статус: {e.status.value}")

    db.close()
    print("\nГотово.")


if __name__ == "__main__":
    main()
