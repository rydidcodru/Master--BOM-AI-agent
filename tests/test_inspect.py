"""정형화·검색 검수 로직 테스트 (LLM/DB 불필요 — fake 주입)."""

from __future__ import annotations

from src.agent.inspect import (
    BackendStatus,
    SearchView,
    detect_backend,
    formalize,
    inspect_query,
    run_search,
)


class _FakeLlm:
    provider = "anthropic"
    model = "claude-sonnet-4-6"

    def __init__(self, slots: dict):
        self._slots = slots

    def complete_json(self, prompt, *, system=None, temperature=0.0):
        return dict(self._slots)


class _BoomLlm:
    provider = "anthropic"
    model = "claude-sonnet-4-6"

    def complete_json(self, *a, **k):
        raise RuntimeError("network down")


# ── 정형화 ──────────────────────────────────────────────────────────────────


def test_formalize_reason_only_queries():
    fv = formalize(change_log="패킹 재질 변경", change_reason="고온 변형 대응")
    # 검색 키는 변경내역+변경사유에서만 — 식별자(part_no/model)는 비어 있어야 한다
    assert fv.reason_intent.part_nos == []
    assert fv.reason_intent.models == []
    assert fv.reason_intent.source == "ppt"
    assert any("패킹" in q for q in fv.reason_queries)
    assert fv.llm_intent is None
    assert fv.llm_provider == "none"


def test_search_queries_include_claude_rewrites():
    slots = {
        "change_attribute": "색상",
        "change_direction": "대체",
        "intent_summary": "외관 색상 변경",
        "rewritten_queries": ["컨트롤러 외관 색상 블랙 변경", "지역 디자인 차별화 색상"],
        "confidence": 0.85,
    }
    fv = formalize(change_log="외관 변경", change_reason="디자인 차별화", llm=_FakeLlm(slots))
    # 검색 쿼리에 reason + Claude 재작성이 모두 들어가야 한다 (Claude 재작성이 검색에 사용됨)
    assert any("색상" in q for q in fv.search_queries), fv.search_queries
    assert set(fv.reason_queries).issubset(set(fv.search_queries))
    assert "컨트롤러 외관 색상 블랙 변경" in fv.search_queries


def test_search_queries_sanitize_injected_identifier():
    # Claude가 입력에 없던 식별자(part_no)를 끌어들이면 검색 쿼리에서 제거되어야 한다
    slots = {
        "change_attribute": "재질",
        "change_direction": "대체",
        "intent_summary": "재질 변경",
        "rewritten_queries": ["MCR68450803 재질 변경", "내열 재질 적용"],
        "confidence": 0.8,
    }
    fv = formalize(change_log="패킹 재질 변경", change_reason="고온 대응", llm=_FakeLlm(slots))
    joined = " ".join(fv.search_queries)
    assert "MCR68450803" not in joined  # 입력에 없던 식별자 제거
    assert any("재질 변경" in q for q in fv.search_queries)  # 나머지 텍스트는 유지


def test_formalize_includes_part_name_and_ids():
    # 부품명/품번을 주면 검색 쿼리에 부품 보강 쿼리가 포함된다(2026-06-10 확정).
    fv = formalize(
        change_log="외관 변경",
        change_reason="디자인 차별화",
        part_name="컨트롤러",
        part_nos=["MCR68450803"],
    )
    assert fv.reason_intent.part_nos == ["MCR68450803"]
    joined = " ".join(fv.search_queries)
    assert "컨트롤러" in joined
    assert "MCR68450803" in joined


def test_formalize_keeps_user_supplied_identifier_through_sanitize():
    # 사용자가 준 품번은 allowed에 포함 → Claude 재작성에 그 품번이 있어도 유지(환각만 제거).
    slots = {
        "change_attribute": "재질",
        "change_direction": "대체",
        "intent_summary": "재질 변경",
        "rewritten_queries": ["MCR68450803 재질 변경", "AAA99999999 끌어온 환각"],
        "confidence": 0.8,
    }
    fv = formalize(
        change_log="패킹 재질 변경",
        change_reason="고온 대응",
        part_nos=["MCR68450803"],
        llm=_FakeLlm(slots),
    )
    joined = " ".join(fv.search_queries)
    assert "MCR68450803" in joined        # 사용자가 준 식별자 → 유지
    assert "AAA99999999" not in joined     # 입력에 없던 환각 식별자 → 제거


def test_formalize_with_claude_slots():
    slots = {
        "change_attribute": "재질",
        "change_direction": "대체",
        "intent_summary": "패킹 재질을 내열 실리콘으로 변경",
        "rewritten_queries": ["내열 패킹 변경", "실리콘 패킹 고온"],
        "confidence": 0.85,
    }
    fv = formalize(
        change_log="패킹 재질 변경", change_reason="고온 변형 대응", llm=_FakeLlm(slots)
    )
    assert fv.llm_provider == "anthropic"
    assert fv.llm_model == "claude-sonnet-4-6"
    assert fv.llm_intent is not None
    assert fv.llm_intent.source == "regex+llm"  # LLM 슬롯이 실제 반영됨
    assert fv.llm_intent.change_attribute == "재질"
    assert "내열 패킹 변경" in fv.llm_queries


def test_formalize_llm_failure_falls_back():
    # structurize는 LLM 실패를 삼키고 결정론 intent를 돌려준다 → source != regex+llm
    fv = formalize(change_log="X 변경", change_reason="Y 대응", llm=_BoomLlm())
    assert fv.llm_intent is not None
    assert fv.llm_intent.source != "regex+llm"
    assert fv.reason_intent.raw_text  # 정형화는 항상 성립


# ── 백엔드 감지 / 검색 폴백 ───────────────────────────────────────────────────


class _BoomFactory:
    """`with sf() as s:` 진입 시 예외 → DB 미가용 시뮬레이션."""

    def __call__(self):
        raise RuntimeError("could not connect")


def test_detect_backend_unavailable():
    status = detect_backend(session_factory=_BoomFactory())
    assert status.status == "no_backend"
    assert status.factory is None


def test_run_search_skips_when_no_backend():
    fv = formalize(change_log="패킹 변경", change_reason="고온 대응")
    backend = BackendStatus("no_backend", "DB 없음", "-", None)
    sv = run_search(fv.reason_intent, fv.reason_queries, backend)
    assert isinstance(sv, SearchView)
    assert sv.status == "no_backend"
    assert sv.candidates == []


def test_inspect_query_end_to_end_without_db():
    res = inspect_query(
        change_log="커넥터 교체",
        change_reason="안전 규제 대응",
        session_factory=_BoomFactory(),
    )
    # 정형화는 항상, 검색은 백엔드 없으면 우아하게 폴백
    assert res.formalization.reason_intent.raw_text
    assert res.search.status == "no_backend"
    assert res.queries_used == res.formalization.reason_queries
