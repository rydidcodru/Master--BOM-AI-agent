"""양식 어댑터 dispatcher (D-012).

분류기가 부여한 ``form_id``를 받아 적절한 어댑터로 라우팅. 모든 어댑터는
``list[ExtractedRow]``를 반환 (D-012 후 BOM 어댑터도 동일 스트림으로 통일).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import ExtractedRow
from src.preprocess.adapters.base_master_24 import extract_base_master_24
from src.preprocess.adapters.bom_ag_grid import extract_bom_ag_grid
from src.preprocess.adapters.changing_parts_list import (
    extract_changing_parts_list_family,
)
from src.preprocess.adapters.new_parts_list_75 import extract_new_parts_list_75
from src.preprocess.adapters.uae_dev_list import extract_uae_dev_list
from src.preprocess.adapters.v1_2_template import extract_v1_2_template
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)


# form_id 또는 sub-variant → 어댑터.
_CHANGING = {
    # legacy 시그니처 이름 (form_signatures.yaml 호환)
    "변경부품_list_family",
    "변경부품_list_91",
    "변경부품_list_95",
    "변경부품_list_96",
    "변경부품_list_97",
    # D-012 form_registry 이름
    "changing_parts_list_91",
    "changing_parts_list_95",
    "changing_parts_list_96",
    "changing_parts_list_97",
}

_NEW_PARTS = {"신규부품리스트_75", "new_parts_list_75"}
_BOM = {"BOM_ag_grid_36", "bom_ag_grid_36"}
_V1_2 = {"v1_2_template_59"}
_BASE_MASTER = {"base_master_24"}
_UAE = {"UAE_신규개발_58", "uae_dev_list"}


def extract_sheet(
    file_path: Path,
    sheet: SheetData,
    form_id: str,
    file_meta: dict[str, Any] | None = None,
) -> list[ExtractedRow]:
    """form_id 기반 어댑터 dispatch.

    Returns:
        ``list[ExtractedRow]``. unknown/error는 빈 리스트.
    """
    file_meta = file_meta or {}

    if form_id in _CHANGING:
        return list(extract_changing_parts_list_family(file_path, sheet, file_meta))
    if form_id in _NEW_PARTS:
        return list(extract_new_parts_list_75(file_path, sheet, file_meta))
    if form_id in _BOM:
        return list(extract_bom_ag_grid(file_path, sheet, file_meta))
    if form_id in _V1_2:
        return list(extract_v1_2_template(file_path, sheet, file_meta))
    if form_id in _BASE_MASTER:
        return list(extract_base_master_24(file_path, sheet, file_meta))
    if form_id in _UAE:
        return list(extract_uae_dev_list(file_path, sheet, file_meta))
    log.warning("dispatcher.unknown_form", form=form_id, file=file_path.name, sheet=sheet.name)
    return []
