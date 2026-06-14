"""diff_boms + master_writer 잠금 테스트 (결정론, 합성 데이터).

정답지 어휘 정렬(번호교체=New)·트리순서 보존·중복 new pno 미중복소비·
master 셀 규약(Common='←'/'-', Delete='X', New=교체품번/<발번대기>)을 고정한다.
"""
from __future__ import annotations

from src.agent.basebom.diff import diff_boms, diff_summary
from src.agent.basebom.parser import BaseBom, BaseBomRow
from src.agent.docgen.master_writer import rows_from_diff


def _bom(model: str, rows: list[tuple[str, str, str, str]]) -> BaseBom:
    """rows = [(part_no, part_name, lvl, qty), ...] → BaseBom."""
    brs = [
        BaseBomRow(row_id=i, part_no=p, part_name=n, parent_part_no="", lvl=lv,
                   depth=len(lv) - len(lv.lstrip(".")) or 1, bom_path=n, qty=q, type="T",
                   excel_row=i + 2)
        for i, (p, n, lv, q) in enumerate(rows)
    ]
    return BaseBom(model=model, file_name=f"{model}.xlsx", rows=brs)


def test_classification_and_order():
    base = _bom("B", [
        ("A100", "Packing,Base", ".1", "1"),      # Common (also in new)
        ("A200", "Heater,Sheath", ".1", "1"),      # base-only, name matches new B201 → New(번호교체)
        ("A300", "Old,Bracket", ".1", "1"),        # base-only, no new name → Delete
    ])
    new = _bom("N", [
        ("A100", "Packing,Base", ".1", "1"),      # Common
        ("B201", "Heater,Sheath", ".1", "1"),      # 교체 대상 (이름 동일)
        ("C400", "Brand,New", ".1", "2"),          # new-only → New
    ])
    rows = diff_boms(base, new)
    # 트리순서 보존: 처음 3행 = base 순서.
    assert [r.base_pno for r in rows[:3]] == ["A100", "A200", "A300"]
    cls = {r.base_pno or r.new_pno: r.classification for r in rows}
    assert cls["A100"] == "Common"
    assert cls["A200"] == "New"        # 번호교체도 정답지 어휘로 New
    assert cls["A300"] == "Delete"
    assert cls["C400"] == "New"        # new-only
    # A200은 B201로 교체 매칭.
    a200 = next(r for r in rows if r.base_pno == "A200")
    assert a200.new_pno == "B201" and a200.matched_by == "name"
    summ = diff_summary(rows)
    assert summ["Common"] == 1 and summ["Delete"] == 1 and summ["New"] == 2


def test_duplicate_new_pno_not_double_consumed():
    base = _bom("B", [("A1", "Widget", ".1", "1")])
    new = _bom("N", [("A1", "Widget", ".1", "1"), ("A1", "Widget", ".1", "1")])
    rows = diff_boms(base, new)
    # base A1 = Common; 중복 new A1 두 개 모두 소비 → 잔여 New 없음.
    assert [r.classification for r in rows] == ["Common"]


def test_two_base_same_name_one_new_only():
    base = _bom("B", [("A1", "Plate", ".1", "1"), ("A2", "Plate", ".1", "1")])
    new = _bom("N", [("Z9", "Plate", ".1", "1")])  # 단 하나의 교체 후보
    rows = diff_boms(base, new)
    klass = sorted(r.classification for r in rows)
    # 하나만 New(교체) 차지, 다른 하나는 Delete (used_new 가드).
    assert klass == ["Delete", "New"]
    new_row = next(r for r in rows if r.classification == "New")
    assert new_row.new_pno == "Z9"


def test_master_cell_conventions():
    base = _bom("B", [
        ("A100", "Keep", ".1", "1"),
        ("A200", "Swap", ".1", "1"),
        ("A300", "Gone", ".1", "1"),
    ])
    new = _bom("N", [("A100", "Keep", ".1", "1"), ("B201", "Swap", ".1", "3")])
    rows = rows_from_diff(diff_boms(base, new))
    by = {r.base_pno or r.part_name: r for r in rows}
    # Common: New P/No='←', 변경점/사유='-'
    assert by["A100"].new_pno == "←" and by["A100"].changing_point == "-" and by["A100"].classification == "Common"
    # New(교체): New P/No=교체품번
    assert by["A200"].new_pno == "B201" and by["A200"].classification == "New"
    # Delete: New P/No='X'
    assert by["A300"].new_pno == "X" and by["A300"].classification == "Delete"


def test_new_without_number_gets_placeholder():
    base = _bom("B", [("A100", "Keep", ".1", "1")])
    new = _bom("N", [("A100", "Keep", ".1", "1"), ("", "Nameless New", ".1", "1")])
    rows = rows_from_diff(diff_boms(base, new))
    nw = next(r for r in rows if r.classification == "New")
    # 신규인데 품번 없음 → <발번대기> (절대원칙: 신규 임의품번 생성 금지).
    assert nw.new_pno == "<발번대기>"
