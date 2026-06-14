"""Phase 4 — L3 Impact Analyzer 테스트 (결정론, 표 기반).

검증: 룰 발화 + priority 충돌 해소, 하향 attribute 게이트(무관 자식 KEEP),
상향 form-fit-function 채반, 발화 trace 전부 표시, **LLM import 0 (코드로 보장)**.
"""

from __future__ import annotations

import inspect

import pytest

import src.agent.impact.models as impact_models
import src.agent.impact.rules as impact_rules
from src.agent.impact import ImpactInput, compat_breaks, compat_up_depth, evaluate


@pytest.mark.parametrize(
    ("inp", "action", "tier", "expect_rule"),
    [
        (ImpactInput("P", relation="seed", event="Change"), "MODIFY", "CORE", "R08_change_part"),
        (ImpactInput("P", relation="seed", event="New"), "ADD", "CORE", "R07_new_part"),
        (ImpactInput("P", relation="seed", event="Carry-over"), "KEEP", "CORE", "R09_carryover"),
        # drbfm(prio85) overrides Change MODIFY(55) → CHECK
        (ImpactInput("P", relation="seed", event="Change", drbfm=True), "CHECK", "CORE", "R04_drbfm"),
        (ImpactInput("P", relation="seed", event="Change", uit_changed=True), "CHECK", "CORE", "R01_uit_change"),
        (ImpactInput("P", relation="seed", supply_type_changed=True), "CHECK", "CORE", "R02_supply_type_change"),
        # 상향 채반(prio90) → 부모 NEW(ADD)
        (ImpactInput("P", relation="parent", r_up_break=True), "ADD", "CASCADE", "R06_r_up_chaeban"),
    ],
)
def test_rule_firing(inp, action, tier, expect_rule):
    v = evaluate(inp)
    assert v.action == action
    assert v.tier == tier
    assert expect_rule in {f.rule_id for f in v.findings}


def test_downward_unrelated_child_keep():
    # change_attribute=재질 → 관련 군 {사출,단품}. part_type=전장 → 무관 → KEEP.
    v = evaluate(ImpactInput("C", relation="child", change_attribute="재질", part_type="전장"))
    assert v.action == "KEEP"
    assert any(f.rule_id == "STRUCT_child_unrelated" for f in v.findings)


def test_downward_related_child_check():
    v = evaluate(ImpactInput("C", relation="child", change_attribute="재질", part_type="사출"))
    assert v.action == "CHECK"


def test_upward_keep_when_compat_holds():
    v = evaluate(ImpactInput("P", relation="parent", r_up_break=False))
    assert v.action == "KEEP"


def test_conflict_shows_all_findings_and_highest_priority_wins():
    v = evaluate(
        ImpactInput("P", relation="seed", event="Change", drbfm=True, uit_changed=True)
    )
    ids = {f.rule_id for f in v.findings}
    assert {"STRUCT_seed", "R08_change_part", "R04_drbfm", "R01_uit_change"} <= ids
    assert v.action == "CHECK"  # R04 drbfm prio 85 최고
    # findings는 priority desc 정렬
    priorities = [f.priority for f in v.findings]
    assert priorities == sorted(priorities, reverse=True)


def test_impact_module_imports_no_llm():
    """L3는 LLM/네트워크를 절대 import하지 않는다 (결정론 보장)."""
    for mod in (impact_rules, impact_models):
        src = inspect.getsource(mod).lower()
        assert "agent.llm" not in src
        assert "ollama" not in src
        assert "import requests" not in src


# ── 상향 호환성 판정 compat_breaks ("호환성 깨질 때만 상위 채반" 게이트) ──


@pytest.mark.parametrize(
    ("attr", "text", "expect"),
    [
        ("치수", "", True),               # break attribute (form-fit-function)
        ("색상", "", False),              # safe attribute (순수 색상 → 비-채반)
        (None, "조립 방식 변경", True),    # break keyword
        (None, "높이 축소(567→430)", True),  # break keyword (높이 축소)
        (None, "색상 변경", False),        # safe keyword (break 신호 없음)
        ("치수", "색상 변경", True),       # break attribute가 safe keyword보다 우선
        (None, "색상 변경 및 조립 방식 변경", True),  # 복합: break(조립)가 safe(색상) 이김
        (None, "기타 사양 검토", False),   # 모호 → break_default(False)
    ],
)
def test_compat_breaks(attr, text, expect):
    broke, reason = compat_breaks(attr, text)
    assert broke is expect
    assert isinstance(reason, str) and reason


def test_compat_up_depth_from_config():
    assert compat_up_depth() == 2


def test_compat_break_drives_parent_chaeban():
    """깨짐(True)일 때만 parent가 r_up_break로 ADD 채반, 아니면 KEEP — 룰 연결 확인."""
    broke, _ = compat_breaks("치수", "조립 방식 변경")
    v = evaluate(ImpactInput("PARENT", relation="parent", r_up_break=broke))
    assert v.action == "ADD" and v.tier == "CASCADE"
    safe, _ = compat_breaks("색상", "")
    v2 = evaluate(ImpactInput("PARENT", relation="parent", r_up_break=safe))
    assert v2.action == "KEEP"
