from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st


def _norm_col_name(x: Any) -> str:
    return re.sub(r"[^A-Z0-9가-힣]", "", str(x or "").upper())


def pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """
    Desc. / P/no. 같이 점이 있어도 잡히게 정규화 비교
    """
    norm_map = {_norm_col_name(c): c for c in df.columns}
    for k in candidates:
        nk = _norm_col_name(k)
        if nk in norm_map:
            return norm_map[nk]
    return None


def _norm_hdr(x) -> str:
    """헤더/셀 텍스트 정규화: 대문자 + 특수문자 제거 (P/no. -> PNO, Desc. -> DESC)"""
    s = "" if x is None else str(x)
    s = s.strip().upper()
    s = re.sub(r"[^A-Z0-9가-힣]+", "", s)
    return s

# 우리가 찾고 싶은 헤더 키(정규화된 형태)
_HDR_TARGETS = {"MODULE", "LVL", "DESC", "PNO"}  # base 템플릿 키

def detect_header_row(uploaded_file, sheet_name=0, scan_rows=40) -> int:
    uploaded_file.seek(0)
    probe = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=None, nrows=scan_rows, dtype=str)

    best_i, best_hit = 0, -1
    for i in range(len(probe)):
        row = probe.iloc[i].tolist()
        normed = {_norm_hdr(v) for v in row if v is not None and str(v).strip() not in ("", "nan", "NaN")}
        hit = len(normed.intersection(_HDR_TARGETS))
        if hit > best_hit:
            best_hit, best_i = hit, i
        if hit >= 3:  # 충분히 확실
            return i
    return best_i

def read_excel_auto_header(uploaded_file, sheet_name=0, scan_rows=40) -> tuple[pd.DataFrame, int]:
    h = detect_header_row(uploaded_file, sheet_name=sheet_name, scan_rows=scan_rows)
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=h, dtype=str)
    return df, h

# -------------------------------
# 1) 등급 입력 범용화 (B/b/B급/등급:B 등)
# -------------------------------
GRADE_PATTERNS = [
    re.compile(r"(?i)(?:개발\s*등급|등급)\s*[:/ ]*\s*([ABCD])\b"),
    re.compile(r"(?i)(?<![A-Z0-9])([ABCD])\s*급\b"),
    re.compile(r"(?i)\(([ABCD])\)"),
    re.compile(r"(?i)^\s*([ABCD])\s*$"),
]

def extract_grade(text: str) -> str:
    t = (text or "").strip()
    for pat in GRADE_PATTERNS:
        m = pat.search(t)
        if m:
            return m.group(1).upper()
    return ""


# -------------------------------
# 2) 모델코드 파서 (최종 요구사항)
# - 결과는 "반드시" 9자리 + W/L 시작(core9)
# - 소문자 OK
# - K/C 채널 prefix가 앞에 오면 제거하고 2번째부터 1번째로 간주
# - 10자리 이상(악세사리 1글자 삽입 등): 앞8 + 마지막1 => core9
#   예) WS9D7652WM -> WS9D7652M
# -------------------------------
MODEL_TOKEN_RE = re.compile(r"(?i)\b[KC]?[A-Z0-9]{9,15}\b")  # 토큰을 넉넉히 잡고 정규화에서 걸러냄

def normalize_model_candidate(raw: str) -> dict:
    s = (raw or "").strip()
    if not s:
        return {"raw": raw, "channel_prefix": "", "core9": ""}

    cleaned = re.sub(r"[^A-Z0-9]", "", s.upper())  # 혹시 섞인 구분자 제거
    channel = ""

    # 채널 prefix(K/C) 제거 (단, 다음 글자가 W/L일 때만 "채널"로 인정)
    if len(cleaned) >= 2 and cleaned[0] in ("K", "C") and cleaned[1] in ("W", "L"):
        channel = cleaned[0]
        cleaned = cleaned[1:]  # 이제 W/L로 시작하는 본체

    # 본체는 반드시 W/L 시작이어야 유효
    if not cleaned or cleaned[0] not in ("W", "L"):
        return {"raw": raw, "channel_prefix": channel, "core9": ""}

    # 9자리면 그대로
    if len(cleaned) == 9:
        return {"raw": raw, "channel_prefix": channel, "core9": cleaned}

    # 길면(10자리 이상): 앞8 + 마지막1 (중간 삽입/추가 구분 무시)
    if len(cleaned) > 9:
        core9 = cleaned[:8] + cleaned[-1]
        if len(core9) == 9 and core9[0] in ("W", "L"):
            return {"raw": raw, "channel_prefix": channel, "core9": core9}

    return {"raw": raw, "channel_prefix": channel, "core9": ""}

def extract_model_code(text: str) -> dict:
    t = text or ""
    for m in MODEL_TOKEN_RE.finditer(t):
        info = normalize_model_candidate(m.group(0))
        if info["core9"]:
            return info
    return {"raw": "", "channel_prefix": "", "core9": ""}


def model_parts(model_core9: str, channel_prefix: str = "") -> dict:
    m = (model_core9 or "").upper().strip()
    ch = (channel_prefix or "").upper().strip()
    if len(m) != 9:
        return {"raw": m, "channel_prefix": ch}

    return {
        "raw": m,
        "channel_prefix": ch,  # K/C or ""
        "p1_product": m[0],    # W/L
        "p2_type": m[1],
        "p3_series_or_fuel": m[2],
        "p4_platform": m[3],
        "p56_capacity": m[4:6],
        "p7_design": m[6],
        "p8_grade": m[7],
        "p9_color": m[8],
    }

def match_prefix_by_dev_grade(model_core9: str, dev_grade: str) -> str:
    """
    등급별 Primary 검색 prefix
    - D: 앞7
    - C: 앞6
    - B 이상/미정: 앞4
    """
    m = (model_core9 or "").upper().strip()
    g = (dev_grade or "").upper().strip()
    if len(m) != 9:
        return m
    if g == "D":
        return m[:7]
    if g == "C":
        return m[:6]
    return m[:4]

def is_reference_trigger(change_items: list[str]) -> bool:
    """Secondary(참고용) 풀 ON 트리거"""
    text = " ".join(change_items or []).lower()
    triggers = ["최초", "first", "최근", "트렌드", "camera", "카메라", "led", "하네스", "화각", "옵셋"]
    return any(k in text for k in triggers)

def select_policy(target_model_text: str, dev_grade: str, change_items: list[str]) -> dict:
    model_info = extract_model_code(target_model_text)
    core9 = model_info.get("core9", "")
    ch = model_info.get("channel_prefix", "")

    parsed = model_parts(core9, ch)
    prefix = match_prefix_by_dev_grade(core9, dev_grade)

    return {
        "dev_grade": (dev_grade or "").upper().strip(),
        "target_model_raw": target_model_text,
        "target_model_core9": core9,
        "channel_prefix": ch,
        "target_parsed": parsed,
        "primary_prefix": prefix,
        # ✅ W/L 고정 제거: core9 1번째 자리로 게이트
        "primary_product_gate": parsed.get("p1_product"),
        "secondary_enabled": is_reference_trigger(change_items),
    }

# =========================
# BASE MASTER 업로드 + Snapshot
# =========================

# ---- 컬럼 동의어(엑셀 헤더가 달라도 자동 매핑)
COL_SYNONYMS = {
    "part_name": ["부품명", "품명", "DESC", "DESC.", "Description", "DESCRIPTION", "Desc", "Desc."],
    "part_no":   ["품번", "부품번호", "P/NO", "P/NO.", "P/no.", "Pno", "P/N", "PARTNO", "Part No"],
    "module":    ["Module", "MODULE", "모듈"],
    "lvl":       ["Lvl", "LVL", "Level", "LEVEL", "레벨"],
    "cmdt":      ["CMDT", "공정", "분류"],
    "grade":     ["Grade", "GRADE", "등급", "Part Grade", "PARTGRADE"],
    "supplier":  ["Supplier", "Supplier Code", "SUPPLIERCODE", "협력사", "업체"],
    "qty":       ["Qty", "QTY", "수량", "개수", "수량(EA)"],
}


@st.cache_data(ttl=3600)
def _load_bom_cached(file_path: str) -> pd.DataFrame:
    try:
        with open(file_path, "rb") as _f:
            df = pd.read_excel(_f, header=0, dtype=str, engine="openpyxl")
        df = df.dropna(how="all").fillna("")
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        st.warning(f"⚠️ BOM 로드 실패: {e}")
        return pd.DataFrame()

def _file_digest(uploaded_file) -> str:
    try:
        b = uploaded_file.getvalue()
        return hashlib.md5(b).hexdigest()
    except Exception:
        return ""


@dataclass
class BaseMasterSnapshot:
    meta: dict
    rows: list[dict]
    part_index: dict
    path_index: dict
    bom_tree: dict
    quality: dict


def build_bom_tree(paths: list[list[str]]) -> dict:
    root: dict = {}
    for toks in paths:
        cur = root
        for t in toks:
            cur = cur.setdefault(t, {})
    return root


def build_bom_path_from_module_lvl(df2: pd.DataFrame) -> list[str]:
    """
    Module + Lvl 기반으로 BOM path 생성
    """
    module_col = None
    lvl_col = None

    for c in df2.columns:
        cc = _norm_col_name(c)
        if module_col is None and cc == "MODULE":
            module_col = c
        if lvl_col is None and cc in ["LVL", "LEVEL", "레벨"]:
            lvl_col = c

    if module_col is None or lvl_col is None:
        return [""] * len(df2)

    paths = []
    stack = []

    for _, row in df2.iterrows():
        mod = str(row.get(module_col, "") or "").strip()
        if not mod:
            paths.append("")
            continue

        try:
            lvl = int(float(str(row.get(lvl_col, "1") or "1").strip()))
        except Exception:
            lvl = 1

        if lvl <= 1:
            stack = [mod]
        else:
            stack = stack[:lvl - 1] + [mod]

        paths.append(" > ".join(stack))

    return paths


def infer_feature_from_text(text: str) -> str:
    t = (text or "").lower()
    if "camera" in t or "카메라" in t:
        return "CAMERA"
    if "led" in t or "조명" in t or "램프" in t:
        return "LED"
    if "하네스" in t or "harness" in t or "wire" in t or "배선" in t:
        return "HARNESS"
    return ""


def build_structured_docs_from_base(df_base: pd.DataFrame, model: str, dev_grade: str) -> list[dict]:
    """
    BOM DataFrame -> Chroma 검색용 문서 리스트
    BOM 컬럼: Lvl(.1, ..2), Part No, Description, Parent Part No(모), Type
    """
    # BOM 컬럼 찾기
    lvl_col  = pick_col(df_base, ["Lvl", "LVL", "Level", "LEVEL", "레벨"])
    desc_col = pick_col(df_base, ["Description", "DESC", "DESC.", "Desc", "부품명", "품명"])
    pno_col  = pick_col(df_base, ["Part No", "P/NO", "P/NO.", "PNO", "품번", "부품번호"])
    parent_col = pick_col(df_base, ["Parent Part No(모)", "Parent Part No", "모품번"])
    type_col = pick_col(df_base, ["Type", "TYPE", "유형"])

    if lvl_col is None or desc_col is None:
        return []

    docs = []
    # BOM path 스택: Lvl 깊이에 따라 경로 빌드
    path_stack = []  # [(lvl_depth, part_no_or_desc)]

    for i, row in df_base.iterrows():
        lvl_raw = str(row.get(lvl_col, "") or "").strip()
        desc = str(row.get(desc_col, "") or "").strip()
        pno  = str(row.get(pno_col, "") or "").strip() if pno_col else ""
        ptype = str(row.get(type_col, "") or "").strip() if type_col else ""

        if not desc or lvl_raw == "0":
            continue

        # Lvl 깊이 계산: ".1" = 1, "..2" = 2, "...3" = 3
        dot_count = len(lvl_raw) - len(lvl_raw.lstrip("."))
        lvl_depth = max(dot_count, 1)

        # 경로 스택 업데이트
        label = desc[:40]
        path_stack = path_stack[:lvl_depth - 1] + [(lvl_depth, label)]
        bom_path = " > ".join([p[1] for p in path_stack])
        level1 = path_stack[0][1] if path_stack else label

        feature = infer_feature_from_text(desc)

        rag_obj = {
            "doc_id": f"{model}_{dev_grade}_{i}",
            "source": {"model": model, "dev_grade": dev_grade},
            "change": {
                "summary_raw": f"{level1} / {desc}",
                "target_object": level1,
                "action": "BASE",
                "feature": feature,
            },
            "bom": {
                "level1": level1,
                "apply_bom_path": bom_path,
                "base_bom_path": bom_path,
            },
            "parts": {
                "main": [{"part_name": desc, "part_no": pno, "qty": 1, "desc": desc}],
                "sub": [],
            },
            "reason": [],
            "review_points": [],
        }

        embedding_text = (
            f"OBJECT: {level1}\n"
            f"FEATURE: {feature}\n"
            f"BOM_PATH: {bom_path}\n"
            f"PART_NAME: {desc}\n"
            f"PART_NO: {pno}\n"
            f"TYPE: {ptype}\n"
            f"MODEL: {model}\n"
            f"GRADE: {dev_grade}\n"
        )
        rag_obj["embedding_text"] = embedding_text

        docs.append({
            "id": rag_obj["doc_id"],
            "document": embedding_text,
            "metadata": {
                "model": model,
                "dev_grade": dev_grade,
                "object": level1,
                "feature": feature,
                "level1": level1,
                "schema": "structured_v3",
                "rag_json": json.dumps(rag_obj, ensure_ascii=False),
            }
        })

    return docs
