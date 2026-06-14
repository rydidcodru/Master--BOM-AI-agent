"""HITL 확정 게이트 경계 스키마 (Pydantic v2).

``CandidateSelection`` = 사용자가 후보 부품 한 행에 내린 결정(UI/헤드리스 공통 입력).
``ConfirmedAnchor`` = 확정된 닻(하위 전개 대상). 신규 P/No는 회수된 기존값 / 사용자 입력 /
``<발번대기>`` 중 하나 — 시스템이 새 번호를 생성하지 않는다([[feedback-search-by-reason-not-id]]
와 별개로 무생성 원칙).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

# CLAUDE.md 구조 B changing_point 집합 (변경점 지정 옵션).
CHANGE_POINTS: tuple[str, ...] = (
    "부품 변경",
    "부품 추가",
    "부품 삭제",
    "하위 부품 변경",
    "부품 수량 변경",
    "인쇄 변경",
)

Decision = Literal["accept", "reject"]

# 통합 master(Compact v1.1) 분류 — UI는 한글 셀렉트, 내보내기는 정답지 영어 라벨.
# 매핑은 scorer._CLASSIFICATION_CANON / master_writer 규약과 일치(신규→New, 변경→Change,
# 기존→Common, 삭제→Delete). 단일 출처로 두어 UI/클라이언트가 함께 import한다.
CLASSIFICATION_KO_OPTIONS: tuple[str, ...] = ("신규", "변경", "기존", "삭제")
CLASSIFICATION_EN_BY_KO: dict[str, str] = {
    "신규": "New",
    "변경": "Change",
    "기존": "Common",
    "삭제": "Delete",
}
# 영어 라벨도 그대로 통과(이미 영어인 입력 대비).
_CLASSIFICATION_EN_PASSTHRU = {"new": "New", "change": "Change", "common": "Common", "delete": "Delete"}


def classification_ko_to_en(value: str | None) -> str:
    """한글/영어 분류 → 정답지 영어 라벨(New/Change/Common/Delete). 미매핑/빈값 → 'Common'."""
    s = str(value or "").strip()
    if not s:
        return "Common"
    if s in CLASSIFICATION_EN_BY_KO:
        return CLASSIFICATION_EN_BY_KO[s]
    return _CLASSIFICATION_EN_PASSTHRU.get(s.lower(), s)


class CandidateSelection(BaseModel):
    """후보 부품 한 행에 대한 사용자 결정.

    ``part_no_new_suggested``는 검색으로 회수된 **기존** 품번(제안값). ``new_pno_input``은
    사용자가 직접 입력한 신규 품번. 둘 다 없으면 게이트가 ``<발번대기>``로 떨군다.
    """

    model_config = {"extra": "ignore"}

    part_no_base: str | None = None
    part_no_new_suggested: str | None = None
    decision: Decision = "reject"
    change_point: str | None = None
    new_pno_input: str | None = None
    classification: str | None = None
    source_ref: str | None = None
    doc_id: int | None = None
    event_id: int | None = None
    # 통합 master 검수 UI 자유편집 필드 (additive, 2026-06-13). 검색/확정 로직엔 영향 없음 —
    # 사용자가 변경 리스트에서 직접 편집한 변경사유/변경내용(표시·내보내기용). 기본 빈값.
    change_reason: str | None = None
    change_detail: str | None = None

    @field_validator("change_point")
    @classmethod
    def _check_change_point(cls, v: str | None) -> str | None:
        if v is not None and v not in CHANGE_POINTS:
            raise ValueError(f"change_point must be one of {CHANGE_POINTS}, got {v!r}")
        return v


class ConfirmedAnchor(BaseModel):
    """확정된 닻 — 하위 전개의 시작점.

    ``part_no_new``는 회수된 기존값 / 사용자 입력 / ``<발번대기>`` 중 하나.
    ``is_placeholder``면 번호 미발번 상태라 트리 전개 대상에서 제외된다.
    """

    model_config = {"extra": "ignore"}

    part_no_base: str | None = None
    part_no_new: str
    change_point: str | None = None
    classification: str | None = None
    source_ref: str | None = None
    is_placeholder: bool = False
