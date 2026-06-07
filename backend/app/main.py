"""Точка входа FastAPI-приложения ChemSource AI.

Запуск (dev):
    cd backend
    uvicorn app.main:app --reload
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import (
    auth,
    dashboard,
    escalations,
    extraction,
    health,
    quotations,
    rfq,
    settings as settings_api,
    substances,
    suppliers,
    templates,
    users,
)
from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.seed import seed_suppliers, seed_templates, seed_users


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="ChemSource AI",
        description="ИИ-ассистент закупок химического сырья (on-premise).",
        version=__version__,
    )

    # CORS для SPA-фронтенда (в проде сузить до домена интерфейса).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(substances.router)
    app.include_router(rfq.router)
    app.include_router(quotations.router)
    app.include_router(extraction.router)
    app.include_router(escalations.router)
    app.include_router(suppliers.router)
    app.include_router(users.router)
    app.include_router(templates.router)
    app.include_router(settings_api.router)
    app.include_router(dashboard.router)

    @app.on_event("startup")
    def _startup() -> None:
        # Создание таблиц при старте (dev/демо). В проде — миграции Alembic.
        init_db()
        with SessionLocal() as db:
            seed_users(db)
            seed_suppliers(db)
            seed_templates(db)

    return app


app = create_app()
