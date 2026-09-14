"""Bounded pre-submit verification and allowlisted network diagnostics."""
import re
from urllib.parse import urlsplit
from captcha_context import CaptchaContext, request_fields, INIT_ACTIONS, VERIFY_ACTIONS
from captcha_probe import CaptchaProbe
from page_state import needs_verification


def captcha_endpoint(url):
    parsed = urlsplit(url)
    return parsed.scheme == "https" and bool(re.fullmatch(
        r"[a-z0-9-]+\.captcha-open(?:-[a-z0-9-]+)?\.aliyuncs\.com", parsed.hostname or ""))


def allowed_request(request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    parsed = urlsplit(request.url)
    if parsed.scheme == "https" and parsed.hostname in {"www.echemi.com", "i.echemi.com"}:
        return True
    return (request.method == "POST" and captcha_endpoint(request.url)
            and request_fields(request.post_data).get("Action") in INIT_ACTIONS | VERIFY_ACTIONS)


class InquiryVerification:
    def __init__(self, page, mouse, attempts):
        self.page, self.mouse = page, mouse
        self.diagnostics = {}
        self.events = []
        self.network_failed = False
        self.context = CaptchaContext(page, self.diagnostics)
        self.probe = CaptchaProbe(self.context, attempts)
        page.on("requestfailed", self.failed)

    def failed(self, request):
        host = urlsplit(request.url).hostname or ""
        if captcha_endpoint(request.url) or host in {"g.alicdn.com", "o.alicdn.com"}:
            self.network_failed = True

    async def check(self, stage):
        if not await needs_verification(self.page):
            return None
        if self.probe.remaining and await self.probe.run(self.page, self.mouse, self.events, stage=stage):
            return None
        if self.network_failed:
            return "captcha_network_error"
        if any((e.get("verification") or {}).get("verify_result") is False for e in self.events):
            return "captcha_rejected"
        return "verification_required"

    async def close(self):
        self.page.remove_listener("requestfailed", self.failed)
        await self.context.close()
