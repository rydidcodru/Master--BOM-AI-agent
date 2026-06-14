"""L2 멀티쿼리 확장 테스트 — base 보존·신규 추가·중복제거·결정론 fallback (fake LLM, 네트워크 0)."""
from __future__ import annotations

from src.agent.orchestrator.expand import expand_queries


class _FakeLlm:
    def __init__(self, queries):
        self._q = queries
        self.calls = 0

    def complete_json(self, prompt, *, system=None, temperature=0.0):
        self.calls += 1
        assert system  # 재작성기 시스템 프롬프트 주입 확인
        return {"queries": self._q}


class _ErrLlm:
    def complete_json(self, *a, **k):
        raise RuntimeError("boom")


def test_expand_keeps_base_first_and_appends_extra():
    out = expand_queries("카메라 모듈 적용", _FakeLlm(["카메라 장착 구조", "Door 카메라 하네스"]),
                         base_queries=["카메라 모듈 적용", "카메라"])
    assert out[:2] == ["카메라 모듈 적용", "카메라"]  # base 우선
    assert "카메라 장착 구조" in out and "Door 카메라 하네스" in out


def test_expand_dedups_case_insensitive():
    out = expand_queries("q", _FakeLlm(["Q", "  q  ", "새문구"]), base_queries=["q"])
    assert out == ["q", "새문구"]  # 'Q'/'  q  '는 base 'q'와 중복


def test_expand_fallback_on_error_returns_base():
    out = expand_queries("q", _ErrLlm(), base_queries=["q", "r"])
    assert out == ["q", "r"]


def test_expand_noop_without_llm():
    assert expand_queries("q", None, base_queries=["q", "r"]) == ["q", "r"]


def test_expand_default_base_is_query():
    out = expand_queries("바뀐점", None)
    assert out == ["바뀐점"]


def test_expand_caps_total():
    out = expand_queries("q", _FakeLlm([f"x{i}" for i in range(10)]),
                         base_queries=["q"], max_extra=10, max_total=4)
    assert len(out) == 4
    assert out[0] == "q"


def test_expand_max_extra_zero_returns_base_only():
    llm = _FakeLlm(["x"])
    out = expand_queries("q", llm, base_queries=["q"], max_extra=0)
    assert out == ["q"]
    assert llm.calls == 0  # max_extra=0이면 LLM 미호출
