from types import SimpleNamespace

import pytest

from app.services.communication_reply_quality import grounded_reply_issue, reply_focus


def issue(reply, *, context="Product: Caffeine\nCAS: 58-08-2\nQuantity: 200 kg", supplier="", stage="reply"):
    return grounded_reply_issue(context=context, supplier_text=supplier, reply=reply, stage=stage)


@pytest.mark.parametrize("reply", [
    "We can confirm the 200 kg of caffeine.",
    "We accept your offer. Please start production.",
    "We will pay tomorrow.",
    "Please proceed with shipment.",
    "I hereby approve the contract.",
    "We can work with USD 16/kg EXW.",
    "We can work with 30% deposit.",
    "We confirm the price of USD 16/kg.",
])
def test_draft_cannot_make_commercial_commitments(reply):
    assert issue(reply)


@pytest.mark.parametrize("reply", [
    "Our requested quantity is 200 kg. Please clarify the dispatch time.",
    "We confirm receipt of the CoA; it needs internal review.",
    "Could you confirm the lead time for our requested quantity?",
])
def test_nonbinding_requirement_and_receipt_are_allowed(reply):
    assert issue(reply) is None


@pytest.mark.parametrize("reply", [
    "We require 200 L containers.",
    "We prefer 200 L returnable containers.",
    "We would like 20 kg bags.",
    "Please quote the 200 L returnable containers.",
])
def test_supplier_packaging_options_do_not_authorise_buyer_choice(reply):
    assert issue(reply, supplier="We offer 20 kg bags and 200 L containers. Which do you need?")


def test_explicit_operator_packaging_is_allowed_but_different_size_or_kind_is_not():
    context = "Quantity: 400 kg\nPackaging: 20 kg bags"
    assert issue("We require 20 kg bags.", context=context) is None
    assert issue("We require 40 kg bags.", context=context)
    assert issue("We require 20 kg drums.", context=context)
    assert issue("We require 200 L returnable containers.", context="Packaging: 200 L non-returnable containers")
    assert issue("We need 400 kg in 20 kg bags.", context=context) is None
    assert issue("We require 20 kg bags.", context="Фасовка: мешки по 20 кг") is None


def test_only_advance_in_russian_is_also_known_payment():
    assert issue("Please confirm your payment terms.", supplier="Только 100% предоплата T/T.")


def test_priority_hints_use_supplier_evidence_not_buyer_requirements():
    assert "MOQ" in reply_focus("Quantity: 200 kg", "USD 16/kg, 20 kg bags")
    assert "MOQ" not in reply_focus("Quantity: 200 kg", "USD 16/kg, 20 kg bags, MOQ 40 kg")
    assert "internal confirmation" in reply_focus("Quantity: 200 kg", "Which packaging do you need, bags or containers?")
    assert "grade" in reply_focus("Required grade: USP", "Caffeine available")
    assert not reply_focus("Required grade: USP", "Caffeine USP, 20 kg bags, MOQ 40 kg")


def test_missing_buyer_prerequisite_takes_priority_over_supplier_checklist():
    supplier = "Please provide your company legal name and registered address before we quote."
    assert "No quotation request" in reply_focus("Grade: USP", supplier)
    assert issue("Could you confirm the price and MOQ?", supplier=supplier)
    assert issue("We need internal confirmation before sharing our company details.", supplier=supplier) is None
    assert issue("Could you confirm the price and MOQ?", supplier=supplier, context="Company: Example Procurement") is None
    assert issue("Could you quote the price?", supplier="Какая тара вас интересует?")
    assert issue("Could you quote the price?", supplier="Пришлите реквизиты вашей компании.")


def test_explicit_current_deadlines_are_not_requested_again():
    supplier = "Dispatch within 9 working days after payment. Quote valid until 30 September 2026."
    assert issue("Could you confirm the dispatch timeline?", supplier=supplier)
    assert issue("Could you confirm the price validity period?", supplier=supplier)
    assert issue("Could you extend the expired price validity?", supplier=supplier) is None
    assert grounded_reply_issue(context="", supplier_text=supplier, latest_supplier_text="New quote: USD 17/kg.",
                                reply="Could you confirm the dispatch timeline?", stage="reply") is None


def test_a_quote_with_only_pack_size_requires_actual_moq():
    supplier = "USD 16/kg EXW, 20 kg bags."
    assert issue("Could you confirm the lead time?", supplier=supplier)
    assert issue("Could you confirm the MOQ and lead time?", supplier=supplier) is None


def test_payment_only_is_not_asked_again_but_acknowledgement_is_allowed():
    supplier = "Payment: 100% T/T in advance only. Lead time: 9 days."
    assert issue("Is 100% T/T in advance the only payment option?", supplier=supplier)
    assert issue("Please confirm your payment terms.", supplier=supplier)
    assert issue("The advance payment terms are noted. What is the MOQ?", supplier=supplier) is None
    assert issue("Could you offer alternative payment terms?", supplier=supplier, context="Operator asks to negotiate alternative payment terms") is None
    assert issue("Could you confirm the dispatch timeframe once payment is received?", supplier=supplier) is None


def test_later_payment_revision_is_not_blocked_by_old_only_clause():
    supplier = "Payment: 100% T/T in advance only.\nRevised payment terms: 30% deposit, balance before shipment."
    assert issue("Could you clarify when the balance is due?", supplier=supplier) is None
    assert issue("Please confirm these revised payment terms.", supplier=supplier) is None


def test_shortness_gate_is_only_for_followups():
    long_reply = "Please provide the missing information. " * 30
    assert issue(long_reply)
    assert issue(long_reply, stage="initial") is None


@pytest.mark.parametrize("repeat_bad", [False, True])
def test_full_generation_retries_then_fails_closed_without_sending(repeat_bad):
    from app.services.communication_testing import _generate_reply, CommunicationTestError

    class SnapshotDB:
        def commit(self):
            pass

        def get(self, *args):
            return None

        def scalar(self, statement):
            from app.models import PromptTemplate
            if statement.column_descriptions[0]["entity"] is PromptTemplate:
                return SimpleNamespace(system_prompt="Write a safe procurement reply.")
            return SimpleNamespace(system_instructions="Collect missing commercial facts.")

    class Client:
        model = "synthetic"
        calls = []

        def generate_text(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1 or repeat_bad:
                return "We can confirm the 200 kg. Please begin production."
            return "Our requested quantity is 200 kg. Could you provide the lead time?"

    run = SimpleNamespace(
        rfq_id=None, actor_id=None, reply_language="en", channel="email",
        additional_instructions="", procurement_context="Caffeine, CAS 58-08-2, 200 kg",
        messages=[SimpleNamespace(sender_role="supplier", content="USD 15/kg EXW.")],
    )
    client = Client()
    if repeat_bad:
        with pytest.raises(CommunicationTestError, match="дважды"):
            _generate_reply(SnapshotDB(), run=run, user_text="Next reply", stage="reply", llm=client)
    else:
        assert _generate_reply(SnapshotDB(), run=run, user_text="Next reply", stage="reply", llm=client).startswith("Our requested")
    assert len(client.calls) == 2
    assert "КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ" in client.calls[1]["additional_instructions"]
