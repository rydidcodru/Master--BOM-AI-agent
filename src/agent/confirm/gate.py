"""HITL 확정 게이트 로직 (트리 전개 전).

``confirm_candidates``: 후보 선택 목록 → 확정 닻 목록. 채택/거절을 ``agent_feedback``에
기록한다. **트리 전개는 하지 않는다**(orchestrator.expand_confirmed가 별도) — 닻이
정해진 뒤에만 하위 전개(절대원칙 #4).

신규 P/No 규칙(코드 강제): 회수된 기존 품번은 제안값으로 쓰고, 사용자 입력이 있으면 그것을,
둘 다 없으면 ``<발번대기>``. 시스템은 새 번호를 생성하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from src.agent.confirm.models import CandidateSelection, ConfirmedAnchor
from src.agent.docgen.generator import PNO_PLACEHOLDER
from src.db.models import AgentFeedback
from src.utils.logging import get_logger

log = get_logger(__name__)

# classification/event → 변경점 기본 추정(사용자가 미지정 시 UI 프리필용, 결정론).
_CLASSIFICATION_TO_CHANGE_POINT = {
    "new": "부품 추가",
    "신규": "부품 추가",
    "change": "부품 변경",
    "변경": "부품 변경",
    "delete": "부품 삭제",
    "삭제": "부품 삭제",
}


def infer_change_point(classification: str | None) -> str | None:
    """classification/event 문자열 → 변경점 기본값(없으면 None — 사용자가 직접 지정)."""
    if not classification:
        return None
    return _CLASSIFICATION_TO_CHANGE_POINT.get(classification.strip().lower())


def resolve_new_pno(sel: CandidateSelection) -> tuple[str, bool]:
    """신규 P/No 결정 — 시스템 무생성.

    우선순위: 사용자 입력 > 검색 회수 기존값 > ``<발번대기>``.

    Returns:
        (part_no_new, is_placeholder).
    """
    if sel.new_pno_input and sel.new_pno_input.strip():
        return sel.new_pno_input.strip(), False
    if sel.part_no_new_suggested and sel.part_no_new_suggested.strip():
        return sel.part_no_new_suggested.strip(), False
    return PNO_PLACEHOLDER, True


def confirm_candidates(
    session: Session,
    *,
    session_id: str,
    selections: Iterable[CandidateSelection],
    record_feedback: bool = True,
) -> list[ConfirmedAnchor]:
    """후보 선택 → 확정 닻. 채택/거절을 agent_feedback에 기록.

    Args:
        session: 활성 SQLAlchemy session.
        session_id: 한 에이전트 실행 단위.
        selections: 사용자 결정 목록.
        record_feedback: agent_feedback 기록 여부(테스트에서 끌 수 있음).

    Returns:
        채택된 :class:`ConfirmedAnchor` 목록(거절은 제외). 트리 전개는 하지 않는다.
    """
    anchors: list[ConfirmedAnchor] = []
    n_accept = 0
    for sel in selections:
        is_accept = sel.decision == "accept"
        if is_accept:
            pno_new, is_ph = resolve_new_pno(sel)
            anchors.append(
                ConfirmedAnchor(
                    part_no_base=sel.part_no_base,
                    part_no_new=pno_new,
                    change_point=sel.change_point,
                    classification=sel.classification,
                    source_ref=sel.source_ref,
                    is_placeholder=is_ph,
                )
            )
            n_accept += 1
        if record_feedback:
            session.add(
                AgentFeedback(
                    session_id=session_id,
                    doc_id=sel.doc_id,
                    part_no=sel.part_no_new_suggested or sel.part_no_base,
                    decision=sel.decision,
                    change_point=sel.change_point,
                    new_pno_input=sel.new_pno_input,
                    is_anchor=is_accept,
                    note=sel.source_ref,
                )
            )
    if record_feedback:
        session.commit()
    log.info("confirm.gate.done", accepted=n_accept, total=len(anchors))
    return anchors
