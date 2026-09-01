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


def test_document_file_is_not_public(client, seeded):
    response = client.get(f"/documents/{seeded['document_id']}/file")
    assert response.status_code == 401


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


# --- решение человека по изготовителю (MEET2-14) ---


def _make_document(rfq_id: int, company: str = "TNJ Chemical") -> int:
    """Документ с паспортом и привязанным поставщиком."""
    from app.models import Supplier
    from app.schemas.document_verification import DocumentVerification
    from app.services.document_text import apply_extraction
    from app.services.document_verification import apply_document_verification

    with SessionLocal() as db:
        supplier = Supplier(company=company)
        db.add(supplier)
        db.flush()
        stored = store_document(
            db,
            payload=_pdf_bytes([*_COA_LINES, "Batch No.: DECIDE-1"]),
            filename="CoA-decision.pdf",
            declared_content_type="application/pdf",
            rfq_id=rfq_id,
        )
        document = stored.document
        document.supplier_id = supplier.id
        apply_extraction(document)
        document.verification = apply_document_verification(
            verification=DocumentVerification.model_validate(
                {
                    "document_kind": "coa",
                    "substance_match": "exact",
                    "verification_status": "confirmed",
                    "recommended_action": "accept",
                    "confidence": 90,
                    "reason": "Документ относится к запрошенному веществу.",
                    "claims": [
                        {
                            "claim_type": "chemical_identity",
                            "claim_value": "CAS 50-78-2",
                            "quote": "CAS No.: 50-78-2",
                        }
                    ],
                    "missing_fields": [],
                    "red_flags": [],
                }
            ),
            document_text=document.text_content,
            expected_cas="50-78-2",
            supplier_company=company,
        )
        db.commit()
        return document.id


@pytest.fixture
def decidable(client, seeded):
    """Один документ на все тесты решения, с чистым состоянием перед каждым.

    Модули набора делят одну базу SQLite: DATABASE_URL ставится через
    setdefault, и выигрывает первый импортированный модуль. Поэтому лишние
    строки видны чужим тестам — создаётся ровно один документ, а решение
    снимается перед каждым использованием.
    """
    document_id = _make_document(seeded["rfq_id"])
    yield document_id
    with SessionLocal() as db:
        from app.models import Supplier, SupplierDocument

        document = db.get(SupplierDocument, document_id)
        if document is not None:
            supplier_id = document.supplier_id
            db.delete(document)
            if supplier_id:
                supplier = db.get(Supplier, supplier_id)
                if supplier is not None:
                    db.delete(supplier)
            db.commit()

def test_a_human_decision_overrides_the_automatic_outcome(client, decidable):
    """Автосверка на сокращённом названии не может решить — решает человек."""
    document_id = decidable
    headers = _auth(client, "ivanov")

    before = client.get(f"/documents/{document_id}", headers=headers).json()
    assert before["verification"]["manufacturer_match"]["status"] == "mismatch"

    response = client.post(
        f"/documents/{document_id}/manufacturer-decision",
        json={"status": "match", "reason": "Проверил по реестру: это одна компания"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    match = response.json()["verification"]["manufacturer_match"]

    assert match["status"] == "match"
    # Автоматический вывод не стёрт: аудиту нужно и то, по какому поводу
    # решение принималось.
    assert match["auto_status"] == "mismatch"
    assert match["decided_by"] == "Иван Иванов"
    assert match["decided_reason"].startswith("Проверил")
    assert match["decided_at"]


def test_the_decision_survives_a_re_verification(client, decidable, monkeypatch):
    """Перепроверка пересобирает verification — решение обязано уцелеть.

    Иначе очередной прогон модели молча отменял бы разбор человека.
    """
    document_id = decidable
    headers = _auth(client, "ivanov")
    client.post(
        f"/documents/{document_id}/manufacturer-decision",
        json={"status": "match", "reason": "Одна компания, сокращённое название"},
        headers=headers,
    )

    from app.extraction.llm_client import LLMUnavailableError
    from app.services import document_agent

    class _DeadLLM:
        model = "stub"

        def generate_json(self, **kwargs):
            raise LLMUnavailableError("нет модели")

    monkeypatch.setattr(document_agent, "LLMClient", lambda *a, **kw: _DeadLLM())
    monkeypatch.setattr(
        document_agent, "communication_llm_client", lambda *a, **kw: _DeadLLM()
    )

    again = client.post(
        f"/documents/{document_id}/verify", json={}, headers=headers
    )
    assert again.status_code == 200, again.text
    match = again.json()["verification"]["manufacturer_match"]
    assert match["status"] == "match", "решение человека затёрто перепроверкой"
    assert match["decided_by"] == "Иван Иванов"


def test_the_decision_can_be_withdrawn_and_the_machine_answer_returns(client, decidable):
    document_id = decidable
    headers = _auth(client, "ivanov")
    client.post(
        f"/documents/{document_id}/manufacturer-decision",
        json={"status": "match", "reason": "Ошибся, сейчас сниму"},
        headers=headers,
    )

    cleared = client.delete(
        f"/documents/{document_id}/manufacturer-decision", headers=headers
    )
    assert cleared.status_code == 200
    match = cleared.json()["verification"]["manufacturer_match"]
    assert match["status"] == "mismatch"
    assert "decided_by" not in match or match.get("decided_by") is None


def test_a_reason_is_required(client, decidable):
    """Решение перекрывает машину — без объяснения его не проверить."""
    document_id = decidable
    headers = _auth(client, "ivanov")
    response = client.post(
        f"/documents/{document_id}/manufacturer-decision",
        json={"status": "match", "reason": ""},
        headers=headers,
    )
    assert response.status_code == 422


def test_an_unknown_decision_is_refused(client, decidable):
    document_id = decidable
    headers = _auth(client, "ivanov")
    response = client.post(
        f"/documents/{document_id}/manufacturer-decision",
        json={"status": "confirmed_by_vibes", "reason": "почему бы и нет"},
        headers=headers,
    )
    assert response.status_code == 422


def test_the_auditor_may_read_but_not_decide(client, decidable):
    document_id = decidable
    auditor = _auth(client, "auditor")

    assert client.get(f"/documents/{document_id}", headers=auditor).status_code == 200
    assert (
        client.post(
            f"/documents/{document_id}/manufacturer-decision",
            json={"status": "match", "reason": "хочу решить"},
            headers=auditor,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/documents/{document_id}/manufacturer-decision", headers=auditor
        ).status_code
        == 403
    )
