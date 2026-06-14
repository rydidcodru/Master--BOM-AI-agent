"""변경사유(K) 보강 — 심의회 PPT 변경 서사를 부품에 결합(결정론, no LLM, no leak).

정답지(v1.1)를 보지 않고 PPT 변경(part·변경내용·변경사유)만으로 각 변경 부품에
사유 초안을 단다. 부품명 토큰과 PPT 변경 토큰(part+change_detail)의 최대 겹침으로
가장 가까운 PPT 항목을 골라 그 변경내용|변경사유를 결합한다. 겹침 0이면 미배정.

한계(정직): PPT 서사는 모듈/한글 위주, BOM 부품명은 영문 위주라 직접 토큰 겹침은
일부만 잡힌다(정답지 변경 부품명 중 PPT 토큰 보유 ≈19/68). 따라서 자동 사유는 부분
커버리지이며, 나머지는 UI에서 사용자가 채운다(절대원칙 #4: 사유는 사람 확정/편집).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = ["ppt_reason_map"]

_TOK_RE = re.compile(r"[^A-Z0-9가-힣]+")


def _toks(s: Any, minlen: int = 3) -> set[str]:
    return {t for t in _TOK_RE.split(str(s or "").upper()) if len(t) >= minlen}


def ppt_reason_map(
    rows: Iterable[Any],
    ppt_changes: list[dict[str, Any]],
    *,
    min_overlap: int = 1,
) -> dict[str, str]:
    """변경 행 → 가장 잘 맞는 PPT 변경의 사유 매핑.

    Args:
        rows: ``part_name`` / ``base_pno`` / ``new_pno`` 속성을 갖는 변경 행(DiffRow 등).
        ppt_changes: ``extract_change_review_from_pptx_bytes``의 ``detailed_changes``.
        min_overlap: 사유 배정에 필요한 최소 토큰 겹침 수.

    Returns:
        ``{key(base_pno or new_pno, 정규화) -> reason}`` (변경내용 | 변경사유).
    """
    pc: list[tuple[set[str], str]] = []
    for c in ppt_changes:
        ctx = f"{c.get('part') or ''} {c.get('change_detail') or ''}"
        detail = str(c.get("change_detail") or "").strip()
        reason = str(c.get("change_reason") or "").strip()
        reason_text = f"{detail} | {reason}".strip(" |") or detail or reason
        pc.append((_toks(ctx), reason_text))

    out: dict[str, str] = {}
    for d in rows:
        key = (getattr(d, "base_pno", "") or getattr(d, "new_pno", "") or "").strip().upper()
        if not key:
            continue
        nt = _toks(getattr(d, "part_name", ""))
        if not nt:
            continue
        best_reason = ""
        best_score = 0
        for toks, reason in pc:
            s = len(nt & toks)
            if s > best_score:
                best_score = s
                best_reason = reason
        if best_reason and best_score >= min_overlap:
            out[key] = best_reason
    return out
