"""Synthetic RFQ, profile, queue and pre-submit failures. No external calls."""
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from app.api.echemi_outreach import router
from app.models import EchemiSearch, EchemiOutreach, User, RFQ
from app.models.enums import UserRole
from app.schemas.echemi_sender import EchemiSenderUpdate
from app.services.echemi_sender import update_sender
from app.services import echemi_delivery
from app.services.integration_settings import _decrypt, save_setting
from tests.test_echemi_search import env
from tests.test_echemi_rfq import rfq
from tests.test_echemi_sender import PROFILE

URL = "https://www.echemi.com/produce/pr123-aspirin.html"


@pytest.fixture
def prepared(env):
    client, user, sessions = env
    client.app.include_router(router)
    with sessions() as db:
        db.add(User(id=42, username="synthetic", full_name="Demo Buyer", password_hash="unused", role=UserRole.BUYER))
        row = rfq(db)
        search = EchemiSearch(rfq_id=row.id, author_id=42, query="50-78-2", status="completed",
            results=[{"product_url": URL, "seller_name": "Demo Chemicals", "cas_numbers": ["50-78-2"]}])
        db.add(search)
        db.commit()
        save_setting(db, channel="email", enabled=False,
                     payload={"email_from": "office@example.test"}, actor_id=42)
        update_sender(db, EchemiSenderUpdate(**PROFILE, city="Boston"), 42, personal=True)
        rid, sid = row.id, search.id
    path = f"/rfq/{rid}/echemi-outreach"
    preview = client.get(path + "/preview")
    assert preview.status_code == 200
    payload = {"search_id": sid, "product_urls": [URL], "message": preview.json()["message"],
               "confirmed": True, "sender_version": preview.json()["sender_version"]}
    return client, user, sessions, path, payload, rid


def test_snapshot_encrypted_and_double_click_deduplicated(prepared, monkeypatch):
    client, _, sessions, path, payload, _ = prepared
    first = client.post(path, json=payload)
    assert first.status_code == 202
    assert client.post(path, json=payload).json()[0]["id"] == first.json()[0]["id"]
    with sessions() as db:
        row = db.scalar(select(EchemiOutreach))
        assert PROFILE["email"] not in row.encrypted_payload
        assert _decrypt(row.encrypted_payload)["sender"]["city"] == "Boston"
        from app.services.integration_settings import effective_email_settings
        assert _decrypt(row.encrypted_payload)["sender"]["email"] == effective_email_settings(db)[0].email_from
    calls = []
    def send(job, url, data):
        with sessions() as db:
            assert db.get(EchemiOutreach, job).status == "sending"
        calls.append(url)
        return {"status": "sent", "message": "Confirmed by synthetic form"}
    monkeypatch.setattr(echemi_delivery, "submit_inquiry", send)
    assert echemi_delivery.run_one(sessions)
    assert not echemi_delivery.run_one(sessions)
    assert calls == [URL]
    assert client.get(path).json()[0]["status"] == "sent"
    assert client.post(path, json=payload).json()[0]["status"] == "sent"


@pytest.mark.parametrize("change,status", [
    ({"confirmed": False}, 422), ({"confirmed": "true"}, 422),
    ({"message": " " * 25}, 422), ({"message": "x" * 5001}, 422),
    ({"product_urls": []}, 422), ({"product_urls": ["https://evil.test/"]}, 422),
    ({"search_id": 9999}, 404), ({"sender_version": "0" * 64}, 409),
])
def test_validation_never_enqueues(prepared, change, status):
    client, _, sessions, path, payload, _ = prepared
    assert client.post(path, json={**payload, **change}).status_code == status
    with sessions() as db:
        assert db.scalar(select(EchemiOutreach)) is None


@pytest.mark.parametrize("role", [UserRole.HEAD, UserRole.AUDITOR, UserRole.GUEST])
def test_roles_cannot_send(prepared, role):
    client, user, _, path, payload, _ = prepared
    user.role = role
    assert client.post(path, json=payload).status_code == 403
    assert client.get(path + "/preview").status_code == 403


def test_cross_buyer_and_deleted_rfq(prepared):
    client, user, _, path, payload, _ = prepared
    user.id = 43
    assert client.get(path).status_code == 404
    assert client.post(path, json=payload).status_code == 404


@pytest.mark.parametrize("status", ["queued", "running", "blocked", "failed"])
def test_unfinished_search_rejected(prepared, status):
    client, _, sessions, path, payload, _ = prepared
    with sessions() as db:
        db.get(EchemiSearch, payload["search_id"]).status = status
        db.commit()
    assert client.post(path, json=payload).status_code == 409


def test_cas_mismatch_rejected(prepared):
    client, _, sessions, path, payload, _ = prepared
    with sessions() as db:
        row = db.get(EchemiSearch, payload["search_id"])
        row.results = [{**row.results[0], "cas_numbers": ["107-43-7"]}]
        db.commit()
    assert client.post(path, json=payload).status_code == 422


def test_unknown_transport_result_and_restart_never_retry(prepared, monkeypatch):
    client, _, sessions, path, payload, _ = prepared
    client.post(path, json=payload)
    def fail(*args):
        raise TimeoutError("private email and network error")
    monkeypatch.setattr(echemi_delivery, "submit_inquiry", fail)
    assert echemi_delivery.run_one(sessions)
    assert client.get(path).json()[0]["status"] == "unknown"
    assert "private" not in client.get(path).text
    assert client.post(path, json=payload).json()[0]["status"] == "unknown"
    with sessions() as db:
        row = db.scalar(select(EchemiOutreach))
        row.status = "sending"
        db.commit()
        echemi_delivery.recover(db)
    assert not echemi_delivery.run_one(sessions)


def test_access_revoked_after_queue_blocks_delivery(prepared, monkeypatch):
    client, _, sessions, path, payload, rid = prepared
    client.post(path, json=payload)
    with sessions() as db:
        db.get(RFQ, rid).owner_id = 43
        db.commit()
    monkeypatch.setattr(echemi_delivery, "submit_inquiry", lambda *a: pytest.fail("Must not send"))
    assert echemi_delivery.run_one(sessions)
    assert client.get(path).status_code == 404
    with sessions() as db:
        assert db.scalar(select(EchemiOutreach)).status == "blocked"


def test_confirmed_retry_preserves_blocked_attempt_audit(prepared, monkeypatch):
    client, _, sessions, path, payload, _ = prepared
    client.post(path, json=payload)
    monkeypatch.setattr(echemi_delivery, "submit_inquiry",
                        lambda *a: {"status": "blocked", "message": "Missing field"})
    echemi_delivery.run_one(sessions)
    assert client.post(path, json=payload).json()[0]["status"] == "queued"
    with sessions() as db:
        attempts = db.scalar(select(EchemiOutreach)).attempts
        assert len(attempts) == 1 and attempts[0]["message"] == "Missing field"
        assert PROFILE["email"] not in attempts[0]["encrypted_payload"]


@pytest.mark.parametrize("status,result", [("sent", "sent"), ("unknown", "unknown"), ("blocked", "blocked"), ("invalid", None)])
def test_connector_validates_status_and_hides_raw_response(monkeypatch, status, result):
    import httpx
    from types import SimpleNamespace
    from app.connectors import echemi
    original = httpx.Client
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, json={"status": status, "message": "private upstream content"})
    monkeypatch.setattr(echemi, "get_settings", lambda: SimpleNamespace(echemi_browser_url="http://synthetic", echemi_captcha_auto_attempts=2))
    monkeypatch.setattr(echemi.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    payload = {"sender": PROFILE, "message": "Please quote synthetic product.", "seller_name": "Demo Chemicals"}
    if result:
        response = echemi.submit_inquiry(1, URL, payload)
        assert response["status"] == result and "private" not in response["message"]
    else:
        with pytest.raises(ValueError):
            echemi.submit_inquiry(1, URL, payload)
    assert len(captured) == 1


@pytest.fixture
def browser_form(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "echemi-browser"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("synthetic_inquiry", directory / "inquiry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def field(name, index=0, **kwargs):
    return {"index": index, "hints": [name], "type": "text", "required": True, **kwargs}


def test_form_mapping_and_unknown_required_fields(browser_form):
    fields = [field("Email"), field("Message", 1), field("City", 2)]
    plan, reason = browser_form.plan_fields(fields, {**PROFILE, "city": "Boston"}, "Please quote this product")
    assert reason is None and plan[2][2] == "Boston"
    assert browser_form.plan_fields(fields, PROFILE, "Please quote")[1] == "missing_fields"
    assert browser_form.plan_fields(fields + [field("Tax ID", 3)], PROFILE, "Please quote")[0] is None
    assert browser_form.plan_fields([field("Email"), field("Message", max_length=3)], PROFILE, "Too long")[1] == "unsupported_fields"
    assert browser_form.field_key({"hints": ["name", "company"]}) is None
    assert browser_form.plan_fields(fields + [field("terms", type="checkbox")], PROFILE, "Please quote")[0] is None


def test_captcha_stops_before_fill(browser_form, monkeypatch):
    page = AsyncMock()
    monkeypatch.setattr(browser_form, "needs_verification", AsyncMock(return_value=True))
    result = asyncio.run(browser_form.submit(page, URL, PROFILE, "Please quote this product"))
    assert result == {"status": "blocked", "reason": "verification_required"}
    page.evaluate.assert_not_called()


@pytest.mark.parametrize("confirmed", [True, False])
def test_browser_fills_profile_and_requires_new_success_confirmation(browser_form, monkeypatch, confirmed):
    from unittest.mock import Mock
    page = Mock()
    page.url = URL
    page.goto = AsyncMock()
    fields = [field("Email"), field("Message", 1), field("City", 2)]
    page.evaluate = AsyncMock(return_value=fields)
    controls = {str(i): AsyncMock() for i in range(3)}
    submit = AsyncMock()
    root = AsyncMock()
    root.evaluate.return_value = True
    alert = AsyncMock()
    alert.inner_text.return_value = "Your inquiry has been sent successfully."
    alert.is_visible.return_value = True
    alerts = AsyncMock()
    alerts.all.side_effect = [[], [alert]] if confirmed else [[]] * 16
    page.get_by_text.return_value = alerts
    seller = AsyncMock()
    seller.is_visible.return_value = True
    seller.get_attribute.return_value = "/shop-us123/index.html"
    sellers = AsyncMock()
    sellers.all.return_value = [seller]
    page.get_by_role.return_value = sellers
    def locator(selector):
        if "submit" in selector:
            return submit
        if "inquiry" in selector:
            return root
        return controls[selector.split('"')[1]]
    page.locator.side_effect = locator
    monkeypatch.setattr(browser_form, "needs_verification", AsyncMock(return_value=False))
    monkeypatch.setattr(browser_form.asyncio, "sleep", AsyncMock())
    message = "Please quote this synthetic product."
    verify = AsyncMock(return_value=None)
    result = asyncio.run(browser_form.submit(page, URL, {**PROFILE, "city": "Boston"}, message, seller_name="Demo Chemicals", verify=verify))
    assert verify.await_count == 2
    assert result["status"] == ("sent" if confirmed else "unknown")
    controls["0"].fill.assert_awaited_once_with(PROFILE["email"])
    controls["1"].fill.assert_awaited_once_with(message)
    controls["2"].fill.assert_awaited_once_with("Boston")
    submit.click.assert_awaited_once()


def test_decryption_failure_blocks_before_network(prepared, monkeypatch):
    client, _, sessions, path, payload, _ = prepared
    client.post(path, json=payload)
    from app.services.integration_settings import IntegrationSettingsError
    def fail_decrypt(value):
        raise IntegrationSettingsError("private encryption details")
    monkeypatch.setattr(echemi_delivery, "_decrypt", fail_decrypt)
    monkeypatch.setattr(echemi_delivery, "submit_inquiry", lambda *a: pytest.fail("Must not send"))
    assert echemi_delivery.run_one(sessions)
    result = client.get(path)
    assert result.json()[0]["status"] == "blocked"
    assert "private" not in result.text
    assert not echemi_delivery.run_one(sessions)
    assert client.post(path, json=payload).json()[0]["status"] == "queued"


def test_inquiry_uses_verification_callback_before_form(browser_form):
    page = AsyncMock()
    verify = AsyncMock(return_value="captcha_network_error")
    result = asyncio.run(browser_form.submit(page, URL, PROFILE, "Please quote this product", verify=verify))
    assert result == {"status": "blocked", "reason": "captcha_network_error"}
    verify.assert_awaited_once_with("navigation")
    page.evaluate.assert_not_called()


def test_inquiry_navigation_exception_is_not_missing_form(browser_form):
    page = AsyncMock()
    page.goto.side_effect = TimeoutError("private url and token")
    result = asyncio.run(browser_form.submit(page, URL, PROFILE, "Please quote this product"))
    assert result == {"status": "blocked", "reason": "page_load_failed"}


@pytest.fixture
def inquiry_verification(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "echemi-browser"))
    import inquiry_verification
    return inquiry_verification


@pytest.mark.parametrize("url,action,expected", [
    ("https://synthetic.captcha-open.aliyuncs.com/", "InitCaptchaV2", True),
    ("https://synthetic.captcha-open-southeast.aliyuncs.com/", "VerifyCaptcha", True),
    ("https://upload.captcha-open.aliyuncs.com/", "Upload", True),
    ("https://synthetic.captcha-open.aliyuncs.com.evil.test/", "VerifyCaptcha", False),
    ("http://synthetic.captcha-open.aliyuncs.com/", "VerifyCaptcha", False),
    ("https://evil.test/", "VerifyCaptcha", False),
    ("https://www.echemi.com/inquiry", "Send", True),
])
def test_inquiry_network_allowlist(inquiry_verification, url, action, expected):
    from types import SimpleNamespace
    request = SimpleNamespace(url=url, method="POST", post_data="Action=" + action)
    assert inquiry_verification.allowed_request(request) is expected


@pytest.mark.parametrize("passed,network,rejected,expected", [
    (True, False, False, None),
    (False, True, False, "captcha_network_error"),
    (False, False, True, "captcha_rejected"),
    (False, False, False, "verification_required"),
])
def test_inquiry_verification_outcome(inquiry_verification, monkeypatch, passed, network, rejected, expected):
    from unittest.mock import Mock
    page = Mock()
    gate = inquiry_verification.InquiryVerification(page, Mock(), 1)
    gate.network_failed = network
    if rejected:
        gate.events.append({"verification": {"verify_result": False}})
    gate.probe.run = AsyncMock(return_value=passed)
    monkeypatch.setattr(inquiry_verification, "needs_verification", AsyncMock(return_value=True))
    assert asyncio.run(gate.check("form_open")) == expected
    gate.probe.run.assert_awaited_once()
    asyncio.run(gate.close())


@pytest.mark.parametrize("host", [
    "cloudauth-device.aliyuncs.com", "cn-shanghai.device.saf.aliyuncs.com",
    "cloudauth-device.ap-southeast-1.aliyuncs.com", "ap-southeast-1.device.saf.aliyuncs.com",
    "ap-southeast-1-ga.device.saf.aliyuncs.com", "cloudauth-device-dualstack.cn-shanghai.aliyuncs.com",
    "cloudauth-device-dualstack.ap-southeast-1.aliyuncs.com", "upload.captcha-open-ga-web-b.aliyuncs.com",
    "synthetic-verify.captcha-open-southeast-dual-b.aliyuncs.com",
])
def test_required_sdk_network_hosts(inquiry_verification, host):
    from types import SimpleNamespace
    req = SimpleNamespace(url="https://"+host+"/", method="POST", post_data="opaque-sdk-data")
    assert inquiry_verification.allowed_request(req)


@pytest.mark.parametrize("url", [
    "https://evil.aliyuncs.com/", "https://cloudauth-device.aliyuncs.com.evil.test/",
    "http://cloudauth-device.aliyuncs.com/", "https://upload.captcha-open-unknown.aliyuncs.com/",
    "https://upload.captcha-open.aliyuncs.com:8443/", "https://user:pass@upload.captcha-open.aliyuncs.com/",
])
def test_sdk_allowlist_rejects_unrelated_destinations(inquiry_verification, url):
    from types import SimpleNamespace
    assert not inquiry_verification.allowed_request(SimpleNamespace(url=url,method="POST"))


def test_network_diagnostics_exclude_secrets_and_navigation_abort(inquiry_verification, caplog):
    from types import SimpleNamespace
    from unittest.mock import Mock
    gate = inquiry_verification.InquiryVerification(Mock(), Mock(), 1)
    req = SimpleNamespace(url="https://synthetic.captcha-open.aliyuncs.com/private?token=secret", failure="net::ERR_ABORTED")
    gate.failed(req)
    assert not gate.network_failed
    req.failure = "net::ERR_CONNECTION_TIMED_OUT"
    gate.failed(req)
    assert gate.network_failed
    assert "ERR_CONNECTION_TIMED_OUT" in caplog.text
    assert "secret" not in caplog.text and "private" not in caplog.text
    asyncio.run(gate.close())
