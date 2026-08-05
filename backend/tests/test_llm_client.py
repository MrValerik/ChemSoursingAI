import httpx
import pytest

from app.extraction.llm_client import (
    LLMClient,
    LLMContextOverflowError,
    LLMUnavailableError,
)


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{}"}}]}


class _Client:
    payloads: list[dict] = []
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, json: dict, headers: dict) -> _Response:
        self.payloads.append(json)
        self.calls.append({"method": "POST", "url": url, "headers": headers})
        return _Response()

    def get(self, url: str, *, headers: dict) -> _Response:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return _Response()


def test_qwen_service_calls_disable_thinking_and_limit_output(monkeypatch):
    _Client.payloads.clear()
    _Client.calls.clear()
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", _Client)
    client = LLMClient(
        base_url="http://llama.test/v1",
        model="qwen-test",
        api_key="test",
        timeout_s=1,
    )

    assert (
        client.generate_text(
            system_prompt="Return a query.",
            user_text="Aspirin",
            max_tokens=64,
        )
        == "{}"
    )
    assert client.extract_quote("USD 10/kg") == {}
    assert (
        client.generate_json(
            system_prompt="Return structured data.",
            user_text="Source text",
            schema_name="test_schema",
            json_schema={"type": "object", "properties": {}},
            max_tokens=128,
        )
        == {}
    )

    text_payload, extraction_payload, json_payload = _Client.payloads
    assert text_payload["max_tokens"] == 64
    assert text_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "отвечай по-русски" in text_payload["messages"][0]["content"]
    assert extraction_payload["max_tokens"] == 512
    assert extraction_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert json_payload["max_tokens"] == 128
    assert json_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert "по-русски" in json_payload["messages"][0]["content"]
    assert json_payload["response_format"]["json_schema"]["name"] == "test_schema"
    assert all(
        call["headers"] == {"Authorization": "Bearer test"}
        for call in _Client.calls
    )


def test_openai_cloud_headers_payload_and_structured_json(monkeypatch):
    _Client.payloads.clear()
    _Client.calls.clear()
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", _Client)
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    client = LLMClient(
        base_url="https://cloud.test/v1",
        model="gpt://folder-id/qwen-test",
        api_key="cloud-secret",
        auth_scheme="api-key",
        project_id="folder-id",
        thinking_control="none",
        timeout_s=1,
    )

    assert client.check_health() == (True, None)
    assert client.generate_text(system_prompt="Answer.", user_text="Text") == "{}"
    assert client.extract_quote("USD 10/kg") == {}
    assert (
        client.generate_json(
            system_prompt="Return structured data.",
            user_text="Source text",
            schema_name="cloud_schema",
            json_schema=schema,
        )
        == {}
    )

    assert _Client.calls[0]["method"] == "GET"
    assert _Client.calls[0]["url"] == "https://cloud.test/v1/models"
    assert all(
        call["headers"]
        == {
            "Authorization": "Api-Key cloud-secret",
            "OpenAI-Project": "folder-id",
        }
        for call in _Client.calls
    )
    # Выключатель рассуждения — отдельная настройка, а не свойство
    # облачного профиля: замер показал, что Yandex принимает
    # chat_template_kwargs. Здесь он выключен явно.
    assert all("chat_template_kwargs" not in payload for payload in _Client.payloads)
    json_payload = _Client.payloads[-1]
    assert json_payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "cloud_schema",
            "schema": schema,
            "strict": True,
        },
    }


class _ScriptedResponse:
    """Ответ по сценарию: код статуса или готовый content."""

    def __init__(self, step) -> None:
        self.step = step
        self.status_code = step.get("status", 200)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://llama.test/v1")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self.step["content"]}}]}


def _scripted_client(script: list[dict]):
    """httpx.Client, который проигрывает шаги сценария по одному на вызов."""

    calls: list[dict] = []

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, json: dict, headers: dict):
            calls.append(json)
            step = script.pop(0)
            if "exception" in step:
                raise step["exception"]
            return _ScriptedResponse(step)

    return Client, calls


def _json_call(client: LLMClient) -> dict:
    return client.generate_json(
        system_prompt="Return structured data.",
        user_text="Source text",
        schema_name="test_schema",
        json_schema={"type": "object", "properties": {}},
    )


def _make_client() -> LLMClient:
    return LLMClient(
        base_url="http://llama.test/v1",
        model="qwen-test",
        api_key="test",
        timeout_s=1,
    )


def test_transient_error_is_retried_once(monkeypatch):
    scripted, calls = _scripted_client(
        [{"status": 503}, {"content": '{"ok": true}'}]
    )
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", scripted)
    client = _make_client()

    assert _json_call(client) == {"ok": True}
    assert len(calls) == 2
    assert [a["kind"] for a in client.last_attempts] == [
        "initial",
        "transient_retry",
    ]
    assert client.last_attempts[1]["retry_reason"] == "http_503"
    assert client.last_attempts[1]["attempt_number"] == 2


def test_second_transient_error_fails_without_more_retries(monkeypatch):
    scripted, calls = _scripted_client([{"status": 503}, {"status": 503}])
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", scripted)
    client = _make_client()

    with pytest.raises(LLMUnavailableError):
        _json_call(client)
    assert len(calls) == 2


def test_logical_error_is_not_retried(monkeypatch):
    scripted, calls = _scripted_client([{"status": 422}])
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", scripted)
    client = _make_client()

    with pytest.raises(LLMUnavailableError):
        _json_call(client)
    assert len(calls) == 1
    assert client.last_attempts[0]["error"].startswith("HTTP 422")


def test_malformed_json_gets_exactly_one_schema_repair(monkeypatch):
    scripted, calls = _scripted_client(
        [
            {"content": "not json at all"},
            {"content": '{"repaired": true}'},
        ]
    )
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", scripted)
    client = _make_client()

    assert _json_call(client) == {"repaired": True}
    assert [a["kind"] for a in client.last_attempts] == [
        "initial",
        "schema_repair",
    ]
    repair_messages = calls[1]["messages"]
    assert repair_messages[-2]["role"] == "assistant"
    assert repair_messages[-2]["content"] == "not json at all"
    assert "валидный JSON" in repair_messages[-1]["content"]


def test_second_malformed_json_fails(monkeypatch):
    scripted, calls = _scripted_client(
        [{"content": "not json"}, {"content": "still not json"}]
    )
    monkeypatch.setattr("app.extraction.llm_client.httpx.Client", scripted)
    client = _make_client()

    with pytest.raises(LLMUnavailableError):
        _json_call(client)
    assert len(calls) == 2


def _overflow_response():
    request = httpx.Request("POST", "http://llama.test/v1")
    return httpx.Response(
        400,
        request=request,
        text=(
            '{"error":{"message":"request (4130 tokens) exceeds the '
            'available context size (4096 tokens), try increasing it",'
            '"type":"exceed_context_size_error"}}'
        ),
    )


class _OverflowClient:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, json: dict, headers: dict):
        type(self).calls += 1
        response = _overflow_response()

        class Wrapper:
            status_code = 400

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "400", request=response.request, response=response
                )

            def json(self):
                return response.json()

        return Wrapper()


def test_context_overflow_reports_real_reason_and_is_not_retried(monkeypatch):
    _OverflowClient.calls = 0
    monkeypatch.setattr(
        "app.extraction.llm_client.httpx.Client", _OverflowClient
    )
    client = _make_client()

    with pytest.raises(LLMContextOverflowError) as caught:
        _json_call(client)

    message = str(caught.value)
    assert "4130" in message and "4096" in message
    assert "контекст" in message
    # Повтор не помог бы: запрос нужно сократить или увеличить контекст.
    assert _OverflowClient.calls == 1
