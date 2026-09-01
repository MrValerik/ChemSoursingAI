"""Детерминированная проверка выводов агента о паспорте качества.

Правила те же, что у аудитора поставщиков: модель не может создать факт.
Утверждение принимается, только если его цитата дословно есть в сохранённом
тексте документа. CAS и номер партии дополнительно сверяются кодом, а не
доверием к модели.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from app.schemas.document_verification import DocumentVerification
from app.services.cas import normalize_cas

# Без этих утверждений документ не может быть принят автоматически.
_REQUIRED_CLAIMS = {"chemical_identity", "batch"}
_CAS_PATTERN = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")
_PRODUCT_NAME_PATTERN = re.compile(
    r"(?im)^\s*(?:product\s+name|chemical\s+name|substance\s+name|"
    r"наименование|название\s+вещества)\s*[:\-]\s*([^\r\n]+)"
)
_BATCH_VALUE_PATTERN = re.compile(
    r"(?i)(?:(?:\bbatch\s*(?:no\.?|number)|\blot\s*(?:no\.?|number)|номер\s+партии)"
    r"(?:\s*[:#：\-]\s*|\s+)|(?:\bbatch|\blot)\s*[:#：\-]\s*)"
    r"([a-zа-я0-9][a-zа-я0-9._/\-]*)"
)
_DOCUMENT_KIND_MARKERS = {
    "coa": ("certificate of analysis", "паспорт качества", "сертификат анализа"),
    "tds": ("technical data sheet", "technical datasheet", "техническая спецификация"),
    "msds": ("material safety data sheet", "safety data sheet", "паспорт безопасности"),
}
_SUPPORTING_CLAIMS = {
    "manufacture_date",
    "expiry_date",
    "standard",
    "assay",
    "impurity",
    "manufacturer",
    "conclusion",
}
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


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalize_name(value: str) -> str:
    """Сопоставимое представление названия, включая русскую транслитерацию."""
    decomposed = unicodedata.normalize("NFKD", (value or "").casefold())
    transliterated = decomposed.translate(_CYRILLIC_TRANSLITERATION)
    return re.sub(r"[^a-z0-9]+", " ", transliterated).strip()


# Правовые формы компаний. «Hunan Huateng Pharmaceutical Co., Ltd.» в
# паспорте и «Hunan Huateng Pharmaceutical» в карточке — одна компания, и
# различать их по суффиксу значит объявлять несовпадением обычную разницу
# в написании. Список закрытый: сюда идут только формы, а не слова вроде
# «pharmaceutical» или «chemical» — они часть имени.
_COMPANY_SUFFIXES = (
    "co ltd", "co limited", "company limited", "cо ltd",
    "ltd", "limited", "llc", "lltd", "inc", "incorporated", "corp",
    "corporation", "gmbh", "ag", "sa", "srl", "bv", "nv", "plc", "pte",
    "pvt", "private limited", "kg", "oy", "ab", "as", "spa",
    "ooo", "oao", "zao", "pao", "ao", "ip",
    "group", "holdings", "holding",
)

def normalize_company(value: str) -> str:
    """Сопоставимое имя компании: без правовой формы, регистра и пунктуации.

    Транслитерация здесь та же, что у названий веществ: паспорт приходит и
    на латинице, и кириллицей, а карточка поставщика заполнена как придётся.
    """
    name = _normalize_name(value)
    if not name:
        return ""
    # Форма снимается с краёв и по одной: «Co Ltd» в середине названия —
    # часть имени. По-английски форма стоит в конце, по-русски впереди
    # («ООО Хунань Хуатэн»), поэтому чистятся оба края.
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            if name.endswith(" " + suffix):
                name = name[: -len(suffix) - 1].strip()
                changed = True
                break
            if name.startswith(suffix + " "):
                name = name[len(suffix) + 1 :].strip()
                changed = True
                break
    return name


def _company_tokens(value: str) -> frozenset[str]:
    """Слова названия без правовой формы.

    Отраслевые и географические слова намеренно не выбрасываются. Соблазн
    считать «chemical», «trading» или «Hunan» шумом велик, но каждое из них
    различает компании: «Huateng Pharmaceutical» и «Huateng Pharmaceutical
    Trading» — завод и его торговый дом, а «Hunan Huateng» и «Hebei Huateng» —
    разные предприятия в разных провинциях. Выбросив эти слова, код объявил
    бы их одной компанией.
    """
    return frozenset(
        token for token in normalize_company(value).split() if len(token) > 1
    )


# Насколько похожими должны быть два слова, чтобы считаться одним именем в
# разной транслитерации. Порог подобран по живым парам: «khunan»/«hunan» —
# 0.91, «kemikals»/«chemicals» — 0.71, а «hunan»/«hebei» — 0.5.
_TOKEN_SIMILARITY = 0.7


def _tokens_pair_up(left: frozenset[str], right: frozenset[str]) -> bool:
    """У каждого слова короткого названия есть близкий двойник в длинном."""
    if not left or not right:
        return False
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return all(
        max(SequenceMatcher(None, token, other).ratio() for other in long)
        >= _TOKEN_SIMILARITY
        for token in short
    )


# Метки, которыми изготовителя называют явно.
_MANUFACTURER_LABEL_PATTERN = re.compile(
    r"(?im)^\s*(?:manufacturer|manufactured\s+by|produced\s+by|producer|"
    r"изготовитель|производитель|изготовлено)\s*[:\-]\s*([^\r\n]+)"
)

# Правовая форма в строке — признак того, что это название компании, а не
# заголовок документа. «CERTIFICATE OF ANALYSIS» формы не содержит,
# «CHEMSOURCE DEMO SUPPLIER CO., LTD.» содержит. По-английски форма стоит
# в конце, по-русски впереди — проверяются оба края.
_LEGAL_FORM_PATTERN = re.compile(
    r"(?i)\b(?:co\.?\s*,?\s*ltd|ltd|limited|llc|inc|corp|corporation|"
    r"gmbh|pte|pvt|plc|s\.?a\.?|b\.?v\.?|n\.?v\.?|"
    r"ооо|оао|зао|пао|ао)\b\.?\s*$"
)
_RU_LEGAL_PREFIX_PATTERN = re.compile(
    r"(?i)^(?:ооо|оао|зао|пао|ао|ип)\s*[«\"]?\s*\b"
)



def document_manufacturer(document_text: str) -> tuple[str, str] | None:
    """Изготовитель прямо из текста паспорта: имя и строка-цитата.

    Детерминированный разбор нужен не для подстраховки модели, а потому что
    модель до изготовителя не доходит: лимит утверждений уходит на анализы.
    На боевом прогоне из двенадцати принятых утверждений шесть оказались
    assay, а manufacturer — ни одного, хотя компания названа первой строкой.

    Сначала явная метка, затем шапка документа. Шапка берётся только с
    правовой формой в конце: без этого условия изготовителем оказался бы
    заголовок «CERTIFICATE OF ANALYSIS» или название лаборатории.
    """
    text = document_text or ""
    labelled = _MANUFACTURER_LABEL_PATTERN.search(text)
    if labelled:
        name = labelled.group(1).strip()
        if name:
            return name, labelled.group(0).strip()

    for line in text.splitlines()[:3]:
        candidate = line.strip()
        if len(candidate) < 4 or len(candidate) > 120:
            continue
        if _LEGAL_FORM_PATTERN.search(candidate) or _RU_LEGAL_PREFIX_PATTERN.search(
            candidate
        ):
            return candidate, candidate
    return None


def match_manufacturer(
    document_manufacturer: str | None,
    supplier_company: str | None,
) -> tuple[str, str]:
    """Сверяет изготовителя из документа с компанией, которая его прислала.

    Возвращает исход и причину человеческим языком.

    Зачем вообще: роль производителя на этапе поиска подтверждается цитатой
    с сайта самого продавца, а «we are a manufacturer» пишет и завод, и
    перекупщик. Паспорт качества относится к конкретной партии, и имя
    изготовителя в нём ставит тот, кто партию выпустил, — это первое
    доказательство роли, не зависящее от маркетинга продавца.

    Исходы разведены намеренно. `insufficient` — не отрицательный ответ:
    отсутствие паспорта ничего не говорит о том, завод перед нами или нет,
    и у настоящего завода со скупым сайтом документов может не быть на
    руках. `manual_review` — частичное совпадение: дочернее предприятие и
    другая фирма того же холдинга выглядят одинаково, и различить их может
    только человек.
    """
    document_name = (document_manufacturer or "").strip()
    supplier_name = (supplier_company or "").strip()
    if not document_name or not supplier_name:
        return (
            "insufficient",
            "Изготовитель в документе не назван или поставщик не указан — "
            "сверять нечего. Отсутствие паспорта не является доказательством "
            "того, что компания не производитель.",
        )

    left = normalize_company(document_name)
    right = normalize_company(supplier_company)
    if not left or not right:
        return (
            "insufficient",
            "После нормализации от одного из названий не осталось значимой "
            "части — сверка ненадёжна.",
        )

    # Пробелы внутри имени сравнение не должно различать: «Hangzhou
    # Keyingchem» и «Hangzhou Keying Chem» — одна компания, и так её пишут
    # в паспорте и в карточке. Найдено на боевых документах.
    if left == right or left.replace(" ", "") == right.replace(" ", ""):
        return (
            "match",
            f"Изготовитель в документе и поставщик — одна компания: «{document_name}».",
        )

    left_tokens = _company_tokens(document_name)
    right_tokens = _company_tokens(supplier_company)
    if left_tokens and right_tokens and (
        left_tokens <= right_tokens or right_tokens <= left_tokens
    ):
        # Одно название целиком входит в другое. Так выглядит и дочернее
        # предприятие, и торговый дом того же завода: «Huateng
        # Pharmaceutical» против «Huateng Pharmaceutical Trading». Разница
        # существенная, а различить их по названию нельзя.
        return (
            "manual_review",
            f"«{document_name}» и «{supplier_company}» совпадают частично: "
            "так выглядит дочернее предприятие или торговый дом того же "
            "завода. Различить может только человек.",
        )

    # Транслитерация расходится с романизацией: «ООО Хунань Хуатэн» даёт
    # «khunan khuaten», а паспорт — «Hunan Huateng».
    #
    # Сравнение идёт пословно, а не по строке целиком. Похожесть строк здесь
    # не работает: «hunan huateng» и «hebei huateng» — разные заводы в разных
    # провинциях — похожи на 0.69, а «qingdao nova chemicals» и «циндао нова
    # кемикалс» — одна компания — только на 0.72. Порог, который поймал бы
    # вторую пару, склеил бы первую.
    #
    # Пословно они разделяются: решает самое непохожее слово. Одна чужая
    # часть названия — провинция, «Trading» — рушит совпадение целиком.
    if _tokens_pair_up(left_tokens, right_tokens):
        return (
            "manual_review",
            f"«{document_name}» и «{supplier_company}» похожи пословно — так "
            "выглядит одно имя в разной транслитерации. Дословно они не "
            "совпадают, поэтому подтвердить изготовителя должен человек.",
        )

    return (
        "mismatch",
        f"Паспорт выпущен другой компанией: «{document_name}», а документ "
        f"прислала «{supplier_company}». Это не порок поставщика — так "
        "выглядит торговый дом, — но роль изготовителя за ним не "
        "подтверждена.",
    )


def _quote_is_verbatim(quote: str, document_text: str) -> bool:
    """Цитата засчитывается только при дословном совпадении."""
    return _normalize_space(quote) in _normalize_space(document_text)


def _batch_claim_matches_quote(claim_value: str, quote: str) -> bool:
    """Номер партии в structured value должен совпасть с самой цитатой."""
    match = _BATCH_VALUE_PATTERN.search(quote or "")
    if match is None:
        return False
    stated = _normalize_name(match.group(1))
    claimed = _normalize_name(claim_value)
    return bool(stated and claimed and stated == claimed)


def document_cas_numbers(document_text: str) -> set[str]:
    """Все синтаксически валидные CAS, встречающиеся в документе."""
    from app.services.cas import is_valid_cas

    found = set()
    for candidate in _CAS_PATTERN.findall(document_text or ""):
        normalized = normalize_cas(candidate)
        if is_valid_cas(normalized):
            found.add(normalized)
    return found


def document_product_names(document_text: str) -> list[str]:
    """Названия только из явно подписанных полей документа."""
    return [match.strip() for match in _PRODUCT_NAME_PATTERN.findall(document_text or "")]


def document_kind_from_text(document_text: str) -> str:
    """Распознаёт стандартный тип документа по его заголовку без модели."""
    normalized = _normalize_space(document_text or "")
    # A TDS can refer to a Certificate of Analysis in its footer. Its own
    # heading comes first; mentioning CoA later does not change the file type.
    matches = [(normalized.find(marker), kind) for kind, markers in _DOCUMENT_KIND_MARKERS.items()
               for marker in markers if marker in normalized]
    return min(matches)[1] if matches else "unknown"


def _name_matches(expected_name: str | None, document_text: str) -> bool:
    expected = _normalize_name(expected_name or "")
    if len(expected) < 3:
        return False
    for candidate in document_product_names(document_text):
        normalized = _normalize_name(candidate)
        if (
            normalized == expected
            or normalized.startswith(expected + " ")
            or normalized.endswith(" " + expected)
        ):
            return True
    return False


def _confidence_breakdown(
    *,
    identity_basis: str,
    accepted_types: set[str],
    accepted_count: int,
    rejected_count: int,
    deterministic_document_kind: str,
    text_status: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    """Считает воспроизводимый балл только по проверяемым признакам."""
    identity_points = {
        "cas": 45,
        "name": 40,
        "name_with_missing_expected_cas": 25,
    }.get(identity_basis, 0)
    identity_reason = {
        "cas": "CAS с корректной контрольной суммой совпал с запросом.",
        "name": "CAS не задан; название совпало с явно подписанным полем документа.",
        "name_with_missing_expected_cas": (
            "Название совпало, но заданный в запросе CAS в документе отсутствует."
        ),
        "conflict": "В документе найден другой валидный CAS.",
    }.get(identity_basis, "Идентичность вещества не подтверждена кодом.")

    batch_points = 20 if "batch" in accepted_types else 0
    total_claims = accepted_count + rejected_count
    citation_points = (
        round(20 * accepted_count / total_claims) if total_claims else 0
    )
    kind_points = 5 if deterministic_document_kind != "unknown" else 0
    supporting_count = len(accepted_types & _SUPPORTING_CLAIMS)
    supporting_points = min(10, supporting_count * 2)

    breakdown = [
        {
            "key": "identity",
            "label": "Идентичность вещества",
            "score": identity_points,
            "max_score": 45,
            "reason": identity_reason,
        },
        {
            "key": "batch",
            "label": "Номер партии",
            "score": batch_points,
            "max_score": 20,
            "reason": (
                "Номер партии подтверждён дословной цитатой."
                if batch_points
                else "Нет подтверждённой цитаты с номером партии."
            ),
        },
        {
            "key": "citations",
            "label": "Проверка цитат",
            "score": citation_points,
            "max_score": 20,
            "reason": (
                f"Дословно найдено {accepted_count} из {total_claims} утверждений."
                if total_claims
                else "Агент не предоставил проверяемых утверждений."
            ),
        },
        {
            "key": "document_structure",
            "label": "Структура и дополнительные поля",
            "score": kind_points + supporting_points,
            "max_score": 15,
            "reason": (
                f"Тип документа распознан; дополнительных подтверждённых полей: "
                f"{supporting_count}."
                if kind_points
                else "Тип документа не распознан как CoA, TDS или MSDS."
            ),
        },
    ]
    raw_score = sum(item["score"] for item in breakdown)
    if text_status == "ocr_extracted":
        adjusted = round(raw_score * 0.85)
        breakdown.append(
            {
                "key": "ocr_quality",
                "label": "Надёжность текстового слоя",
                "score": adjusted - raw_score,
                "max_score": 0,
                "reason": "Текст получен OCR; применено снижение 15% из-за риска ошибок распознавания.",
            }
        )
        raw_score = adjusted
    return max(0, min(100, raw_score)), breakdown


def apply_document_verification(
    *,
    verification: DocumentVerification | None,
    document_text: str | None,
    expected_cas: str | None,
    expected_name: str | None = None,
    supplier_company: str | None = None,
    text_status: str | None = "extracted",
    synthetic_demo: bool = False,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Применяет veto-gate к выводу агента о документе."""
    if verification is None or not document_text:
        reason = unavailable_reason or (
            "Проверяющий агент не вернул корректную структурированную оценку."
        )
        return {
            "status": "unavailable",
            "model_status": None,
            "document_kind": None,
            "substance_match": "unknown",
            "recommended_action": "manual_review",
            "confidence": 0,
            "model_confidence": None,
            "confidence_breakdown": [],
            "reason": reason,
            "gate_reason": "Документ не принят до ручной проверки.",
            "accepted_claims": [],
            "rejected_claims": [],
            "missing_fields": ["Независимая проверка документа"],
            "red_flags": [],
            "cas_in_document": [],
            "expected_cas": expected_cas,
            "expected_name": expected_name,
            "manufacturer_match": {
                "status": "insufficient",
                "document_manufacturer": None,
                "supplier_company": supplier_company,
                "quote": None,
                "source": None,
                "reason": (
                    "Документ не проверен, изготовителя сверять не с чем. "
                    "Это не довод против поставщика."
                ),
                "lead": None,
            },
            "synthetic_demo": synthetic_demo,
        }

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for claim in verification.claims:
        entry = {
            "claim_type": claim.claim_type,
            "claim_value": claim.claim_value,
            "quote": claim.quote,
        }
        quote_verified = _quote_is_verbatim(claim.quote, document_text)
        value_verified = (
            claim.claim_type != "batch"
            or _batch_claim_matches_quote(claim.claim_value, claim.quote)
        )
        if claim.claim_type == "manufacture_date" and not re.search(
            r"manufactur(?:e|ing)|mfg|production\s+date|дата\s+(?:производства|изготовления)",
            claim.quote, re.I,
        ):
            value_verified = False
        if claim.claim_type == "standard":
            named_standards = re.findall(r"\b(?:USP|EP|BP|JP|FCC)(?=\b|\d)", claim.claim_value, re.I)
            if any(not re.search(rf"\b{standard}(?=\b|\d)", claim.quote, re.I) for standard in named_standards):
                value_verified = False
        if quote_verified and value_verified:
            accepted.append({**entry, "quote_verified": True})
        else:
            rejection_reason = (
                ("номер партии в claim_value не совпадает с цитатой"
                 if claim.claim_type == "batch"
                 else ("заявленный стандарт не указан в цитате; его нельзя вывести из набора анализов"
                       if claim.claim_type == "standard"
                       else "цитата не содержит дату производства; дата документа её не заменяет"))
                if quote_verified and not value_verified
                else "цитата дословно не найдена в тексте документа"
            )
            rejected.append(
                {
                    **entry,
                    "quote_verified": False,
                    "rejection_reason": rejection_reason,
                }
            )

    accepted_types = {claim["claim_type"] for claim in accepted}

    # Изготовитель берётся только из принятого утверждения: его цитата
    # дословно найдена в документе. Пересказ модели здесь не годится —
    # именно на нём и держалась бы ошибка, которую карточка запрещает.
    manufacturer_claim = next(
        (claim for claim in accepted if claim["claim_type"] == "manufacturer"),
        None,
    )
    manufacturer_name = (manufacturer_claim or {}).get("claim_value") or None
    manufacturer_quote = (manufacturer_claim or {}).get("quote")
    manufacturer_source = "claim" if manufacturer_name else None
    if not manufacturer_name:
        # Модель до изготовителя обычно не доходит, а в документе он есть.
        extracted = document_manufacturer(document_text)
        if extracted:
            manufacturer_name, manufacturer_quote = extracted
            manufacturer_source = "document"
    manufacturer_status, manufacturer_reason = match_manufacturer(
        manufacturer_name, supplier_company
    )
    manufacturer_match = {
        "status": manufacturer_status,
        # Обе исходные строки показываются человеку как есть: решение
        # принимает он, и сравнивать ему нужно то, что написано, а не
        # то, что осталось после нормализации.
        "document_manufacturer": manufacturer_name,
        "supplier_company": supplier_company,
        "quote": manufacturer_quote,
        # Откуда взято имя: из утверждения модели или из разбора текста.
        # Оба опираются на дословную строку документа, но происхождение
        # должно быть видно в аудите.
        "source": manufacturer_source,
        "reason": manufacturer_reason,
        # Найденный в паспорте чужой изготовитель — наводка для нового
        # поиска, а не подтверждённый поставщик: о нём известно только имя
        # из чужого документа.
        "lead": manufacturer_name if manufacturer_status == "mismatch" else None,
    }

    # CAS сверяем сами: это дешёвая детерминированная проверка, которая не
    # должна зависеть от аккуратности модели.
    document_cas = document_cas_numbers(document_text)
    normalized_expected = normalize_cas(expected_cas or "")
    cas_matches = bool(normalized_expected) and normalized_expected in document_cas
    cas_conflict = (
        bool(normalized_expected)
        and bool(document_cas)
        and not cas_matches
    )
    name_matches = _name_matches(expected_name, document_text)
    if cas_matches:
        identity_basis = "cas"
    elif cas_conflict:
        identity_basis = "conflict"
    elif not normalized_expected and name_matches:
        identity_basis = "name"
    elif normalized_expected and not document_cas and name_matches:
        identity_basis = "name_with_missing_expected_cas"
    else:
        identity_basis = "missing"

    confidence, confidence_breakdown = _confidence_breakdown(
        identity_basis=identity_basis,
        accepted_types=accepted_types,
        accepted_count=len(accepted),
        rejected_count=len(rejected),
        deterministic_document_kind=document_kind_from_text(document_text),
        text_status=text_status,
    )

    red_flags = list(verification.red_flags)
    missing_fields = list(verification.missing_fields)

    def flag(message: str) -> None:
        if message not in red_flags:
            red_flags.append(message)

    if cas_conflict:
        flag(
            "В документе указан другой CAS: "
            + ", ".join(sorted(document_cas))
        )
    if normalized_expected and not document_cas:
        if "CAS в документе" not in missing_fields:
            missing_fields.append("CAS в документе")

    # Несовпадение изготовителя не отклоняет документ и не уничтожает
    # кандидата: паспорт подлинный, вещество то самое, партия та самая —
    # другой оказалась роль приславшей компании. Это меняет то, кем мы её
    # считаем, а не то, годен ли документ, поэтому здесь отметка, а не veto.
    if manufacturer_match["status"] == "mismatch":
        flag(
            "Паспорт выпущен другой компанией: "
            f"«{manufacturer_match['document_manufacturer']}». Роль "
            "изготовителя за поставщиком не подтверждена — похоже на "
            "торговый дом."
        )
    elif manufacturer_match["status"] == "manual_review":
        flag(
            "Изготовителя в паспорте нужно сверить вручную: "
            f"{manufacturer_match['reason']}"
        )
    elif manufacturer_match["status"] == "insufficient" and supplier_company:
        # Отсутствие изготовителя в документе — пробел в данных, а не довод
        # против поставщика: у настоящего завода паспорт может быть скупым.
        if "Изготовитель в документе" not in missing_fields:
            missing_fields.append("Изготовитель в документе")

    model_rejected = (
        verification.verification_status == "rejected"
        or verification.recommended_action == "reject"
        or verification.substance_match == "mismatch"
    )
    deterministic_identity_match = cas_matches or (
        not normalized_expected and name_matches
    )
    model_accepts = (
        verification.verification_status == "confirmed"
        and verification.recommended_action == "accept"
        and verification.substance_match == "exact"
    )
    confirmed = (
        not cas_conflict
        and deterministic_identity_match
        and (model_accepts or synthetic_demo)
        and confidence >= 80
        and _REQUIRED_CLAIMS.issubset(accepted_types)
        and not rejected
    )

    if confirmed:
        status = "confirmed"
        gate_reason = (
            "Вещество и номер партии подтверждены дословными цитатами, "
            + (
                "CAS в документе совпадает с запросом."
                if cas_matches
                else "название совпадает с явно подписанным полем документа."
            )
        )
    elif (model_rejected and not synthetic_demo) or cas_conflict:
        status = "rejected"
        gate_reason = (
            "CAS в документе не совпадает с запросом."
            if cas_conflict
            else "Агент обнаружил несоответствие документа запросу."
        )
    else:
        status = "needs_review"
        gaps: list[str] = []
        if not deterministic_identity_match:
            gaps.append(
                "CAS запроса не найден в документе"
                if normalized_expected
                else "название запроса не совпало с полем названия в документе"
            )
        if verification.substance_match != "exact" and not synthetic_demo:
            gaps.append("нет точного соответствия вещества")
        if confidence < 80:
            gaps.append(f"проверяемая уверенность ниже 80% ({confidence}%)")
        if not _REQUIRED_CLAIMS.issubset(accepted_types):
            if document_kind_from_text(document_text) == "tds" and "batch" not in accepted_types:
                gaps.append("TDS описывает продукт в целом, для проверки конкретной партии требуется CoA")
            else:
                gaps.append("не подтверждены вещество и номер партии")
        if rejected:
            gaps.append("часть утверждений не подтверждена текстом документа")
        gate_reason = (
            "Требуется ручная проверка: " + "; ".join(gaps)
            if gaps
            else "Требуется ручная проверка документа."
        )

    if rejected:
        flag("Часть утверждений агента не подтверждена цитатами из документа")

    return {
        "status": status,
        "model_status": verification.verification_status,
        "document_kind": verification.document_kind,
        "deterministic_document_kind": document_kind_from_text(document_text),
        "substance_match": verification.substance_match,
        "recommended_action": verification.recommended_action,
        "confidence": confidence,
        "model_confidence": verification.confidence,
        "confidence_breakdown": confidence_breakdown,
        "reason": verification.reason,
        "gate_reason": gate_reason,
        "accepted_claims": accepted,
        "rejected_claims": rejected,
        "missing_fields": missing_fields,
        "red_flags": red_flags,
        "cas_in_document": sorted(document_cas),
        "expected_cas": normalized_expected or expected_cas,
        "expected_name": expected_name,
        "name_matches": name_matches,
        "manufacturer_match": manufacturer_match,
        "identity_basis": identity_basis,
        "text_status": text_status,
        "synthetic_demo": synthetic_demo,
    }
