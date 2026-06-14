"""L2 LLM 재랭커 테스트 — 재정렬·누락보존·결정론 fallback (fake LLM, 네트워크 0)."""
from __future__ import annotations

import types

from src.agent.orchestrator.rerank import rerank_candidates


def _cand(reason: str, parts: list[str]):
    ev = types.SimpleNamespace(change_reason=reason, change_log=reason)
    lines = [types.SimpleNamespace(part_name=p) for p in parts]
    return types.SimpleNamespace(event=ev, lines=lines)


class _FakeLlm:
    def __init__(self, ranked):
        self._r = ranked
        self.calls = 0

    def complete_json(self, prompt, *, system=None, temperature=0.0):
        self.calls += 1
        assert "재랭" in (system or "") or "rerank" in (system or "").lower() or system
        return {"ranked": self._r}


class _ErrLlm:
    def complete_json(self, *a, **k):
        raise RuntimeError("boom")


def test_rerank_reorders_and_preserves_all():
    c = [_cand("r1", ["A"]), _cand("r2", ["B"]), _cand("r3", ["C"])]
    llm = _FakeLlm([3, 1])
    out = rerank_candidates("q", c, llm)
    # LLM이 고른 3,1이 먼저, 누락된 2는 원래 순서로 뒤에 보존(소스 손실 0)
    assert [x.event.change_reason for x in out] == ["r3", "r1", "r2"]
    assert llm.calls == 1


def test_rerank_fallback_on_error_preserves_original():
    c = [_cand("r1", ["A"]), _cand("r2", ["B"])]
    out = rerank_candidates("q", c, _ErrLlm())
    assert [x.event.change_reason for x in out] == ["r1", "r2"]


def test_rerank_noop_without_llm():
    c = [_cand("r1", ["A"]), _cand("r2", ["B"])]
    assert rerank_candidates("q", c, None) is c


def test_rerank_ignores_invalid_indices():
    c = [_cand("r1", ["A"]), _cand("r2", ["B"])]
    out = rerank_candidates("q", c, _FakeLlm([99, 2, "x", 1]))  # 99 범위초과·'x' 무시
    assert [x.event.change_reason for x in out] == ["r2", "r1"]
    assert len(out) == 2


def test_rerank_max_keep_truncates():
    c = [_cand(f"r{i}", [chr(65 + i)]) for i in range(5)]
    out = rerank_candidates("q", c, _FakeLlm([5, 4, 3, 2, 1]), max_keep=3)
    assert [x.event.change_reason for x in out] == ["r4", "r3", "r2"]
