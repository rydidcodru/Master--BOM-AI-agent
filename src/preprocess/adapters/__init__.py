"""D-012 양식 어댑터 + dispatcher.

각 어댑터는 ``(file_path, sheet, file_meta) → list[ExtractedRow]`` 반환.
BOM 어댑터(bom_ag_grid)도 D-012 후 동일 스트림으로 통일됨 (BomExtraction 제거).
"""

from __future__ import annotations

from src.preprocess.adapters.base import (
    ExtractedRow,
    SheetMeta,
    build_extracted_row,
)
from src.preprocess.adapters.base_master_24 import extract_base_master_24
from src.preprocess.adapters.bom_ag_grid import extract_bom_ag_grid
from src.preprocess.adapters.changing_parts_list import (
    extract_changing_parts_list_family,
)
from src.preprocess.adapters.dispatcher import extract_sheet
from src.preprocess.adapters.new_parts_list_75 import extract_new_parts_list_75
from src.preprocess.adapters.uae_dev_list import extract_uae_dev_list
from src.preprocess.adapters.v1_2_template import extract_v1_2_template

__all__ = [
    "ExtractedRow",
    "SheetMeta",
    "build_extracted_row",
    "extract_base_master_24",
    "extract_bom_ag_grid",
    "extract_changing_parts_list_family",
    "extract_new_parts_list_75",
    "extract_sheet",
    "extract_uae_dev_list",
    "extract_v1_2_template",
]
