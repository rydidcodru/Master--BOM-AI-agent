"""Phase 1 — L1 변경점 유도 v3 (derive_change_slots) 테스트.

표 기반(작업 지시 §1.5): 결정론 R0~R6 경로별 기대 슬롯 + LLM 2차 가드.
추가 단언: ① 결정론 경로 LLM import/호출 0회, ② grounding이 사유 밖 영숫자 토큰
(가짜 품번) 거부, ③ ``DERIVE_CP=0``이면 intent_from_change queries 불변.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import src.agent.intent.derive as derive_mod
from src.agent.intent.derive import ChangeSlot, derive_change_slots, generic_labels
from src.agent.intent.structurizer import intent_from_change


class FakeLlm:
    """주입 payload를 그대로 반환하는 LLM (호출 횟수 기록)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def complete_json(
        self, prompt: str, *, system: str | None = None, temperature: float = 0.0
    ) -> dict[str, Any]:
        self.calls += 1
        return self.payload


class PoisonLlm:
    """호출되면 즉시 실패 — 결정론 경로에서 LLM 미호출 보장용."""

    def complete_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("결정론 경로에서 LLM이 호출되면 안 된다")


# ── 결정론 1차 (R0~R6) — 작업 지시 §1.5 표 ──────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "part_name", "expected"),
    [
        # R2+R3, 수정→변경
        ("히터 체결부 간섭으로 브래킷 형상 수정 필요", None, ("브래킷", "형상", "변경")),
        # R2
        ("원가 절감을 위해 재질 변경", None, ("재질", None, "변경")),
        # R2
        ("낙하 시험시 파손 발생하여 리브 보강", None, ("리브", None, "보강")),
        # R5 합성 (target 없음 → part_name)
        ("간섭 회피 위해 형상 수정", "BRACKET", ("BRACKET", "형상", "변경")),
        # R1 (무마커 직결형)
        ("히터 브래킷 형상 변경 요망", None, ("히터 브래킷", "형상", "변경")),
        # R1 — 말단 명사구가 attribute("법랑 종류")로 분리
        ("Tray 법랑 종류 변경", None, ("Tray", "법랑 종류", "변경")),
        # cue 사상 (불요/제외 → 삭제)
        ("워런티카드 불요하여 제외", None, ("워런티카드", None, "삭제")),
    ],
)
def test_deterministic_slots(reason, part_name, expected):
    result = derive_change_slots(reason, part_name, llm=PoisonLlm())  # type: ignore[arg-type]
    assert result is not None, f"결정론 유도 실패: {reason!r}"
    slots, source = result
    assert source == "det"
    assert (slots[0].target, slots[0].attribute, slots[0].action) == expected


@pytest.mark.parametrize("reason", ["원가 절감", "법규 대응"])
def test_deterministic_failure_returns_none_without_llm(reason):
    # R4 실패(행위어 없음) — llm 미주입이면 None.
    assert derive_change_slots(reason, None, llm=None) is None


def test_compound_reason_yields_multiple_slots():
    result = derive_change_slots("사이즈 축소 및 수량 변경", None)
    assert result is not None
    slots, source = result
    assert source == "det"
    assert len(slots) == 2
    assert (slots[0].target, slots[0].action) == ("사이즈", "축소")
    assert (slots[1].target, slots[1].action) == ("수량", "변경")


# ── LLM 2차 — 결정론 실패분만 + 코드 측 이중 가드 ───────────────────────────


def test_llm_second_pass_on_deterministic_failure():
    llm = FakeLlm({"changes": [{"target": "원가", "attribute": None, "action": "축소"}]})
    result = derive_change_slots("원가 절감", None, llm=llm)  # type: ignore[arg-type]
    assert result is not None
    slots, source = result
    assert source == "llm" and llm.calls == 1
    assert (slots[0].target, slots[0].action) == ("원가", "축소")


def test_llm_empty_changes_returns_none():
    llm = FakeLlm({"changes": []})
    assert derive_change_slots("법규 대응", None, llm=llm) is None  # type: ignore[arg-type]
    assert llm.calls == 1


def test_grounding_rejects_alnum_token_outside_reason():
    # 가짜 품번류 영숫자 토큰 — 사유/부품명에 없으면 슬롯 폐기 → None.
    llm = FakeLlm({"changes": [{"target": "ABC1234567", "action": "변경"}]})
    assert derive_change_slots("원가 절감", None, llm=llm) is None  # type: ignore[arg-type]


def test_invalid_action_literal_discards_slot():
    llm = FakeLlm({"changes": [{"target": "원가", "action": "개선"}]})  # enum 밖
    assert derive_change_slots("원가 절감", None, llm=llm) is None  # type: ignore[arg-type]


def test_grounded_attribute_required():
    llm = FakeLlm(
        {"changes": [{"target": "원가", "attribute": "두께", "action": "변경"}]}
    )  # attribute "두께"는 사유에 없음 → 폐기
    assert derive_change_slots("원가 절감", None, llm=llm) is None  # type: ignore[arg-type]


# ── generic_labels (vocab generic_map 조회) ─────────────────────────────────


def test_generic_labels_lookup():
    slots = [ChangeSlot(target="재질", action="변경")]
    assert generic_labels(slots) == ["하위 원재료 변경"]
    slots = [ChangeSlot(target="브래킷", attribute="형상", action="변경")]
    assert generic_labels(slots) == ["구조 변경"]
    slots = [ChangeSlot(target="사이즈", action="축소")]
    assert generic_labels(slots) == ["사이즈 축소"]


# ── intent_from_change 통합 (DERIVE_CP 게이트) ──────────────────────────────

_REASON = "원가 절감을 위해 재질 변경"


def test_intent_from_change_gate_off_keeps_queries(monkeypatch):
    monkeypatch.setenv("DERIVE_CP", "0")
    intent = intent_from_change(change_detail="", change_reason=_REASON)
    assert intent.rewritten_queries == [_REASON]  # ③ 게이트 off → 기존과 동일
    assert intent.derived_change_slots == []
    assert intent.derived_cp_source is None


def test_intent_from_change_gate_on_appends_aligned_queries(monkeypatch):
    monkeypatch.delenv("DERIVE_CP", raising=False)  # 기본 on
    monkeypatch.delenv("ENABLE_LLM", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    intent = intent_from_change(change_detail="", change_reason=_REASON)
    assert intent.rewritten_queries == [
        _REASON,
        f"재질 변경 {_REASON}",            # ① 합성형 정렬 (합본형)
        f"하위 원재료 변경 {_REASON}",      # ② 라벨형 정렬 (합본형)
    ]
    assert intent.derived_cp_source == "det"
    assert [s.target for s in intent.derived_change_slots] == ["재질"]


def test_intent_from_change_detail_present_skips_derive(monkeypatch):
    monkeypatch.delenv("DERIVE_CP", raising=False)
    intent = intent_from_change(change_detail="브래킷 형상 변경", change_reason="원가 절감")
    assert intent.derived_change_slots == []
    assert intent.derived_cp_source is None


def test_intent_from_change_derive_failure_keeps_queries(monkeypatch):
    monkeypatch.delenv("DERIVE_CP", raising=False)
    monkeypatch.delenv("ENABLE_LLM", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    intent = intent_from_change(change_detail="", change_reason="원가 절감")
    assert intent.rewritten_queries == ["원가 절감"]  # 유도 실패 → 불변


def test_intent_from_change_query_cap_with_parts(monkeypatch):
    monkeypatch.delenv("DERIVE_CP", raising=False)
    monkeypatch.delenv("ENABLE_LLM", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    intent = intent_from_change(
        change_detail="", change_reason=_REASON, part_name="BRACKET"
    )
    assert len(intent.rewritten_queries) <= 4
    assert intent.rewritten_queries[0] == _REASON
    # 부품 보강 쿼리는 cap 안에서 유지된다 (parts 채널 매칭용 식별 토큰 포함).
    assert any("BRACKET" in q for q in intent.rewritten_queries)


# ── 결정론 경로 LLM 격리 ────────────────────────────────────────────────────


def test_derive_module_no_runtime_llm_import():
    """derive.py는 LLM/네트워크를 런타임 import하지 않는다 (TYPE_CHECKING 전용)."""
    src_text = inspect.getsource(derive_mod)
    assert "ollama" not in src_text.lower()
    assert "import requests" not in src_text
    for line in src_text.splitlines():
        if "src.agent.llm" in line and "import" in line:
            assert line.startswith("    "), "agent.llm import는 TYPE_CHECKING 블록 안에서만"


def test_deterministic_path_never_calls_llm():
    result = derive_change_slots("히터 브래킷 형상 변경 요망", None, llm=PoisonLlm())  # type: ignore[arg-type]
    assert result is not None and result[1] == "det"
