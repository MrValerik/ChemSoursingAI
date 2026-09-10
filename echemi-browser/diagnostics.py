"""Allowlisted diagnostics: never retain cookies, request bodies or challenge tokens."""
import re
from urllib.parse import urlsplit, urlunsplit


def public_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.hostname or "", p.path, "", ""))


def verification_result(payload):
    if not isinstance(payload, dict):
        return {}
    result = payload.get("Result")
    if not isinstance(result, dict):
        return {}
    out = {}
    code = result.get("VerifyCode")
    if isinstance(code, str) and re.fullmatch(r"[FT]\d{3}", code):
        out["verify_code"] = code
    passed = result.get("VerifyResult")
    if isinstance(passed, bool):
        out["verify_result"] = passed
    return out
