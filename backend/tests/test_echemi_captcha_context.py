import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "echemi-browser"))
    return importlib.import_module("captcha_context"), importlib.import_module("captcha_probe")


class Page:
    main_frame = object()
    values = {"sceneId": "synthetic-scene", "token": "SECRET_TOKEN", "traceid": "SECRET_TRACE",
              "userId": "SECRET_USER", "userUserId": "SECRET_USER2", "type": "1.0", "region": "sgp",
              "userAgent": "SyntheticBrowser", "apiGetLib": "https://o.echemi.com/AliyunCaptcha.js?t=1"}

    def __init__(self):
        self.listeners = {}

    def on(self, name, handler):
        self.listeners[name] = handler

    def remove_listener(self, name, handler):
        del self.listeners[name]

    async def evaluate(self, _):
        return self.values


class Request:
    def __init__(self, action="InitCaptcha", *, url="https://synthetic.captcha-open.aliyuncs.com/", navigation=False):
        self.url = url
        self.post_data = "Action=" + action + "&SceneId=synthetic-scene"
        self.frame = Page.main_frame
        self.navigation = navigation

    def is_navigation_request(self):
        return self.navigation


class Response:
    status = 200

    def __init__(self, request, payload):
        self.request, self.payload = request, payload

    async def json(self):
        return self.payload


async def initialized(module, page=None):
    context = module.CaptchaContext(page or Page(), {})
    req = Request()
    context.on_request(req)
    await context.on_response(Response(req, {"Result": {"CertifyId": "SECRET_CERTIFY"}}))
    return context


def test_capture_aliases_and_summary_never_exposes_values(modules):
    async def run():
        context = await initialized(modules[0])
        assert await context.capture()
        assert context._values["u_atoken"] == "SECRET_TOKEN"
        assert context._values["u_asig"] == context._values["userCertifyId"] == "SECRET_TRACE"
        assert context._values["userCertifyId"] != context._certify_id
        summary = context.summary()
        assert summary["missing_optional"] == []
        assert "SECRET" not in json.dumps(summary)
        assert "synthetic-scene" not in json.dumps(summary)
        await context.close()
        assert not context._values and not context.page.listeners
    asyncio.run(run())


def test_reload_drops_tokens_and_ignores_late_init_response(modules):
    async def run():
        context = await initialized(modules[0])
        await context.capture()
        old = Request()
        context.on_request(old)
        current = Request()
        context.on_request(current)
        await context.on_response(Response(old, {"Result": {"CertifyId": "STALE"}}))
        assert not context.initialized and "u_atoken" not in context._values
        await context.on_response(Response(current, {"Result": {"CertifyId": "FRESH"}}))
        assert context.initialized and context._certify_id == "FRESH"
        context.on_request(Request(url="https://www.echemi.com/", navigation=True))
        assert not context.initialized and not context._values
    asyncio.run(run())


def test_regional_endpoint_and_top_level_init_certify_id_observed_on_echemi(modules):
    async def run():
        context = modules[0].CaptchaContext(Page(), {})
        req = Request("InitCaptchaV2", url="https://synthetic.captcha-open-southeast.aliyuncs.com/")
        req.post_data += "&UserUserId=SECRET_USER2&UserCertifyId=SECRET_TRACE"
        context.on_request(req)
        await context.on_response(Response(req, {"CertifyId": "SECRET_CERTIFY", "Success": True,
                                                 "Code": "Success", "DeviceConfig": "SECRET_DEVICE"}))
        assert await context.capture()
        assert context._values["prefix"] == "synthetic"
        assert "DeviceConfig" not in context._values
        verify = Request("VerifyCaptchaV2")
        context.on_request(verify)
        await context.on_response(Response(verify, {"Result": {"VerifyCode": "F001", "VerifyResult": False}}))
        assert context.outcome(context.epoch, 0)["verify_result"] is False
        context.page.values = {"apiGetLib": "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js?t=1"}
        assert await context.capture()
        assert context._values["userUserId"] == "SECRET_USER2"
        assert context._sources["userCertifyId"] == "init_request"
        assert context._values["apiGetLib"].startswith("https://o.alicdn.com/")
    asyncio.run(run())


def test_verify_responses_belong_to_original_challenge_and_top_success_is_not_pass(modules):
    async def run():
        context = await initialized(modules[0])
        verify = Request("VerifyCaptcha")
        context.on_request(verify)
        epoch = context.epoch
        context.reset()
        await context.on_response(Response(verify, {"Success": True, "Code": "Success"}))
        assert context.outcome(epoch, 0) is None
        await context.on_response(Response(verify, {"Result": {"VerifyCode": "F001", "VerifyResult": False,
                                                               "token": "SECRET"}}))
        assert context.outcome(context.epoch, 0) is None
        assert context.outcome(epoch, 0)["verify_code"] == "F001"
        assert "SECRET" not in str(context.responses)
    asyncio.run(run())


@pytest.mark.parametrize("url", ["https://evil.example/", "https://captcha.aliyuncs.com.evil.example/",
                                  "http://synthetic.captcha-open.aliyuncs.com/"])
def test_unrelated_responses_cannot_initialize_challenge(modules, url):
    async def run():
        context = modules[0].CaptchaContext(Page(), {})
        request = Request(url=url)
        context.on_request(request)
        await context.on_response(Response(request, {"Result": {"CertifyId": "UNTRUSTED"}}))
        assert not context.initialized and not context._values
    asyncio.run(run())


def test_conflicting_scene_blocks_attempt_and_missing_optional_does_not_reuse_old_value(modules):
    async def run():
        page = Page()
        context = await initialized(modules[0], page)
        await context.capture()
        page.values = {"sceneId": "different-scene", "userAgent": "SyntheticBrowser"}
        assert not await context.capture()
        assert context.summary()["conflicts"] == ["sceneId"]
        assert "u_atoken" not in context._values
        page.values = {"userAgent": "SyntheticBrowser"}
        assert await context.capture()  # Scene remains available from the current InitCaptcha.
        assert context._sources["sceneId"] == "init_request"
    asyncio.run(run())


@pytest.mark.parametrize("body", ["x" * 262145, "{broken", "[]", "&".join(f"x{i}=1" for i in range(101))],
                         ids=["oversized", "invalid_json", "array", "too_many_fields"])
def test_malformed_request_is_bounded(modules, body):
    assert modules[0].request_fields(body) == {}


@pytest.mark.parametrize("outcome,content,passed", [
    ({"verify_code": "T001", "verify_result": True}, True, True),
    ({"verify_code": "T001", "verify_result": True}, False, False),
    ({"verify_code": "F001", "verify_result": False}, True, False),
    ({"Success": True}, True, False), (None, True, False),
])
def test_pass_requires_both_verification_and_content(modules, outcome, content, passed):
    assert modules[1].accepted(outcome, content) is passed


def test_probe_stops_after_two_rejections_without_manual_wait(modules, monkeypatch):
    async def run():
        context = await initialized(modules[0])
        page = context.page
        calls = []
        async def fake_drag(*args):
            calls.append(context.epoch)
            context.responses.append({"context_id": context.epoch, "verify_code": "F001", "verify_result": False})
            return 2.5
        async def reload(**kwargs):
            req = Request()
            context.on_request(req)
            await context.on_response(Response(req, {"Result": {"CertifyId": "NEW"}}))
        async def noop(*args): pass
        async def unavailable(*args): return False
        page.reload = reload
        monkeypatch.setattr(modules[1], "drag", fake_drag)
        monkeypatch.setattr(modules[1], "protected_content", unavailable)
        monkeypatch.setattr(modules[1].asyncio, "sleep", noop)
        probe = modules[1].CaptchaProbe(context, 2)
        events = []
        assert not await probe.run(page, None, events)
        assert len(calls) == 2 and calls[0] != calls[1]
        assert all(e["status"] == "rejected" for e in events)
        assert not await probe.run(page, None, events)
        assert len(calls) == 2  # Budget applies to the whole search, not each product.
    asyncio.run(run())


def test_refresh_access_is_not_reported_as_captcha_success(modules, monkeypatch):
    async def run():
        context = await initialized(modules[0])
        checks = iter([False, True])
        async def fake_drag(*args):
            context.responses.append({"context_id": context.epoch, "verify_result": False, "verify_code": "F001"})
            return 2.5
        async def content(*args): return next(checks)
        async def noop(*args, **kwargs): pass
        context.page.reload = noop
        monkeypatch.setattr(modules[1], "drag", fake_drag)
        monkeypatch.setattr(modules[1], "protected_content", content)
        monkeypatch.setattr(modules[1].asyncio, "sleep", noop)
        events = []
        assert await modules[1].CaptchaProbe(context, 2).run(context.page, None, events)
        assert [e["status"] for e in events] == ["rejected", "access_after_reload"]
    asyncio.run(run())
