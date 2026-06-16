"""Base BOM 원본 엑셀(.xlsx)을 apply 가능한 BOM row 리스트로 파싱한다.

회의록 INPUT의 한 축인 'Base BOM(원본 엑셀)'을 받아, 변경 병합(`/bom/apply`)의
base로 바로 쓸 수 있는 행 포맷으로 변환한다. 외부 라이브러리 없이 표준 zip/xml로 읽는다.

xlsx 컬럼(헤더 기준 자동 매핑):
    Part No / Lvl / Parent Part No(모) / Part Name(자) / Description / Qty / UOM / Type / Maker ...
"""

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# 헤더명 -> 내부 필드. 헤더 텍스트는 정규화(소문자/공백제거) 후 비교.
HEADER_ALIASES = {
    "partno": "base_part_no",
    "lvl": "bom_level",
    "level": "bom_level",
    "parentpartno(모)": "parent_part_no",
    "parentpartno": "parent_part_no",
    "partname(자)": "child_part_no",
    "description": "part_name",
    "technicalspec": "technical_spec",
    "qty": "qty",
    "uom": "uom",
    "type": "part_type",
    "maker": "supplier",
    "substitutefor": "substitute_for",
    "change": "source_change",
}


def _norm_header(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _col_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "").group(1)
    num = 0
    for ch in letters:
        num = num * 26 + (ord(ch) - 64)
    return num


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(M + "t")) for si in root.findall(M + "si")]


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        is_node = cell.find(M + "is")
        return "".join(t.text or "" for t in is_node.iter(M + "t")) if is_node is not None else ""
    value = cell.find(M + "v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared[int(value.text)]
        except (ValueError, IndexError):
            return ""
    return value.text


def _sheet_rows(xlsx_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(xlsx_bytes)) as archive:
        shared = _shared_strings(archive)
        sheet_paths = sorted(p for p in archive.namelist() if p.startswith("xl/worksheets/") and p.endswith(".xml"))
        if not sheet_paths:
            return []
        sheet = ElementTree.fromstring(archive.read(sheet_paths[0]))
        data = sheet.find(M + "sheetData")
        if data is None:
            return []
        rows = []
        for row in data.findall(M + "row"):
            cells: dict[int, str] = {}
            max_col = 0
            for cell in row.findall(M + "c"):
                idx = _col_index(cell.get("r", ""))
                if idx:
                    cells[idx] = _cell_value(cell, shared)
                    max_col = max(max_col, idx)
            rows.append([cells.get(i, "") for i in range(1, max_col + 1)])
        return rows


def _depth_from_level(level: str) -> int | None:
    match = re.search(r"(\d+)\s*$", level or "")
    if match:
        return int(match.group(1))
    return 0 if level.strip() == "0" else None


def parse_base_bom_xlsx(xlsx_bytes: bytes) -> list[dict[str, Any]]:
    """xlsx -> apply 가능한 base BOM row 리스트."""
    raw_rows = _sheet_rows(xlsx_bytes)
    if not raw_rows:
        return []

    # 헤더 행 탐색: 'Part No'와 'Description'이 모두 보이는 첫 행.
    header_index = None
    for i, row in enumerate(raw_rows[:10]):
        normalized = {_norm_header(c) for c in row}
        if "partno" in normalized and "description" in normalized:
            header_index = i
            break
    if header_index is None:
        return []

    header = raw_rows[header_index]
    col_field: dict[int, str] = {}
    for col_i, name in enumerate(header):
        field = HEADER_ALIASES.get(_norm_header(name))
        if field and col_i not in col_field:
            col_field[col_i] = field

    rows: list[dict[str, Any]] = []
    substitutes: list[dict[str, str]] = []
    # 깊이 스택으로 parent 링크(line_order 기반 합성 id).
    depth_to_line: dict[int, int] = {}
    line_order = 0
    for raw in raw_rows[header_index + 1 :]:
        record: dict[str, str] = {}
        for col_i, value in enumerate(raw):
            field = col_field.get(col_i)
            if field:
                record[field] = (value or "").strip()
        base_part_no = record.get("base_part_no", "")
        part_name = record.get("part_name", "") or record.get("child_part_no", "")
        if not base_part_no and not part_name:
            continue
        bom_level = record.get("bom_level", "")
        bom_depth = _depth_from_level(bom_level)
        # 레벨이 없는 *S* 행은 트리 노드가 아니라 대체품(Substitute). 트리에서 제외하고
        # 나중에 대상 부품의 substitutes로 붙인다.
        if bom_depth is None:
            substitutes.append(
                {
                    "part_no": base_part_no,
                    "part_name": part_name,
                    "substitute_for": record.get("substitute_for", ""),
                }
            )
            continue
        line_order += 1
        qty = record.get("qty", "")

        parent_line = None
        if bom_depth is not None:
            depth_to_line[bom_depth] = line_order
            if bom_depth > 0:
                parent_line = depth_to_line.get(bom_depth - 1)

        rows.append(
            {
                "detail_id": line_order,  # 합성 id(출력/링크용)
                "source_candidate_detail_id": None,
                "master_id": None,
                "line_order": line_order,
                "row_no": line_order,
                "bom_level": bom_level,
                "bom_depth": bom_depth,
                "parent_detail_id": parent_line,
                "part_type": record.get("part_type", ""),
                "base_part_no": base_part_no,
                # base BOM은 현재 상태이므로 new=base(변경 없음)로 초기화. apply가 덮어쓴다.
                "new_part_no": base_part_no,
                "normalized_new_part_no": re.sub(r"[\s\-_/.]+", "", base_part_no.upper()),
                "part_name": part_name,
                "base_qty": qty,
                "new_qty": qty,
                "normalized_new_qty": qty,
                "changing_point": "",
                "changing_reason": "",
                "supplier": record.get("supplier", ""),
                "classification": "",
                "technical_spec": record.get("technical_spec", ""),
                "parent_part_no": record.get("parent_part_no", ""),
                "substitutes": [],
            }
        )

    # 대체품을 substitute_for 기준으로 트리 부품에 연결.
    by_part_no: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_part_no.setdefault(row["base_part_no"], row)
    for sub in substitutes:
        target = by_part_no.get(sub.get("substitute_for", ""))
        if target is not None:
            target["substitutes"].append({"part_no": sub["part_no"], "part_name": sub["part_name"]})
    return rows


def base_bom_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    depths = [r.get("bom_depth") for r in rows if r.get("bom_depth") is not None]
    substitute_count = sum(len(r.get("substitutes") or []) for r in rows)
    return {
        "rows": len(rows),
        "max_depth": max(depths) if depths else None,
        "substitutes": substitute_count,
        "root_part_no": rows[0].get("base_part_no") if rows else None,
        "root_part_name": rows[0].get("part_name") if rows else None,
    }


def parse_base_bom_path(path: str | Path) -> list[dict[str, Any]]:
    return parse_base_bom_xlsx(Path(path).read_bytes())
