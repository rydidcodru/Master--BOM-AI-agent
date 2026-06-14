"""②.5 후보-BOM 매핑 경계 스키마 (Pydantic v2).

``MappedEvent``는 ``CandidateSet.mapping``에 additive로 부착되어 ③ HITL 게이트로
흘러간다 — **UI 표시·확정 플로우 변경은 이번 범위 아님**(데이터만). 매핑 결과가
문서/New BOM에 "사실"로 출력되는 경로를 만들지 않는다(작업 지시 절대 제약).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeRef(BaseModel):
    """S(모듈 하위트리) 노드 참조 — ScopeNode의 직렬화 가능 사영."""

    pno: str
    part_name: str
    part_type: str | None = None
    depth: int = 0
    path: str = ""


class MatchEvidence(BaseModel):
    """매칭 근거 1건 — trace/검수 표시용."""

    kind: Literal[
        "pno_exact",
        "canon_trgm",
        "type_match",
        "event_coherence",
        "classification",
        "cp_label",
    ]
    detail: str
    score: float


class MappedLine(BaseModel):
    """과거 change_line 1건의 S 사상 결과.

    ``proposed_pno``는 add_proposal일 때만 ``<발번대기>`` — 신규 품번 생성 금지
    (절대원칙 #2): 시스템이 새 번호를 만들지 않는다.
    """

    line_ref: dict[str, Any]  # line_id, part_name, base/new_pno, classification, changepoint, source_ref
    status: Literal["matched", "ambiguous", "add_proposal", "dropped"]
    target_node: NodeRef | None = None
    candidates: list[NodeRef] = Field(default_factory=list)  # ambiguous 상위 ≤3
    proposed_action: Literal["MODIFY", "ADD", "DELETE", "CHECK"] | None = None
    proposed_pno: str | None = None  # add_proposal → 항상 <발번대기>
    confidence: float = 0.0
    evidence: list[MatchEvidence] = Field(default_factory=list)


class MappedEvent(BaseModel):
    """한 후보 이벤트(라인 세트)의 매핑 결과."""

    event_id: int
    coherence: float  # matched 라인 비율(가중)
    lines: list[MappedLine] = Field(default_factory=list)


__all__ = ["MappedEvent", "MappedLine", "MatchEvidence", "NodeRef"]
