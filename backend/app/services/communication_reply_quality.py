"""Grounding rules for supplier-facing drafts, independent of the LLM provider."""

from __future__ import annotations

import re

REPLY_POLICY_VERSION = "reply_quality.v5"
REPLY_DISCIPLINE = """
Before writing the next reply, silently check the latest supplier question and
all earlier supplier facts. Reply to that question first; do not restart the RFQ.
Keep the reply under 100 words where possible and ask at most three related
questions. A short acknowledgement needs no repeated checklist or closing line.
The checklist is a menu, not a mandatory list in every turn. Count requested
facts, not question marks: do not hide eight separate questions in one sentence.
If the supplier needs our packaging/address/company details before quoting,
and those are absent, say we need internal confirmation and STOP. Do not demand
the same quotation before providing the prerequisite. If all commercial facts
are supplied, acknowledge internal review and STOP; do not invent new tests,
numerical purity thresholds, batch requirements or questions to prolong the chat.
If documents are attached but their contents are unavailable, review them
internally first; do not ask the supplier to read every field back to us.
Never say 'we confirm/accept the order/offer' or 'we can confirm the 500 kg'. Say
'our requested quantity is ...' instead. Do not say 'we can work with' a price or
payment terms: acknowledge receipt without accepting them. You cannot authorise
an order or payment.
Buyer-owned choices (packaging selection, address, company details, application)
must come from the operator context, not from your preference or an earlier AI
message. If missing, say the choice/details need internal confirmation; do not
select a supplier's packaging option and do not ask the supplier to invent them.
If payment is stated as 100% T/T in advance only, do not ask whether it is the
only option or request payment terms again. Do not negotiate alternatives unless
the operator explicitly requested negotiation. Acknowledge and move to a gap.
Do not request a document already attached; receipt is not verification. Do not
claim a document has passed review unless a verification result was provided.
Preserve the scope of each earlier price: quantity, currency, price unit and
Incoterm. Ask whether an earlier offer still applies when a later quote changes
quantity or delivery basis. 'To your door' does not prove DDP. 'In stock' is not
a dispatch deadline. A bag size is not automatically MOQ. Do not ask for a known
currency again or treat requested grade as supplier-confirmed grade.
For EXW/FCA, do not ask the supplier to choose our destination. If a delivered
price was requested but no destination was provided by the operator, say that
the destination needs internal confirmation; proceed with other missing facts.
End after the useful question/fact. No signature, empty courtesy or repeated RFQ.

Examples of concise continuations (use only when applicable, not as a template):
Supplier needs an unspecified packaging choice before quoting ->
'The packaging choice needs internal confirmation. We will get back to you with it.'
Supplier needs unknown company details ->
'We need to confirm our company details internally before sharing them.'
Complete quote and attached documents ->
'Thank you for the quotation and documents. We will review them internally.'
Everything supplied except dispatch deadline and validity ->
'Could you confirm when the goods would be ready for collection and how long the price is valid?'
Requested quantity is unavailable and the supplier did not state an alternative ->
'What is the maximum quantity you can supply, and what unit price and currency apply to it?'
Buyer specified packaging and supplier asks which packaging -> give the specified
packaging and ask only price/currency, Incoterm and MOQ now. Other gaps can wait.
""".strip()

_COMMITMENT = re.compile(
    r"\b(?:we|i)\s+(?:(?:can|hereby)\s+)?(?:confirm|accept|approve|place)\s+"
    r"(?:(?:the|this|your|our|an?)\s+)?(?:order|purchase|offer|quotation|quote|contract|price|"
    r"\d[\d.,]*\s*(?:kg|mt|tons?|tonnes?|litres?|liters?)\b)"
    r"|\b(?:we|i)\s+(?:will|shall|agree\s+to)\s+(?:pay|purchase|order|place)\b"
    r"|\bplease\s+(?:proceed\s+with|start|begin)\s+(?:the\s+)?(?:production|shipment)\b"
    r"|\bwe\s+can\s+work\s+with\s+(?:the\s+)?(?:\d|(?:USD|CNY|EUR|price|payment|terms)\b)",
    re.IGNORECASE,
)
_PACKAGING = re.compile(r"\b(?:bags?|drums?|containers?|ibcs?|canisters?|bottles?)\b|упаков|фасов|бочк", re.I)
_PACKAGING_CHOICE = re.compile(
    r"(?:\b(?:we|i)\s+(?:(?:would|will)\s+)?(?:require|need|want|prefer|choose|select|use|like)\b"
    r"|\bplease\s+(?:quote|use|provide)\s+(?:for\s+)?(?:the\s+)?\d)"
    r"[^.!?\n]{0,140}\b(?:bags?|drums?|containers?|ibcs?|canisters?|bottles?)\b", re.I
)
_CAPACITY = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:kg|кг|l|л|litres?|liters?)\b", re.I)
_PAYMENT_QUESTION = re.compile(
    r"\b(?:confirm|clarify|advise|provide|share|specify|what|is|are|could|can)\b"
    r"[^.!?\n]{0,160}\b(?:payment\s+(?:terms|options?|methods?)|T\s*/\s*T|advance)\b", re.I
)
_ONLY_ADVANCE = re.compile(
    r"\b100\s*%\s*(?:T\s*/\s*T\s*)?(?:in\s+)?advance\s+only\b"
    r"|\bonly\s+(?:accept\s+)?100\s*%\s*(?:T\s*/\s*T\s*)?(?:in\s+)?advance\b"
    r"|только\s+100\s*%\s*(?:предоплат|аванс)", re.I
)
_PAYMENT_DECLARATION = re.compile(r"\bpayment(?:\s+terms)?\s*(?::|is\b|are\b)|\b\d+\s*%\s*(?:T\s*/\s*T|deposit|advance)|\b(?:net\s+\d+|T\s*/\s*T)\b|предоплат|условия\s+оплат", re.I)
_GRADE_CODE_RE = re.compile(r"\b(?:USP|BP|EP|FCC|ACS|HPLC)\b", re.I)
_REQUESTED_QUANTITY_RE = re.compile(
    r"(?:quantity|qty|количество|об[ъь]?[её]м)\s*:\s*"
    r"(\d+(?:[.,]\d+)?\s*(?:kg|кг|mt|tons?|tonnes?|l|л|litres?|liters?))\b",
    re.I,
)
_REQUESTED_INCOTERM_RE = re.compile(
    r"(?:requested\s+incoterms?|incoterms?|запрошенн\w*\s+инкотермс|инкотермс)\s*:\s*([A-Z]{3})\b",
    re.I,
)
_PRICE_AMOUNT_RE = re.compile(
    r"\b(?:USD|CNY|EUR|RMB)\s*(\d+(?:[.,]\d+)?)"
    r"|\b(\d+(?:[.,]\d+)?)\s*(?:USD|CNY|EUR|RMB)\b",
    re.I,
)
_INCOTERM_RE = re.compile(
    r"\b(?:EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DAP|DPU|DDP)\b",
    re.I,
)
_MOQ_RE = re.compile(r"\bMOQ\b|minimum\s+order|минимальн\w*\s+(?:заказ|парти)", re.I)
_LEAD_TIME_RE = re.compile(
    r"\blead\s+time\s*(?::|is\b|=)|\bdispatch\s+within\b|"
    r"\b(?:goods?|order|production|shipment)\b[^.!?\n]{0,80}"
    r"\b(?:ready|produced|shipped|dispatched)\b[^.!?\n]{0,40}\b\d+\s*"
    r"(?:working\s+|business\s+)?(?:days?|weeks?)\b|"
    r"(?:срок\s+(?:производства|поставки|отгрузки)|готов\w*\s+к\s+отгрузке)"
    r"[^.!?\n]{0,50}\d+\s*(?:дн|недел)",
    re.I,
)
_SUPPLIED_DOCUMENT_RE = re.compile(
    r"(?:\b(?:CoA|certificate\s+of\s+analysis|TDS|technical\s+data\s+sheet)\b"
    r"[^.!?\n]{0,80}\b(?:attached|enclosed|sent|provided|included)\b|"
    r"\b(?:attached|enclosed|sent|provided|included)\b[^.!?\n]{0,80}"
    r"\b(?:CoA|certificate\s+of\s+analysis|TDS|technical\s+data\s+sheet)\b|"
    r"\b(?:CoA|TDS)\b[^.!?\n]{0,50}(?:прилож|отправ|предостав))",
    re.I,
)
_CAPACITY_LIMITATION_RE = re.compile(
    r"(?:\b(?:cannot|can['’]?t|unable\s+to|not\s+able\s+to)\b"
    r"[^.!?\n]{0,100}\b(?:supply|offer|provide|produce|requested\s+quantity)\b|"
    r"\brequested\s+quantity\b[^.!?\n]{0,80}\b(?:unavailable|not\s+available)\b|"
    r"\bonly\s+\d+(?:[.,]\d+)?\s*(?:kg|mt|tons?|tonnes?|l|litres?|liters?)\b"
    r"[^.!?\n]{0,50}\b(?:available|possible|can\s+be\s+supplied)\b|"
    r"(?:не\s+(?:можем|готовы)\s+(?:поставить|предложить)|"
    r"запрошенн\w*\s+(?:об[ъь]?[её]м|количеств\w*)\s+не\s+доступ))",
    re.I,
)
_MAX_AVAILABLE_RE = re.compile(
    r"(?:\b(?:maximum|max\.?|up\s+to|only)\s+\d+(?:[.,]\d+)?\s*"
    r"(?:kg|mt|tons?|tonnes?|l|litres?|liters?)\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:kg|mt|tons?|tonnes?|l|litres?|liters?)\b"
    r"[^.!?\n]{0,50}\b(?:available|maximum|can\s+(?:supply|offer|provide))\b|"
    r"(?:максим\w*|только|до)\s+\d+(?:[.,]\d+)?\s*(?:кг|л|тонн))",
    re.I,
)
_MAX_QUANTITY_REQUEST_RE = re.compile(
    r"\b(?:maximum|max(?:imum)?\s+available|available)\s+quantity\b|"
    r"\b(?:maximum|max\.?)\b[^.!?\n]{0,45}\b(?:kg|mt|quantity|volume|supply)\b|"
    r"\bquantity\b[^.!?\n]{0,60}\b(?:can|could|are\s+able\s+to)\s+"
    r"(?:supply|offer|provide)\b",
    re.I,
)
_PRICE_REQUEST_RE = re.compile(r"\b(?:unit\s+price|price|quotation|quote)\b", re.I)
_CURRENCY_REQUEST_RE = re.compile(
    r"\b(?:currency|USD|CNY|EUR|RMB|GBP|RUB)\b", re.I
)


def _affirmed_grade_codes(text: str) -> set[str]:
    result: set[str] = set()
    for match in _GRADE_CODE_RE.finditer(text):
        before = text[max(0, match.start() - 60):match.start()]
        after = text[match.end():match.end() + 35]
        if re.search(
            r"(?:\bnot\b|cannot|can['’]?t|does\s+not|do\s+not|"
            r"unable\s+to|не)\s+(?:(?:meet|supply|offer|provide|"
            r"соответств\w*|постав\w*|предлаг\w*)\s+)?(?:the\s+)?$",
            before,
            re.I,
        ) or re.match(
            r"\s*(?:is\s+)?(?:not\s+available|unavailable|не\s+доступ)",
            after,
            re.I,
        ):
            continue
        result.add(match.group().upper())
    return result


def _missing_required_grades(context: str, supplier_text: str) -> set[str]:
    required = _affirmed_grade_codes(context)
    confirmed = _affirmed_grade_codes(supplier_text)
    return required - confirmed


def _earlier_scoped_price(
    context: str,
    supplier_text: str,
    latest_supplier_text: str,
) -> str | None:
    """Return the earlier requested-scope price when the latest quote changes scope."""
    if not latest_supplier_text:
        return None
    latest_at = supplier_text.rfind(latest_supplier_text)
    if latest_at <= 0:
        return None
    earlier = supplier_text[:latest_at].strip()
    requested_match = _REQUESTED_QUANTITY_RE.search(context)
    incoterm_match = _REQUESTED_INCOTERM_RE.search(context)
    if requested_match is None or incoterm_match is None:
        return None
    requested_quantity = next(iter(_capacities(requested_match.group(1))), "")
    if not requested_quantity or requested_quantity not in _capacities(earlier):
        return None
    latest_capacities = _capacities(latest_supplier_text)
    if not latest_capacities or requested_quantity in latest_capacities:
        return None
    incoterm = incoterm_match.group(1)
    scoped_prices = [
        match
        for match in _PRICE_AMOUNT_RE.finditer(earlier)
        if re.search(rf"\b{re.escape(incoterm)}\b", earlier[match.end():match.end() + 24], re.I)
    ]
    latest_prices = list(_PRICE_AMOUNT_RE.finditer(latest_supplier_text))
    if not scoped_prices or not latest_prices:
        return None
    earlier_price = next(group for group in scoped_prices[-1].groups() if group)
    latest_values = {next(group for group in match.groups() if group) for match in latest_prices}
    return earlier_price if earlier_price not in latest_values else None


def _capacities(value: str) -> set[str]:
    return {re.sub(r"\s+", "", re.sub(r"litres?|liters?|л", "l", v.casefold()).replace("кг", "kg"))
            for v in _CAPACITY.findall(value)}


def _packaging_types(value: str) -> set[str]:
    kinds = {match.group().casefold().rstrip("s") for match in re.finditer(
        r"\b(?:bags?|drums?|containers?|ibcs?|canisters?|bottles?)\b", value, re.I
    )}
    for pattern, kind in [(r"мешк", "bag"), (r"бочк", "drum"), (r"контейнер", "container"),
                          (r"канистр", "canister"), (r"бутыл", "bottle")]:
        if re.search(pattern, value, re.I):
            kinds.add(kind)
    return kinds


def _chosen_packaging_capacities(value: str) -> set[str]:
    # Total requested quantity in the same sentence is not a package size.
    return _capacities(" ".join(match.group() for match in re.finditer(
        _CAPACITY.pattern + r"\s+(?:(?:non[- ]?)?returnable\s+)?(?:bags?|drums?|containers?|ibcs?|canisters?|bottles?)\b",
        value, re.I,
    )))


def _buyer_blocker(context: str, supplier_text: str) -> str | None:
    if (re.search(r"your\s+company|company\s+legal\s+name|registered\s+address|contact\s+person|реквизит|название\s+(?:вашей\s+)?компании", supplier_text, re.I)
            and re.search(r"provide|share|send|need|пришл|укаж|сообщ|предостав", supplier_text, re.I)
            and not re.search(r"(?:company|address|contact person|компани[яи]|адрес|контактное лицо)\s*:", context, re.I)):
        return "company details"
    if (re.search(r"which\s+packaging|packaging.*(?:choice|choose|selection).*before\s+quot|какая.*(?:упаков|тара)|какой.*(?:тар|упаков)|интересу.*тар|выбер.*упаков", supplier_text, re.I)
            and not _PACKAGING.search(context)):
        return "packaging choice"
    return None


def _needs_moq(supplier_text: str) -> bool:
    has_price = bool(re.search(r"(?:USD|CNY|EUR|RMB|\$)\s*\d|\d\s*(?:USD|CNY|EUR|RMB)", supplier_text, re.I))
    return bool(has_price and _PACKAGING.search(supplier_text)
                and not re.search(r"\bMOQ\b|minimum\s+order|минимальн.*(?:заказ|парти)", supplier_text, re.I))


def _supplier_offer_complete(supplier_text: str) -> bool:
    """Require literal evidence for every field that closes an RFQ dialogue."""

    has_grade = bool(
        _affirmed_grade_codes(supplier_text)
        or re.search(
            r"\b(?:grade|purity)\s*(?::|is\b|=)\s*(?!not\b|unavailable\b)"
            r"[^.!?\n]{1,50}|\b\d+(?:[.,]\d+)?\s*%\s*(?:purity|pure)\b|"
            r"(?:грейд|чистот\w*)\s*(?::|—|-|составляет)",
            supplier_text,
            re.I,
        )
    )
    return all(
        (
            _PRICE_AMOUNT_RE.search(supplier_text),
            _INCOTERM_RE.search(supplier_text),
            _MOQ_RE.search(supplier_text),
            has_grade,
            _PAYMENT_DECLARATION.search(supplier_text),
            _LEAD_TIME_RE.search(supplier_text),
            _SUPPLIED_DOCUMENT_RE.search(supplier_text),
        )
    )


def _capacity_reply_issue(latest_supplier_text: str, reply: str) -> str | None:
    """Keep capacity shortfalls actionable instead of acknowledging them vaguely."""

    if not _CAPACITY_LIMITATION_RE.search(latest_supplier_text):
        return None
    if not _MAX_AVAILABLE_RE.search(latest_supplier_text) and not _MAX_QUANTITY_REQUEST_RE.search(reply):
        return (
            "Запрошенный объём недоступен, но поставщик не назвал доступный "
            "максимум. Явно спроси максимальное количество, которое он может поставить."
        )
    if not _PRICE_AMOUNT_RE.search(latest_supplier_text):
        if not (_PRICE_REQUEST_RE.search(reply) and _CURRENCY_REQUEST_RE.search(reply)):
            return (
                "Для доступного объёма не названа цена. Запроси соответствующую "
                "цену за единицу и валюту в той же короткой реплике."
            )
    return None


def safe_reply_fallback(
    supplier_text: str,
    latest_supplier_text: str | None = None,
) -> str | None:
    """Return a narrow deterministic reply only for fully understood safe states."""

    if _supplier_offer_complete(supplier_text):
        return (
            "Thank you for the quotation and documents. "
            "We will review them internally."
        )
    latest = supplier_text if latest_supplier_text is None else latest_supplier_text
    if not _CAPACITY_LIMITATION_RE.search(latest):
        return None
    requests = []
    if not _MAX_AVAILABLE_RE.search(latest):
        requests.append("the maximum quantity you can supply")
    if not _PRICE_AMOUNT_RE.search(latest):
        requests.append("the corresponding unit price and currency")
    if not requests:
        return None
    return "Could you please confirm " + " and ".join(requests) + "?"


def reply_focus(context: str, supplier_text: str, latest_supplier_text: str | None = None) -> str:
    """Hints from literal evidence only, not invented commercial values."""
    blocker = _buyer_blocker(context, supplier_text if latest_supplier_text is None else latest_supplier_text)
    if blocker:
        return f"PRIORITY: supplier awaits our {blocker}, which the operator has not provided. Reply only that internal confirmation is needed. No quotation request or checklist until this prerequisite is resolved."
    latest = supplier_text if latest_supplier_text is None else latest_supplier_text
    if _supplier_offer_complete(supplier_text):
        return (
            "PRIORITY: the quotation is complete. Reply in no more than 45 words, "
            "ask no questions, do not recap its fields, and say only that the "
            "quotation and documents will be reviewed internally."
        )
    if _CAPACITY_LIMITATION_RE.search(latest):
        missing_requests = []
        if not _MAX_AVAILABLE_RE.search(latest):
            missing_requests.append("the maximum quantity the supplier can supply")
        if not _PRICE_AMOUNT_RE.search(latest):
            missing_requests.append("the corresponding unit price and currency")
        if missing_requests:
            return (
                "PRIORITY: the requested quantity is unavailable. Ask only for "
                + " and ".join(missing_requests)
                + ". Do not merely acknowledge the limitation and do not add the remaining checklist now."
            )
    hints = []
    earlier_price = _earlier_scoped_price(
        context,
        supplier_text,
        supplier_text if latest_supplier_text is None else latest_supplier_text,
    )
    if earlier_price:
        hints.append(
            "PRIORITY: the latest price is for a different quantity than our request. "
            f"Explicitly cite the earlier {earlier_price} price for the requested quantity "
            "and Incoterm, then ask whether that earlier offer still applies. Do not ask "
            "the supplier to quote the already-known requested scope from scratch."
        )
    required_grades = _missing_required_grades(context, supplier_text)
    if required_grades:
        hints.append("PRIORITY: our requested grade is not supplier-confirmed. Ask if the offered product meets it before lower-priority timing/validity questions.")
    if _needs_moq(supplier_text):
        hints.append("PRIORITY: package size is present but no explicit MOQ was found. Ask the actual minimum order quantity now, together with at most two related gaps.")
    return "\n".join(hints)


def grounded_reply_issue(*, context: str, supplier_text: str, reply: str, stage: str,
                         latest_supplier_text: str | None = None) -> str | None:
    """Reject specific unsupported actions; never substitute invented facts."""
    if _COMMITMENT.search(reply):
        return "Нельзя подтверждать заказ, предложение, оплату или запуск отгрузки; можно только уточнять потребность."
    for choice in _PACKAGING_CHOICE.finditer(reply):
        packaging_context = "\n".join(line for line in context.splitlines() if _PACKAGING.search(line))
        if (not packaging_context
            or not _chosen_packaging_capacities(choice.group()).issubset(_capacities(packaging_context))
            or not _packaging_types(choice.group()).issubset(_packaging_types(packaging_context))):
            return "Тару выбирает покупатель: вариант и ёмкость не заданы оператором; сообщи о внутреннем уточнении, не выбирай сам."
        # Specifying a returnable option cannot follow from a generic container.
        returnability = re.search(r"\b(?:non[- ]?)?returnable\b", choice.group(), re.I)
        if returnability and returnability.group().casefold() not in packaging_context.casefold():
            return "Возвратность тары не выбрана оператором."
        if returnability and returnability.group().casefold() == "returnable" and re.search(r"\bnon[- ]?returnable\b", packaging_context, re.I):
            return "Оператор указал невозвратную тару; нельзя заменить её возвратной."
    if stage != "reply":
        return None
    if len(re.findall(r"\b[\w'-]+\b", reply)) > 130 or reply.count("?") > 3:
        return "Реплика слишком длинная: ответь на последний вопрос и задай не более трёх связанных вопросов без повторения всего RFQ."
    latest = supplier_text if latest_supplier_text is None else latest_supplier_text
    if _supplier_offer_complete(supplier_text):
        if len(re.findall(r"\b[\w'-]+\b", reply)) > 45:
            return (
                "Котировка уже полная. Не пересказывай её: поблагодари и кратко "
                "сообщи только о внутренней проверке, не более 45 слов."
            )
        if "?" in reply or re.search(
            r"\b(?:please|could|would|can)\s+(?:you\s+)?"
            r"(?:confirm|clarify|provide|share|send|advise|quote)\b",
            reply,
            re.I,
        ):
            return (
                "Все обязательные условия уже получены. Не задавай новых или "
                "повторных вопросов; сообщи только о внутренней проверке."
            )
        if not re.search(r"\b(?:internal|internally|review)\b", reply, re.I):
            return (
                "После полной котировки нужно кратко сообщить, что предложение "
                "и документы будут проверены внутри компании."
            )
        return None
    capacity_issue = _capacity_reply_issue(latest, reply)
    if capacity_issue:
        return capacity_issue
    if _buyer_blocker(context, latest) and ("?" in reply or re.search(r"\b(?:please|could|can)\s+(?:you\s+)?(?:provide|quote|confirm|send)", reply, re.I)):
        return "Поставщик ждёт данные покупателя для котировки. Сначала внутреннее уточнение, без повторного запроса цены и анкеты."
    if (_needs_moq(supplier_text) and not _buyer_blocker(context, latest)
            and not re.search(r"\bMOQ\b|minimum\s+order", reply, re.I)):
        return "Известен размер упаковки, но не MOQ. Спроси минимальное количество заказа, не считай размер мешка MOQ и не пропускай этот пробел."
    missing_grades = _missing_required_grades(context, supplier_text)
    if (
        missing_grades
        and not _buyer_blocker(context, latest)
        and not (
            any(
                re.search(rf"\b{re.escape(grade)}\b", reply, re.I)
                for grade in missing_grades
            )
            or re.search(r"\b(?:grade|specification)\b|грейд|стандарт", reply, re.I)
        )
    ):
        return (
            "Требуемый грейд не подтверждён поставщиком. Спроси о нём до "
            "вопросов о сроке действия цены и других менее важных пробелах."
        )
    earlier_price = _earlier_scoped_price(context, supplier_text, latest)
    if (
        earlier_price
        and not re.search(rf"\b{re.escape(earlier_price)}\b", reply)
        and not re.search(r"\b(?:earlier|previous|original|still)\b", reply, re.I)
    ):
        return (
            "Последняя цена относится к другому объёму. Укажи ранее полученную "
            f"цену {earlier_price} для запрошенного объёма и Incoterm и спроси, "
            "остаётся ли это прежнее предложение в силе; не запрашивай цену заново."
        )
    # Only current-message terms: an old deadline must not close a revised quote.
    questions = " ".join(sentence for sentence in re.split(r"[.!\n]+", reply)
                         if re.search(r"\b(?:could|can|please|what|how|is|does)\b", sentence, re.I))
    if re.search(r"\bdispatch\s+(?:within\s+)?\d+(?:[–-]\d+)?\s+(?:working\s+|business\s+)?days?\s+after\s+payment\b", latest, re.I):
        if re.search(r"\b(?:dispatch\s+(?:date|time(?:line)?|timing|lead\s+time)|lead\s+time\s+for\s+dispatch)\b", questions, re.I):
            return "В последнем сообщении уже указан срок отгрузки после оплаты. Не переспрашивай его; если иных пробелов нет, сообщи о внутренней проверке."
    if re.search(r"\b(?:quote|offer|price)\s+(?:is\s+)?valid\s+(?:until|through)\s+\d", latest, re.I):
        if (re.search(r"\b(?:validity|how\s+long[^.?]{0,40}(?:price|valid)|price[^.?]{0,25}(?:valid|expire))\b", questions, re.I)
                and not re.search(r"\b(?:renew|extend|expired|updated|still\s+valid)\b", questions, re.I)):
            return "Срок действия цены уже назван в последнем сообщении. Не спрашивай его повторно; истёкшую цену можно явно попросить продлить."
    payment_statements = [sentence for sentence in re.split(r"[.!?\n]+", supplier_text)
                          if _PAYMENT_DECLARATION.search(sentence)]
    if (
        payment_statements and _ONLY_ADVANCE.search(payment_statements[-1])
        and _PAYMENT_QUESTION.search(reply)
        and not re.search(r"negotiat|alternative\s+payment|переговор|альтернатив.*оплат", context, re.I)
    ):
        return "Поставщик уже указал 100% T/T в аванс как единственный вариант; не переспрашивай оплату и не предлагай переговоры без поручения."
    return None
