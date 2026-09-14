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
    request_id = payload.get('RequestId')
    if out and isinstance(request_id, str) and re.fullmatch(
            r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', request_id):
        out['request_id'] = request_id
    return out


def rejection_message(events):
    for event in reversed(events):
        result = event.get('verification') or {}
        if result.get('verify_result') is True:
            return ''  # A later successful retry supersedes older rejections.
        if result.get('verify_result') is not False:
            continue
        code = result.get('verify_code')
        descriptions = {'F001': 'отказ по правилам оценки риска',
                        'F008': 'повторная отправка проверки',
                        'F014': 'нет действующей инициализации',
                        'F015': 'не принято перемещение ползунка',
                        'F017': 'отклонён протокол или параметры'}
        if isinstance(code, str) and re.fullmatch(r'F\d{3}', code):
            return f"Echemi отклонил проверку: {code} ({descriptions.get(code, 'причина не уточнена')})."
    return ''
