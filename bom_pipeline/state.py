from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class ChangePoint(TypedDict):
    # ── pptx 파싱 결과 ──────────────────────────────
    module: str           # 상위 모듈명 (pptx 표기 그대로)
    part: str             # 부품명 (pptx 표기 그대로)
    change_detail: str    # 변경 내역 ("변경 전 → 변경 후" 또는 텍스트)
    change_reason: str    # 변경 사유
    discipline: str       # 기구 / 제어 / ThinQ / 기타
    change_type: str      # Changing / NEW / 삭제
    concern: str          # 걱정점
    source_pptx: str      # 출처 pptx 파일명
    evidence_slide: int   # 근거 슬라이드 번호
    bom_level: str        # .1 / ..2 / ...3 (상세 표에서 직접 추출된 경우)

    # ── P/No (상세 표에서 직접 추출된 경우 채워짐, 없으면 "") ──
    base_part_no: str     # 변경 전 P/No
    new_part_no: str      # 변경 후 P/No ("TBD" / "삭제" / "←" 포함 가능)

    # ── history_search 결과 ─────────────────────────
    history_candidates: list[dict]


class BOMPipelineState(TypedDict):
    pptx_path: str
    base_bom_path: str
    change_points: list[ChangePoint]

    # fan-out 병렬 합산용
    search_results: Annotated[list[dict], operator.add]

    human_selections: list[dict]
    bom_updates: list[dict]