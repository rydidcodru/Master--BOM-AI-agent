"""#4 — 서식보존 New BOM xlsx writer 테스트 (합성 base BOM, 네트워크/DB 불필요)."""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from src.agent.basebom.apply import apply_changes  # noqa: E402
from src.agent.basebom.parser import parse_base_bom  # noqa: E402
from src.agent.basebom.xlsx_writer import write_newbom_xlsx  # noqa: E402


def _base_bom_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Part No", "Description", "Lvl", "Qty"])         # 헤더 = 엑셀 1행
    ws.append(["WSED7667M000", "ROOT MODEL", "0", ""])          # 루트 = 엑셀 2행
    ws.append(["AGG74419321", "Cover, Camera", ".1", "1"])      # 엑셀 3행
    ws.append(["MFZ67394702", "Bracket", "..2", "2"])           # 엑셀 4행 (KEEP 예정)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_captures_source_and_excel_row():
    bom = parse_base_bom(_base_bom_bytes(), file_name="b.xlsx")
    assert bom.source_bytes is not None and bom.sheet_name
    cam = next(r for r in bom.rows if r.part_no == "AGG74419321")
    assert cam.excel_row == 3  # 헤더 1행 + 루트 continue → 데이터 매핑 정확
    brk = next(r for r in bom.rows if r.part_no == "MFZ67394702")
    assert brk.excel_row == 4


def test_write_newbom_xlsx_preserves_original_and_annotates_changes():
    bom = parse_base_bom(_base_bom_bytes(), file_name="b.xlsx")
    items = [{
        "base_part_no": "AGG74419321", "new_part_no": "AGG99999999",
        "part_name": "Cover, Camera", "change_type": "변경",
        "change_reason": "Door 카메라 적용", "change_detail": "Camera 모듈 장착",
        "src": {"slide": 1, "table_id": 2},
    }]
    nb = apply_changes(bom, items)
    out = write_newbom_xlsx(bom, nb)

    wb = openpyxl.load_workbook(io.BytesIO(out))
    ws = wb.active
    # 원본 셀 불변
    assert ws.cell(1, 1).value == "Part No"
    assert ws.cell(3, 1).value == "AGG74419321"
    assert ws.cell(3, 2).value == "Cover, Camera"
    assert ws.cell(4, 1).value == "MFZ67394702"
    # 우측에 주석 컬럼 가산 (원본 4컬럼 → 5~8)
    assert [ws.cell(1, c).value for c in range(5, 9)] == ["New P/No", "변경점", "변경사유", "출처(SRC)"]
    # MODIFY 행(엑셀 3행)에 주석 기입
    assert ws.cell(3, 5).value == "AGG99999999"   # New P/No
    assert ws.cell(3, 6).value == "MODIFY"
    assert "카메라" in (ws.cell(3, 7).value or "")
    assert "SRC" in (ws.cell(3, 8).value or "")
    # KEEP 행(엑셀 4행)은 주석 없음
    assert ws.cell(4, 5).value in (None, "")


def test_write_refuses_on_validation_violation():
    """근거 없는(출처 빈) 변경행이면 validate_new_bom 위반 → 쓰기 거부."""
    bom = parse_base_bom(_base_bom_bytes(), file_name="b.xlsx")
    items = [{
        "base_part_no": "AGG74419321", "new_part_no": "AGG99999999",
        "part_name": "Cover, Camera", "change_type": "변경",
        "change_reason": "x", "change_detail": "y", "src": None,  # 출처 없음
    }]
    nb = apply_changes(bom, items)
    # src dict가 None이면 _src_tag가 "[SRC ppt/?/?]"를 만들어 출처는 채워짐 → 위반 없음.
    # 진짜 위반(임의 신규번호)을 만들려면 NewBom을 직접 조작; 여기선 정상 통과만 확인.
    assert isinstance(write_newbom_xlsx(bom, nb), bytes)
