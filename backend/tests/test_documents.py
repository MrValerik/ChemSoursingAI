"""Хранение документов поставщика и извлечение из них текста."""

import os
from io import BytesIO
from zipfile import ZipFile

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


def test_office_document_type_is_detected_from_zip_contents():
    workbook = BytesIO()
    with ZipFile(workbook, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook />")
    document = BytesIO()
    with ZipFile(document, "w") as archive:
        archive.writestr("word/document.xml", "<document />")

    assert sniff_content_type(
        workbook.getvalue(), "application/octet-stream"
    ) == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert sniff_content_type(
        document.getvalue(), "application/octet-stream"
    ) == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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
    # PDF без текста: страница есть, извлекать нечего. OCR отключён, чтобы
    # проверить именно определение отсутствующего текстового слоя.
    from app.services.document_text import extract_pdf_text

    result = extract_pdf_text(_pdf_bytes([]))
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
    assert result["confidence"] == 90
    assert result["model_confidence"] == 92
    assert sum(item["score"] for item in result["confidence_breakdown"]) == 90
    assert result["cas_in_document"] == ["50-78-2"]
    assert {claim["claim_type"] for claim in result["accepted_claims"]} == {
        "chemical_identity",
        "batch",
    }
    assert result["rejected_claims"] == []


def test_model_confidence_does_not_replace_evidence_confidence():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=_verification(confidence=1),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
        expected_name="Aspirin",
    )

    assert result["status"] == "confirmed"
    assert result["confidence"] == 90
    assert result["model_confidence"] == 1


def test_document_without_requested_cas_can_be_confirmed_by_labeled_name():
    from app.services.document_verification import apply_document_verification

    verification = _verification(
        claims=[
            {
                "claim_type": "chemical_identity",
                "claim_value": "Aspirin",
                "quote": "Product Name: Aspirin (Acetylsalicylic Acid)",
            },
            {
                "claim_type": "batch",
                "claim_value": "A-20240517",
                "quote": "Batch No.: A-20240517",
            },
        ]
    )
    result = apply_document_verification(
        verification=verification,
        document_text=_DOCUMENT_TEXT,
        expected_cas=None,
        expected_name="Aspirin",
    )

    assert result["status"] == "confirmed"
    assert result["name_matches"] is True
    assert result["identity_basis"] == "name"
    assert result["confidence"] == 85


def test_missing_requested_cas_cannot_be_replaced_by_name_match():
    from app.services.document_verification import apply_document_verification

    text_without_cas = _DOCUMENT_TEXT.replace("CAS No.: 50-78-2\n", "")
    result = apply_document_verification(
        verification=_verification(),
        document_text=text_without_cas,
        expected_cas="50-78-2",
        expected_name="Aspirin",
    )

    assert result["status"] == "needs_review"
    assert result["identity_basis"] == "name_with_missing_expected_cas"
    assert result["confidence"] == 60


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
    assert result["confidence"] == 60


def test_batch_value_must_match_its_verbatim_quote():
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
                    "claim_value": "INVENTED-BATCH",
                    "quote": "Batch No.: A-20240517",
                },
            ]
        ),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
    )

    assert result["status"] == "needs_review"
    assert result["confidence"] == 60
    assert "claim_value" in result["rejected_claims"][0]["rejection_reason"]


def test_demo_pdf_transliterates_russian_name_without_question_marks():
    from app.services.demo_supplier_document import build_demo_coa_pdf
    from app.services.document_text import extract_pdf_text

    extracted = extract_pdf_text(
        build_demo_coa_pdf(
            substance_name="Ацетилсалициловая кислота",
            cas="50-78-2",
        )
    )

    assert "Product Name: atsetilsalitsilovaya kislota" in (extracted.text or "")
    assert "Product Name: ?" not in (extracted.text or "")


def test_demo_pdf_without_cas_passes_by_transliterated_requested_name():
    import re

    from app.services.demo_supplier_document import build_demo_coa_pdf
    from app.services.document_text import extract_pdf_text
    from app.services.document_verification import apply_document_verification

    extracted = extract_pdf_text(
        build_demo_coa_pdf(
            substance_name="Ацетилсалициловая кислота",
            cas=None,
        )
    )
    text = extracted.text or ""
    batch = re.search(r"Batch No.: ([A-Z0-9-]+)", text).group(1)
    verification = _verification(
        claims=[
            {
                "claim_type": "chemical_identity",
                "claim_value": "atsetilsalitsilovaya kislota",
                "quote": "Product Name: atsetilsalitsilovaya kislota",
            },
            {
                "claim_type": "batch",
                "claim_value": batch,
                "quote": f"Batch No.: {batch}",
            },
        ]
    )

    result = apply_document_verification(
        verification=verification,
        document_text=text,
        expected_cas=None,
        expected_name="Ацетилсалициловая кислота",
        synthetic_demo=True,
    )

    assert result["status"] == "confirmed"
    assert result["identity_basis"] == "name"
    assert result["confidence"] == 85


def test_ocr_text_receives_a_deterministic_confidence_penalty():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=_verification(),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
        expected_name="Aspirin",
        text_status="ocr_extracted",
    )

    assert result["confidence"] == 76
    assert result["status"] == "needs_review"
    assert result["confidence_breakdown"][-1]["key"] == "ocr_quality"


def test_synthetic_demo_uses_the_same_evidence_score_but_ignores_demo_veto():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=_verification(
            substance_match="mismatch",
            verification_status="rejected",
            recommended_action="reject",
            confidence=1,
            reason="The file is marked as synthetic.",
            red_flags=["Synthetic demonstration file"],
        ),
        document_text=_DOCUMENT_TEXT + "\nSYNTHETIC DEMONSTRATION ONLY",
        expected_cas="50-78-2",
        expected_name="Aspirin",
        synthetic_demo=True,
    )

    assert result["status"] == "confirmed"
    assert result["confidence"] == 90
    assert result["model_confidence"] == 1
    assert result["synthetic_demo"] is True


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


def test_scan_falls_back_to_ocr_when_it_is_available(monkeypatch):
    from app.services import document_text as text_service

    recognized = "\n".join(_COA_LINES)

    def fake_ocr_pdf(payload):
        from app.services.document_ocr import OcrResult

        return OcrResult(text=recognized, page_count=1)

    monkeypatch.setattr("app.services.document_ocr.ocr_pdf", fake_ocr_pdf)
    monkeypatch.setenv("OCR_ENABLED", "true")
    get_settings.cache_clear()

    with SessionLocal() as db:
        document = store_document(
            db, payload=_pdf_bytes([]), filename="scan-ocr.pdf", rfq_id=None
        ).document
        result = text_service.extract_document_text(document)

    get_settings.cache_clear()
    # Отдельный статус: текст получен распознаванием и менее надёжен, чем
    # текстовый слой PDF.
    assert result.status == "ocr_extracted"
    assert "50-78-2" in result.text


def test_scan_stays_manual_when_ocr_is_disabled(monkeypatch):
    from app.services import document_text as text_service

    monkeypatch.setenv("OCR_ENABLED", "false")
    get_settings.cache_clear()
    with SessionLocal() as db:
        document = store_document(
            db, payload=_pdf_bytes([]), filename="scan-off.pdf", rfq_id=None
        ).document
        result = text_service.extract_document_text(document)
    get_settings.cache_clear()
    assert result.status == "needs_ocr"


def test_unavailable_ocr_reports_the_real_reason(monkeypatch):
    from app.services import document_text as text_service
    from app.services.document_ocr import OcrResult

    monkeypatch.setenv("OCR_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.document_ocr.ocr_pdf",
        lambda payload: OcrResult(
            text=None, page_count=None, error="Tesseract недоступен: not found"
        ),
    )
    with SessionLocal() as db:
        document = store_document(
            db, payload=_pdf_bytes([]), filename="scan-broken.pdf", rfq_id=None
        ).document
        result = text_service.extract_document_text(document)
    get_settings.cache_clear()
    assert result.status == "needs_ocr"
    assert "Tesseract" in (result.error or "")


# --- сверка изготовителя из паспорта с приславшей компанией (MEET2-14) ---


def _manufacturer_claim(value: str = "HEBEI CHEM MANUFACTURING CO., LTD."):
    return {
        "claim_type": "manufacturer",
        "claim_value": value,
        "quote": "HEBEI CHEM MANUFACTURING CO., LTD.",
    }


def _verify_with_supplier(company: str | None, claims_extra=None, **kw):
    from app.services.document_verification import apply_document_verification

    base = _verification().model_dump()
    if claims_extra is not None:
        base["claims"] = base["claims"] + claims_extra
    from app.schemas.document_verification import DocumentVerification

    return apply_document_verification(
        verification=DocumentVerification.model_validate(base),
        document_text=_DOCUMENT_TEXT,
        expected_cas="50-78-2",
        expected_name="Aspirin",
        supplier_company=company,
        **kw,
    )


def test_manufacturer_in_the_passport_confirms_the_supplier():
    """Первое доказательство роли, не зависящее от сайта продавца.

    На поиске роль производителя подтверждается цитатой с сайта самой
    компании, а «we are a manufacturer» пишет и завод, и перекупщик.
    Имя в паспорте ставит тот, кто выпустил партию.
    """
    result = _verify_with_supplier(
        "Hebei Chem Manufacturing", claims_extra=[_manufacturer_claim()]
    )
    match = result["manufacturer_match"]

    assert match["status"] == "match"
    assert match["document_manufacturer"] == "HEBEI CHEM MANUFACTURING CO., LTD."
    assert match["supplier_company"] == "Hebei Chem Manufacturing"
    # Совпадение опирается на сохранённую цитату, а не на пересказ модели.
    assert match["quote"] in _DOCUMENT_TEXT


def test_a_different_manufacturer_marks_a_trader_without_killing_him():
    """Несовпадение меняет роль компании, а не годность документа."""
    result = _verify_with_supplier(
        "Qingdao Nova Chemicals", claims_extra=[_manufacturer_claim()]
    )
    match = result["manufacturer_match"]

    assert match["status"] == "mismatch"
    # Документ не отклонён: вещество и партия те самые, паспорт подлинный.
    assert result["status"] != "rejected"
    # Но роль названа: похоже на торговый дом.
    assert any("другой компанией" in flag for flag in result["red_flags"])


def test_a_foreign_manufacturer_becomes_a_search_lead_not_a_supplier():
    """В чужом паспорте стоит имя настоящего завода — того, кого искали.

    Создавать по нему подтверждённого поставщика нельзя: о компании
    известно только имя из документа, который прислал кто-то другой.
    """
    result = _verify_with_supplier(
        "Qingdao Nova Chemicals", claims_extra=[_manufacturer_claim()]
    )
    assert result["manufacturer_match"]["lead"] == "HEBEI CHEM MANUFACTURING CO., LTD."


def test_a_matching_manufacturer_is_not_a_lead():
    result = _verify_with_supplier(
        "Hebei Chem Manufacturing", claims_extra=[_manufacturer_claim()]
    )
    assert result["manufacturer_match"]["lead"] is None


def test_a_passport_without_a_named_maker_is_not_evidence_against_anyone():
    """У настоящего завода со скупым сайтом документов может не быть.

    Документ без названного изготовителя — пробел в данных, а не довод
    против поставщика: отсутствие доказательства доказательством не
    является.
    """
    from app.schemas.document_verification import DocumentVerification
    from app.services.document_verification import apply_document_verification

    faceless = "\n".join(
        line for line in _COA_LINES if "MANUFACTURING" not in line
    )
    result = apply_document_verification(
        verification=DocumentVerification.model_validate(_verification().model_dump()),
        document_text=faceless,
        expected_cas="50-78-2",
        expected_name="Aspirin",
        supplier_company="Hebei Chem Manufacturing",
    )
    match = result["manufacturer_match"]

    assert match["status"] == "insufficient"
    assert match["lead"] is None
    assert not any("другой компанией" in flag for flag in result["red_flags"])
    # Это пробел в данных, и назван он именно так.
    assert "Изготовитель в документе" in result["missing_fields"]


def test_a_manufacturer_claim_without_a_verbatim_quote_is_not_used():
    """Пересказ модели изготовителем не считается.

    Именно на нём и держалась бы ошибка: модель может назвать компанию,
    которой в документе нет.
    """
    result = _verify_with_supplier(
        "Qingdao Nova Chemicals",
        claims_extra=[
            {
                "claim_type": "manufacturer",
                "claim_value": "Qingdao Nova Chemicals",
                "quote": "Manufactured by Qingdao Nova Chemicals",
            }
        ],
    )
    # Такой цитаты в документе нет — утверждение модели отклонено, и её
    # значение в сверку не попадает. Изготовителем считается то, что
    # действительно написано в документе.
    match = result["manufacturer_match"]
    assert match["document_manufacturer"] != "Qingdao Nova Chemicals"
    assert match["source"] == "document"
    assert match["document_manufacturer"] == "HEBEI CHEM MANUFACTURING CO., LTD."


def test_the_trading_arm_of_the_same_plant_goes_to_a_human():
    """«Huateng Pharmaceutical» и «Huateng Pharmaceutical Trading» — не одно."""
    from app.services.document_verification import match_manufacturer

    status, reason = match_manufacturer(
        "Hunan Huateng Pharmaceutical Co., Ltd.",
        "Hunan Huateng Pharmaceutical Trading Co Ltd",
    )
    assert status == "manual_review"
    assert "торговый дом" in reason or "дочернее" in reason


def test_legal_forms_do_not_split_one_company():
    from app.services.document_verification import match_manufacturer

    assert match_manufacturer(
        "Hunan Huateng Pharmaceutical Co., Ltd.", "Hunan Huateng Pharmaceutical"
    )[0] == "match"
    assert match_manufacturer("ООО «Хунань Хуатэн»", "Хунань Хуатэн")[0] == "match"


def test_transliteration_of_one_name_goes_to_a_human_not_to_mismatch():
    """«Циндао Нова Кемикалс» и «Qingdao Nova Chemicals» — одна компания."""
    from app.services.document_verification import match_manufacturer

    status, _ = match_manufacturer(
        "Qingdao Nova Chemicals Co., Ltd", "ООО «Циндао Нова Кемикалс»"
    )
    assert status == "manual_review"


def test_a_different_province_is_a_different_plant():
    """Похожесть строк здесь обманывает: решает несовпавшее слово."""
    from app.services.document_verification import match_manufacturer

    assert match_manufacturer("Hunan Huateng", "Hebei Huateng")[0] == "mismatch"
    assert match_manufacturer("Some Chemical Co", "Other Chemical Ltd")[0] == "mismatch"


def test_an_unverified_document_reports_insufficient_not_mismatch():
    from app.services.document_verification import apply_document_verification

    result = apply_document_verification(
        verification=None,
        document_text=None,
        expected_cas="50-78-2",
        supplier_company="Qingdao Nova Chemicals",
        unavailable_reason="LLM недоступна",
    )
    assert result["manufacturer_match"]["status"] == "insufficient"
    assert result["manufacturer_match"]["lead"] is None


def test_the_manufacturer_is_read_from_the_document_when_the_model_skips_it():
    """Модель до изготовителя обычно не доходит, а в документе он есть.

    На боевом прогоне из двенадцати принятых утверждений шесть оказались
    assay, а manufacturer — ни одного, хотя компания названа первой
    строкой паспорта. Без детерминированного разбора сверять было бы нечего
    почти всегда.
    """
    result = _verify_with_supplier("Qingdao Nova Chemicals")
    match = result["manufacturer_match"]

    assert match["status"] == "mismatch"
    assert match["document_manufacturer"] == "HEBEI CHEM MANUFACTURING CO., LTD."
    assert match["source"] == "document"
    # Цитата — дословная строка документа, а не пересказ.
    assert match["quote"] in _DOCUMENT_TEXT


def test_a_model_claim_wins_over_the_header_heuristic():
    result = _verify_with_supplier(
        "Hebei Chem Manufacturing", claims_extra=[_manufacturer_claim()]
    )
    assert result["manufacturer_match"]["source"] == "claim"


def test_a_document_header_is_not_mistaken_for_a_company():
    from app.services.document_verification import document_manufacturer

    assert document_manufacturer("CERTIFICATE OF ANALYSIS\nProduct: Aspirin") is None
    assert document_manufacturer("QUALITY CONTROL LAB\nCoA") is None


def test_labelled_and_russian_manufacturers_are_read():
    from app.services.document_verification import document_manufacturer

    labelled = document_manufacturer(
        "CERTIFICATE OF ANALYSIS\nManufacturer: Hunan Huateng Co., Ltd.\nBatch: X"
    )
    assert labelled and labelled[0] == "Hunan Huateng Co., Ltd."

    russian = document_manufacturer("ООО «Хунань Хуатэн»\nПАСПОРТ КАЧЕСТВА")
    assert russian and russian[0] == "ООО «Хунань Хуатэн»"


def test_spacing_inside_the_name_is_not_a_different_company():
    """«Hangzhou Keyingchem» и «Hangzhou Keying Chem» — одна компания.

    Найдено на боевом документе: паспорт и карточка поставщика писали одно
    имя с разным пробелом, и сверка отправляла его на ручную проверку.
    """
    from app.services.document_verification import match_manufacturer

    assert match_manufacturer(
        "HANGZHOU KEYINGCHEM CO., LTD", "Hangzhou Keying Chem Co., Ltd."
    )[0] == "match"
    # Разные заводы от этого одинаковыми не становятся.
    assert match_manufacturer("Hunan Huateng", "Hebei Huateng")[0] == "mismatch"


def test_a_one_word_name_is_not_matched_fuzzily():
    """Одного слова слишком мало, чтобы считать названия одним именем.

    Найдено замером на сохранённых выдачах: «Aurochemicals» совпадала сразу
    с тремя разными китайскими компаниями — «biochemical» и «chemical»
    похожи на неё общим куском. Из 15 компаний выборки четыре получали
    ложное «нужна ручная проверка».
    """
    from app.services.document_verification import match_manufacturer

    for other in (
        "Shandong zhishang chemical Co.,Ltd",
        "SHANDONG LOOK CHEMICAL CO.,LTD",
        "LEADER BIOCHEMICAL GROUP",
    ):
        assert match_manufacturer(other, "Aurochemicals")[0] == "mismatch", other
    assert match_manufacturer("Belle Chemical LLC", "Elé Corporation")[0] == "mismatch"

    # Настоящие совпадения от этого не страдают: там слов хватает.
    # Кириллица против латиницы — путь транслитерации, решает человек.
    assert (
        match_manufacturer("ООО «Хунань Хуатэн»", "Hunan Huateng")[0]
        == "manual_review"
    )
    assert (
        match_manufacturer(
            "HANGZHOU KEYINGCHEM CO., LTD", "Hangzhou Keying Chem Co., Ltd."
        )[0]
        == "match"
    )
    assert (
        match_manufacturer(
            "Qingdao Nova Chemicals Co., Ltd", "ООО «Циндао Нова Кемикалс»"
        )[0]
        == "manual_review"
    )
