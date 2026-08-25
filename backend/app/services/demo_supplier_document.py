"""Синтетический паспорт качества для демонстрации обработки вложений.

Документ не содержит реальных данных поставщика или партии. Он создаётся только
по явному действию администратора в тестовом диалоге и хранится тем же способом,
что и обычные входящие вложения.
"""

from __future__ import annotations

from datetime import date, timedelta
import unicodedata


_CYRILLIC_TRANSLITERATION = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
)


def _pdf_safe_name(value: str) -> str:
    """Читаемая ASCII-транслитерация вместо знаков вопроса в Helvetica."""
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    transliterated = decomposed.translate(_CYRILLIC_TRANSLITERATION)
    return "".join(char for char in transliterated if ord(char) < 128).strip()


def _pdf_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_demo_coa_pdf(*, substance_name: str, cas: str | None) -> bytes:
    """Возвращает минимальный PDF с текстовым слоем и синтетическим CoA."""
    manufactured = date.today().replace(day=1)
    expires = manufactured + timedelta(days=3 * 365)
    safe_substance_name = _pdf_safe_name(substance_name) or "Requested chemical"
    lines = [
        "CHEMSOURCE DEMO SUPPLIER CO., LTD.",
        "CERTIFICATE OF ANALYSIS - SYNTHETIC DEMONSTRATION ONLY",
        f"Product Name: {safe_substance_name}",
        f"CAS No.: {cas or 'Not supplied in RFQ'}",
        f"Batch No.: DEMO-{manufactured:%Y%m}-01",
        f"Manufacturing Date: {manufactured:%Y-%m-%d}",
        f"Expiry Date: {expires:%Y-%m-%d}",
        "Quantity: 500 kg",
        "Standard: Supplier internal demonstration specification",
        "Appearance: White crystalline powder  Conforms",
        "Identification: Conforms to reference standard",
        "Assay (HPLC, dried basis): 99.7 %   Limit: not less than 99.0 %",
        "Loss on Drying: 0.08 %   Limit: not more than 0.5 %",
        "Heavy Metals: less than 10 ppm   Limit: not more than 20 ppm",
        "Residue on Ignition: 0.05 %   Limit: not more than 0.1 %",
        "Conclusion: The batch complies with the demonstration specification.",
        "Quality Control Manager: Demo User",
        "THIS FILE IS SYNTHETIC AND MUST NOT BE USED FOR A REAL PURCHASE.",
    ]

    content = "BT /F1 11 Tf 40 750 Td 14 TL\n"
    for line in lines:
        # Встроенный Helvetica хранит только latin-1. Имя заранее переведено в
        # однозначную ASCII-транслитерацию, поэтому текстовый слой не теряет его.
        safe = _pdf_escape(line).encode("latin-1", errors="replace").decode("latin-1")
        content += f"({safe}) Tj T*\n"
    content += "ET"
    stream = content.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(output)
    output += f"xref\n0 {len(objects) + 1}\n".encode()
    output += b"0000000000 65535 f \n"
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(output)
