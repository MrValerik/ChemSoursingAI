"""Observe one Alibaba challenge at a time. Sensitive values live only in memory."""
import asyncio
import json
import re
import time
import weakref
from urllib.parse import parse_qs, urlsplit

from diagnostics import verification_result

FIELDS = ("sceneId", "prefix", "userId", "userUserId", "verifyType", "region",
          "userCertifyId", "apiGetLib", "userAgent", "u_atoken", "u_asig")
REQUIRED = ("sceneId", "prefix")
INIT_ACTIONS = {"InitCaptcha", "InitCaptchaV2"}
VERIFY_ACTIONS = {"VerifyCaptcha", "VerifyCaptchaV2"}
READ_PAGE = """() => {
    const info = typeof requestInfo === 'object' && requestInfo !== null ? requestInfo : {};
    const result = {};
    for (const key of ['sceneId', 'userId', 'userUserId', 'type', 'region', 'traceid', 'token']) {
        const value = info[key];
        if (typeof value === 'string' && value.length <= 16384) result[key] = value;
    }
    result.userAgent = navigator.userAgent;
    result.apiGetLib = [...document.scripts].map(s => s.src)
        .find(src => /\\/AliyunCaptcha\\.js(?:\\?|$)/i.test(src)) || '';
    return result;
}"""


def captcha_host(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return parsed.scheme == "https" and host.endswith(".aliyuncs.com") and "captcha" in host
    except ValueError:
        return False


def scalar(value):
    return value if isinstance(value, str) and 0 < len(value) <= 16384 else None


def request_fields(body):
    if not isinstance(body, str) or len(body) > 262144:
        return {}
    try:
        if body.lstrip().startswith("{"):
            data = json.loads(body)
            return data if isinstance(data, dict) else {}
        return {key: values[-1] for key, values in parse_qs(body, max_num_fields=100).items()}
    except (ValueError, TypeError):
        return {}


class CaptchaContext:
    def __init__(self, page, diagnostics):
        self.page = page
        self.epoch = 0
        self.started = time.monotonic()
        self._values = {}
        self._sources = {}
        self._init_values = {}
        self._init_sources = {}
        self.conflicts = []
        self._certify_id = None
        self.initialized = False
        self._requests = weakref.WeakKeyDictionary()
        self._tasks = set()
        self.responses = diagnostics.setdefault("verification_responses", [])
        self._request_handler = self.on_request
        self._response_handler = self._schedule_response
        page.on("request", self._request_handler)
        page.on("response", self._response_handler)

    def reset(self):
        self.epoch += 1
        self.started = time.monotonic()
        self._values.clear()
        self._sources.clear()
        self._init_values.clear()
        self._init_sources.clear()
        self.conflicts.clear()
        self._certify_id = None
        self.initialized = False

    def put(self, name, value, source):
        value = scalar(value)
        if name in FIELDS and value is not None:
            self._values[name] = value
            self._sources[name] = source

    def on_request(self, request):
        try:
            if request.is_navigation_request() and request.frame == self.page.main_frame:
                self.reset()
            if not captcha_host(request.url):
                return
            data = request_fields(request.post_data or urlsplit(request.url).query)
            action = data.get("Action")
            if action not in INIT_ACTIONS | VERIFY_ACTIONS:
                return
            if action in INIT_ACTIONS:
                self.reset()
                for key, name in (("SceneId", "sceneId"), ("CaptchaSceneId", "sceneId"),
                                  ("sId", "sceneId"), ("UserId", "userId"),
                                  ("UserUserId", "userUserId"), ("UserCertifyId", "userCertifyId")):
                    self.put(name, data.get(key), "init_request")
                host = urlsplit(request.url).hostname
                match = re.fullmatch(r"([^.]+)\.captcha-open(?:-[a-z0-9-]+)?\.aliyuncs\.com", host)
                if match:
                    self.put("prefix", match.group(1), "init_endpoint")
                self._init_values = dict(self._values)
                self._init_sources = dict(self._sources)
            self._requests[request] = (self.epoch, action)
        except (ValueError, AttributeError, TypeError):
            pass

    def _schedule_response(self, response):
        if response.request not in self._requests:
            return
        task = asyncio.create_task(self.on_response(response))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_response(self, response):
        identity = self._requests.get(response.request)
        if identity is None:
            return
        epoch, action = identity
        try:
            payload = await response.json()
            if action in INIT_ACTIONS:
                if epoch != self.epoch:
                    return  # A late response must not revive a previous challenge.
                if isinstance(payload, dict):
                    result = payload.get("Result")
                    result = result if isinstance(result, dict) else payload
                    self._certify_id = scalar(result.get("CertifyId"))
                    self.initialized = self._certify_id is not None
                return
            safe = verification_result(payload)
            if safe and len(self.responses) < 30:
                self.responses.append({"context_id": epoch, "http_status": response.status, **safe})
        except Exception:
            pass  # Never log upstream bodies, URLs or exception messages.

    async def capture(self):
        epoch = self.epoch
        values = await self.page.evaluate(READ_PAGE)
        if epoch != self.epoch or not isinstance(values, dict):
            return False
        # These are re-read from the live page, not carried across refreshes.
        self._values = dict(self._init_values)
        self._sources = dict(self._init_sources)
        self.conflicts = []
        for source, target in (("sceneId", "sceneId"), ("userId", "userId"),
                               ("userUserId", "userUserId"), ("type", "verifyType"),
                               ("region", "region"), ("traceid", "userCertifyId"),
                               ("traceid", "u_asig"), ("token", "u_atoken")):
            value = scalar(values.get(source))
            if target in self._values and value and value != self._values[target]:
                self.conflicts.append(target)
            self.put(target, value, "requestInfo")
        self.put("userAgent", values.get("userAgent"), "browser")
        library = scalar(values.get("apiGetLib"))
        if library:
            parsed = urlsplit(library)
            host = parsed.hostname or ""
            if parsed.scheme == "https" and any(host.endswith(suffix) for suffix in
                                                (".echemi.com", ".aliyuncs.com", ".alicdn.com")):
                self.put("apiGetLib", library, "script_src")
        return self.initialized and not self.conflicts and all(self._values.get(key) for key in REQUIRED)

    def summary(self):
        return {"context_id": self.epoch, "initialized": self.initialized,
                "age_seconds": round(time.monotonic() - self.started, 2),
                "conflicts": list(self.conflicts),
                "present": sorted(self._values),
                "missing_required": [key for key in REQUIRED if not self._values.get(key)],
                "missing_optional": [key for key in FIELDS if key not in REQUIRED and key not in self._values],
                "sources": dict(self._sources)}

    def outcome(self, epoch, after):
        return next((item for item in reversed(self.responses[after:])
                     if item["context_id"] == epoch), None)

    async def close(self):
        self.page.remove_listener("request", self._request_handler)
        self.page.remove_listener("response", self._response_handler)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.reset()
        self._requests.clear()
