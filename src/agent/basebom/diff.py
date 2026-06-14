"""base BOM ↔ new BOM 집합 diff → 분류(Common/New/Change/Delete) + 매칭 New P/No.

PPT는 품번을 거의 안 준다(Compact Oven 0/51) — 통합 master의 *구조화된* 변경
(어느 부품이 무슨 번호로 바뀌었나)은 **base BOM ↔ new BOM 비교**에서 나온다.

분류(2026-06-13 빌드, 통합 master Compact 정답지 형식 기준 — 정답지 어휘에 정렬):
  * **Common** : base 품번이 new BOM에 그대로 존재(미변경). v1.1 New P/No = '←'.
  * **New**    : (a) base 품번이 new에 없고 *같은 부품명*의 new-only 품번이 있음(번호 교체)
                또는 (b) new-only 품번(base 대응 없음). 정답지는 **새 P/No가 발번된 부품을
                "신규(New)"로 분류**(54/68) — 번호 교체도 New다(2026-06-13 역공학으로 확정).
  * **Delete** : base 품번이 new에 없고 교체 후보도 없음(삭제).
  * (정답지의 "Change" 9건 = 품번 동일·스펙만 변경 → pno 집합차로는 Common에 묻힘.
     attribute diff 없이는 회수 불가 → 진단상 분류 미스로 남김.)

이 diff는 *제안*이다 — raw 집합차는 잡음(재번호/재정렬)이 많아 정답지의 큐레이션된
변경보다 과다 생성될 수 있다. 실제 확정은 HITL/PPT 결합으로 가지친다(절대원칙 #4).
LLM/DB/네트워크 0회.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.agent.basebom.parser import BaseBom, _norm_pno

__all__ = ["DiffRow", "diff_boms", "diff_summary"]


def _name_key(s: str) -> str:
    """부품명 정규화 키 (대문자·영숫자/한글만). 교체(Change) 매칭용."""
    return re.sub(r"[^A-Z0-9가-힣]+", "", str(s or "").upper())


@dataclass
class DiffRow:
    base_pno: str            # base BOM 품번 ('' = New only)
    new_pno: str             # 매칭된 new 품번 (Common=base와 동일, Delete='', Change=교체품번, New=신규품번)
    part_name: str
    lvl: str
    qty: str
    part_type: str
    classification: str      # Common / Change / Delete / New
    base_row_id: int | None  # base BOM 행 id (New은 None)
    matched_by: str | None   # 'pno'(Common) / 'name'(Change) / None(Delete·New)
    confidence: str = ""     # refine 단계가 채움: high/medium/low (가지치기·랭킹용, 진단)


def diff_boms(base: BaseBom, new: BaseBom) -> list[DiffRow]:
    """base ↔ new BOM → DiffRow 리스트. **base 트리 순서 보존** + 끝에 New 추가.

    1패스: base 품번이 new에 있으면 Common(해당 new 행 소비). 2패스: base 트리 순서대로
    Common / (부품명 매칭) Change / Delete 판정. 남은 new-only는 New로 append.
    """
    new_by_pno = new.index_by_pno()  # norm pno → [row_id]
    # Common 매칭으로 소비된 new 행
    consumed_new: set[int] = set()
    for r in base.rows:
        bk = _norm_pno(r.part_no)
        if bk and bk in new_by_pno:
            consumed_new.update(new_by_pno[bk])

    # new-only 행(미소비) — Change 교체 후보 / New
    new_only = [r for r in new.rows if r.row_id not in consumed_new]
    new_only_by_name: dict[str, list] = {}
    for nr in new_only:
        new_only_by_name.setdefault(_name_key(nr.part_name), []).append(nr)
    used_new: set[int] = set()

    out: list[DiffRow] = []
    for r in base.rows:
        bk = _norm_pno(r.part_no)
        if bk and bk in new_by_pno:
            out.append(DiffRow(r.part_no, r.part_no, r.part_name, r.lvl, r.qty, r.type,
                               "Common", r.row_id, "pno"))
            continue
        # base-only — 같은 부품명의 new-only가 있으면 Change(교체), 없으면 Delete.
        nk = _name_key(r.part_name)
        cand = next((nr for nr in new_only_by_name.get(nk, []) if nr.row_id not in used_new), None) if nk else None
        if cand is not None:
            used_new.add(cand.row_id)
            # 번호 교체 = 정답지 어휘로 "New"(새 P/No 발번). matched_by='name'로 교체임을 보존.
            out.append(DiffRow(r.part_no, cand.part_no, r.part_name, r.lvl, r.qty, r.type,
                               "New", r.row_id, "name"))
        else:
            out.append(DiffRow(r.part_no, "", r.part_name, r.lvl, r.qty, r.type,
                               "Delete", r.row_id, None))

    # 남은 new-only → New (base 대응 없음). new 트리 순서 보존.
    for nr in new_only:
        if nr.row_id in used_new:
            continue
        out.append(DiffRow("", nr.part_no, nr.part_name, nr.lvl, nr.qty, nr.type,
                           "New", None, None))
    return out


def diff_summary(rows: list[DiffRow]) -> dict[str, int]:
    """분류별 집계 (잡음/규모 진단용)."""
    from collections import Counter
    c = Counter(r.classification for r in rows)
    return {"total": len(rows), "Common": c["Common"], "Change": c["Change"],
            "Delete": c["Delete"], "New": c["New"],
            "changed": c["Change"] + c["Delete"] + c["New"]}
