from app.extraction.llm_client import LLMClient


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "{}"}}]}


class _Client:
    payloads: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def post(self, url: str, *, json: dict, headers: dict) -> _Response:
        self.payloads.append(json)
        return _Response()


def test_qwen_service_calls_disable_thinking_and_limit_output(monkeypatch):
    _Client.payloads.clear()
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
    assert extraction_payload["max_tokens"] == 512
    assert extraction_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert json_payload["max_tokens"] == 128
    assert json_payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert json_payload["response_format"]["json_schema"]["name"] == "test_schema"
