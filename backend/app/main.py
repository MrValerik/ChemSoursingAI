"""Точка входа FastAPI-приложения ChemSource AI.

Запуск (dev):
    cd backend
    uvicorn app.main:app --reload
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health, rfq, substances
from app.core.config import get_settings
from app.core.db import init_db


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
    app.include_router(substances.router)
    app.include_router(rfq.router)

    @app.on_event("startup")
    def _startup() -> None:
        # Создание таблиц при старте (dev/демо). В проде — миграции Alembic.
        init_db()

    return app


app = create_app()
