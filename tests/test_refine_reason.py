"""refine_diff(가지치기·신뢰도) + ppt_reason_map(사유 보강) 잠금 테스트.

가짜 삭제 제거(범용명만)·고유명 delete 보존·신뢰도 티어·PPT 사유 결합을 고정.
"""
from __future__ import annotations

from src.agent.basebom.diff import diff_boms
from src.agent.basebom.parser import BaseBom, BaseBomRow
from src.agent.basebom.refine import refine_diff, refine_summary
from src.agent.docgen.reason_join import ppt_reason_map


def _bom(model: str, rows: list[tuple[str, str, str, str]]) -> BaseBom:
    brs = [
        BaseBomRow(row_id=i, part_no=p, part_name=n, parent_part_no="", lvl=lv,
                   depth=len(lv) - len(lv.lstrip(".")) or 1, bom_path=n, qty=q, type="T",
                   excel_row=i + 2)
        for i, (p, n, lv, q) in enumerate(rows)
    ]
    return BaseBom(model=model, file_name=f"{model}.xlsx", rows=brs)


def test_fake_delete_generic_name_reclassified_common():
    # 범용명 'Capacitor'가 base 2개·new 2개(번호개정). 한 base가 매칭 안 돼 Delete로 떨어짐
    # → new에 같은 이름 ≥2회 존재하므로 가짜 삭제 → Common 이월.
    base = _bom("B", [
        ("CAP001", "Capacitor", ".1", "1"),
        ("CAP002", "Capacitor", ".1", "1"),
    ])
    new = _bom("N", [
        ("CAP101", "Capacitor", ".1", "1"),
        ("CAP102", "Capacitor", ".1", "1"),
    ])
    refined = refine_diff(diff_boms(base, new), base, new)
    # 둘 다 진짜 삭제로 남으면 안 됨 — 최소 하나는 Common 이월.
    dels = [r for r in refined if r.classification == "Delete"]
    assert not dels, f"범용명 가짜 삭제가 남음: {[r.part_no_base if hasattr(r,'part_no_base') else r.base_pno for r in dels]}"


def test_unique_name_delete_preserved():
    # 고유명 'Handle,Door' 삭제 — new에 같은 이름 없음 → 진짜 삭제 보존.
    base = _bom("B", [("HND001", "Handle,Door", ".1", "1"), ("KEEP1", "Frame", ".1", "1")])
    new = _bom("N", [("KEEP1", "Frame", ".1", "1")])
    refined = refine_diff(diff_boms(base, new), base, new)
    hnd = next(r for r in refined if r.base_pno == "HND001")
    assert hnd.classification == "Delete"


def test_confidence_tiers():
    base = _bom("B", [
        ("A100", "Cavity Assembly", ".1", "1"),   # 고유명 번호교체 → high
        ("KEEP", "Frame", ".1", "1"),
    ])
    new = _bom("N", [
        ("B200", "Cavity Assembly", ".1", "1"),   # 교체 대상
        ("KEEP", "Frame", ".1", "1"),
        ("C300", "Brand New Sensor", ".1", "1"),  # new-only → low
    ])
    refined = refine_diff(diff_boms(base, new), base, new)
    by = {r.base_pno or r.new_pno: r for r in refined}
    assert by["A100"].confidence == "high"        # 고유명 번호교체
    assert by["C300"].confidence == "low"          # new-only 신규
    summ = refine_summary(refined)
    assert summ["changed_by_confidence"].get("high", 0) >= 1


def test_ppt_reason_map_token_overlap():
    base = _bom("B", [("A100", "Cavity Assembly", ".1", "1")])
    new = _bom("N", [("B200", "Cavity Assembly", ".1", "1")])
    refined = refine_diff(diff_boms(base, new), base, new)
    changed = [r for r in refined if r.classification != "Common"]
    ppt = [
        {"part": "캐비티", "change_detail": "Cavity 조립 방식 변경", "change_reason": "구조 변경"},
        {"part": "도어", "change_detail": "Handle 위치 변경", "change_reason": "사용성"},
    ]
    rm = ppt_reason_map(changed, ppt)
    # 'CAVITY' 토큰 겹침으로 첫 PPT 항목 사유가 A100/B200에 매핑.
    key = (changed[0].base_pno or changed[0].new_pno).upper()
    assert key in rm and "Cavity" in rm[key]


def test_ppt_reason_map_no_overlap_unassigned():
    base = _bom("B", [("A100", "Thermostat", ".1", "1")])
    new = _bom("N", [("B200", "Thermostat", ".1", "1")])
    refined = refine_diff(diff_boms(base, new), base, new)
    changed = [r for r in refined if r.classification != "Common"]
    ppt = [{"part": "도어", "change_detail": "Handle 변경", "change_reason": "사용성"}]
    rm = ppt_reason_map(changed, ppt)
    assert rm == {}  # 겹치는 토큰 없음 → 미배정
