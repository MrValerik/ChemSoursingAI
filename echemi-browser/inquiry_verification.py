"""Bounded pre-submit verification and allowlisted network diagnostics."""
import re
import logging
from urllib.parse import urlsplit
from captcha_context import CaptchaContext
from captcha_probe import CaptchaProbe
from page_state import needs_verification


# Vendor-documented required API hosts; no blanket *.aliyuncs.com exception.
# https://help.aliyun.com/zh/captcha/captcha2-0/user-guide/captcha-2-0-client-access-faq
DEVICE_HOSTS = {
    "cloudauth-device.aliyuncs.com", "cn-shanghai.device.saf.aliyuncs.com",
    "cloudauth-device.ap-southeast-1.aliyuncs.com", "ap-southeast-1.device.saf.aliyuncs.com",
    "ap-southeast-1-ga.device.saf.aliyuncs.com",
    "cloudauth-device-dualstack.cn-shanghai.aliyuncs.com",
    "cloudauth-device-dualstack.ap-southeast-1.aliyuncs.com",
}
RESOURCE_HOSTS = {"g.alicdn.com", "o.alicdn.com", "x.alicdn.com",
                  "static-captcha.aliyuncs.com", "static-captcha-sgp.aliyuncs.com"}
logger = logging.getLogger("echemi.inquiry.network")


def captcha_endpoint(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.port in {None, 443}
            and not parsed.username and not parsed.password
            and (parsed.hostname in DEVICE_HOSTS or bool(re.fullmatch(
                r"[a-z0-9-]+\.captcha-open(?:-southeast|-dual|-southeast-dual|-ga-web)?(?:-b)?\.aliyuncs\.com",
                parsed.hostname or ""))))


def allowed_request(request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    parsed = urlsplit(request.url)
    if parsed.scheme == "https" and parsed.hostname in {"www.echemi.com", "i.echemi.com"}:
        return True
    # The SDK also performs device initialization and opaque signed uploads;
    # its POST bodies are not restricted to InitCaptcha/VerifyCaptcha Actions.
    return request.method == "POST" and captcha_endpoint(request.url)


class InquiryVerification:
    def __init__(self, page, mouse, attempts):
        self.page, self.mouse = page, mouse
        self.diagnostics = {}
        self.events = []
        self.network_failed = False
        self.logged = 0
        self.context = CaptchaContext(page, self.diagnostics)
        self.probe = CaptchaProbe(self.context, attempts)
        page.on("requestfailed", self.failed)

    def record(self, request, kind, error=""):
        if self.logged >= 12:
            return
        self.logged += 1
        host = urlsplit(request.url).hostname or ""
        # Log no query, path, headers, body, identifiers or exception text.
        safe_host = host if re.fullmatch(r"[a-z0-9.-]{1,253}", host) else "invalid"
        safe_error = error if re.fullmatch(r"net::ERR_[A-Z_]+", error) else "unspecified"
        logger.warning("inquiry_network kind=%s host=%s error=%s", kind, safe_host, safe_error)

    def blocked(self, request):
        self.record(request, "blocked_by_filter")

    def failed(self, request):
        host = urlsplit(request.url).hostname or ""
        error = request.failure or ""
        # Closing/reloading a page cancels requests; that is not a network outage.
        if error == "net::ERR_ABORTED":
            return
        if captcha_endpoint(request.url) or host in RESOURCE_HOSTS:
            self.network_failed = True
            self.record(request, "request_failed", error)

    async def check(self, stage):
        if not await needs_verification(self.page):
            return None
        if self.probe.remaining and await self.probe.run(self.page, self.mouse, self.events, stage=stage):
            return None
        if any((e.get("verification") or {}).get("verify_result") is False for e in self.events):
            return "captcha_rejected"
        if self.network_failed:
            return "captcha_network_error"
        return "verification_required"

    async def close(self):
        self.page.remove_listener("requestfailed", self.failed)
        await self.context.close()
