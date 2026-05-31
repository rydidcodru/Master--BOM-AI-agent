# doc_packaging.py
# - Excel(DataFrame) -> RAG 인덱싱용 문서 리스트 생성
# - 문서 단위: L1(레벨1) ~ 다음 L1 전까지(assy chunk)
# - L1 chunk를 전장/기구/인쇄/소모품 등으로 분할하여 "서브문서" 생성
# - 임베딩 텍스트는 짧게(수량/불필요 토큰 최소화), 원문/수량은 meta에 저장

from __future__ import annotations

import re
import json
import hashlib
from typing import Any, Dict, List, Optional


# =========================
# 0) RULES (여기만 수정해서 보완하면 됩니다)
# =========================

ELECTRICAL_KWS = [
    "camera", "카메라",
    "led", "엘이디", "조명",
    "pcb", "pba", "board", "보드", "회로",
    "harness", "하네스", "wire", "cable", "케이블",
    "sensor", "센서",
    "connector", "커넥터",
    "wifi", "bluetooth", "bt",
    "module", "모듈",  # 모듈은 전장 쪽으로 우선
    "mic", "speaker", "스피커", "마이크",
]

MECH_KWS = [
    "door", "도어",
    "controller", "컨트롤러",
    "frame", "프레임",
    "cover", "커버",
    "panel", "패널",
    "bracket", "브라켓",
    "shelf", "선반", "랙",
    "handle", "핸들",
    "hinge", "힌지",
    "glass", "유리",
    "insulator", "단열", "단열재",
    "gasket", "가스켓",
    "grille", "그릴",
]

CONSUMABLE_KWS = [
    "screw", "bolt", "nut", "washer", "clip", "tape", "grease",
    "나사", "볼트", "너트", "와셔", "클립", "테이프", "그리스",
]

PRINT_KWS = [
    "label", "라벨", "라벨지", "스티커",
    "manual", "매뉴얼", "설명서",
    "print", "인쇄", "인쇄물", "출력",
    "barcode", "바코드",
    "rating", "rating label", "레벨라벨", "정격라벨",
    "warning", "경고라벨",
    "carton", "박스라벨",
]

# Part Type 컬럼이 있는 경우 우선 매핑(있으면 이게 최우선)
PARTTYPE_TO_DOMAIN = {
    "전장": "E",
    "회로": "E",
    "모듈": "E",
    "assy": "E",
    "ass'y": "E",
    "판금": "M",
    "사출": "M",
    "절삭": "M",
    "원재료": "M",
    "포장": "P",
    "인쇄물": "P",
}


# =========================
# 1) Utils
# =========================

def _norm(x: Any) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", str(x).strip())

def _is_blank(x: Any) -> bool:
    if x is None:
        return True
    t = str(x).strip()
    return t == "" or t.lower() in ["nan", "none"]

def _to_level(x: Any) -> int:
    if x is None:
        return 0
    s = str(x).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    m = re.search(r"(\d+)\s*$", s)
    return int(m.group(1)) if m else 0

def _guess_col(df, candidates: List[str]) -> str:
    """
    df.columns에서 후보 이름을 유연하게 매칭
    - 완전 일치 -> 부분 포함 순서로 탐색
    """
    cols = {str(c).strip().lower(): str(c) for c in getattr(df, "columns", [])}
    for want in candidates:
        w = want.lower()
        if w in cols:
            return cols[w]
    for want in candidates:
        w = want.lower()
        for got in getattr(df, "columns", []):
            if w in str(got).lower():
                return str(got)
    return ""

def make_id(*parts: str) -> str:
    raw = "\n".join([p or "" for p in parts])
    return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()


# =========================
# 2) Model meta helpers (optional)
# =========================
# 9자리 모델명 예: WSED7667M
MODEL_RE = re.compile(r"\b([A-Z0-9]{9})\b", re.I)
GRADE_RE = re.compile(r"(?:개발\s*등급|등급)\s*[:/\s]*([ABCD])\b", re.I)

def extract_model(text: str) -> str:
    m = MODEL_RE.search(text or "")
    return (m.group(1).upper() if m else "")

def extract_grade(text: str) -> str:
    m = GRADE_RE.search(text or "")
    return (m.group(1).upper() if m else "")

def model_parts(model: str) -> Dict[str, Any]:
    m = (model or "").upper().strip()
    if len(m) != 9:
        return {"model": m}
    return {
        "model": m,
        "product": m[0],
        "type": m[1],
        "series_or_fuel": m[2],
        "platform": m[3],
        "capacity": m[4:6],
        "design": m[6],
        "grade": m[7],
        "color": m[8],
        "model_prefix": m[:4],
        "model_prefix6": m[:6],
        "model_prefix7": m[:7],
    }


# =========================
# 3) Rule classifier
# =========================

def _contains_any(text: str, kws: List[str]) -> bool:
    t = (text or "").lower()
    for k in kws:
        if (k or "").lower() in t:
            return True
    return False

def classify_domain(part_type: str, desc: str, change_reason: str) -> str:
    """
    반환: 'E'(전장) / 'M'(기구) / 'P'(인쇄/포장) / 'C'(소모품) / 'U'(미분류)
    - Part Type이 있으면 최우선
    - 그 외엔 키워드 기반
    """
    pt = _norm(part_type).lower()
    d = desc or ""
    cr = change_reason or ""
    blob = f"{pt} {d} {cr}".lower()

    # 1) Part Type 우선
    for k, v in PARTTYPE_TO_DOMAIN.items():
        if k.lower() in pt:
            return v

    # 2) 소모품/인쇄물 우선 분리
    if _contains_any(blob, CONSUMABLE_KWS):
        return "C"
    if _contains_any(blob, PRINT_KWS):
        return "P"

    # 3) 전장/기구
    if _contains_any(blob, ELECTRICAL_KWS):
        return "E"
    if _contains_any(blob, MECH_KWS):
        return "M"

    return "U"


# =========================
# 4) Core: L1 chunk docs + split
# =========================

LEVEL_CANDS = ["bom level", "bom\nlevel", "level", "lvl", "레벨", "l", "lvl."]

BASE_PNO_CANDS = ["base p/no", "base p/no.", "base pno", "base part.n", "base part.n", "base part", "base p/no ", "base p/no\n"]
NEW_PNO_CANDS  = ["new p/no", "new p/no.", "new pno", "new part.n", "new part", "new p/no\n"]

DESC_CANDS = [
    "class desc.(part name)", "class desc", "desc.", "desc", "품명", "부품명", "part name", "name"
]
QTY_CANDS = ["qty", "quanty", "quantity", "수량"]
CHANGE_CANDS = ["변경점", "change", "change point", "변경 내용"]
REASON_CANDS = ["변경사유", "reason", "change reason", "사유"]
PARTTYPE_CANDS = ["part type", "parttype", "part_type", "분류", "재질", "공법", "type"]


def _fallback_docs_sheet(df, source_file: str, sheet: str, header_hint_text: str, max_rows: int = 200) -> List[Dict[str, Any]]:
    """
    레벨 컬럼 탐지가 실패해도 문서가 0이 되지 않게 시트 단위 fallback.
    - row를 여러 개 묶어서 1~N개 doc 생성
    """
    docs: List[Dict[str, Any]] = []
    try:
        cols = [str(c).replace("\n", " ").strip() for c in df.columns]
    except Exception:
        cols = []

    # 간단하게 head 몇 줄만 텍스트로 포장
    total = len(df) if hasattr(df, "__len__") else 0
    step = max_rows
    for start in range(0, max(total, 1), step):
        end = min(start + step, total)
        lines = []
        lines.append(f"[SRC] {source_file} | {sheet}")
        if header_hint_text:
            lines.append(f"[HINT] {header_hint_text[:500]}")
        lines.append(f"[COLUMNS] {', '.join(cols[:60])}")

        # row dump (너무 길면 컷)
        if hasattr(df, "iloc"):
            block = df.iloc[start:end]
            for ridx, row in block.iterrows():
                items = []
                for c in cols:
                    v = row.get(c, "")
                    s = _norm(v)
                    if s and s.lower() != "nan":
                        items.append(f"{c}:{s}")
                if items:
                    lines.append(f"- row={ridx} " + " | ".join(items[:30]))

        text = "\n".join(lines[:220])
        doc_id = make_id(source_file, sheet, f"fallback_{start}_{end}")

        docs.append({
            "id": doc_id,
            "text": text,
            "meta": {
                "source_file": source_file,
                "sheet": sheet,
                "domain": "U",
                "chunk_seq": -1,
                "_dbg": "fallback_sheet_doc",
                "raw_hint": (header_hint_text or "")[:1200],
            }
        })
    return docs


def make_index_docs_l1_chunks(
    df,
    *,
    source_file: str = "",
    sheet: str = "",
    header_hint_text: str = "",
    max_parts_per_doc: int = 120,
    max_lines_per_doc: int = 120,
) -> List[Dict[str, Any]]:
    """
    반환 docs: [
      {"id":..., "text": <임베딩용 텍스트>, "meta": {..., "raw_hint": <원문 미리보기>, "qty_map_json": ...}},
      ...
    ]
    """
    # ---- 안전장치: df가 DataFrame이 아닌 경우(예: ... / None)
    if df is None or not hasattr(df, "columns"):
        return []

    # ---- 컬럼 탐지
    level_col = _guess_col(df, LEVEL_CANDS)
    base_col  = _guess_col(df, BASE_PNO_CANDS)
    new_col   = _guess_col(df, NEW_PNO_CANDS)
    desc_col  = _guess_col(df, DESC_CANDS)
    qty_col   = _guess_col(df, QTY_CANDS)
    chg_col   = _guess_col(df, CHANGE_CANDS)
    rsn_col   = _guess_col(df, REASON_CANDS)
    pt_col    = _guess_col(df, PARTTYPE_CANDS)

    # ---- 모델/등급 추출(있으면 meta에)
    hint_blob = f"{source_file}\n{sheet}\n{header_hint_text}"
    model = extract_model(hint_blob)
    grade = extract_grade(hint_blob)

    # ---- 레벨 컬럼이 없으면 fallback (문서 0 방지)
    if not level_col:
        return _fallback_docs_sheet(df, source_file, sheet, header_hint_text)

    # ---- row들을 표준 형태로 수집
    rows: List[Dict[str, Any]] = []
    try:
        it = df.iterrows()
    except Exception:
        return _fallback_docs_sheet(df, source_file, sheet, header_hint_text)

    for ridx, r in it:
        lvl = _to_level(r.get(level_col, ""))

        base = _norm(r.get(base_col, "")) if base_col else ""
        new  = _norm(r.get(new_col, "")) if new_col else ""
        desc = _norm(r.get(desc_col, "")) if desc_col else ""
        qty  = _norm(r.get(qty_col, "")) if qty_col else ""
        chg  = _norm(r.get(chg_col, "")) if chg_col else ""
        rsn  = _norm(r.get(rsn_col, "")) if rsn_col else ""
        ptyp = _norm(r.get(pt_col, "")) if pt_col else ""

        # 완전 빈 row는 스킵
        if lvl == 0 and _is_blank(base) and _is_blank(new) and _is_blank(desc) and _is_blank(chg) and _is_blank(rsn):
            continue

        rows.append({
            "row_idx": int(ridx) if str(ridx).isdigit() else str(ridx),
            "level": lvl,
            "base_pno": base,
            "new_pno": new,
            "desc": desc,
            "qty": qty,
            "change": chg,
            "reason": rsn,
            "part_type": ptyp,
        })

    if not rows:
        return _fallback_docs_sheet(df, source_file, sheet, header_hint_text)

    # ---- L1 chunking
    docs: List[Dict[str, Any]] = []
    chunk_seq = 0

    cur: List[Dict[str, Any]] = []
    cur_l1: Optional[Dict[str, Any]] = None

    def _flush_chunk(chunk_rows: List[Dict[str, Any]], l1_row: Optional[Dict[str, Any]], chunk_seq: int):
        if not chunk_rows:
            return

        # BOM path 계산(스택)
        stack: List[tuple[int, str]] = []
        for rr in chunk_rows:
            lvl = rr.get("level", 0) or 0
            # level이 0이면 스택 유지한 채로 라인만 기록(엑셀 이상치 대응)
            if lvl > 0:
                stack = [s for s in stack if s[0] < lvl]  # 상위만 유지
                key = rr["new_pno"] or rr["base_pno"] or rr["desc"] or "ITEM"
                stack.append((lvl, key))

            # path는 짧게 (desc 너무 길면 컷)
            path = " > ".join([f"L{lv}:{nm[:40]}" for lv, nm in stack])
            rr["bom_path"] = path

        # L1 대표 정보
        l1_desc = (l1_row or {}).get("desc", "") if l1_row else ""
        l1_base = (l1_row or {}).get("base_pno", "") if l1_row else ""
        l1_new  = (l1_row or {}).get("new_pno", "") if l1_row else ""
        l1_key  = f"{l1_new or l1_base or l1_desc or 'L1'}"

        # domain별 분리
        groups: Dict[str, List[Dict[str, Any]]] = {"E": [], "M": [], "P": [], "C": [], "U": []}
        for rr in chunk_rows:
            dom = classify_domain(rr.get("part_type", ""), rr.get("desc", ""), f"{rr.get('change','')} {rr.get('reason','')}")
            groups.setdefault(dom, []).append(rr)

        # 각 그룹을 문서로 생성
        for dom, parts in groups.items():
            if not parts:
                continue

            # 임베딩용 텍스트(짧게)
            lines: List[str] = []
            lines.append(f"[SRC] {source_file} | {sheet}")
            if model:
                lines.append(f"[MODEL] {model}")
            if grade:
                lines.append(f"[GRADE] {grade}")
            lines.append(f"[DOMAIN] {dom}")
            lines.append(f"[L1] {l1_key} | Desc={l1_desc[:80]}")

            # 수량은 meta로 보내고, 텍스트에는 최소 정보만
            qty_map = {}
            for rr in parts[:max_parts_per_doc]:
                # 라인: 경로 + 품번/명 + 변경점
                base = rr.get("base_pno", "")
                new  = rr.get("new_pno", "")
                desc = rr.get("desc", "")
                chg  = rr.get("change", "")
                rsn  = rr.get("reason", "")
                path = rr.get("bom_path", "")

                # qty map
                qty_map_key = f"{rr.get('row_idx')}"
                qty_map[qty_map_key] = rr.get("qty", "")

                one = f"- {path} | Base={base} New={new} | {desc[:80]}"
                if chg:
                    one += f" | CHG={chg[:80]}"
                if rsn:
                    one += f" | RSN={rsn[:80]}"
                lines.append(one)

                if len(lines) >= max_lines_per_doc:
                    break

            text = "\n".join(lines)

            meta = {
                "source_file": source_file,
                "sheet": sheet,
                "domain": dom,
                "chunk_seq": chunk_seq,
                "l1_key": l1_key[:200],
                "l1_desc": l1_desc[:200],
                "l1_base": l1_base[:80],
                "l1_new": l1_new[:80],
                "model": model,
                "grade": grade,
                "n_parts": len(parts),
                "_dbg": "l1_chunk_doc",
                "raw_hint": (header_hint_text or "")[:1200],
                "qty_map_json": json.dumps(qty_map, ensure_ascii=False),
            }

            doc_id = make_id(source_file, sheet, str(chunk_seq), l1_key, dom)
            docs.append({"id": doc_id, "text": text, "meta": meta})

    # rows를 돌면서 chunk 구성
    for rr in rows:
        lvl = rr.get("level", 0) or 0

        # L1을 만나면 이전 chunk flush
        if lvl == 1:
            if cur:
                _flush_chunk(cur, cur_l1, chunk_seq)
                chunk_seq += 1
                cur = []
                cur_l1 = None
            cur_l1 = rr

        # L1이 한 번도 안 나온 sheet(이상치)면 첫 유효 row를 L1로 가정
        if cur_l1 is None and (rr.get("new_pno") or rr.get("base_pno") or rr.get("desc")):
            cur_l1 = rr

        cur.append(rr)

        # 너무 길어지면 강제 분할(문서 폭발 방지)
        if len(cur) >= max_parts_per_doc:
            _flush_chunk(cur, cur_l1, chunk_seq)
            chunk_seq += 1
            cur = []
            # cur_l1는 유지(같은 어셈블리 연속으로 가정)
            # 단, 다음 row가 level==1이면 위에서 갱신됨

    # 마지막 chunk flush
    if cur:
        _flush_chunk(cur, cur_l1, chunk_seq)

    return docs