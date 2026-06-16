"""PPTX의 'Design Points' BOM 표를 셀 단위로 충실하게 추출한다.

기존 pptx_text.py는 표의 행/열을 무시하고 모든 텍스트를 이어붙이지만,
Design Points 슬라이드는 다음 컬럼의 BOM 표다.

    No. | Lev. | Base P/No | New P/No | Part Name | UOM | Qty | 상세 변경점

이 모듈은 표 구조를 그대로 보존해 CSV로 덤프한다(가공/매핑 없음).
"""

import csv
import io
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
SLIDE_RE = __import__("re").compile(r"ppt/slides/slide(\d+)\.xml$")

# BOM 표를 식별하는 헤더 토큰(정규화 후 비교).
HEADER_TOKENS = ("base p/no", "new p/no", "part name")
# 충실한 덤프 CSV 컬럼 순서.
DUMP_COLUMNS = [
    "slide_number",
    "design_point",
    "no",
    "lev",
    "base_part_no",
    "new_part_no",
    "part_name",
    "uom",
    "qty",
    "change_point",
]


def _slide_number(path: str) -> int:
    match = SLIDE_RE.search(path)
    return int(match.group(1)) if match else 0


def _cell_text(cell: ElementTree.Element) -> str:
    return " ".join("".join(node.text or "" for node in cell.iter(A + "t")).split())


def _row_cells(row: ElementTree.Element) -> list[str]:
    return [_cell_text(tc) for tc in row.findall(A + "tc")]


def _is_header(cells: list[str]) -> bool:
    joined = " ".join(cells).lower()
    return all(token in joined for token in HEADER_TOKENS)


def _shape_texts(root: ElementTree.Element) -> list[str]:
    texts = []
    for shape in root.iter(P + "sp"):
        text = " ".join("".join(node.text or "" for node in shape.iter(A + "t")).split())
        if text:
            texts.append(text)
    return texts


def _slide_title(root: ElementTree.Element) -> str:
    """design_point(모듈) 제목을 찾는다.

    이 PPT는 제목 placeholder가 아니라 일반 텍스트 박스에
    "별첨 1. ... 작성 가이드 - Plate Assembly,Upper" 형태로 제목을 둔다.
    먼저 title placeholder를 보고, 없으면 ' - '가 들어간 가이드 텍스트를 쓴다.
    """
    for shape in root.iter(P + "sp"):
        ph = shape.find(f"./{P}nvSpPr/{P}nvPr/{P}ph")
        if ph is not None and ph.get("type") in {"title", "ctrTitle"}:
            text = " ".join("".join(node.text or "" for node in shape.iter(A + "t")).split())
            if text:
                return text
    # 가이드 보일러플레이트가 들어간 텍스트 박스를 제목으로 사용.
    candidates = [t for t in _shape_texts(root) if " - " in t and "Design Point" not in t]
    guide = [t for t in candidates if "가이드" in t or "별첨" in t]
    if guide:
        return guide[0]
    return candidates[0] if candidates else ""


# 요약 박스의 항목 머리기호(①②③ …). 요약 문구를 식별하는 보조 신호.
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
SUMMARY_MARKERS = ("내용 요약", "변경점 요약", "변경 요약", "변경 내용")


def _clean_summary(text: str) -> str:
    """요약 박스에서 '※ 내용 요약' 같은 머리말을 떼어낸다."""
    t = text.strip()
    for marker in ("※ 내용 요약", "내용 요약", "변경점 요약", "변경 요약", "변경 내용", "※"):
        if t.startswith(marker):
            t = t[len(marker):].lstrip(" :：·-").strip()
            break
    return t


def _slide_summary(root: ElementTree.Element) -> str:
    """슬라이드 하단의 '내용 요약'(= 변경점 요약 문구)을 찾는다.

    퀵존 심의표는 표 아래에 "※ 내용 요약 ① ... ② ..." 형태로 변경점을
    요약해 둔다. 표만으로는 의도가 안 보이므로 이 문구를 함께 가져온다.
    1순위: '내용 요약' 등 머리말이 든 텍스트 박스.
    2순위: 동그라미 번호(①②③)로 시작하는 가장 긴 텍스트.
    """
    fallback = ""
    for text in _shape_texts(root):
        t = text.strip()
        if any(marker in t for marker in SUMMARY_MARKERS):
            return _clean_summary(t)
        if t and t[0] in CIRCLED and len(t) > len(fallback):
            fallback = t
    return _clean_summary(fallback)


def _clean_design_point(title: str) -> str:
    """제목에서 보일러플레이트를 떼고 모듈명만 남긴다."""
    text = title.strip()
    # "별첨 1. ... - Plate Assembly,Upper" 형태에서 마지막 ' - ' 뒤를 모듈명으로 본다.
    if " - " in text:
        text = text.split(" - ")[-1].strip()
    for marker in ("Design Points", "Design Point"):
        if marker in text:
            text = text.split(marker)[0].strip()
    return text


def extract_bom_tables(pptx_bytes: bytes) -> list[dict[str, Any]]:
    """BOM 표가 있는 슬라이드만 {slide_number, design_point, header, rows} 로 반환."""
    results: list[dict[str, Any]] = []
    with zipfile.ZipFile(BytesIO(pptx_bytes)) as archive:
        slide_paths = sorted(
            (p for p in archive.namelist() if SLIDE_RE.search(p)),
            key=_slide_number,
        )
        for path in slide_paths:
            try:
                root = ElementTree.fromstring(archive.read(path))
            except ElementTree.ParseError:
                continue
            design_point = _clean_design_point(_slide_title(root))
            change_summary = _slide_summary(root)
            for table in root.iter(A + "tbl"):
                rows = [_row_cells(tr) for tr in table.iter(A + "tr")]
                rows = [r for r in rows if any(cell.strip() for cell in r)]
                if not rows:
                    continue
                header_index = next((i for i, r in enumerate(rows) if _is_header(r)), None)
                if header_index is None:
                    continue
                header = rows[header_index]
                body = rows[header_index + 1 :]
                results.append(
                    {
                        "slide_number": _slide_number(path),
                        "design_point": design_point,
                        "change_summary": change_summary,
                        "header": header,
                        "rows": body,
                    }
                )
    return results


def _row_to_record(slide_number: int, design_point: str, cells: list[str]) -> dict[str, Any]:
    """헤더 위치 기준 8컬럼을 충실히 매핑(가공/정규화 없음)."""
    # 컬럼 순서: No. | Lev. | Base P/No | New P/No | Part Name | UOM | Qty | 상세 변경점
    padded = (cells + [""] * 8)[:8]
    no, lev, base_pno, new_pno, part_name, uom, qty, change_point = padded
    return {
        "slide_number": slide_number,
        "design_point": design_point,
        "no": no,
        "lev": lev,
        "base_part_no": base_pno,
        "new_part_no": new_pno,
        "part_name": part_name,
        "uom": uom,
        "qty": qty,
        "change_point": change_point,
    }


def bom_dump_rows(pptx_bytes: bytes) -> list[dict[str, Any]]:
    """모든 BOM 표를 충실한 덤프 row 리스트로 평탄화."""
    records: list[dict[str, Any]] = []
    for table in extract_bom_tables(pptx_bytes):
        for cells in table["rows"]:
            records.append(
                _row_to_record(table["slide_number"], table["design_point"], cells)
            )
    return records


def _write_rows(handle: Any, rows: list[dict[str, Any]]) -> None:
    writer = csv.DictWriter(handle, fieldnames=DUMP_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)


def bom_dump_csv_text(pptx_bytes: bytes) -> str:
    """디스크에 쓰지 않고 충실한 덤프 CSV 텍스트를 반환(웹 다운로드용)."""
    buffer = io.StringIO()
    _write_rows(buffer, bom_dump_rows(pptx_bytes))
    return buffer.getvalue()


def bom_dump_summary(pptx_bytes: bytes) -> dict[str, Any]:
    """추출 결과 요약 + 행 데이터(웹 응답용)."""
    tables = extract_bom_tables(pptx_bytes)
    rows = bom_dump_rows(pptx_bytes)
    # design_point(모듈)별 변경점 요약 문구. 같은 모듈이 여러 슬라이드면 이어붙인다.
    summaries: dict[str, str] = {}
    for t in tables:
        dp = t.get("design_point") or ""
        s = (t.get("change_summary") or "").strip()
        if not dp or not s:
            continue
        prev = summaries.get(dp)
        summaries[dp] = f"{prev}\n{s}" if prev and s not in prev else (prev or s)
    return {
        "columns": list(DUMP_COLUMNS),
        "tables": len(tables),
        "rows": len(rows),
        "design_points": sorted({t["design_point"] for t in tables if t["design_point"]}),
        "summaries": summaries,
        "records": rows,
    }


def write_bom_dump_csv(pptx_bytes: bytes, output_csv: str | Path) -> dict[str, Any]:
    rows = bom_dump_rows(pptx_bytes)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        _write_rows(f, rows)
    tables = extract_bom_tables(pptx_bytes)
    return {
        "output_csv": str(output_path),
        "tables": len(tables),
        "rows": len(rows),
        "design_points": sorted({t["design_point"] for t in tables if t["design_point"]}),
    }
