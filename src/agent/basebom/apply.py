"""변경점을 베이스 BOM에 적용 → New BOM + 개발 master 행 (결정론).

원칙(루트 CLAUDE.md): 신규 품번 무생성(NEW=``<발번대기>``), 모든 변경/신규 행에 ``[SRC]``,
근거 없는 행 출력 금지, LLM 0회. New BOM = 베이스 BOM 전체 트리 복제 후
MODIFY(base→new)/ADD(신규)/DELETE 표기. 매칭 실패한 변경점도 누락 없이 ADD(unmatched)로 표기.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.basebom.parser import BaseBom, _norm_pno
from src.agent.docgen.generator import PNO_PLACEHOLDER

__all__ = ["NewBom", "NewBomRow", "apply_changes", "validate_new_bom"]

# base/new 칸에 들어오지만 실제 품번이 아닌 토큰.
_NON_PNO = {"", "TBD", "N/A", "NA", "-", "ASSEMBLY", "CONTROLLER", "정보 없음", "내용 없음"}
_PNO_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-./]{5,}$")


def _is_meaningful_pno(p: str) -> bool:
    s = str(p or "").strip().upper()
    if s in _NON_PNO:
        return False
    return bool(_PNO_RE.match(s)) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def _action_from_type(change_type: str, *, has_base: bool) -> str:
    t = str(change_type or "").strip().upper()
    if t in ("삭제", "DELETE", "REMOVE", "DELETED"):
        return "DELETE"
    if t in ("NEW", "신규", "신규추가", "ADD") or not has_base:
        return "ADD"
    return "MODIFY"


def _src_tag(src: dict[str, Any] | None) -> str:
    src = src or {}
    slide = src.get("slide")
    tid = src.get("table_id")
    slide_s = f"slide{slide}" if slide else "?"
    tid_s = str(tid) if tid else "?"
    return f"[SRC ppt/{slide_s}/{tid_s}]"


@dataclass
class NewBomRow:
    """New BOM 한 행. 베이스 트리 복제 + 변경 표기."""

    row_id: int | None
    part_no_base: str
    part_no_new: str  # 표기용 — NEW면 <발번대기>
    part_name: str
    bom_path: str
    depth: int
    status: str  # KEEP / MODIFY / ADD / DELETE
    is_new: bool = False
    qty: str = ""
    type: str = ""
    reason: str = ""
    src: str = ""
    matched: bool = True
    excel_row: int | None = None  # 원본 엑셀 행(서식보존 쓰기용; ADD 행은 None)


@dataclass
class NewBom:
    model: str
    rows: list[NewBomRow] = field(default_factory=list)
    unmatched: int = 0

    @property
    def changed_rows(self) -> list[NewBomRow]:
        return [r for r in self.rows if r.status != "KEEP"]

    @property
    def dev_master_rows(self) -> list[NewBomRow]:
        """개발 master 행 = 추가/변경된 부품."""
        return [r for r in self.rows if r.status in ("ADD", "MODIFY")]


def _match_row_id(bom: BaseBom, index: dict[str, list[int]], base_pno: str, part_name: str) -> int | None:
    """base_pno 우선, 없으면 part_name 토큰 겹침으로 베이스 행 매칭."""
    if _is_meaningful_pno(base_pno):
        ids = index.get(_norm_pno(base_pno))
        if ids:
            return ids[0]
    name = str(part_name or "").strip().upper()
    if len(name) < 3:
        return None
    name_toks = {t for t in re.split(r"[^A-Z0-9가-힣]+", name) if len(t) >= 3}
    if not name_toks:
        return None
    best_id = None
    best_score = 0
    for r in bom.rows:
        rtoks = {t for t in re.split(r"[^A-Z0-9가-힣]+", (r.part_name or "").upper()) if len(t) >= 3}
        score = len(name_toks & rtoks)
        if score > best_score:
            best_score = score
            best_id = r.row_id
    return best_id if best_score >= 1 else None


def apply_changes(bom: BaseBom, change_items: list[dict[str, Any]]) -> NewBom:
    """베이스 BOM + 변경항목 → New BOM (전체 트리 + 변경 표기).

    change_items: ``extract_ppt`` items 형태(change_detail/change_reason/change_type/
    base_part_no/new_part_no/part_name/src). 각 항목을 베이스 행에 매칭해 MODIFY/DELETE,
    매칭 안 되거나 신규면 ADD. 베이스의 나머지 행은 KEEP로 보존.
    """
    index = bom.index_by_pno()
    # row_id → 적용된 변경 (마지막 적용이 우선되지 않도록 첫 매칭만)
    applied: dict[int, dict[str, Any]] = {}
    adds: list[NewBomRow] = []
    unmatched = 0

    for it in change_items:
        base_pno = str(it.get("base_part_no") or "").strip()
        new_pno = str(it.get("new_part_no") or "").strip()
        part_name = str(it.get("part_name") or "").strip()
        change_type = str(it.get("change_type") or "").strip()
        reason = str(it.get("change_reason") or "").strip()
        detail = str(it.get("change_detail") or "").strip()
        src = _src_tag(it.get("src") if isinstance(it.get("src"), dict) else {})
        reason_full = f"{detail} | {reason}".strip(" |") or detail or reason

        action = _action_from_type(change_type, has_base=_is_meaningful_pno(base_pno))
        rid = _match_row_id(bom, index, base_pno, part_name)

        new_display = new_pno if _is_meaningful_pno(new_pno) else PNO_PLACEHOLDER
        is_new = not _is_meaningful_pno(new_pno)

        if action in ("MODIFY", "DELETE") and rid is not None:
            if rid in applied:
                # 같은 베이스 행에 여러 변경점 → 사유 누적(누락 금지).
                prev = applied[rid]
                if reason_full and reason_full not in prev["reason"]:
                    prev["reason"] = f"{prev['reason']} ; {reason_full}".strip(" ;")
                if src not in prev["src"]:
                    prev["src"] = f"{prev['src']} {src}".strip()
                if action == "DELETE":
                    prev["status"] = "DELETE"
            else:
                applied[rid] = {
                    "status": action,
                    "part_no_new": new_display if action == "MODIFY" else "",
                    "is_new": is_new if action == "MODIFY" else False,
                    "reason": reason_full,
                    "src": src,
                }
            continue

        # ADD 또는 매칭 실패 → 신규 행으로 표기(누락 금지).
        if rid is None and action != "ADD":
            unmatched += 1
        adds.append(
            NewBomRow(
                row_id=None,
                part_no_base=base_pno if _is_meaningful_pno(base_pno) else "",
                part_no_new=new_display,
                part_name=part_name or detail[:40],
                bom_path=str(it.get("module") or "신규"),
                depth=1,
                status="ADD",
                is_new=is_new,
                reason=reason_full,
                src=src,
                matched=(rid is not None),
            )
        )

    # 베이스 트리 전체를 New BOM으로 복제 + 변경 표기.
    rows: list[NewBomRow] = []
    for r in bom.rows:
        ch = applied.get(r.row_id)
        if ch is None:
            rows.append(
                NewBomRow(
                    row_id=r.row_id,
                    part_no_base=r.part_no,
                    part_no_new=r.part_no,
                    part_name=r.part_name,
                    bom_path=r.bom_path,
                    depth=r.depth,
                    status="KEEP",
                    qty=r.qty,
                    type=r.type,
                    src="[SRC base_bom/-/%d]" % r.row_id,
                    excel_row=r.excel_row,
                )
            )
        else:
            status = ch["status"]
            rows.append(
                NewBomRow(
                    row_id=r.row_id,
                    part_no_base=r.part_no,
                    part_no_new=(ch.get("part_no_new") or r.part_no) if status == "MODIFY" else r.part_no,
                    part_name=r.part_name,
                    bom_path=r.bom_path,
                    depth=r.depth,
                    status=status,
                    is_new=bool(ch.get("is_new")),
                    qty=r.qty,
                    type=r.type,
                    reason=str(ch.get("reason") or ""),
                    src=str(ch.get("src") or ""),
                    excel_row=r.excel_row,
                )
            )

    rows.extend(adds)
    return NewBom(model=bom.model, rows=rows, unmatched=unmatched)


def validate_new_bom(nb: NewBom) -> list[str]:
    """New BOM 출력 검증(루트 CLAUDE.md). 위반 목록 반환(빈 리스트=통과).

    - 모든 MODIFY/ADD 행에 ``[SRC]`` 출처 필수(근거 없는 행 출력 금지).
    - 모든 ``is_new`` 행의 new P/No = ``<발번대기>``(임의 신규번호 생성 금지).

    ``docgen.validate_doc``와 대칭 — 트리복제 New BOM 경로에도 동일 게이트를 적용한다.
    """
    violations: list[str] = []
    for r in nb.rows:
        if r.status not in ("MODIFY", "ADD"):
            continue
        label = r.part_name or r.part_no_base or r.part_no_new or "?"
        if not str(r.src or "").strip():
            violations.append(f"출처 없음: {label} ({r.status})")
        if r.is_new and r.part_no_new != PNO_PLACEHOLDER:
            violations.append(f"신규 임의품번: {label}={r.part_no_new!r} (NEW은 {PNO_PLACEHOLDER})")
    return violations
