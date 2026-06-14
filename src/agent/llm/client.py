"""LLM 클라이언트 — JSON 모드(슬롯 추출/자연어 설명 전용).

제공자(provider)는 env ``LLM_PROVIDER``로 게이트한다:

- ``ollama``(기본): 로컬 Ollama ``/api/generate`` (``format=json``). ``ENABLE_LLM=1`` 필요.
- ``anthropic``(opt-in): Anthropic Claude Messages API. ``ANTHROPIC_API_KEY`` env 필요.
- ``openai``(opt-in): OpenAI Chat Completions JSON 모드. ``OPENAI_API_KEY`` env 필요.

CLAUDE.md 기본 원칙은 **로컬 LLM**이다. 외부 API(Claude)는 ``LLM_PROVIDER=anthropic``로
명시적으로 opt-in 했을 때만 ``default_llm()``이 선택하며, API 키는 **코드/로그에 하드코딩하지
않고** ``ANTHROPIC_API_KEY`` 환경변수(.env, gitignored)에서만 읽는다. import만으로는 어떤
네트워크 호출도 하지 않는다.

L1 structurizer의 의미 슬롯, L4의 자연어 설명에만 사용. L3 결정론 룰 엔진에서는 import 금지.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

import requests

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "qwen2.5:32b"
# 사용자 지정 모델(클로드 소넷). claude-api 스킬 모델표 기준 정확한 ID — 날짜 접미사 금지.
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-6"
# OpenAI/GPT 기본 모델(재랭킹 등 가벼운 JSON 작업에 빠르고 저렴). OPENAI_MODEL env로 override.
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"
HTTP_TIMEOUT = 120


def current_provider() -> str:
    """현재 선택된 LLM 제공자 (``ollama`` | ``anthropic``). env ``LLM_PROVIDER`` 게이트."""
    return os.environ.get("LLM_PROVIDER", "ollama").strip().lower()


def llm_enabled() -> bool:
    """선택된 제공자가 사용 가능하게 설정되었는지.

    - anthropic: ``ANTHROPIC_API_KEY`` 존재 여부.
    - openai: ``OPENAI_API_KEY`` 존재 여부.
    - ollama(기본): ``ENABLE_LLM=1`` 여부(기존 게이트 유지).
    """
    provider = current_provider()
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return os.environ.get("ENABLE_LLM", "0") == "1"


def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def _safe_json(raw: str) -> dict[str, Any]:
    """LLM 응답 문자열 → dict. 스키마 강제 경계.

    Ollama(``format=json``)는 깨끗한 JSON을 주지만, Claude 등 일반 텍스트 응답을 위해
    (1) ```json 코드펜스 제거, (2) 실패 시 최외곽 ``{...}`` 추출까지 시도한다. 그래도
    객체가 아니면 ValueError(structurizer가 결정론 fallback으로 떨어짐).
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    data: Any
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"LLM did not return valid JSON: {text[:120]!r}") from exc
        else:
            raise ValueError(f"LLM did not return valid JSON: {text[:120]!r}")
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON is not an object: {type(data).__name__}")
    return data


class LlmClient(Protocol):
    """JSON 모드 LLM 인터페이스 (테스트는 fake 주입).

    구현체는 introspection용 ``provider``/``model`` 속성을 노출하는 것을 권장(UI 표시용,
    필수는 아님 — 호출부는 getattr 기본값으로 안전 접근).
    """

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]: ...


class OllamaClient:
    """Ollama ``/api/generate`` (``format=json``) 백엔드 — 로컬 전용."""

    provider = "ollama"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            # think=False: thinking 모델(qwen3 등)이 JSON 모드에서 reasoning을 흘려 빈/깨진
            # 응답을 내는 것을 차단. 비-thinking 모델은 이 필드를 무시(무영향).
            "think": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        resp = requests.post(
            f"{_ollama_host()}/api/generate", json=payload, timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return _safe_json(resp.json().get("response", ""))


_JSON_ONLY = (
    "반드시 단일 JSON 객체 하나만 출력한다. 마크다운 코드펜스/설명/접두문 없이 JSON만 반환한다."
)


class AnthropicClient:
    """Anthropic Claude Messages API JSON 모드 백엔드 (env opt-in 제공자).

    공식 ``anthropic`` SDK 사용(claude-api 스킬 권장 — Python에서 raw HTTP 금지). 키는
    ``ANTHROPIC_API_KEY`` env에서만 읽는다(SDK 기본 동작). 시스템 프롬프트에 JSON-only 지시
    + prompt caching(``cache_control``)을 붙이고, 깨끗한 JSON을 위해 thinking을 끈다.
    """

    provider = "anthropic"

    def __init__(self, model: str | None = None, *, max_tokens: int = 2048) -> None:
        try:
            import anthropic
        except ModuleNotFoundError as exc:  # SDK 미설치
            raise RuntimeError(
                "anthropic SDK 미설치 — `pip install anthropic` (또는 `.[anthropic]` extra) "
                "후 LLM_PROVIDER=anthropic 사용."
            ) from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY 미설정 — Claude provider는 환경변수에서만 키를 읽는다 "
                "(코드 하드코딩 금지)."
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # ANTHROPIC_API_KEY env에서 자동 해석
        self.model = model or os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL)
        self._max_tokens = max_tokens

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]:
        sys_text = f"{system}\n\n{_JSON_ONLY}" if system else _JSON_ONLY
        system_blocks = [
            {"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}
        ]
        base: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": system_blocks,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = self._client.messages.create(
                **base, temperature=temperature, thinking={"type": "disabled"}
            )
        except (self._anthropic.BadRequestError, TypeError) as exc:
            # 두 폴백 경로를 한 번에 흡수:
            #  - BadRequestError: 모델이 temperature/thinking을 거부(예: Opus 4.7+).
            #  - TypeError: SDK 버전이 thinking kwarg를 모름(<0.47). pyproject floor는
            #    >=0.49지만 구버전 설치 환경에서도 최소 파라미터로 안전 폴백.
            log.warning("llm.anthropic_param_retry", error=str(exc)[:160])
            resp = self._client.messages.create(**base)
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
        return _safe_json(text)


class OpenAIClient:
    """OpenAI Chat Completions JSON 모드 백엔드 (env opt-in 제공자).

    공식 ``openai`` SDK 사용. 키는 ``OPENAI_API_KEY`` env에서만 읽는다(SDK 기본 동작, 코드
    하드코딩 금지). ``response_format={"type":"json_object"}``로 JSON을 강제하며, 일부 모델이
    temperature/response_format을 거부하면 단계적으로 빼고 재시도한다. import만으로는 네트워크 0.
    """

    provider = "openai"

    def __init__(self, model: str | None = None) -> None:
        try:
            import openai
        except ModuleNotFoundError as exc:  # SDK 미설치
            raise RuntimeError(
                "openai SDK 미설치 — `pip install openai` (또는 `.[openai]` extra) 후 "
                "LLM_PROVIDER=openai 사용."
            ) from exc
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY 미설정 — OpenAI provider는 환경변수에서만 키를 읽는다 "
                "(코드 하드코딩 금지)."
            )
        self._openai = openai
        self._client = openai.OpenAI()  # OPENAI_API_KEY env에서 자동 해석
        self.model = model or os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]:
        sys_text = f"{system}\n\n{_JSON_ONLY}" if system else _JSON_ONLY
        base: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": prompt},
            ],
        }
        json_fmt = {"response_format": {"type": "json_object"}}
        try:
            resp = self._client.chat.completions.create(**base, temperature=temperature, **json_fmt)
        except (self._openai.BadRequestError, TypeError) as exc:
            # 일부 모델(o1/o3 등)이 temperature 또는 response_format을 거부 → 단계적 폴백.
            log.warning("llm.openai_param_retry", error=str(exc)[:160])
            try:
                resp = self._client.chat.completions.create(**base, **json_fmt)
            except (self._openai.BadRequestError, TypeError):
                resp = self._client.chat.completions.create(**base)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        return _safe_json(text)


def default_llm() -> LlmClient:
    """게이트된 기본 클라이언트. 제공자별 게이트:

    - ``LLM_PROVIDER=anthropic``: ``ANTHROPIC_API_KEY`` 필요 → :class:`AnthropicClient`.
    - ``LLM_PROVIDER=openai``: ``OPENAI_API_KEY`` 필요 → :class:`OpenAIClient`.
    - 그 외(기본 ollama): ``ENABLE_LLM=1`` 필요 → :class:`OllamaClient`.
    """
    provider = current_provider()
    if provider == "anthropic":
        return AnthropicClient()
    if provider == "openai":
        return OpenAIClient()
    if os.environ.get("ENABLE_LLM", "0") != "1":
        raise RuntimeError(
            "LLM disabled. Set ENABLE_LLM=1 and run Ollama (qwen2.5:32b), or set "
            "LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY (Claude) / LLM_PROVIDER=openai + "
            "OPENAI_API_KEY (GPT)."
        )
    return OllamaClient()
