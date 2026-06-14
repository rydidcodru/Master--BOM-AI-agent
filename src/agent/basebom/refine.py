"""diff 과다생성 가지치기 + 신뢰도 (결정론, base↔new BOM 집합차 후처리).

`diff_boms`는 full BOM 집합차라 정답지(큐레이션 68건)보다 변경을 과다 생성한다(≈253).
FP 구성 분석(2026-06-14)으로 확인된 **깨끗한** 정리만 적용하고, 깎으면 recall이 무너지는
중복-부품명 매칭은 **삭제하지 않고 신뢰도로 랭킹**한다(절대원칙: 검색/회수 우선).

규칙:
  1. **가짜 삭제 제거** — classification=Delete 인데 *같은 부품명*이 new BOM에 존재하면
     진짜 삭제가 아니라 번호개정 이월 → **Common 으로 재분류**(FP 43건 제거, 실삭제 6건 보존).
  2. **신뢰도(confidence)**:
     - **high**  : 번호교체(New, matched_by='name')이고 부품명이 base/new에서 **고유**(≤1회).
                   → FP 분석상 정밀도 0.81(21 TP / 5 FP). 큐레이션 변경에 가장 근접.
     - **medium**: 중복 부품명 번호교체 / 실삭제(Delete).
     - **low**   : new-only 신규(matched_by=None) — 정답지는 거의 미수록(40 FP / 1 TP).

신뢰도는 **진단/랭킹용**(정답지에 신뢰도 컬럼 없음 → master xlsx엔 미기입). UI/사용자는
high 티어만 보면 정밀도 높은 변경 집합을, 전체를 보면 recall 높은 집합을 얻는다.
LLM/DB/네트워크 0회.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from src.agent.basebom.diff import DiffRow, _name_key
from src.agent.basebom.parser import BaseBom

__all__ = ["refine_diff", "refine_summary"]


def refine_diff(diff_rows: list[DiffRow], base: BaseBom, new: BaseBom) -> list[DiffRow]:
    """DiffRow 리스트 → 가지치기·재분류·신뢰도 적용된 새 리스트(순서 보존)."""
    new_name_count = Counter(_name_key(r.part_name) for r in new.rows if r.part_name)
    base_name_count = Counter(_name_key(r.part_name) for r in base.rows if r.part_name)

    out: list[DiffRow] = []
    for d in diff_rows:
        nk = _name_key(d.part_name)
        nd = d
        # 규칙 1 — 가짜 삭제만 Common 이월. **부품명이 new에 ≥2회** 존재할 때만(범용 부품
        # 컴포넌트 = 캐패시터/코일/저항류 이월). 고유명(≤1회) 삭제는 진짜 삭제일 수 있어
        # 보존한다(정답지 'Assy 단위 공급' 삭제 = 부품명이 new에 1회 남는 케이스 → recall 보호).
        if d.classification == "Delete" and nk and new_name_count.get(nk, 0) >= 2:
            nd = replace(d, classification="Common", new_pno=d.base_pno,
                         matched_by="name-carry")
        # 규칙 2 — 신뢰도.
        conf = ""
        if nd.classification in ("New", "Change") and nd.matched_by == "name":
            unique = base_name_count.get(nk, 0) <= 1 and new_name_count.get(nk, 0) <= 1
            conf = "high" if unique else "medium"
        elif nd.classification == "Delete":
            conf = "medium"
        elif nd.classification in ("New", "Change") and nd.matched_by is None:
            conf = "low"
        out.append(replace(nd, confidence=conf))
    return out


def refine_summary(rows: list[DiffRow]) -> dict[str, object]:
    """가지치기 후 분류·신뢰도 집계."""
    cls = Counter(r.classification for r in rows)
    conf = Counter(r.confidence for r in rows if r.classification != "Common")
    changed = [r for r in rows if r.classification != "Common"]
    return {
        "total": len(rows),
        "changed": len(changed),
        "by_classification": dict(cls),
        "changed_by_confidence": dict(conf),
    }
