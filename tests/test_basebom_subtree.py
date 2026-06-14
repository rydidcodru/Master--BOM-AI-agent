"""walk_base_bom_subtree — 들여쓰기 BOM 하위 전개(유저 지정 레벨) 테스트 (DB/파일 불필요)."""

from __future__ import annotations

from src.agent.basebom.parser import BaseBom, BaseBomRow, walk_base_bom_subtree


def _row(rid: int, pno: str, name: str, depth: int) -> BaseBomRow:
    return BaseBomRow(
        row_id=rid, part_no=pno, part_name=name, parent_part_no="",
        lvl="." * depth + str(depth), depth=depth, bom_path=name, qty="1",
    )


def _bom() -> BaseBom:
    # A(1) ├ B(2) └ C(3) ; A ├ D(2) ; E(1) sibling of A ; E └ F(2)
    rows = [
        _row(0, "A", "Asm A", 1),
        _row(1, "B", "B", 2),
        _row(2, "C", "C", 3),
        _row(3, "D", "D", 2),
        _row(4, "E", "Asm E", 1),
        _row(5, "F", "F", 2),
    ]
    return BaseBom(model="M", file_name="t.xlsx", rows=rows)


def test_subtree_depth1_direct_children_only():
    sub = walk_base_bom_subtree(_bom(), "A", max_depth=1)
    assert [(s.part_no, s.rel_level) for s in sub] == [("B", 1), ("D", 1)]  # C(d3) 제외, E 형제로 종료


def test_subtree_depth2_includes_grandchild():
    sub = walk_base_bom_subtree(_bom(), "A", max_depth=2)
    assert [(s.part_no, s.rel_level) for s in sub] == [("B", 1), ("C", 2), ("D", 1)]


def test_subtree_stops_at_sibling():
    # E의 서브트리는 F만 (A 쪽으로 새지 않음)
    assert [s.part_no for s in walk_base_bom_subtree(_bom(), "E", max_depth=3)] == ["F"]


def test_subtree_norm_pno_and_missing():
    assert [s.part_no for s in walk_base_bom_subtree(_bom(), " a ", max_depth=1)] == ["B", "D"]  # 정규화
    assert walk_base_bom_subtree(_bom(), "ZZZ", max_depth=2) == []  # 부재
    assert walk_base_bom_subtree(_bom(), "C", max_depth=2) == []  # 잎노드(자식 없음)
