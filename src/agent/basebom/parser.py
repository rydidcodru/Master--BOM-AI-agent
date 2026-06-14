"""업로드된 베이스 BOM(xlsx) 파서.

심의회 PPT와 함께 올라오는 프로젝트 시작 BOM을 트리 스냅샷으로 읽는다. dotted Lvl
(".1", "..2", "...3") 깊이 기반으로 ``bom_path``를 만들고, 변경점 적용(New BOM 생성)의
출발 트리로 쓴다. DB의 ``bom_edge``(`src/agent/repository/bom.py`)와 별개 — 이쪽은
파일 단위 업로드 스냅샷이다. 260508 ``build_structured_docs_from_base`` 로직 재사용.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BaseBom",
    "BaseBomRow",
    "BomSubtreeRow",
    "parse_base_bom",
    "walk_base_bom_subtree",
]


# 컬럼 동의어 (260508 COL_SYNONYMS + BOM 전용 헤더).
_COL_SYNONYMS: dict[str, list[str]] = {
    "part_no": ["Part No", "P/NO", "P/NO.", "PNO", "품번", "부품번호", "P/N", "PARTNO"],
    "part_name": ["Description", "DESC", "DESC.", "Desc", "부품명", "품명", "Part Name(자)", "Part Name"],
    "parent_part_no": ["Parent Part No(모)", "Parent Part No", "모품번", "Parent"],
    "lvl": ["Lvl", "LVL", "Level", "LEVEL", "레벨"],
    "qty": ["Qty", "QTY", "수량", "개수"],
    "type": ["Type", "TYPE", "유형"],
}


def _norm_col(x: Any) -> str:
    s = "" if x is None else str(x)
    return re.sub(r"[^A-Z0-9가-힣]+", "", s.strip().upper())


def _detect_columns(columns: list[str]) -> dict[str, str]:
    """표준필드 → 원본 컬럼명 매핑 (정규화 비교)."""
    norm_to_orig = {_norm_col(c): c for c in columns}
    colmap: dict[str, str] = {}
    for std, syns in _COL_SYNONYMS.items():
        for s in syns:
            ns = _norm_col(s)
            if ns in norm_to_orig:
                colmap[std] = norm_to_orig[ns]
                break
    return colmap


def _depth_from_lvl(lvl_raw: str) -> int:
    """dotted Lvl(".1","..2") → 깊이. 점 개수 우선, 없으면 숫자, 둘 다 없으면 1."""
    s = str(lvl_raw or "").strip()
    dots = len(s) - len(s.lstrip("."))
    if dots:
        return dots
    digits = re.sub(r"[^0-9]", "", s)
    if digits:
        try:
            return max(int(digits), 1)
        except ValueError:
            return 1
    return 1


@dataclass
class BaseBomRow:
    row_id: int
    part_no: str
    part_name: str
    parent_part_no: str
    lvl: str
    depth: int
    bom_path: str
    qty: str = ""
    type: str = ""
    excel_row: int | None = None  # 1-indexed 원본 엑셀 행(서식보존 쓰기용)


@dataclass
class BaseBom:
    model: str
    file_name: str
    rows: list[BaseBomRow] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    source_bytes: bytes | None = None  # 원본 xlsx(서식보존 쓰기용 — 리로드 후 변경셀만 덮어씀)
    sheet_name: str | None = None      # 파싱한 시트명

    def index_by_pno(self) -> dict[str, list[int]]:
        """정규화 품번 → row_id 리스트 (대소문자/공백 무시)."""
        idx: dict[str, list[int]] = {}
        for r in self.rows:
            key = _norm_pno(r.part_no)
            if key:
                idx.setdefault(key, []).append(r.row_id)
        return idx


def _norm_pno(p: str) -> str:
    return re.sub(r"\s+", "", str(p or "")).upper()


def _model_from_rows(rows: list[BaseBomRow], raw_root: str) -> str:
    """루트(lvl=0) Part No에서 모델코드 추출. 'WSED7667M.ABMQEUR@...' → 'WSED7667M'."""
    head = str(raw_root or "").strip()
    m = re.match(r"([A-Z]{2,5}\d{3,5}[A-Z0-9]{0,2})", head.upper())
    if m:
        return m.group(1)
    return head.split(".")[0].split("@")[0].strip()


def parse_base_bom(file_bytes: bytes, *, file_name: str = "base_bom.xlsx") -> BaseBom:
    """베이스 BOM xlsx bytes → BaseBom 스냅샷. LLM/DB/네트워크 호출 0회."""
    try:
        import pandas as pd
    except Exception as e:  # pragma: no cover
        raise RuntimeError("pandas/openpyxl이 필요합니다.") from e

    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    sheet_name = xls.sheet_names[0]
    df = xls.parse(sheet_name, header=0, dtype=str)
    df = df.dropna(how="all").fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    cols = _detect_columns(list(df.columns))

    pno_c = cols.get("part_no")
    name_c = cols.get("part_name")
    lvl_c = cols.get("lvl")
    parent_c = cols.get("parent_part_no")
    qty_c = cols.get("qty")
    type_c = cols.get("type")

    rows: list[BaseBomRow] = []
    root_raw = ""
    path_stack: list[str] = []
    for i, r in df.iterrows():
        idx = int(i)
        pno = str(r.get(pno_c, "") if pno_c else "").strip()
        name = str(r.get(name_c, "") if name_c else "").strip()
        lvl_raw = str(r.get(lvl_c, "") if lvl_c else "").strip()
        parent = str(r.get(parent_c, "") if parent_c else "").strip()
        qty = str(r.get(qty_c, "") if qty_c else "").strip()
        ptype = str(r.get(type_c, "") if type_c else "").strip()

        # 루트(모델) 행: lvl 0 또는 점 없음 + 첫 행.
        if lvl_raw in ("0", "") and not root_raw and pno:
            root_raw = pno
            # 루트는 트리 노드로 넣지 않고 모델로만 사용.
            continue
        if not pno and not name:
            continue

        depth = _depth_from_lvl(lvl_raw)
        label = (name or pno)[:40]
        path_stack = path_stack[: depth - 1] + [label]
        bom_path = " > ".join(path_stack)

        rows.append(
            BaseBomRow(
                row_id=idx,
                part_no=pno,
                part_name=name,
                parent_part_no=parent,
                lvl=lvl_raw,
                depth=depth,
                bom_path=bom_path,
                qty=qty,
                type=ptype,
                excel_row=idx + 2,  # header=0(엑셀 1행) → 데이터 idx 0 = 엑셀 2행
            )
        )

    model = _model_from_rows(rows, root_raw)
    return BaseBom(
        model=model, file_name=file_name, rows=rows, columns=cols,
        source_bytes=file_bytes, sheet_name=sheet_name,
    )


@dataclass
class BomSubtreeRow:
    """``walk_base_bom_subtree`` 결과 — anchor 아래 한 부품."""

    rel_level: int  # anchor 기준 상대 깊이 (1 = 직속 자식)
    part_no: str
    part_name: str
    lvl: str
    bom_path: str
    qty: str = ""


def walk_base_bom_subtree(
    bom: BaseBom, anchor_pno: str, max_depth: int = 2
) -> list[BomSubtreeRow]:
    """업로드 BaseBom에서 ``anchor_pno`` 부품 아래 ``max_depth`` 레벨까지 하위 트리.

    DB ``bom_edge``(엣지 CTE)와 달리, 들여쓰기 BOM의 **행 순서 + dotted-Lvl 깊이**로
    서브트리를 잘라낸다(parent_part_no 미충전이어도 동작). anchor 행 다음부터 깊이가
    anchor보다 큰 연속 구간이 그 서브트리이며, ``max_depth`` 상대 레벨까지만 수집한다.

    Args:
        bom: 업로드한 베이스 BOM 스냅샷.
        anchor_pno: 전개 기준 부품 P/No(정규화 비교).
        max_depth: 가져올 하위 레벨 수(유저 지정).

    Returns:
        :class:`BomSubtreeRow` 리스트(없으면 빈 리스트 — anchor 부재/자식 없음).
    """
    key = _norm_pno(anchor_pno)
    if not key:
        return []
    rows = bom.rows
    start = next((i for i, r in enumerate(rows) if _norm_pno(r.part_no) == key), None)
    if start is None:
        return []
    base_depth = rows[start].depth
    out: list[BomSubtreeRow] = []
    for r in rows[start + 1:]:
        if r.depth <= base_depth:
            break  # 서브트리 종료(형제/상위로 복귀)
        rel = r.depth - base_depth
        if rel > max_depth:
            continue  # 요청 레벨보다 깊은 노드는 건너뛰되 스캔은 계속
        out.append(
            BomSubtreeRow(
                rel_level=rel,
                part_no=r.part_no,
                part_name=r.part_name,
                lvl=r.lvl,
                bom_path=r.bom_path,
                qty=r.qty,
            )
        )
    return out
