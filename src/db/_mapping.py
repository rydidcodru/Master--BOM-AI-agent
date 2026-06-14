"""D-012 — Core 13 (우리 스키마) → dev_part_master (팀원 스키마) 매핑.

기존 v2.0 어댑터는 column_dictionary.yaml에서 룩업한 Core 13 필드명을 사용한다.
팀원 dev_part_master 컬럼명과 1:1 매핑돼있지 않아 본 모듈이 변환 책임.

매핑 누락 필드(grade, event_stage)는 ``extra_fields`` JSONB로 보존.

사용처:
  - adapters/*.py: 어댑터가 ``map_core_to_dpm()`` 호출해 행 단위 변환.
  - db/load.py: DevPartMaster ORM 인스턴스화 시 dict unpack.
"""

from __future__ import annotations

from typing import Any

# Core 13 필드명 (우리) → dev_part_master 컬럼명 (팀원)
#
# 추가로 column_dictionary.yaml에서 직접 추출되는 dpm 전용 필드
# (qty_new/qty_base/supplier/classification/bom_level_raw)는 identity 매핑.
CORE_TO_DEV_PART_MASTER: dict[str, str] = {
    "part_no":           "part_no_new",
    "base_part_no":      "part_no_base",
    "part_name":         "part_name",
    "new_model_code":    "new_model",
    "base_model_code":   "base_model",
    "region":            "region",
    "change_type":       "event",
    "change_point":      "change_point_raw",
    "change_reason":     "change_reason_raw",
    "bom_level":         "bom_depth",
    "part_type":         "part_type",
    # column_dictionary로 직접 추출되는 dpm 컬럼 (identity)
    "qty_new":           "qty_new",
    "qty_base":          "qty_base",
    "supplier":          "supplier",
    "classification":    "classification",
    "bom_level_raw":     "bom_level_raw",
}

# Core 13에는 없지만 dev_part_master에 있는 컬럼 — 어댑터/payload에서 추출 필요.
DEV_PART_MASTER_EXTRA: tuple[str, ...] = (
    "bom_level_raw",
    "qty_base",
    "qty_new",
    "supplier",
    "classification",
)

# Core 13에는 있지만 dev_part_master 컬럼이 없는 필드 — extra_fields JSONB로 보존.
CORE_RESIDUAL_TO_EXTRA: tuple[str, ...] = (
    "grade",
    "event_stage",
)

# dev_part_master 컬럼 전체 (load.py가 SQL 생성에 사용)
DEV_PART_MASTER_COLUMNS: tuple[str, ...] = (
    "region",
    "base_model",
    "new_model",
    "event",
    "bom_level_raw",
    "bom_depth",
    "part_type",
    "part_no_base",
    "part_no_new",
    "part_name",
    "qty_base",
    "qty_new",
    "change_point_raw",
    "change_reason_raw",
    "supplier",
    "classification",
)


def map_core_to_dpm(
    core: dict[str, Any],
    extra_native: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Core dict + 추가 추출 필드 → (dpm_fields, residual_extra).

    Args:
        core: 어댑터가 추출한 Core 13 dict (정규화 전/후 모두 가능).
        extra_native: 어댑터가 dev_part_master 컬럼 이름으로 직접 추출한 값
            (qty_base, qty_new, supplier, classification, bom_level_raw 등).

    Returns:
        - dpm_fields: DevPartMaster ORM 컬럼명으로 매핑된 dict (None 값 포함).
        - residual_extra: Core 13에 있었지만 dpm에 자리 없는 필드(grade 등)
          → extra_fields JSONB에 합쳐질 dict.
    """
    extra_native = extra_native or {}
    dpm: dict[str, Any] = {}

    # 1) Core 13 → dpm 컬럼명 변환
    for core_key, dpm_col in CORE_TO_DEV_PART_MASTER.items():
        if core_key in core and core[core_key] is not None:
            dpm[dpm_col] = core[core_key]

    # 2) dev_part_master 전용 추가 필드 병합 (어댑터가 직접 채운 값)
    for col in DEV_PART_MASTER_EXTRA:
        if col in extra_native and extra_native[col] is not None:
            dpm[col] = extra_native[col]

    # 3) Core 잔여 (grade, event_stage) → extra_fields용 dict
    residual: dict[str, Any] = {}
    for core_key in CORE_RESIDUAL_TO_EXTRA:
        if core_key in core and core[core_key] is not None:
            residual[core_key] = core[core_key]

    return dpm, residual


def coerce_bom_depth(level_raw: Any) -> int | None:
    """bom_level 원본 ('.  .2', 2, '2') → bom_depth 정수.

    점 형식 ('...3'): 점 개수 == 깊이 (BOM ag-grid 패턴).
    숫자 형식 (2, '2'): 그대로 int.
    """
    if level_raw is None:
        return None
    if isinstance(level_raw, int):
        return level_raw
    if isinstance(level_raw, float):
        try:
            return int(level_raw)
        except (TypeError, ValueError):
            return None
    s = str(level_raw).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        pass
    # '.  .2' 또는 '...3' 형식
    stripped = s.lstrip(".")
    if stripped:
        try:
            return int(stripped)
        except ValueError:
            pass
    return s.count(".") or None
