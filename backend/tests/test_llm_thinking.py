"""Рассуждающая модель должна отвечать, а не размышлять до конца лимита.

Замер на Yandex AI Studio, Qwen3.6: без выключателя модель вернула 2205
символов рассуждения, израсходовала 700 выходных токенов из 700 и оставила
`content` пустым. Профиль `openai_compatible` намеренно не шлёт расширение
llama.cpp — и правильно, — но замены ему не было, то есть для облака
выключатель просто убрали.

Проверено там же: работают и `chat_template_kwargs`, и `reasoning_effort`.
Маркер `/no_think` в тексте запроса — нет. Способ зависит от провайдера,
поэтому он настройка, а не свойство профиля.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_llm_thinking.db")

import pytest

from app.extraction.llm_client import LLMClient, LLMUnavailableError


def _client(**kw) -> LLMClient:
    base = dict(
        base_url="http://model.invalid/v1",
        model="probe",
        api_key="k",
        thinking_control="chat_template_kwargs",
    )
    base.update(kw)
    return LLMClient(**base)


# --- выбор выключателя ---


def test_chat_template_kwargs_is_sent_when_configured():
    payload = _client(thinking_control="chat_template_kwargs")._with_provider_options({})
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_reasoning_effort_is_sent_when_configured():
    payload = _client(thinking_control="reasoning_effort")._with_provider_options({})
    assert payload["reasoning_effort"] == "none"
    assert "chat_template_kwargs" not in payload


def test_none_sends_nothing():
    """Для модели без режима рассуждения лишний параметр не нужен."""
    assert _client(thinking_control="none")._with_provider_options({}) == {}


def test_an_unknown_control_is_refused_at_construction():
    """Опечатка в настройке должна падать сразу, а не на первом вызове."""
    with pytest.raises(ValueError):
        _client(thinking_control="magic")


# --- разбор ответа ---


def test_reasoning_without_an_answer_names_the_cause():
    """Иначе это выглядит как испорченный JSON и уводит разбор в сторону."""
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "Here's a thinking process: …",
                },
                "finish_reason": "length",
            }
        ]
    }
    with pytest.raises(LLMUnavailableError) as exc:
        LLMClient._message_content(response)
    assert "LLM_THINKING_CONTROL" in str(exc.value)


def test_a_normal_answer_passes_through():
    response = {"choices": [{"message": {"content": '{"cas": "107-43-7"}'}}]}
    assert LLMClient._message_content(response) == '{"cas": "107-43-7"}'


def test_an_empty_answer_without_reasoning_is_left_to_the_caller():
    """Пустой ответ без рассуждения — другая беда, и разбирать её не здесь."""
    response = {"choices": [{"message": {"content": None}}]}
    assert LLMClient._message_content(response) is None
