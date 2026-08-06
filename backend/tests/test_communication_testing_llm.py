import pytest

from app.core.config import get_settings
from app.extraction.llm_client import LLMUnavailableError
from app.services.communication_testing import _communication_test_llm_client


def _set_dedicated_profile(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_url: str,
    model: str,
    api_key: str,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "communication_test_llm_base_url", base_url)
    monkeypatch.setattr(settings, "communication_test_llm_model", model)
    monkeypatch.setattr(settings, "communication_test_llm_api_key", api_key)


def test_communication_testing_prefers_dedicated_cloud_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    _set_dedicated_profile(
        monkeypatch,
        base_url="https://cloud.example/v1/",
        model="gpt://folder/qwen3.6-35b-a3b/latest",
        api_key="cloud-secret",
    )
    monkeypatch.setattr(
        settings, "communication_test_llm_auth_scheme", "api-key"
    )
    monkeypatch.setattr(settings, "communication_test_llm_project_id", "folder")
    monkeypatch.setattr(
        settings,
        "communication_test_llm_thinking_control",
        "chat_template_kwargs",
    )
    monkeypatch.setattr(settings, "communication_test_llm_timeout_s", 45)

    client = _communication_test_llm_client()

    assert client.base_url == "https://cloud.example/v1"
    assert client.model == "gpt://folder/qwen3.6-35b-a3b/latest"
    assert client.api_key == "cloud-secret"
    assert client.auth_scheme == "api-key"
    assert client.project_id == "folder"
    assert client.thinking_control == "chat_template_kwargs"
    assert client.timeout_s == 45


def test_empty_dedicated_profile_uses_primary_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    _set_dedicated_profile(
        monkeypatch,
        base_url="",
        model="",
        api_key="",
    )
    monkeypatch.setattr(settings, "llm_base_url", "http://primary.example/v1/")
    monkeypatch.setattr(settings, "llm_model", "primary-model")
    monkeypatch.setattr(settings, "llm_api_key", "primary-secret")

    client = _communication_test_llm_client()

    assert client.base_url == "http://primary.example/v1"
    assert client.model == "primary-model"
    assert client.api_key == "primary-secret"


def test_partial_dedicated_profile_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_dedicated_profile(
        monkeypatch,
        base_url="https://cloud.example/v1",
        model="",
        api_key="",
    )

    with pytest.raises(LLMUnavailableError, match="заполнен не полностью"):
        _communication_test_llm_client()
