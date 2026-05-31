from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ChangePoint(TypedDict):
    module: str
    part: str
    change_detail: str
    change_reason: str
    discipline: str       # 기구 / 제어 / ThinQ
    type: str             # Changing / NEW / 삭제
    concern: str
    source_pptx: str
    evidence_slide: int
    canonical_part: str   # normalize 노드에서 채워짐
    aliases: list[str]    # normalize 노드에서 채워짐


class SearchResult(TypedDict):
    change_point: ChangePoint
    candidates: list[dict]   # Neo4j 원본 후보풀
    ranked: list[dict]       # LLM이 선정한 최종 후보 (상위 5건)
    retry_count: int


class BOMSearchState(TypedDict):
    # 1단계: 파싱
    pptx_paths: list[str]
    change_points: list[ChangePoint]

    # 2단계: 검색 — fan-out 병렬 합산용 Annotated
    search_results: Annotated[list[SearchResult], operator.add]

    # 3단계: 사용자 선택 (추후 구현)
    human_selections: list[dict]

    # 4단계: BOM 작성 (추후 구현)
    bom_updates: list[dict]
