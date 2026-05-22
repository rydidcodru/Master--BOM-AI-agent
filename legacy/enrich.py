"""
enrich.py — 변경부품리스트 빈칸 채우기 (Base BOM + DB, NO LLM)
"""
from __future__ import annotations
import re
import pandas as pd

# ─────────────────────────────────────────
# 0) 컬럼 동의어 매핑
# ─────────────────────────────────────────

COL_SYNONYMS = {
    "part_no":   ["P/NO","P/NO.","P/no.","Part No","품번","부품번호","PNO","PARTNO","Part No."],
    "part_name": ["Description","DESC","DESC.","Desc","Desc.","부품명","품명",
                  "Part Name","Part Name(자)"],
    "maker":     ["Maker","MAKER","제조사","업체","Supplier","Supplier Code"],
    "standard":  ["Standard","STANDARD","규격","Spec","SPEC"],
    "tech_spec": ["Technical Spec","TECHNICAL SPEC","기술규격","Tech Spec"],
    "qty":       ["Qty","QTY","수량","개수","수량(EA)","Qty(EA)"],
    "uom":       ["UOM","단위","Unit","UNIT"],

    # ✅ type과 supply_type을 분리
    "type":        ["Type","TYPE"],                          # ref_bom 내부 분류 (CircuitComponentPart 등)
    "supply_type": ["Supply Type","SUPPLY TYPE","SupplyType","supply_type"],  # BOM의 Supply Type 컬럼

    "lvl":       ["Lvl","LVL","Level","LEVEL","레벨"],
    "module":    ["Module","MODULE","모듈"],
    "grade":     ["Grade","GRADE","등급","Part Grade"],
    "parent_pno":["Parent Part No(모)","Parent Part No","모품번"],
    "i_s":       ["I.S"],
    "mr":        ["MR"],
    "document":  ["Document","DOCUMENT"],
    "check_out": ["Check Out","CHECK OUT","CHECKOUT"],
    "change":    ["Change","CHANGE"],
    "r":         ["R"],
    "uit":       ["UIT", "uit"],
    "svc_loc":   ["SVC Loc","SVCLOC","SVC LOC"],
    "svc":       ["SVC"],
    "ckd":       ["CKD"],
    "from_ckd":  ["From CKD","FROMCKD"],
    "sc":        ["SC"],
    "substitute_for": ["Substitute For","SUBSTITUTEFOR","Substitute for"],
    "copy_from": ["Copy From","COPYFROM"],
    "start_date":["Start Date","STARTDATE"],
    "end_date":  ["End Date","ENDDATE"],
    "change_in": ["Change In","CHANGEIN"],
    "change_out":["Change Out","CHANGEOUT"],
    "bom_exp_flag":["BOM Exp. Flag","BOMEXPFLAG"],
    "job_explanation":["Job Explanation","JOBEXPLANATION"],
    "designator":["Designator","DESIGNATOR"],
    "designator_split_qty":["Designator/Split Qty"],
    "designator_split_comments":["Designator/Split Comments"],
}

FILL_TARGETS = {
    "maker":       ["Maker","MAKER","제조사","업체","Supplier","Supplier Code"],
    "standard":    ["Standard","STANDARD","규격","Spec","SPEC"],
    "tech_spec":   ["Technical Spec","TECHNICAL SPEC","기술규격","Tech Spec"],
    "qty":         ["Qty","QTY","수량","개수","수량(EA)","Qty(EA)"],
    "uom":         ["UOM","단위","Unit","UNIT"],

    # ✅ type과 supply_type 분리 — 각각 별도 컬럼에 채움
    "type":        ["Type","TYPE"],
    "supply_type": ["Supply Type","SUPPLY TYPE","SupplyType"],

    "lvl":         ["Lvl","LVL","Level","LEVEL","레벨"],
    "module":      ["Module","MODULE","모듈"],
    "grade":       ["Grade","GRADE","등급","Part Grade"],
    "parent_pno":  ["Parent Part No(모)","Parent Part No","모품번"],
    "i_s":         ["I.S"],
    "mr":          ["MR"],
    "document":    ["Document","DOCUMENT"],
    "check_out":   ["Check Out","CHECK OUT","CHECKOUT"],
    "change":      ["Change","CHANGE"],
    "r":           ["R"],
    "uit":         ["UIT"],
    "svc_loc":     ["SVC Loc","SVCLOC","SVC LOC"],
    "svc":         ["SVC"],
    "ckd":         ["CKD"],
    "from_ckd":    ["From CKD","FROMCKD"],
    "sc":          ["SC"],
    "substitute_for": ["Substitute For","SUBSTITUTEFOR","Substitute for"],
    "copy_from":   ["Copy From","COPYFROM"],
    "start_date":  ["Start Date","STARTDATE"],
    "end_date":    ["End Date","ENDDATE"],
    "change_in":   ["Change In","CHANGEIN"],
    "change_out":  ["Change Out","CHANGEOUT"],
    "bom_exp_flag":["BOM Exp. Flag","BOMEXPFLAG"],
    "job_explanation":["Job Explanation","JOBEXPLANATION"],
    "designator":  ["Designator","DESIGNATOR"],
}

# ─────────────────────────────────────────
# FILL_TARGETS에서 parent_pno 제거 또는 별도 처리
# ─────────────────────────────────────────

# ✅ 참고품번에서 가져오면 안 되는 컬럼 (신규 부품 자신의 값이어야 함)
_NO_COPY_FROM_REF = {
    "parent_pno",   # 참고품번의 상위 Assy ≠ 신규 부품의 상위 Assy
    "lvl",          # 참고품번의 레벨 ≠ 신규 부품의 레벨 (구조가 다를 수 있음)
    "part_no",      # 품번 자체는 덮어쓰면 안 됨
    "part_name",    # 부품명도 참고품번으로 덮어쓰면 안 됨
}

# ✅ Base BOM에서 가져올 때는 parent_pno 허용 (같은 BOM 구조이므로)
_NO_COPY_FROM_REF_ONLY = {
    "parent_pno",
    "lvl",
}

# ─────────────────────────────────────────
# 1) 유틸 함수들
# ─────────────────────────────────────────

def _pick_col(df, key):
    if df is None or len(df.columns) == 0:
        return None
    synonyms = COL_SYNONYMS.get(key, [])
    norm_map = {}
    for c in df.columns:
        n = re.sub(r"[^A-Z0-9가-힣]", "", str(c).strip().upper())
        norm_map[n] = c
    for s in synonyms:
        ns = re.sub(r"[^A-Z0-9가-힣]", "", s.strip().upper())
        if ns in norm_map:
            return norm_map[ns]
    return None


def _pick_change_col(change_df, key):
    synonyms = FILL_TARGETS.get(key) or COL_SYNONYMS.get(key, [])
    norm_map = {}
    for c in change_df.columns:
        # ✅ 공백/특수문자 포함 컬럼도 정규화해서 비교
        norm_map[re.sub(r"[^A-Z0-9가-힣]", "", str(c).strip().upper())] = c
        # ✅ 원본도 키로 추가 (I.S, MR 등 단순 컬럼명)
        norm_map[str(c).strip()] = c

    for s in synonyms:
        # 정규화 비교
        s_norm = re.sub(r"[^A-Z0-9가-힣]", "", s.strip().upper())
        if s_norm in norm_map:
            return norm_map[s_norm]
        # 원본 직접 비교 (대소문자 무시)
        for c in change_df.columns:
            if str(c).strip().lower() == s.strip().lower():
                return c
    return None


def _norm_pno(x):
    if x is None:
        return ""
    s = str(x).strip().upper()
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def _norm_name(x):
    s = str(x or "").strip().upper()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^A-Z0-9가-힣 ]", "", s)
    return s.strip()


def _is_real_pno(pno_raw):
    s = str(pno_raw or "").strip()
    if not s:
        return False
    skip = {"채번필요", "(채번필요)", "(채번 필요)", "채번 필요", "TBD", "N/A", "NAN", ""}
    if s.upper() in {x.upper() for x in skip}:
        return False
    cleaned = re.sub(r"[^A-Z0-9]", "", s.upper())
    return len(cleaned) >= 5


# ─────────────────────────────────────────
# 1-2) 참고품번 추출 함수 (반드시 _is_real_pno 위에 위치)
# ─────────────────────────────────────────

_REF_PNO_PATTERNS = [
    re.compile(r"🟡\s*참고\s*[:：]\s*([A-Z0-9]{5,})", re.IGNORECASE),
    re.compile(r"참고\s*[:：]\s*([A-Z0-9]{5,})", re.IGNORECASE),
    re.compile(r"REF\s*[:：]\s*([A-Z0-9]{5,})", re.IGNORECASE),
    re.compile(r"Ref\.\s*([A-Z0-9]{5,})", re.IGNORECASE),
]

def _extract_ref_pno(text: str) -> str:
    """비고/변경사유 텍스트에서 참고 품번 추출"""
    if not text or not isinstance(text, str):
        return ""
    for pat in _REF_PNO_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return ""

# ─────────────────────────────────────────
# 3) Base 인덱스 — 품번 기준
# ─────────────────────────────────────────

def _build_base_index(base_df_raw):
    """
    Base BOM DataFrame → 품번 기준 인덱스
    반환: { norm_pno: {std_key: value, ...}, ... }
    """
    index = {}
    if base_df_raw is None or not isinstance(base_df_raw, pd.DataFrame) or len(base_df_raw) == 0:
        return index

    pno_col = _pick_col(base_df_raw, "part_no")
    if not pno_col:
        return index

    col_map = {}
    for std_key in COL_SYNONYMS:
        found = _pick_col(base_df_raw, std_key)
        if found:
            col_map[std_key] = found

    for _, row in base_df_raw.iterrows():
        raw_pno = str(row.get(pno_col, "") or "").strip()
        pno_key = _norm_pno(raw_pno)
        if not pno_key or len(pno_key) < 5:
            continue

        entry = {}
        for std_key, real_col in col_map.items():
            val = str(row.get(real_col, "") or "").strip()
            if val and val.lower() not in ("nan", "none", ""):
                entry[std_key] = val

        if pno_key not in index:  # 첫 번째 행 우선
            index[pno_key] = entry

    return index


# ─────────────────────────────────────────
# 4) Base 인덱스 — 부품명 기준 (4순위)
# ─────────────────────────────────────────

def _build_base_name_index(base_df_raw):
    """
    Base BOM DataFrame → 부품명 기준 인덱스
    반환: { norm_name: {std_key: value, ...}, ... }
    """
    index = {}
    if base_df_raw is None or not isinstance(base_df_raw, pd.DataFrame) or len(base_df_raw) == 0:
        return index

    name_col = _pick_col(base_df_raw, "part_name")
    pno_col  = _pick_col(base_df_raw, "part_no")
    if not name_col:
        return index

    col_map = {}
    for std_key in COL_SYNONYMS:
        found = _pick_col(base_df_raw, std_key)
        if found:
            col_map[std_key] = found

    for _, row in base_df_raw.iterrows():
        raw_name = str(row.get(name_col, "") or "").strip()
        name_key = _norm_name(raw_name)
        if not name_key or len(name_key) < 2:
            continue

        entry = {}
        for std_key, real_col in col_map.items():
            val = str(row.get(real_col, "") or "").strip()
            if val and val.lower() not in ("nan", "none", ""):
                entry[std_key] = val

        if name_key not in index:
            index[name_key] = entry

    return index


# ─────────────────────────────────────────
# 5) DB metadata → 우리 entry 형식으로 변환
# ─────────────────────────────────────────

def _convert_db_meta(meta):
    """
    Chroma metadata dict → COL_SYNONYMS 표준 키 기준 entry로 변환
    meta 키가 이미 표준 키면 그대로 사용,
    아니면 COL_SYNONYMS 동의어로 매핑 시도
    """
    if not meta or not isinstance(meta, dict):
        return {}

    entry = {}

    # 1) 이미 표준 키인 것은 그대로
    for std_key in COL_SYNONYMS:
        if std_key in meta:
            val = str(meta[std_key] or "").strip()
            if val and val.lower() not in ("nan", "none", ""):
                entry[std_key] = val

    # 2) 동의어로 매핑
    for raw_key, raw_val in meta.items():
        raw_norm = re.sub(r"[^A-Z0-9가-힣]", "", str(raw_key).strip().upper())
        for std_key, synonyms in COL_SYNONYMS.items():
            if std_key in entry:
                continue
            for s in synonyms:
                s_norm = re.sub(r"[^A-Z0-9가-힣]", "", s.strip().upper())
                if raw_norm == s_norm:
                    val = str(raw_val or "").strip()
                    if val and val.lower() not in ("nan", "none", ""):
                        entry[std_key] = val
                    break

    return entry


# ─────────────────────────────────────────
# 6) 메인: enrich_change_parts
# ─────────────────────────────────────────

def enrich_change_parts(change_df, base_df_raw, db_index=None):
    """
    변경부품리스트 빈칸 채우기
    우선순위:
      1순위: 품번 exact → Base BOM
      2순위: 비고/변경사유의 참고품번 → Base BOM
      3순위: 참고품번 → db_index (ref_boms 또는 Chroma)
      4순위: 부품명 → Base BOM
    """
    if change_df is None or len(change_df) == 0:
        return change_df

    df = change_df.copy()

    # ── 표준 키 → change_df 실제 컬럼명 매핑 ──
    fill_map = {}
    for key in FILL_TARGETS:
        col = _pick_change_col(df, key)
        if col:
            fill_map[key] = col

    import sys
    print(f"[enrich] fill_map keys: {list(fill_map.keys())}", file=sys.stderr)
    print(f"[enrich] fill_map values: {list(fill_map.values())}", file=sys.stderr)

    # ✅ Supply Type 전용 디버그
    type_col = _pick_change_col(df, "type")
    print(f"[enrich] type_col 매핑: {type_col}", file=sys.stderr)
    if type_col:
        sample_vals = df[type_col].dropna().unique()[:5].tolist()
        print(f"[enrich] change_df '{type_col}' 샘플값: {sample_vals}", file=sys.stderr)

    # ── Base 인덱스 구성 ──
    base_pno_idx  = _build_base_index(base_df_raw)
    base_name_idx = _build_base_name_index(base_df_raw)

    # ── 품번 컬럼 찾기 ──
    pno_col  = _pick_change_col(df, "part_no")
    name_col = _pick_change_col(df, "part_name")

    # ── 비고/변경사유 컬럼 (참고품번 추출용) ──
    REF_SEARCH_COLS = ["비고", "변경사유", "Job Explanation", "출처", "rsn", "chg"]
    ref_cols = [c for c in REF_SEARCH_COLS if c in df.columns]

    # ── filled_from 컬럼 추가 (디버그용) ──
    df["filled_from"] = ""

    for idx in df.index:
        # 현재 행의 품번/부품명
        raw_pno  = str(df.at[idx, pno_col] or "").strip() if pno_col else ""
        raw_name = str(df.at[idx, name_col] or "").strip() if name_col else ""

        matched_entry = None
        source_label  = ""
        is_ref_source = False   # ✅ 참고품번/db_index 출처 여부 플래그

        # ── 1순위: 품번 exact → Base BOM ──
        if _is_real_pno(raw_pno):
            norm = _norm_pno(raw_pno)
            if norm in base_pno_idx:
                matched_entry = base_pno_idx[norm]
                source_label  = f"base_exact:{raw_pno}"
                is_ref_source = False   # Base BOM → parent_pno 복사 허용

        # ── 2순위: 비고/변경사유 참고품번 → Base BOM ──
        if matched_entry is None:
            for col in ref_cols:
                text = str(df.at[idx, col] or "").strip()
                if not text or text.lower() in ("nan", "none", ""):
                    continue
                ref_pno = _extract_ref_pno(text)
                if not ref_pno:
                    continue
                ref_norm = _norm_pno(ref_pno)
                if ref_norm in base_pno_idx:
                    matched_entry = base_pno_idx[ref_norm]
                    source_label  = f"base_via_ref:{ref_pno}"
                    is_ref_source = True   # ✅ 참고품번 출처 → parent_pno 복사 금지
                    break

        # ── 3순위: 참고품번 → db_index ──
        if matched_entry is None and db_index:
            for col in ref_cols:
                text = str(df.at[idx, col] or "").strip()
                if not text or text.lower() in ("nan", "none", ""):
                    continue
                ref_pno = _extract_ref_pno(text)
                if not ref_pno:
                    ref_pno = raw_pno if _is_real_pno(raw_pno) else ""
                if ref_pno:
                    ref_norm = _norm_pno(ref_pno)
                    if ref_norm in db_index:
                        raw_entry = db_index[ref_norm]
                        matched_entry = {k: v for k, v in raw_entry.items()
                                         if k != "_source_file"}
                        source_label = f"db_index:{ref_pno}"
                        is_ref_source = True   # ✅ 참고품번 출처 → parent_pno 복사 금지
                        break

            # db_index 부품명 매칭
            if matched_entry is None and raw_name and db_index:
                name_norm = _norm_name(raw_name)
                for db_pno_key, db_entry in db_index.items():
                    db_name = _norm_name(db_entry.get("part_name", ""))
                    if db_name and db_name == name_norm:
                        matched_entry = {k: v for k, v in db_entry.items()
                                         if k != "_source_file"}
                        source_label = f"db_index_by_name:{raw_name}"
                        is_ref_source = True   # ✅ 참고품번 출처 → parent_pno 복사 금지
                        break

        # ── 4순위: 부품명 → Base BOM ──
        if matched_entry is None and raw_name:
            name_norm = _norm_name(raw_name)
            if name_norm in base_name_idx:
                matched_entry = base_name_idx[name_norm]
                source_label  = f"base_via_name:{raw_name}"
                is_ref_source = True   # ✅ 다른 부품의 base 행 → parent_pno 복사 금지

        if matched_entry is None:
            continue

        # ── 빈칸만 채우기 ──
        filled_cols = []
        for key, change_col in fill_map.items():
            try:
                if is_ref_source and key in _NO_COPY_FROM_REF_ONLY:
                    continue

                current = str(df.at[idx, change_col] or "").strip()
                if current.lower() in ("nan", "none", ""):
                    current = ""
                base_val = matched_entry.get(key, "")
                if isinstance(base_val, float):
                    import math
                    base_val = "" if math.isnan(base_val) else str(base_val)
                base_val = str(base_val or "").strip()

                if not current and base_val:
                    df.at[idx, change_col] = base_val
                    filled_cols.append(change_col)

            except Exception as e:
                import sys
                print(f"[enrich] fill 오류: key={key}, col={change_col}, err={e}", file=sys.stderr)
                continue

        if source_label:
            df.at[idx, "filled_from"] = source_label

    return df

## ─────────────────────────────────────────
## 7) BOM 트리 확장 — 상위 Assy를 그룹별 올바른 위치에 삽입
## ─────────────────────────────────────────

def expand_with_assy_tree(change_df, base_df_raw):
    """
    change_df에 '_group_parent_pno' 컬럼이 있으면
    그룹별로 상위 Assy 체인을 역추적 → 자기 그룹 바로 앞에 삽입.
    """
    if change_df is None or len(change_df) == 0:
        return change_df
    if "_group_parent_pno" not in change_df.columns:
        return change_df
    if base_df_raw is None or not isinstance(base_df_raw, pd.DataFrame) or len(base_df_raw) == 0:
        return change_df

    # ── 1) 컬럼 탐색 ──
    def _fc(df, synonyms):
        nm = {}
        for c in df.columns:
            n = re.sub(r"[^A-Z0-9가-힣]", "", str(c).strip().upper())
            nm[n] = c
        for s in synonyms:
            ns = re.sub(r"[^A-Z0-9가-힣]", "", s.strip().upper())
            if ns in nm:
                return nm[ns]
        return None

    base_pno_col    = _pick_col(base_df_raw, "part_no")
    base_desc_col   = _pick_col(base_df_raw, "part_name")
    base_lvl_col    = _fc(base_df_raw, ["Lvl", "LVL", "Level", "LEVEL", "레벨"])
    base_parent_col = _fc(base_df_raw, ["Parent Part No(모)", "Parent Part No", "모품번"])

    chg_pno_col  = _pick_col(change_df, "part_no")
    chg_desc_col = _pick_col(change_df, "part_name")

    if not base_pno_col:
        return change_df

    # ── 2) depth 계산 ──
    def _depth(lvl_str):
        s = str(lvl_str or "").strip()
        if s.startswith('.'):
            return s.count('.')
        try:
            return int(s)
        except:
            return 0

    # ── 3) Base BOM 인덱스 구축 ──
    pno_to_row      = {}
    child_to_parent = {}

    if base_parent_col:
        for _, row in base_df_raw.iterrows():
            pn = _norm_pno(str(row.get(base_pno_col, '')))
            if not pn:
                continue
            pno_to_row[pn] = row.to_dict()
            pp = _norm_pno(str(row.get(base_parent_col, '')))
            if pp and pp != pn:
                child_to_parent[pn] = pp
    elif base_lvl_col:
        stack = []
        for _, row in base_df_raw.iterrows():
            pn = _norm_pno(str(row.get(base_pno_col, '')))
            if not pn:
                continue
            d = _depth(row.get(base_lvl_col, ''))
            pno_to_row[pn] = row.to_dict()
            while stack and stack[-1][1] >= d:
                stack.pop()
            if stack:
                child_to_parent[pn] = stack[-1][0]
            stack.append((pn, d))
    else:
        return change_df

    # ── 4) change_df 내 기존 PNO (중복 삽입 방지) ──
    existing_pnos = set()
    if chg_pno_col:
        for _, row in change_df.iterrows():
            pn = _norm_pno(str(row.get(chg_pno_col, '')))
            if pn and _is_real_pno(str(row.get(chg_pno_col, ''))):
                existing_pnos.add(pn)

    # ── 5) Base → change_df 컬럼 매핑 ──
    chg_cols = list(change_df.columns)
    base_to_chg = {}
    for bc in base_df_raw.columns:
        bc_n = re.sub(r"[^A-Z0-9가-힣]", "", str(bc).strip().upper())
        for cc in chg_cols:
            cc_n = re.sub(r"[^A-Z0-9가-힣]", "", str(cc).strip().upper())
            if bc_n == cc_n:
                base_to_chg[bc] = cc
                break

    def _make_assy_row(apno):
        base_row = pno_to_row.get(apno, {})
        new_row = {c: "" for c in chg_cols}
        for bc, cc in base_to_chg.items():
            val = str(base_row.get(bc, "")).strip()
            if val and val.lower() not in ("nan", "none"):
                new_row[cc] = val
        if chg_pno_col and base_pno_col:
            v = str(base_row.get(base_pno_col, "")).strip()
            if v and v.lower() not in ("nan", "none"):
                new_row[chg_pno_col] = v
        if chg_desc_col and base_desc_col:
            v = str(base_row.get(base_desc_col, "")).strip()
            if v and v.lower() not in ("nan", "none"):
                new_row[chg_desc_col] = v
        if "변경유형" in new_row:
            new_row["변경유형"] = "변경"
        if "변경사유" in new_row:
            new_row["변경사유"] = "하위 부품 변경"
        if "비고" in new_row:
            new_row["비고"] = "▲ 상위 Assy"
        if "출처" in new_row:
            new_row["출처"] = "Base BOM"
        if "_group_parent_pno" in new_row:
            new_row["_group_parent_pno"] = ""
        return new_row

    # ── 6) 핵심: 그룹별로 Assy 체인을 자기 하위 바로 앞에 삽입 ──
    already_inserted = set()
    result_parts = []

    for gpno, group_df in change_df.groupby("_group_parent_pno", sort=False):
        gpno_norm = _norm_pno(str(gpno))

        if gpno_norm and gpno_norm in pno_to_row:
            # 이 그룹의 Assy 체인 역추적 (최상위까지)
            chain = []
            cur = gpno_norm
            visited = set()
            while cur and cur not in visited:
                visited.add(cur)
                if cur in pno_to_row:
                    chain.append(cur)
                cur = child_to_parent.get(cur)

            chain.reverse()   # 최상위(Single Oven) → 하위(Door Assy) 순

            # 이미 삽입됐거나 change_df에 이미 있는 건 제외
            chain = [c for c in chain
                     if c not in already_inserted and c not in existing_pnos]

            if chain:
                assy_rows = [_make_assy_row(c) for c in chain]
                assy_df = pd.DataFrame(assy_rows, columns=chg_cols)
                result_parts.append(assy_df)
                already_inserted.update(chain)

        result_parts.append(group_df)

    result = pd.concat(result_parts, ignore_index=True)

    # 태그 컬럼 제거
    result = result.drop(columns=["_group_parent_pno"], errors="ignore")

    return result