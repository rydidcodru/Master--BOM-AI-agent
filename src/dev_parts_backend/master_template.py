"""개발부품마스터(master) 산출물 생성.

data/input/example.xlsx 를 그대로 템플릿으로 쓴다(헤더 2행·병합셀·서식 보존).
컬럼 틀만 살리고 샘플 데이터(10행 이하)는 비운 뒤, 변경내역 list를 채운다.
openpyxl 의존 없이(릴리스 무의존 유지) 템플릿 zip의 시트 XML 데이터 영역만 교체한다.
"""

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

TEMPLATE_PATH = Path(__file__).parent / "templates" / "master_template.xlsx"
SHEET_PATH = "xl/worksheets/sheet1.xml"
HEADER_ROWS = 9   # 1~9행(메타+2행 헤더)은 그대로 두고, 10행부터 데이터.
DATA_START_ROW = 10

# 템플릿 컬럼 순서(A~O) → new_bom 행의 키 + CSV 헤더.
MASTER_FIELDS: list[tuple[str, str]] = [
    ("no", "No."),
    ("bom_level", "BOM Level"),
    ("part_type", "Part Type"),
    ("base_part_no", "Base P/No"),
    ("new_part_no", "New P/No"),
    ("part_name", "부품명(Part Name)"),
    ("base_qty", "Base Q'ty"),
    ("new_qty", "New Q'ty"),
    ("changing_point", "변경점(Changing Point)"),
    ("changing_reason", "변경사유(Changing Reason)"),
    ("supplier", "양산처(Supplier)"),
    ("classification", "Classification"),
    ("mold", "금형 개발/수정"),
    ("inhouse", "사내 제작"),
    ("approval", "부품 인정시험"),
]


def _col_letter(index: int) -> str:
    letters = ""
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _depth_of(row: dict[str, Any]) -> int:
    d = row.get("bom_depth")
    if isinstance(d, int):
        return d
    m = re.search(r"(\d+)\s*$", str(row.get("bom_level") or ""))
    return int(m.group(1)) if m else 0


def _is_anchor(row: dict[str, Any]) -> bool:
    """이번 반영에서 실제로 바뀐 행(변경/추가/삭제). 트리 포함의 기준점."""
    action = str(row.get("applied_action") or "").lower()
    return action in {"add", "add_bundle", "delete", "change"} or action.startswith("subtree")


def _classify(row: dict[str, Any], *, anchor: bool) -> str:
    """master Classification 라벨(Common/Change/New/Delete)."""
    action = str(row.get("applied_action") or "").lower()
    new_pno = str(row.get("new_part_no") or "").strip().upper()
    if action in {"add", "add_bundle"}:
        return "New"
    if action == "delete":
        return "Delete"
    if action == "change" or action.startswith("subtree"):
        return "Change"
    if action == "propagated_change" or new_pno == "TBD":
        return "Change"  # 하위 변경으로 상위 Assembly 재발행
    # 변경점 트리 안의 재사용 부품 → 공용
    return "Common"


def master_records_from_bom(new_bom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """반영된 BOM에서 '변경점에 따른 상·하위 트리'를 골라 master 템플릿 컬럼으로 매핑.

    변경(anchor) 행뿐 아니라, 그 조상(상위 Assembly)·후손(재사용 하위 부품)도 포함해
    공용(Common) 부품까지 master에 실린다. 변경점과 무관한 가지는 제외.
    """
    n = len(new_bom)
    if n == 0:
        return []
    depths = [_depth_of(r) for r in new_bom]
    include = [False] * n
    for i, row in enumerate(new_bom):
        if not _is_anchor(row):
            continue
        include[i] = True
        # 상위 트리(조상): 뒤로 가며 더 얕은 깊이를 차례로 포함.
        cur = depths[i]
        j = i - 1
        while j >= 0 and cur > 0:
            if depths[j] < cur:
                include[j] = True
                cur = depths[j]
            j -= 1
        # 하위 트리(후손): 더 깊은 연속 구간.
        k = i + 1
        while k < n and depths[k] > depths[i]:
            include[k] = True
            k += 1

    records: list[dict[str, Any]] = []
    seq = 0
    for i, row in enumerate(new_bom):
        if not include[i]:
            continue
        seq += 1
        base_pno = row.get("base_part_no") or ""
        new_pno = row.get("new_part_no") or ""
        cls = _classify(row, anchor=_is_anchor(row))
        # 공용(재사용)인데 New P/No가 비면 base를 그대로(부품 재사용).
        if cls == "Common" and not new_pno:
            new_pno = base_pno
        records.append({
            "no": seq,
            "bom_level": row.get("bom_level") or "",
            "part_type": row.get("part_type") or "",
            "base_part_no": base_pno,
            "new_part_no": new_pno,
            "part_name": row.get("part_name") or "",
            "base_qty": row.get("base_qty") or "",
            "new_qty": row.get("new_qty") or "",
            "changing_point": row.get("changing_point") or "",
            "changing_reason": row.get("changing_reason") or "",
            "supplier": row.get("supplier") or "",
            "classification": cls,
            "mold": "",      # 사용자가 채울 칸(빈칸)
            "inhouse": "",
            "approval": "",
        })
    return records


def build_master_csv(records: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _, header in MASTER_FIELDS])
    for r in records:
        writer.writerow([r.get(key, "") for key, _ in MASTER_FIELDS])
    # utf-8-sig: Excel 한글 깨짐 방지
    return buffer.getvalue().encode("utf-8-sig")


# ─────────── XLSX(템플릿 복사 + 데이터 교체) ───────────

_ROW_RE = re.compile(r'<row r="(\d+)"(?:[^>]*?/>|[^>]*?>.*?</row>)', re.DOTALL)
_SHEETDATA_RE = re.compile(r"(<sheetData[^>]*>)(.*)(</sheetData>)", re.DOTALL)
_DIM_RE = re.compile(r'<dimension ref="A1:[A-Z]+\d+"\s*/>')


def _data_styles(inner: str) -> dict[str, str]:
    """10행(샘플 데이터)에서 컬럼별 스타일(s=)을 뽑아 새 행에 재사용(테두리 보존)."""
    styles: dict[str, str] = {}
    for m in _ROW_RE.finditer(inner):
        if m.group(1) != str(DATA_START_ROW):
            continue
        for cm in re.finditer(r'<c r="([A-Z]+)%d"([^>]*?)(?:/>|>)' % DATA_START_ROW, m.group(0)):
            sm = re.search(r's="(\d+)"', cm.group(2))
            if sm:
                styles[cm.group(1)] = sm.group(1)
        break
    return styles


def _data_row_xml(row_index: int, record: dict[str, Any], styles: dict[str, str]) -> str:
    cells = []
    for col_index, (key, _) in enumerate(MASTER_FIELDS, start=1):
        letter = _col_letter(col_index)
        style = f' s="{styles[letter]}"' if letter in styles else ""
        value = escape("" if record.get(key) is None else str(record.get(key)))
        cells.append(
            f'<c r="{letter}{row_index}"{style} t="inlineStr">'
            f'<is><t xml:space="preserve">{value}</t></is></c>'
        )
    return f'<row r="{row_index}" spans="1:15">{"".join(cells)}</row>'


def _set_cell(xml: str, ref: str, value: str) -> str:
    """메타 셀(Base/New Model 등) 값을 교체. 스타일 유지, inlineStr로 기록."""
    m = re.search(r'<c r="%s"([^>]*?)(?:/>|>.*?</c>)' % re.escape(ref), xml, re.DOTALL)
    if not m:
        return xml
    sm = re.search(r's="(\d+)"', m.group(1))
    style = f' s="{sm.group(1)}"' if sm else ""
    cell = (
        f'<c r="{ref}"{style} t="inlineStr">'
        f'<is><t xml:space="preserve">{escape(value)}</t></is></c>'
    )
    return xml[:m.start()] + cell + xml[m.end():]


def build_master_xlsx(
    records: list[dict[str, Any]],
    *,
    base_model: str = "",
    new_model: str = "",
    event: str = "",
) -> bytes:
    template = TEMPLATE_PATH.read_bytes()
    with zipfile.ZipFile(io.BytesIO(template)) as zin:
        names = zin.namelist()
        sheet_xml = zin.read(SHEET_PATH).decode("utf-8")
        others = {n: zin.read(n) for n in names if n != SHEET_PATH}

    sd = _SHEETDATA_RE.search(sheet_xml)
    if not sd:
        raise ValueError("template sheetData not found")
    inner = sd.group(2)
    styles = _data_styles(inner)

    # 헤더(1~9행)만 남기고 샘플 데이터 제거.
    kept = [m.group(0) for m in _ROW_RE.finditer(inner) if int(m.group(1)) <= HEADER_ROWS]
    generated = [_data_row_xml(DATA_START_ROW + i, rec, styles) for i, rec in enumerate(records)]
    new_inner = "".join(kept) + "".join(generated)
    sheet_xml = sheet_xml[:sd.start(2)] + new_inner + sheet_xml[sd.end(2):]

    # dimension 갱신 + 메타 셀(샘플값 비우고 제공값 채움).
    last_row = HEADER_ROWS + len(records)
    sheet_xml = _DIM_RE.sub(f'<dimension ref="A1:O{max(last_row, HEADER_ROWS)}"/>', sheet_xml)
    sheet_xml = _set_cell(sheet_xml, "E2", base_model)
    sheet_xml = _set_cell(sheet_xml, "E3", new_model)
    sheet_xml = _set_cell(sheet_xml, "E4", event)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in others.items():
            zout.writestr(name, data)
        zout.writestr(SHEET_PATH, sheet_xml.encode("utf-8"))
    return buffer.getvalue()
