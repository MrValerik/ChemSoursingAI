"""Сидирование демо-пользователей (dev/демо; в проде пользователей заводит админ)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Supplier, Template, User
from app.models.enums import SupplierType, UserRole
from app.models.manager import Manager
from app.models.template import TemplateKind, WhatsappModeration

logger = logging.getLogger(__name__)

_DEMO_USERS = [
    ("ivanov", "Иван Иванов", UserRole.BUYER, "demo123"),
    ("petrova", "Анна Петрова", UserRole.HEAD, "demo123"),
    ("admin", "Администратор", UserRole.ADMIN, "demo123"),
    ("auditor", "Аудитор", UserRole.AUDITOR, "demo123"),
]


def seed_users(db: Session) -> None:
    """Создаёт демо-пользователей, если таблица пуста."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return
    for username, full_name, role, password in _DEMO_USERS:
        db.add(
            User(
                username=username,
                full_name=full_name,
                role=role,
                password_hash=hash_password(password),
            )
        )
    db.commit()
    logger.info("Seeded %d demo users (password: demo123)", len(_DEMO_USERS))


_DEMO_SUPPLIERS = [
    {
        "company": "Shandong Haihua",
        "country": "Китай",
        "type": SupplierType.MANUFACTURER,
        "reputation": "4",
        "source": "сайт компании",
        "certificates": ["GMP", "ISO 9001"],
        "email": "sales@haihua.example.cn",
        "whatsapp": None,
    },
    {
        "company": "Hubei Xinghuo",
        "country": "Китай",
        "type": SupplierType.DISTRIBUTOR,
        "reputation": "3",
        "source": "каталог / новости",
        "certificates": ["ISO 9001"],
        "email": "office@xinghuo.example.cn",
        "whatsapp": "+86-139-0000-0001",
    },
    {
        "company": "Jiangsu Chem",
        "country": "Китай",
        "type": SupplierType.MANUFACTURER,
        "reputation": "2",
        "source": "реестр поставщиков",
        "certificates": None,
        "email": "info@jiangsuchem.example.cn",
        "whatsapp": None,
    },
]


def seed_suppliers(db: Session) -> None:
    """Создаёт демо-поставщиков, если реестр пуст (dev/демо)."""
    if db.scalar(select(Supplier.id).limit(1)) is not None:
        return
    for item in _DEMO_SUPPLIERS:
        supplier = Supplier(
            company=item["company"],
            country=item["country"],
            type=item["type"],
            reputation=item["reputation"],
            source=item["source"],
            certificates=item["certificates"],
        )
        supplier.managers.append(
            Manager(email=item["email"], whatsapp=item["whatsapp"])
        )
        db.add(supplier)
    db.commit()
    logger.info("Seeded %d demo suppliers", len(_DEMO_SUPPLIERS))


_DEMO_TEMPLATES = [
    (
        TemplateKind.FOLLOWUP,
        "Дозапрос недостающих данных",
        "Dear {manager},\n\nThank you for your quotation for {substance} "
        "(CAS {cas}). Could you please also provide: {missing_fields}?\n\n"
        "Best regards,\n{buyer}",
        None,
    ),
    (
        TemplateKind.REPLY,
        "Ответ: запрос CoA/TDS",
        "Dear {manager},\n\nPlease find our request details attached. "
        "Kindly share the CoA and TDS for the offered material.\n\n"
        "Best regards,\n{buyer}",
        None,
    ),
    (
        TemplateKind.WHATSAPP,
        "Первый контакт (вне окна 24ч)",
        "Hello {manager}, this is {buyer} from {company}. We are sourcing "
        "{substance} (CAS {cas}) and would appreciate your best quotation. "
        "Details were sent to your email.",
        WhatsappModeration.PENDING,
    ),
]


def seed_templates(db: Session) -> None:
    """Создаёт базовые шаблоны, если их нет (dev/демо)."""
    if db.scalar(select(Template.id).limit(1)) is not None:
        return
    for kind, name, body, moderation in _DEMO_TEMPLATES:
        db.add(
            Template(
                kind=kind,
                name=name,
                body=body,
                version=1,
                moderation=moderation,
                updated_by="система",
            )
        )
    db.commit()
    logger.info("Seeded %d demo templates", len(_DEMO_TEMPLATES))
