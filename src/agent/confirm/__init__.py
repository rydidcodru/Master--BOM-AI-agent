"""Phase 4 — HITL 확정 게이트 (트리 전개 전 사용자 확정).

후보 부품 세트(propose_candidates) → 채택/거절·변경점·신규 P/No 입력 → 확정 닻.
확정된 닻에 한해서만 expand_confirmed가 하위 트리를 전개한다(절대원칙 #4).
"""

from src.agent.confirm.gate import (
    confirm_candidates,
    infer_change_point,
    resolve_new_pno,
)
from src.agent.confirm.models import (
    CHANGE_POINTS,
    CLASSIFICATION_EN_BY_KO,
    CLASSIFICATION_KO_OPTIONS,
    CandidateSelection,
    ConfirmedAnchor,
    classification_ko_to_en,
)

__all__ = [
    "CHANGE_POINTS",
    "CLASSIFICATION_EN_BY_KO",
    "CLASSIFICATION_KO_OPTIONS",
    "CandidateSelection",
    "ConfirmedAnchor",
    "classification_ko_to_en",
    "confirm_candidates",
    "infer_change_point",
    "resolve_new_pno",
]
