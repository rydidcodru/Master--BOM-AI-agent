from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class BomRow(TypedDict):
    """Base BOM의 단일 행 (하위 트리 전개용)."""
    part_no: str
    lvl: str
    is_order: int
    parent_part_no: str
    description: str
    qty: str
    uom: str
    maker: str
    part_type: str
    supply_type: str
    ckd: str
    technical_spec: str


class LinkedPart(TypedDict):
    """과거 이력 케이스에서 함께 변경된 연동 부품 후보."""
    part_no: str
    part_name: str
    change_type: str        # 추가 / 변경 / 삭제
    relevance_reason: str   # LLM 판단 근거


class HistoryCandidate(TypedDict):
    """과거 유사 이력 후보 1건."""
    case_id: str
    model_name: str
    base_model: str
    rank: int               # 1~5
    select_reason: str      # LLM 선정 이유
    linked_parts: list[LinkedPart]


class ChangePoint(TypedDict):
    module: str
    part: str
    change_detail: str
    change_reason: str
    discipline: str           # 기구 / 제어 / ThinQ
    type: str                 # Changing / NEW / 삭제
    concern: str
    source_pptx: str
    evidence_slide: int
    # bom_match 노드에서 채워짐
    base_part_no: str         # Base BOM 매칭 품번 (New 부품이면 "")
    bom_level: str            # .1 / ..2 / ...3
    part_type: str            # MechanicalPart 등
    qty: str
    supplier: str             # Maker
    supply_type: str          # Assembly Pull / Phantom / Supplier
    ckd: str
    bom_matched: bool         # Base BOM 매칭 성공 여부
    match_confidence: str     # high / medium / low
    match_reason: str         # LLM 매칭 근거
    matched_subtree: list[BomRow]  # 매칭된 부품 + 하위 트리 전체
    # history_search 노드에서 채워짐
    history_candidates: list[HistoryCandidate]


class SearchResult(TypedDict):
    change_point: ChangePoint
    candidates: list[dict]   # Neo4j 원본 후보풀
    ranked: list[dict]       # LLM이 선정한 최종 후보 (상위 5건)
    retry_count: int


class BOMSearchState(TypedDict):
    # 1단계: 파싱
    pptx_paths: list[str]
    base_bom_path: str
    change_points: list[ChangePoint]

    # 2단계: 검색 — fan-out 병렬 합산용 Annotated
    search_results: Annotated[list[SearchResult], operator.add]

    # 3단계: 사용자 선택 (추후 구현)
    human_selections: list[dict]

    # 4단계: BOM 작성 (추후 구현)
    bom_updates: list[dict]
