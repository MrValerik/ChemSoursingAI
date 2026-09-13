from app.extraction.llm_client import LLMUnavailableError
from app.models.rfq import RFQ
from app.services.escalation_reply import (
    prepare_escalation_reply,
    safe_escalation_reply,
)


class _ReplyLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def generate_text(self, **_kwargs) -> str:
        return self.reply


class _UnavailableLLM:
    def generate_text(self, **_kwargs) -> str:
        raise LLMUnavailableError("offline")


def _rfq() -> RFQ:
    return RFQ(name="Acetaldehyde", cas="75-07-0", volume="500 kg")


def test_prepares_concise_editable_reply_without_supplier_quote() -> None:
    reply = prepare_escalation_reply(
        rfq=_rfq(),
        supplier_text="How are you today?",
        escalation_note="Social question requires human review.",
        conversation_context="Product: Acetaldehyde\nRequested quantity: 500 kg",
        llm=_ReplyLLM(
            "Thank you for asking. I am well. Could you please continue with "
            "the quotation for the requested product?"
        ),
    )

    assert reply.startswith("Thank you for asking")
    assert "How are you today" not in reply


def test_unsafe_model_commitment_is_replaced_with_safe_fallback() -> None:
    rfq = _rfq()
    reply = prepare_escalation_reply(
        rfq=rfq,
        supplier_text="Can you accept our offer?",
        escalation_note="Commercial decision requires a buyer.",
        llm=_ReplyLLM("We accept your offer and will pay tomorrow."),
    )

    assert reply == safe_escalation_reply(rfq)
    assert "accept" not in reply.casefold()


def test_unavailable_model_still_returns_safe_draft() -> None:
    rfq = _rfq()
    reply = prepare_escalation_reply(
        rfq=rfq,
        supplier_text="Please share your company details.",
        escalation_note="Buyer details are not present.",
        llm=_UnavailableLLM(),
    )

    assert reply == safe_escalation_reply(rfq)
    assert "CAS 75-07-0" in reply
