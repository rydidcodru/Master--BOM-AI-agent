"""LLM provider 게이트 + AnthropicClient JSON 모드 테스트 (네트워크/DB 불필요).

AnthropicClient는 fake ``anthropic`` 모듈을 sys.modules에 주입해 SDK 없이 검증한다.
"""

from __future__ import annotations

import sys
import types

import pytest

from src.agent.llm.client import (
    _safe_json,
    current_provider,
    default_llm,
    llm_enabled,
)


# ── _safe_json 견고성 ────────────────────────────────────────────────────────


def test_safe_json_clean():
    assert _safe_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_safe_json_code_fence():
    assert _safe_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_safe_json_prose_wrapped():
    assert _safe_json('네, 결과입니다:\n{"a": 1}\n감사합니다') == {"a": 1}


def test_safe_json_non_object_raises():
    with pytest.raises(ValueError):
        _safe_json("[1, 2, 3]")


def test_safe_json_garbage_raises():
    with pytest.raises(ValueError):
        _safe_json("이건 JSON이 아닙니다")


# ── provider 게이트 ──────────────────────────────────────────────────────────


def test_provider_default_ollama(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert current_provider() == "ollama"


def test_llm_enabled_ollama_gate(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("ENABLE_LLM", raising=False)
    assert llm_enabled() is False
    monkeypatch.setenv("ENABLE_LLM", "1")
    assert llm_enabled() is True


def test_llm_enabled_anthropic_gate(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert llm_enabled() is True


def test_default_llm_anthropic_requires_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _install_fake_anthropic(monkeypatch, reply='{"x": 1}')
    with pytest.raises(RuntimeError):
        default_llm()


# ── AnthropicClient (fake SDK) ───────────────────────────────────────────────


def _install_fake_anthropic(
    monkeypatch,
    *,
    reply: str,
    fail_on_temperature: bool = False,
    typeerror_on_thinking: bool = False,
):
    """sys.modules에 fake ``anthropic`` 모듈 주입. create 호출 인자는 calls에 적재."""
    calls: list[dict] = []

    class BadRequestError(Exception):
        pass

    class _Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Resp:
        def __init__(self, text):
            self.content = [_Block(text)]

    class _Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            if fail_on_temperature and "temperature" in kwargs:
                raise BadRequestError("temperature not supported")
            # 구버전 SDK(<0.47)는 thinking kwarg를 모름 → 클라이언트측 TypeError 시뮬레이션
            if typeerror_on_thinking and "thinking" in kwargs:
                raise TypeError("create() got an unexpected keyword argument 'thinking'")
            return _Resp(reply)

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    fake.BadRequestError = BadRequestError
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return calls


def test_anthropic_client_complete_json(monkeypatch):
    calls = _install_fake_anthropic(
        monkeypatch, reply='{"intent_summary": "패킹 변경", "confidence": 0.7}'
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    from src.agent.llm.client import AnthropicClient

    client = AnthropicClient()
    out = client.complete_json("변경 설명", system="너는 분석가다", temperature=0.0)

    assert out["intent_summary"] == "패킹 변경"
    assert out["confidence"] == 0.7
    kw = calls[0]
    assert kw["model"] == "claude-sonnet-4-6"
    # prompt caching breakpoint + JSON-only 지시가 system block에 붙는다
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "JSON" in kw["system"][0]["text"]
    assert kw["thinking"] == {"type": "disabled"}


def test_anthropic_client_retries_without_params(monkeypatch):
    calls = _install_fake_anthropic(
        monkeypatch, reply='{"ok": true}', fail_on_temperature=True
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from src.agent.llm.client import AnthropicClient

    out = AnthropicClient().complete_json("x", system="s", temperature=0.4)
    assert out == {"ok": True}
    # 첫 호출(temperature 포함) 실패 → 두 번째는 temperature/thinking 없이 재시도
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
    assert "thinking" not in calls[1]


def test_anthropic_client_retries_on_typeerror(monkeypatch):
    # 구버전 SDK가 thinking kwarg를 거부(TypeError) → BadRequestError가 아니어도 폴백해야 함
    calls = _install_fake_anthropic(
        monkeypatch, reply='{"ok": true}', typeerror_on_thinking=True
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from src.agent.llm.client import AnthropicClient

    out = AnthropicClient().complete_json("x", system="s", temperature=0.0)
    assert out == {"ok": True}
    assert "thinking" in calls[0]
    assert "thinking" not in calls[1]


def test_anthropic_client_missing_key(monkeypatch):
    _install_fake_anthropic(monkeypatch, reply="{}")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.agent.llm.client import AnthropicClient

    with pytest.raises(RuntimeError):
        AnthropicClient()
