"""Хранение документов поставщика и извлечение из них текста."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_documents.db")

import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal, engine, init_db
from app.models import Base, SupplierDocument
from app.services.document_intake import store_incoming_attachments
from app.services.document_storage import (
    DocumentTooLargeError,
    UnsupportedDocumentError,
    document_path,
    guess_kind,
    safe_filename,
    sniff_content_type,
    store_document,
)
from app.services.document_text import apply_extraction, extract_document_text


def _pdf_bytes(lines: list[str]) -> bytes:
    """Минимальный валидный PDF с текстовым слоем."""
    from pypdf import PdfWriter

    try:
        from reportlab.pdfgen import canvas  # noqa: F401
    except ImportError:
        # reportlab не нужен: собираем PDF вручную, без внешних зависимостей.
        pass

    content = "BT /F1 12 Tf 40 750 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({escaped}) Tj T*\n"
    content += "ET"
    stream = content.encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    assert PdfWriter is not None
    return bytes(out)


# Реалистичный паспорт качества: текстовый слой заведомо длиннее порога,
# ниже которого документ считается сканом.
_COA_LINES = [
    "HEBEI CHEM MANUFACTURING CO., LTD.",
    "CERTIFICATE OF ANALYSIS",
    "Product Name: Aspirin (Acetylsalicylic Acid)",
    "CAS No.: 50-78-2",
    "Batch No.: A-20240517",
    "Manufacturing Date: 2024-05-17",
    "Expiry Date: 2027-05-16",
    "Quantity: 500 kg",
    "Standard: USP 43 / EP 10.0",
    "Appearance: White crystalline powder  Conforms",
    "Identification IR: Conforms to reference spectrum",
    "Assay (HPLC, dried basis): 99.7 %   Limit: 99.5 - 100.5 %",
    "Loss on Drying: 0.08 %   Limit: not more than 0.5 %",
    "Heavy Metals: less than 10 ppm   Limit: not more than 20 ppm",
    "Residue on Ignition: 0.05 %   Limit: not more than 0.1 %",
    "Free Salicylic Acid: 0.05 %   Limit: not more than 0.1 %",
    "Conclusion: The batch complies with the specification.",
    "Quality Control Manager: Li Wei",
]


@pytest.fixture(scope="module", autouse=True)
def _database(tmp_path_factory):
    storage = tmp_path_factory.mktemp("attachments")
    os.environ["ATTACHMENT_STORAGE_DIR"] = str(storage)
    get_settings.cache_clear()
    if os.path.exists("test_documents.db"):
        os.remove("test_documents.db")
    init_db()
    yield
    engine.dispose()
    get_settings.cache_clear()
    if os.path.exists("test_documents.db"):
        os.remove("test_documents.db")


def test_declared_content_type_is_not_trusted():
    pdf = _pdf_bytes(["Certificate of Analysis"])
    # Отправитель объявил безобидный тип, но содержимое — PDF.
    assert sniff_content_type(pdf, "text/plain") == "application/pdf"
    assert sniff_content_type(b"plain body", "text/plain") == "text/plain"


def test_filename_cannot_escape_storage_directory():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename(r"C:\Windows\system32\evil.pdf") == "evil.pdf"
    assert safe_filename("") == "document"


def test_kind_is_guessed_from_filename():
    assert guess_kind("CoA_batch_2024.pdf") == "coa"
    assert guess_kind("Сертификат анализа.pdf") == "coa"
    assert guess_kind("aspirin-TDS.pdf") == "tds"
    assert guess_kind("MSDS.pdf") == "msds"
    assert guess_kind("price-list.pdf") == "other"


def test_stored_pdf_is_extracted_to_text():
    payload = _pdf_bytes(_COA_LINES)
    with SessionLocal() as db:
        stored = store_document(
            db, payload=payload, filename="CoA_A-20240517.pdf",
            declared_content_type="application/pdf", rfq_id=None,
        )
        document = stored.document
        assert stored.created is True
        assert document.content_type == "application/pdf"
        assert document.kind == "coa"
        # Имя файла не участвует в пути хранения.
        assert document.sha256 in document.storage_path
        assert document_path(document).is_file()

        apply_extraction(document)
        db.commit()

        assert document.text_status == "extracted"
        assert "50-78-2" in document.text_content
        assert "A-20240517" in document.text_content
        assert document.page_count == 1


def test_scan_without_text_layer_is_marked_for_ocr():
    # PDF без текста: страница есть, извлекать нечего.
    payload = _pdf_bytes([])
    with SessionLocal() as db:
        document = store_document(
            db, payload=payload, filename="scan.pdf", rfq_id=None
        ).document
        result = extract_document_text(document)
        assert result.status == "needs_ocr"
        assert "текстового слоя" in (result.error or "")


def test_oversized_and_unsupported_files_are_rejected():
    with SessionLocal() as db:
        with pytest.raises(DocumentTooLargeError):
            store_document(
                db,
                payload=b"x" * (get_settings().attachment_max_size_mb * 1024 * 1024 + 1),
                filename="huge.pdf",
            )
        with pytest.raises(UnsupportedDocumentError):
            store_document(
                db, payload=b"MZ\x90\x00executable", filename="virus.exe",
                declared_content_type="application/x-msdownload",
            )
        with pytest.raises(UnsupportedDocumentError):
            store_document(db, payload=b"", filename="empty.pdf")


def test_incoming_attachments_are_stored_without_payload_in_metadata():
    payload = _pdf_bytes(_COA_LINES)
    with SessionLocal() as db:
        metadata = store_incoming_attachments(
            db,
            rfq_id=None,
            communication_id=None,
            attachments=[
                {"filename": "CoA.pdf", "content_type": "application/pdf",
                 "size": len(payload), "content": payload},
                {"filename": "virus.exe", "content_type": "application/x-msdownload",
                 "size": 9, "content": b"MZ\x90\x00bad"},
            ],
        )
        db.commit()

    assert len(metadata) == 2
    good, bad = metadata
    # Содержимое не сохраняется в JSON коммуникации.
    assert "content" not in good and "content" not in bad
    assert good["document_id"] > 0
    assert good["status"] == "extracted"
    assert good["kind"] == "coa"
    assert bad["status"] == "rejected"
    assert "не поддерживается" in bad["error"]


def test_repeated_delivery_does_not_duplicate_the_file():
    # Отдельная партия: файл не должен совпасть с сохранённым в других тестах.
    payload = _pdf_bytes([*_COA_LINES, "Batch No.: DEDUP-0001"])
    with SessionLocal() as db:
        first = store_document(db, payload=payload, filename="CoA.pdf", rfq_id=None)
        second = store_document(db, payload=payload, filename="CoA.pdf", rfq_id=None)
        db.commit()
        assert first.created is True
        assert second.created is False
        assert first.document.id == second.document.id
        assert (
            db.query(SupplierDocument)
            .filter(SupplierDocument.sha256 == first.document.sha256)
            .count()
            == 1
        )


assert Base is not None


def _verification(**overrides):
    from app.schemas.document_verification import DocumentVerification

    payload = {
        "document_kind": "coa",
        "substance_match": "exact",
        "verification_status": "confirmed",
        "recommended_action": "accept",
        "confidence": 92,
        "reason": "Документ относится к запрошенному веществу и партии.",
        "claims": [
            {
                "claim_type": "chemical_identity",
                "claim_value": "CAS 50-78-2",
                "quote": "CAS No.: 50-78-2",
            },
            {
                "claim_type": "batch",
                "claim_value": "A-20240517",
                "quote": "Batch No.: A-20240517",
            },
        ],
        "missing_fields": [],
        "red_flags": [],
    }
    payload.update(overrides)
    return DocumentVerification.model_validate(payload)


_DOCUMENT_TEXT = "\n".join(_COA_LINES)


def test_passport_with_matching_cas_and_batch_is_confirmed():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=_verification(),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
        expected_name="Aspirin",
    )
    assert result["status"] == "confirmed"
    assert result["cas_in_document"] == ["50-78-2"]
    assert {claim["claim_type"] for claim in result["accepted_claims"]} == {
        "chemical_identity",
        "batch",
    }
    assert result["rejected_claims"] == []


def test_document_for_another_substance_is_rejected():
    from app.services.document_verification import apply_document_verification

    # Паспорт на салициловую кислоту вместо аспирина.
    other = _DOCUMENT_TEXT.replace("50-78-2", "69-72-7")
    result = apply_document_verification(
        verification=_verification(
            claims=[
                {
                    "claim_type": "chemical_identity",
                    "claim_value": "CAS 69-72-7",
                    "quote": "CAS No.: 69-72-7",
                },
                {
                    "claim_type": "batch",
                    "claim_value": "A-20240517",
                    "quote": "Batch No.: A-20240517",
                },
            ]
        ),
        document_text=other,
        expected_cas="50-78-2",
    )
    assert result["status"] == "rejected"
    assert result["cas_in_document"] == ["69-72-7"]
    assert any("другой CAS" in flag for flag in result["red_flags"])


def test_invented_quote_is_not_accepted_as_evidence():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=_verification(
            claims=[
                {
                    "claim_type": "chemical_identity",
                    "claim_value": "CAS 50-78-2",
                    "quote": "CAS No.: 50-78-2",
                },
                {
                    "claim_type": "batch",
                    "claim_value": "A-20240517",
                    "quote": "Batch No.: TOTALLY-MADE-UP",
                },
            ]
        ),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
    )
    assert result["status"] == "needs_review"
    assert len(result["rejected_claims"]) == 1
    assert result["rejected_claims"][0]["quote_verified"] is False


def test_unavailable_agent_blocks_acceptance():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=None,
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
        unavailable_reason="Локальная ИИ-модель недоступна",
    )
    assert result["status"] == "unavailable"
    assert result["recommended_action"] == "manual_review"


def test_scan_without_text_is_not_verified_by_the_agent():
    from app.extraction.llm_client import LLMClient
    from app.services.document_agent import verify_document

    with SessionLocal() as db:
        document = store_document(
            db, payload=_pdf_bytes([]), filename="scan-only.pdf", rfq_id=None
        ).document
        apply_extraction(document)
        db.commit()

        class ForbiddenClient(LLMClient):
            def generate_json(self, **kwargs):
                raise AssertionError("модель не должна вызываться без текста")

        result = verify_document(
            db, document, expected_cas="50-78-2", llm=ForbiddenClient()
        )
        assert result["status"] == "unavailable"
        assert "текст" in result["reason"].lower()


def test_agent_result_is_gated_before_it_is_stored():
    from app.extraction.llm_client import LLMClient
    from app.services.document_agent import verify_document

    payload = _pdf_bytes([*_COA_LINES, "Batch No.: GATE-1"])
    with SessionLocal() as db:
        document = store_document(
            db, payload=payload, filename="CoA_gate.pdf", rfq_id=None
        ).document
        apply_extraction(document)
        db.commit()

        class OverconfidentClient(LLMClient):
            def generate_json(self, **kwargs):
                # Модель уверенно подтверждает, ссылаясь на несуществующую цитату.
                return {
                    "document_kind": "coa",
                    "substance_match": "exact",
                    "verification_status": "confirmed",
                    "recommended_action": "accept",
                    "confidence": 99,
                    "reason": "Всё в порядке.",
                    "claims": [
                        {
                            "claim_type": "chemical_identity",
                            "claim_value": "CAS 50-78-2",
                            "quote": "Этой строки в документе нет",
                        }
                    ],
                    "missing_fields": [],
                    "red_flags": [],
                }

        result = verify_document(
            db, document, expected_cas="50-78-2", llm=OverconfidentClient()
        )
        db.commit()

        assert result["status"] == "needs_review"
        assert document.verification["status"] == "needs_review"
        assert result["rejected_claims"]
