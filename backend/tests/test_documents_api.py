"""API документов: доступ по ролям, скачивание и запуск проверки."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_documents_api.db")

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.main import app
from app.models import RFQ, User
from app.services.document_storage import store_document
from app.services.document_text import apply_extraction

from tests.test_documents import _COA_LINES, _pdf_bytes


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    storage = tmp_path_factory.mktemp("api-attachments")
    os.environ["ATTACHMENT_STORAGE_DIR"] = str(storage)
    get_settings.cache_clear()
    if os.path.exists("test_documents_api.db"):
        os.remove("test_documents_api.db")
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    get_settings.cache_clear()
    if os.path.exists("test_documents_api.db"):
        os.remove("test_documents_api.db")


def _auth(client, username: str) -> dict:
    response = client.post(
        "/auth/login", json={"username": username, "password": "demo123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def seeded(client):
    with SessionLocal() as db:
        buyer = db.query(User).filter(User.username == "ivanov").one()
        rfq = RFQ(cas="50-78-2", name="Аспирин", owner_id=buyer.id)
        db.add(rfq)
        db.flush()
        document = store_document(
            db,
            payload=_pdf_bytes([*_COA_LINES, "Batch No.: API-1"]),
            filename="CoA_api.pdf",
            rfq_id=rfq.id,
        ).document
        apply_extraction(document)
        db.commit()
        return {"rfq_id": rfq.id, "document_id": document.id}


def test_owner_sees_document_and_downloads_it_as_attachment(client, seeded):
    headers = _auth(client, "ivanov")
    listed = client.get(f"/rfq/{seeded['rfq_id']}/documents", headers=headers)
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert items[0]["kind"] == "coa"
    assert items[0]["text_status"] == "extracted"

    detail = client.get(f"/documents/{seeded['document_id']}", headers=headers)
    assert detail.status_code == 200
    assert "50-78-2" in detail.json()["text_content"]

    downloaded = client.get(
        f"/documents/{seeded['document_id']}/file", headers=headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF-")
    # Файл извне не должен открываться браузером как страница.
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert "attachment;" in downloaded.headers["content-disposition"]
    assert "filename*=UTF-8''CoA_api.pdf" in downloaded.headers[
        "content-disposition"
    ]
    assert downloaded.headers["x-content-type-options"] == "nosniff"


def test_foreign_buyer_cannot_reach_the_document(client, seeded):
    other = _auth(client, "petrova")
    assert client.get(
        f"/documents/{seeded['document_id']}", headers=other
    ).status_code == 200  # руководитель видит все запросы

    with SessionLocal() as db:
        stranger = User(
            username="stranger",
            full_name="Чужой Закупщик",
            role=db.query(User).filter(User.username == "ivanov").one().role,
            password_hash=db.query(User)
            .filter(User.username == "ivanov")
            .one()
            .password_hash,
        )
        db.add(stranger)
        db.commit()

    token = client.post(
        "/auth/login", json={"username": "stranger", "password": "demo123"}
    )
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    assert client.get(
        f"/documents/{seeded['document_id']}", headers=headers
    ).status_code == 404


def test_auditor_cannot_launch_verification(client, seeded):
    headers = _auth(client, "auditor")
    response = client.post(
        f"/documents/{seeded['document_id']}/verify", headers=headers, json={}
    )
    assert response.status_code == 403


def test_verification_uses_rfq_substance_and_stores_gate_result(
    client, seeded, monkeypatch
):
    def fake_generate_json(self, **kwargs):
        return {
            "document_kind": "coa",
            "substance_match": "exact",
            "verification_status": "confirmed",
            "recommended_action": "accept",
            "confidence": 95,
            "reason": "Паспорт соответствует запросу.",
            "claims": [
                {
                    "claim_type": "chemical_identity",
                    "claim_value": "CAS 50-78-2",
                    "quote": "CAS No.: 50-78-2",
                },
                {
                    "claim_type": "batch",
                    "claim_value": "API-1",
                    "quote": "Batch No.: API-1",
                },
            ],
            "missing_fields": [],
            "red_flags": [],
        }

    monkeypatch.setattr(
        "app.extraction.llm_client.LLMClient.generate_json", fake_generate_json
    )
    headers = _auth(client, "ivanov")
    response = client.post(
        f"/documents/{seeded['document_id']}/verify", headers=headers, json={}
    )
    assert response.status_code == 200
    verification = response.json()["verification"]
    assert verification["status"] == "confirmed"
    assert verification["expected_cas"] == "50-78-2"
    assert verification["cas_in_document"] == ["50-78-2"]
