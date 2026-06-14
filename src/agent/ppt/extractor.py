"""L0 — 심의회 PPT 결정론 추출기.

"개발 유형 및 등급 확정 심의회" PPT(.pptx)에서 슬라이드/테이블을 역할 분류해
``project_meta``(base_model 포함) + ``detailed_changes``(변경내역·변경사유) +
``change_points_for_bom``(모듈/ base·new 품번)을 뽑는다. LLM 호출 0회 — python-pptx
파싱 + 키워드 화이트리스트만 사용(결정론). ``src/ui/app.py``의 검증된 추출 로직을
이식하되 UI 전용 표기(ui_type/ui_remark)는 제외했다.

에이전트 진입점: :func:`extract_change_review_from_pptx_bytes` + :func:`change_items_from_extraction`.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.intent.connectives import CAUSE_CONN, NOMINAL_CAUSE, PURPOSE_CONN

__all__ = [
    "ChangeItem",
    "change_items_from_extraction",
    "extract_change_review_from_pptx_bytes",
]


# --- 텍스트/헤더 정규화 -----------------------------------------------------


def _norm_header(h: str) -> str:
    return re.sub(r"[^A-Z0-9가-힣]", "", str(h or "").upper())


def _split_lines_text(v: str) -> list[str]:
    txt = str(v or "")
    parts = re.split(r"[\n\r•·]+", txt)
    out = []
    for p in parts:
        s = re.sub(r"\s+", " ", p).strip(" -\t")
        if s:
            out.append(s)
    return out


def _ppt_text_from_shape(shape: Any) -> str:
    """취소선(strike) run은 제외하고 도형의 텍스트를 추출."""
    if not getattr(shape, "has_text_frame", False):
        return ""
    chunks = []
    tf = shape.text_frame
    for p in tf.paragraphs:
        run_text = []
        for r in p.runs:
            try:
                if getattr(r.font, "strike", False):
                    continue
            except Exception:
                pass
            run_text.append(r.text or "")
        line = "".join(run_text).strip()
        if line:
            chunks.append(line)
    return "\n".join(chunks).strip()


def _extract_part_no_candidates(text: str) -> list[str]:
    cands = re.findall(r"\b[A-Z0-9]{8,14}\b", str(text or "").upper())
    out = []
    for c in cands:
        if c not in out:
            out.append(c)
    return out


def _strip_part_no_from_desc(text: str, pnos: list[str]) -> str:
    s = str(text or "")
    for p in pnos or []:
        s = re.sub(re.escape(p), "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip(" -:|\t")


def _auto_tags(part: str, detail: str, limit: int = 6) -> list[str]:
    txt = f"{part} {detail}".upper()
    found: list[str] = []
    seed = [
        "BLDC", "AC", "DC", "MOTOR", "FAN", "CAMERA", "HARNESS", "PCB", "UI",
        "BRACKET", "INVERTER", "HEATER", "SENSOR", "DISPLAY", "TOUCH", "THINQ",
        "도어", "카메라", "모터", "하네스", "제어", "앱", "팬", "브라켓", "센서",
    ]
    for s in seed:
        if s in txt and s not in found:
            found.append(s)
    toks = re.findall(r"[A-Z][A-Z0-9\-]{1,15}|[가-힣]{2,12}", txt)
    stop = {"PART", "CHANGE", "MODEL", "BASE", "NEW", "NO", "AND", "THE", "변경", "내역"}
    for t in toks:
        if t in stop or t.isdigit() or t in found:
            continue
        found.append(t)
        if len(found) >= limit:
            break
    return found[:limit]


# --- 제품군/프로젝트 메타 추출 ----------------------------------------------


def _split_module_changes(text: str) -> list[str]:
    """'주요 변경점' 셀의 번호 매김/줄바꿈 다중 항목을 개별 변경으로 분리.

    예: '1. 제품 치수 변경 H 137mm 감소 2. Cavity 조립 방식 변경'
        → ['제품 치수 변경 H 137mm 감소', 'Cavity 조립 방식 변경']
    번호 뒤 점/괄호(``1.`` ``2)``)만 분리자로 — '137mm','1,170' 같은 측정치는 안 쪼갬.
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return []
    parts = re.split(r"\d+\s*[.)]\s+", t)
    out = [re.sub(r"^[\s\-·•]+", "", p).strip() for p in parts if p and p.strip()]
    out = [p for p in out if len(p) >= 4]
    return out or ([t] if len(t) >= 4 else [])


def _is_no_change(text: str) -> bool:
    """'변경점 없음 / 동일 / 해당 없음' 등 실제 변경이 아닌 셀 판별."""
    s = re.sub(r"\s+", "", str(text or "")).strip(" -·.")
    if not s or len(s) < 4:
        return True
    if s in {"동일", "없음", "N/A", "NA", "X"}:
        return True
    return any(k in s for k in ["변경점없음", "변경없음", "변경사항없음", "해당없음"])


# 한국어 인과/목적 연결어 — '주요 변경점' 한 셀에 '사유 → 변경' 형태가 섞여 있을 때
# 변경사유(원인/목적)를 분리 추출한다. 변경사유 컬럼이 따로 없는 모듈요약표 대응.
# 상수는 src/agent/intent/connectives.py로 이동(공용) — 동작 불변 리팩터.
_CAUSE_CONN = CAUSE_CONN
_NOMINAL_CAUSE = NOMINAL_CAUSE
_PURPOSE_CONN = PURPOSE_CONN


def _split_cause(text: str) -> str:
    """'주요 변경점' 텍스트에서 변경사유(원인/목적)만 추출. 인과어 없으면 '' 반환.

    'A에 따른 B' / 'A(으)로 인한 B' / 'A변경으로 B' / 'A 위해 B' → 사유=A.
    변경내역(원문)은 호출부에서 그대로 두고, 여기서는 사유만 떼어낸다.
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) < 6:
        return ""
    for conn in _CAUSE_CONN:
        i = t.find(conn)
        if i > 1:
            return t[:i].strip(" ,·-")
    for conn in _NOMINAL_CAUSE:  # 명사+(으)로 인과: 'Door 무게 변경으로 …'
        i = t.find(conn)
        if i > 1:
            cause = t[: i + len(conn)]
            for tail in ("으로", "로"):
                if cause.endswith(tail):
                    cause = cause[: -len(tail)]
                    break
            return cause.strip(" ,·-")
    for conn in _PURPOSE_CONN:  # 'A (을/를) 위해 B' → 목적=A
        i = t.find(conn)
        if i > 1:
            return t[:i].strip(" ,·-").rstrip("을를 ")
    return ""


def _sanitize_product_type(v: Any) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip(" -:\t")
    if not s:
        return ""
    bad_markers = ["동일 사업부", "다르면", "으로 봄", "EX:", "예:", "정의", "설명", "기준", "참고", "해당"]
    su = s.upper()
    if any(b.upper() in su for b in bad_markers):
        return ""
    if any(x in s for x in [".", "!", "?", ";", "(ex", "예)"]):
        return ""
    if " " in s and len(s) > 24:
        return ""
    if len(s) > 40:
        return ""
    return s


def _infer_product_type_from_model(model_text: str) -> str:
    m = str(model_text or "").strip().upper()
    if m.startswith("W"):
        return "Built-in Oven"
    if m.startswith("M"):
        return "Microwave Oven"
    return "Oven/Microwave"


# 모델코드: 영문 2~5 + 숫자 3~5 + (옵션) 영숫자 1~2. 예: WSED7667M, WSED7664S.
_MODEL_CODE_RE = re.compile(r"\b([A-Z]{2,5}\d{3,5}[A-Z0-9]{0,2})\b")
_BASE_MARKER_RE = re.compile(r"BASE\s*(?:모델|MODEL)", re.IGNORECASE)


def _looks_like_model_code(v: str) -> bool:
    s = str(v or "").strip().upper()
    return bool(_MODEL_CODE_RE.fullmatch(s)) and not s.isdigit()


def _extract_base_model(lines: list[str], ctx_hint: str = "") -> str:
    """"Base 모델" 마커 뒤에서 가장 가까운 모델코드 토큰을 base_model로 채택.

    세로형 키/값 표(키 셀과 값 셀이 분리)에 대응. 마커 근처에 없으면 전체에서
    최빈 모델코드. ctx_hint(BOM 업로드 등 외부)가 모델코드면 그대로 사용.
    """
    if _looks_like_model_code(ctx_hint):
        return str(ctx_hint).strip().upper()
    joined = "\n".join(lines or [])
    markers = [m.end() for m in _BASE_MARKER_RE.finditer(joined)]
    if markers:
        tail = joined[markers[0]:]
        m = _MODEL_CODE_RE.search(tail)
        if m:
            return m.group(1).upper()
    # fallback: 전체에서 최빈 모델코드 (등급 변형이 여럿이면 가장 많이 등장한 것)
    codes = [c.upper() for c in _MODEL_CODE_RE.findall(joined)]
    if codes:
        from collections import Counter
        return str(Counter(codes).most_common(1)[0][0])
    return ""


def _extract_product_type_from_page1(text_p1: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in str(text_p1 or "").splitlines() if str(x or "").strip()]
    cands: list[str] = []

    p1 = [
        r"(?:^|\|)\s*제품군\s*(?:\||[:：])\s*([^|\n\r]+)",
        r"(?:^|\|)\s*PRODUCT(?:\s*TYPE)?\s*(?:\||[:：])\s*([^|\n\r]+)",
    ]
    for p in p1:
        for m in re.finditer(p, text_p1, flags=re.IGNORECASE):
            v = _sanitize_product_type(m.group(1))
            if v:
                cands.append(v)

    for ln in lines:
        if "제품군" in ln or "PRODUCT" in ln.upper():
            toks = [t.strip() for t in ln.split("|") if t.strip()]
            for i, t in enumerate(toks):
                tu = t.upper()
                if t == "제품군" or tu in ("PRODUCT", "PRODUCT TYPE"):
                    if i + 1 < len(toks):
                        v = _sanitize_product_type(toks[i + 1])
                        if v:
                            cands.append(v)

    for i, ln in enumerate(lines):
        lnu = ln.upper()
        is_key_only = _norm_header(ln) in {"제품군", "PRODUCT", "PRODUCTTYPE"}
        if is_key_only or ("제품군" in ln) or ("PRODUCT TYPE" in lnu):
            if i + 1 < len(lines):
                nxt = lines[i + 1]
                if _norm_header(nxt) not in {"PJT명", "PROJECTNAME", "BASE모델", "BASEMODEL", "개발등급", "확정등급", "개발유형"}:
                    v = _sanitize_product_type(nxt)
                    if v:
                        cands.append(v)

    def _score(v: str) -> int:
        s = str(v or "").strip()
        su = s.upper()
        score = 0
        if "BUILT-IN" in su:
            score += 50
        if any(k in su for k in ["OVEN", "MICROWAVE", "레인지", "전자레인지", "오븐"]):
            score += 10
        if len(s) <= 20:
            score += 4
        if " " not in s:
            score += 2
        if any(k in su for k in ["동일사업부", "다르면", "설명", "정의", "EX", "예"]):
            score -= 100
        return score

    cands = [c for c in cands if c]
    if not cands:
        return ""
    cands = sorted(cands, key=lambda x: (_score(x), -len(x)), reverse=True)
    return cands[0]


def _extract_product_type_from_anywhere(text: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in str(text or "").splitlines() if str(x or "").strip()]
    cands: list[str] = []

    for ln in lines:
        up = ln.upper()
        if ("제품군" not in ln) and ("PRODUCT" not in up):
            continue
        toks = [t.strip() for t in ln.split("|") if t.strip()]
        for i, t in enumerate(toks):
            tu = t.upper()
            if t == "제품군" or tu in ("PRODUCT", "PRODUCT TYPE"):
                if i + 1 < len(toks):
                    v = _sanitize_product_type(toks[i + 1])
                    if v:
                        cands.append(v)

    for i, ln in enumerate(lines):
        lnu = ln.upper()
        is_key_only = _norm_header(ln) in {"제품군", "PRODUCT", "PRODUCTTYPE"}
        if is_key_only or ("제품군" in ln) or ("PRODUCT TYPE" in lnu):
            if i + 1 < len(lines):
                v = _sanitize_product_type(lines[i + 1])
                if v:
                    cands.append(v)

    def _score(v: str) -> int:
        s = str(v or "").strip()
        su = str(v or "").upper()
        score = 0
        if any(k in su for k in ["OVEN", "MICROWAVE", "BUILT-IN", "BUILT IN", "RANGE", "COOKTOP", "REFRIGERATOR", "WASHER", "DRYER", "DISHWASHER"]):
            score += 10
        if len(s) <= 24:
            score += 3
        if any(k in su for k in ["동일사업부", "다르면", "설명", "정의", "EX", "예", "기준", "참고"]):
            score -= 100
        return score

    cands = [c for c in cands if c]
    if not cands:
        return ""
    cands.sort(key=lambda x: (_score(x), -len(x)), reverse=True)
    return cands[0]


def _classify_slide_role(slide_lines: list[str], table_headers: list[str]) -> dict[str, Any]:
    text = "\n".join(slide_lines or [])
    hdr = " | ".join(table_headers or [])
    blob = f"{text}\n{hdr}".upper()

    overview_hits = 0
    detail_hits = 0
    ref_hits = 0

    if "개발 PJT 개요".upper() in blob:
        overview_hits += 3
    if "심의 결과".upper() in blob or "확정".upper() in blob:
        overview_hits += 3
    for k in ["개발 유형", "개발 등급", "제품군", "BASE MODEL", "BASE 모델", "PJT명", "PROJECT NAME"]:
        if k.upper() in blob:
            overview_hits += 1

    for k in ["유첨", "개발 변경점 상세", "CAVITY", "DOOR", "CONTROLLER ASSEMBLY", "구분", "변경 내역", "BASE", "NEW"]:
        if k.upper() in blob:
            detail_hits += 1

    for k in ["개발 등급 분류 기준", "운영 기준", "부표", "기준", "절차", "정의", "상세", "참고"]:
        if k.upper() in blob:
            ref_hits += 1

    if "개발 PJT 개요".upper() in blob and "심의 결과".upper() in blob:
        return {"role": "OVERVIEW", "reason": "overview_high_conf"}

    if overview_hits >= max(4, detail_hits + 1) and overview_hits >= ref_hits:
        return {"role": "OVERVIEW", "reason": "overview_score"}

    if detail_hits >= max(2, ref_hits + 1):
        return {"role": "DETAIL", "reason": "detail_score"}

    if ref_hits >= 2:
        return {"role": "REFERENCE", "reason": "reference_score"}

    return {"role": "UNKNOWN", "reason": "weak_signal"}


def _extract_project_meta(lines: list[str], ctx: dict[str, Any], page1_lines: list[str] | None = None) -> dict[str, Any]:
    text = "\n".join(lines)
    text_p1 = "\n".join(page1_lines or [])
    text_for_ptype = f"{text_p1}\n{text}"
    row = {
        "product_type": "정보 없음",
        "project_name": "정보 없음",
        "base_model": str(ctx.get("source_model") or "정보 없음"),
        "target_country": "정보 없음",
        "rating": "정보 없음",
        "capacity": "정보 없음",
        "dev_grade": str(ctx.get("dev_grade") or "정보 없음"),
        "dev_type": "정보 없음",
    }
    kv_patterns = {
        "project_name": [r"PJT명\s*[:：]\s*(.+)", r"PROJECT\s*NAME\s*[:：]\s*(.+)"],
        "base_model": [r"BASE\s*모델\s*[:：]\s*([A-Z0-9]{8,14})", r"BASE\s*MODEL\s*[:：]\s*([A-Z0-9]{8,14})"],
        "target_country": [r"출시\s*국가\s*[:：]\s*(.+)", r"COUNTRY\s*[:：]\s*(.+)"],
        "rating": [r"정격\s*[:：]\s*(.+)", r"RATING\s*[:：]\s*(.+)"],
        "capacity": [r"용량\s*[:：]\s*(.+)", r"CAPACITY\s*[:：]\s*(.+)"],
        "dev_grade": [r"확정\s*등급\s*[:：]\s*([A-Z0-9\.\-]+)", r"등급\s*[:：]\s*([A-Z0-9\.\-]+)"],
        "dev_type": [r"개발\s*유형\s*[:：]\s*([A-Z0-9\.\-]+)", r"DEV\s*TYPE\s*[:：]\s*([A-Z0-9\.\-]+)"],
    }

    def _clean_meta_value(v: Any) -> str:
        s = re.sub(r"\s+", " ", str(v or "")).strip(" -:\t")
        if not s:
            return ""
        if s.upper() in {"TBD", "N/A", "NA", "-", "X", "O"}:
            return ""
        if any(k in s for k in ["정보 없음", "내용 없음"]):
            return ""
        return s

    def _extract_from_pipe_lines(src_lines: list[str], key_aliases: list[str]) -> str:
        alias_norm = [_norm_header(a) for a in key_aliases]
        for ln in src_lines or []:
            toks = [re.sub(r"\s+", " ", t).strip() for t in str(ln or "").split("|") if str(t or "").strip()]
            if len(toks) < 2:
                continue
            for i, t in enumerate(toks):
                tn = _norm_header(t)
                if any(a and (a == tn or a in tn) for a in alias_norm):
                    if i + 1 < len(toks):
                        cand = _clean_meta_value(toks[i + 1])
                        if cand:
                            return cand
        return ""

    for k, pats in kv_patterns.items():
        for p in pats:
            m = re.search(p, text, flags=re.IGNORECASE)
            if m and str(m.group(1)).strip():
                row[k] = str(m.group(1)).strip()
                break

    p1_lines = [x for x in str(text_p1 or "").splitlines() if str(x or "").strip()]
    all_src_lines = [x for x in str(text or "").splitlines() if str(x or "").strip()]
    table_keys = {
        "project_name": ["PJT명", "PROJECT NAME", "프로젝트명"],
        "base_model": ["BASE 모델", "BASE MODEL", "BASE"],
        "target_country": ["출시 국가", "COUNTRY", "적용 국가"],
        "rating": ["정격", "RATING"],
        "capacity": ["용량", "CAPACITY"],
        "dev_grade": ["확정 등급", "개발 등급", "등급"],
        "dev_type": ["개발 유형", "DEV TYPE", "유형"],
    }
    for k, aliases in table_keys.items():
        if row.get(k) not in ("", "정보 없음"):
            continue
        cand = _extract_from_pipe_lines(p1_lines, aliases) or _extract_from_pipe_lines(all_src_lines, aliases)
        if cand:
            row[k] = cand

    # base_model: 느슨한 별칭 매칭이 등급 문구를 오추출하므로, 모델코드 토큰을 우선한다.
    if not _looks_like_model_code(row.get("base_model", "")):
        bm = _extract_base_model(all_src_lines or lines, ctx_hint=str(ctx.get("source_model") or ""))
        row["base_model"] = bm or "정보 없음"

    p1_ptype = _extract_product_type_from_page1(text_p1)
    if p1_ptype:
        row["product_type"] = p1_ptype

    if row["product_type"] in ("", "정보 없음"):
        any_ptype = _extract_product_type_from_anywhere(text_for_ptype)
        if any_ptype:
            row["product_type"] = any_ptype

    if row["product_type"] == "정보 없음":
        model_hint = str(
            ctx.get("target_model")
            or ctx.get("source_model")
            or row.get("base_model")
            or ""
        )
        row["product_type"] = str(_sanitize_product_type(ctx.get("product_type")) or _infer_product_type_from_model(model_hint))
    return row


# --- 메인 추출기 ------------------------------------------------------------


def extract_change_review_from_pptx_bytes(pptx_bytes: bytes, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """심의회 PPT bytes → 구조화 dict (project_meta / detailed_changes / change_points_for_bom 등)."""
    ctx = ctx or {}
    try:
        from pptx import Presentation
    except Exception as e:  # pragma: no cover - 패키지 미설치 환경
        raise RuntimeError("python-pptx 패키지가 필요합니다. 설치 후 다시 시도하세요.") from e

    prs = Presentation(io.BytesIO(pptx_bytes))

    discarded: list[dict[str, Any]] = []
    all_lines: list[str] = []
    all_table_text_lines: list[str] = []
    slide_lines_map: dict[int, list[str]] = {}
    slide_table_headers_map: dict[int, list[str]] = {}
    slide_table_text_map: dict[int, list[str]] = {}
    table_rows: list[dict[str, Any]] = []
    image_presence: list[dict[str, Any]] = []

    def _flat_cells(rows: list[list[str]]) -> list[str]:
        out = []
        for r in rows or []:
            for c in r or []:
                t = re.sub(r"\s+", " ", str(c or "")).strip()
                if t:
                    out.append(t)
        return out

    def _classify_table_candidate(rows: list[list[str]], slide_lines: list[str], slide: Any, table_id: Any) -> dict[str, Any]:
        if not rows:
            return {"type": "IGNORE", "why": "empty_table"}

        raw_header = [re.sub(r"\s+", " ", str(x or "")).strip() for x in (rows[0] or [])]
        header_norm = [_norm_header(x) for x in raw_header]
        cells = _flat_cells(rows)
        all_text = " ".join(raw_header + cells + (slide_lines or []))

        blk = ["적용여부", "과전압", "PDR", "ODR", "EVENT", "NPI", "ACTIVITY"]
        all_up = all_text.upper()
        if any(k.upper() in all_up for k in blk):
            return {"type": "IGNORE", "why": "blacklist_keyword"}

        chk_tokens = {"O", "X", "OX", "(O,X)", "(O,X)", "(O, X)"}
        non_empty = [c for c in cells if str(c).strip()]
        chk_cnt = 0
        for c in non_empty:
            n = re.sub(r"\s+", "", str(c).upper())
            if n in chk_tokens:
                chk_cnt += 1
        if non_empty and (chk_cnt / max(len(non_empty), 1)) >= 0.20 and len(non_empty) >= 4:
            return {"type": "IGNORE", "why": "checkbox_ratio_high"}

        hraw_join = " | ".join(raw_header).upper()
        hnorm_join = "|".join(header_norm)
        hraw_compact = re.sub(r"[^A-Z0-9가-힣]", "", hraw_join.upper())

        def _has_any(src: str, kws: list[str]) -> bool:
            s = src.upper()
            return any(k.upper() in s for k in kws)

        def _has_any_compact(src_compact: str, kws: list[str]) -> bool:
            return any(re.sub(r"[^A-Z0-9가-힣]", "", k.upper()) in src_compact for k in kws)

        has_change = _has_any(hraw_join, ["변경내역", "변경 내역", "CHANGE"]) or _has_any_compact(hraw_compact, ["변경내역"])
        has_before_after = (
            _has_any(hraw_join, ["변경 전", "변경 후", "->", "→", "BEFORE", "AFTER"])
            or _has_any_compact(hraw_compact, ["변경전", "변경후"])
            or ("변경전" in hnorm_join or "변경후" in hnorm_join)
        )
        has_reason = _has_any(hraw_join, ["변경사유", "변경 사유", "REASON"]) or _has_any_compact(hraw_compact, ["변경사유"])
        has_concern = _has_any(hraw_join, ["걱정점", "걱정 점", "우려", "CONCERN"]) or _has_any_compact(hraw_compact, ["걱정점", "우려"])
        has_part = _has_any(hraw_join, ["PART", "부품"]) or _has_any_compact(hraw_compact, ["PART", "부품"])
        if has_change and has_before_after and has_reason and has_concern and has_part:
            return {"type": "MAIN_CHANGE_TABLE", "why": "whitelist_main_change"}

        has_base = _has_any(hraw_join, ["BASE"])
        has_new = _has_any(hraw_join, ["NEW"])
        has_mod_or_remark = _has_any(hraw_join, ["MODULE", "모듈", "REMARK", "비고"])
        if has_base and has_new and has_mod_or_remark:
            return {"type": "DETAIL_CHANGE_TABLE", "why": "whitelist_detail_change"}

        has_risk = _has_any(hraw_join, ["우려", "걱정", "CONCERN"]) or _has_any_compact(hraw_compact, ["우려", "걱정"])
        has_counter = _has_any(hraw_join, ["대책", "대응", "검증", "방안", "COUNTER"]) or _has_any_compact(hraw_compact, ["대책", "대응", "검증방안"])
        if has_risk and has_counter:
            return {"type": "RISK_TABLE", "why": "whitelist_risk"}

        # 'Module명 / 주요 변경점' 요약표 (유첨 상세표가 아닌 모듈 단위 변경 요약).
        # Base/New 동시 보유 시 DETAIL_CHANGE_TABLE이 우선이므로 제외.
        has_module = _has_any(hraw_join, ["MODULE", "모듈"]) or _has_any_compact(hraw_compact, ["MODULE", "모듈명"])
        has_major_change = (
            _has_any(hraw_join, ["주요 변경점", "주요변경점", "변경점"])
            or _has_any_compact(hraw_compact, ["주요변경점", "변경점"])
        )
        if has_module and has_major_change and not (has_base and has_new):
            return {"type": "MODULE_SUMMARY_TABLE", "why": "whitelist_module_summary"}

        # 개발부품 Master / BOM 변경표 (Cb급 'Master로 작성' — No./Lev./Base P/No/New P/No/
        # Part Name/.../상세 변경점). MODULE/REMARK 없이 부품·품번·변경점으로 구성되어 위
        # DETAIL_CHANGE_TABLE(MODULE 필요)에 안 걸린다 → 별도 인식. Base/New P/No + Part + 변경점.
        has_pno = _has_any(hraw_join, ["P/NO", "P/N", "PNO", "품번"]) or _has_any_compact(hraw_compact, ["품번", "PNO"])
        has_changepoint = _has_any(hraw_join, ["변경점", "CHANGE POINT"]) or _has_any_compact(hraw_compact, ["변경점"])
        if has_base and has_new and has_pno and has_part and has_changepoint:
            return {"type": "MASTER_BOM_CHANGE_TABLE", "why": "whitelist_master_bom"}

        return {"type": "IGNORE", "why": "not_whitelisted"}

    for s_idx, slide in enumerate(prs.slides, 1):
        slide_lines: list[str] = []
        slide_table_headers: list[str] = []
        slide_table_texts: list[str] = []
        has_non_text_attach = False
        table_seq = 0
        for sh in slide.shapes:
            if getattr(sh, "has_text_frame", False):
                txt = _ppt_text_from_shape(sh)
                if txt:
                    ls = _split_lines_text(txt)
                    slide_lines.extend(ls)
                    all_lines.extend(ls)
            if getattr(sh, "shape_type", None) in (13, 14):
                has_non_text_attach = True
            if getattr(sh, "has_table", False):
                table_seq += 1
                t = sh.table
                rows = []
                for r in t.rows:
                    row = []
                    for c in r.cells:
                        row.append(re.sub(r"\s+", " ", c.text or "").strip())
                    rows.append(row)
                if rows:
                    for rr in rows:
                        rrj = " | ".join([re.sub(r"\s+", " ", str(x or "")).strip() for x in rr if str(x or "").strip()])
                        if rrj:
                            slide_table_texts.append(rrj)
                            all_table_text_lines.append(rrj)
                    hdr_join = " | ".join([re.sub(r"\s+", " ", str(x or "")).strip() for x in (rows[0] or []) if str(x or "").strip()])
                    if hdr_join:
                        slide_table_headers.append(hdr_join)
                    table_rows.append({
                        "slide": s_idx,
                        "table_id": f"S{s_idx}-T{table_seq}",
                        "rows": rows,
                        "slide_lines": list(slide_lines),
                    })
        slide_lines_map[s_idx] = list(slide_lines)
        slide_table_headers_map[s_idx] = list(slide_table_headers)
        slide_table_text_map[s_idx] = list(slide_table_texts)
        image_presence.append({"slide": s_idx, "has_attachment": bool(has_non_text_attach)})

    slide_roles: dict[int, str] = {}
    slide_role_details: list[dict[str, Any]] = []
    for s_idx in sorted(slide_lines_map.keys()):
        cls = _classify_slide_role(slide_lines_map.get(s_idx) or [], slide_table_headers_map.get(s_idx) or [])
        role = str(cls.get("role") or "UNKNOWN")
        slide_roles[s_idx] = role
        slide_role_details.append({"slide": s_idx, "role": role, "reason": cls.get("reason", "")})

    overview_scope_lines: list[str] = []
    for s_idx, role in slide_roles.items():
        if role == "OVERVIEW":
            overview_scope_lines.extend(slide_lines_map.get(s_idx) or [])
            overview_scope_lines.extend(slide_table_text_map.get(s_idx) or [])
            overview_scope_lines.extend(slide_table_headers_map.get(s_idx) or [])

    if not overview_scope_lines and slide_lines_map:
        first_idx = sorted(slide_lines_map.keys())[0]
        overview_scope_lines.extend(slide_lines_map.get(first_idx) or [])
        overview_scope_lines.extend(slide_table_text_map.get(first_idx) or [])
        overview_scope_lines.extend(slide_table_headers_map.get(first_idx) or [])

    page1_scope_lines: list[str] = []
    if slide_lines_map:
        first_idx = sorted(slide_lines_map.keys())[0]
        page1_scope_lines.extend(slide_lines_map.get(first_idx) or [])
        page1_scope_lines.extend(slide_table_text_map.get(first_idx) or [])
        page1_scope_lines.extend(slide_table_headers_map.get(first_idx) or [])

    meta_scope_lines: list[str] = []
    meta_scope_lines.extend(overview_scope_lines)
    meta_scope_lines.extend(all_table_text_lines)
    meta_scope_lines.extend(all_lines)

    project_meta = _extract_project_meta(meta_scope_lines, ctx, page1_lines=page1_scope_lines or overview_scope_lines)

    module_summary: list[dict[str, Any]] = []
    detailed_changes: list[dict[str, Any]] = []
    module_details: list[dict[str, Any]] = []
    risk_review: list[dict[str, Any]] = []
    module_summary_hints: list[dict[str, Any]] = []
    seen_master_bom: set[tuple[str, str, str, str]] = set()  # 개발부품 Master 행 중복 제거(표 간 포함)

    for tb in table_rows:
        rows = tb["rows"]
        slide_no = int(tb.get("slide") or 0)
        slide_role = slide_roles.get(slide_no, "UNKNOWN")

        cls = _classify_table_candidate(rows, tb.get("slide_lines") or [], tb.get("slide"), tb.get("table_id"))
        ttype = cls.get("type", "IGNORE")

        # MODULE_SUMMARY: 'Module명 / 주요 변경점' 요약표 — 슬라이드 역할 무관하게 변경항목화.
        # (유첨 상세표가 없는 심의회 PPT 대응: 모듈 단위 주요 변경점을 detailed_changes로.)
        if ttype == "MODULE_SUMMARY_TABLE":
            hdr0 = [_norm_header(x) for x in rows[0]]
            m_i = next((i for i, h in enumerate(hdr0) if ("MODULE" in h or "모듈" in h)), 0)
            c_i = next(
                (i for i, h in enumerate(hdr0) if ("주요변경점" in h or "변경점" in h or "변경내역" in h)),
                min(1, len(rows[0]) - 1),
            )
            cur_mod = ""
            for rr in rows[1:]:
                mod = (rr[m_i] if m_i < len(rr) else "").strip() or cur_mod
                cur_mod = mod or cur_mod
                raw_chg = (rr[c_i] if c_i < len(rr) else "").strip()
                if not mod or not raw_chg:
                    continue
                module_summary_hints.append({
                    "module": mod, "text": raw_chg,
                    "src": {"slide": tb.get("slide"), "table_id": tb.get("table_id")},
                })
                for one in _split_module_changes(raw_chg):
                    if _is_no_change(one):
                        continue
                    detailed_changes.append({
                        "category": mod or "정보 없음",
                        "discipline": mod or "정보 없음",
                        "no": "",
                        "part": "정보 없음",
                        "change_detail": one,
                        # 모듈요약표엔 변경사유 컬럼이 없음 — 변경점 텍스트의 인과절에서 사유 추출.
                        "change_reason": _split_cause(one) or "정보 없음",
                        "concern": "내용 없음",
                        "tags": _auto_tags(mod, one),
                        "_src": {"slide": tb.get("slide"), "table_id": tb.get("table_id"), "type": "module_summary"},
                    })
            continue

        if slide_role == "REFERENCE":
            flat = _flat_cells(rows)
            discarded.append({
                "why": "reference_slide_scope",
                "slide": tb.get("slide"),
                "table_id": tb.get("table_id"),
                "sample_text": " | ".join(flat[:6]) if flat else "",
            })
            continue

        if ttype == "IGNORE":
            if slide_role == "DETAIL" and rows and rows[0]:
                hdr0 = [_norm_header(x) for x in rows[0]]
                has_mod_col = any(("MODULE" in h) or ("모듈" in h) for h in hdr0)
                has_change_col = any(("주요변경점" in h) or ("변경내역" in h) or ("CHANGE" in h) for h in hdr0)
                if has_mod_col and has_change_col:
                    m_i = next((i for i, h in enumerate(hdr0) if ("MODULE" in h or "모듈" in h)), 0)
                    c_i = next((i for i, h in enumerate(hdr0) if ("주요변경점" in h or "변경내역" in h or "CHANGE" in h)), min(1, len(hdr0) - 1))
                    cur_mod = ""
                    for rr in rows[1:]:
                        mod = (rr[m_i] if m_i < len(rr) else "").strip() or cur_mod
                        cur_mod = mod or cur_mod
                        chg = (rr[c_i] if c_i < len(rr) else "").strip()
                        if mod and chg:
                            module_summary_hints.append({
                                "module": mod,
                                "text": chg,
                                "src": {"slide": tb.get("slide"), "table_id": tb.get("table_id")},
                            })

            flat = _flat_cells(rows)
            discarded.append({
                "why": cls.get("why", "ignored"),
                "slide": tb.get("slide"),
                "table_id": tb.get("table_id"),
                "sample_text": " | ".join(flat[:6]) if flat else "",
            })
            continue

        hdr = [_norm_header(x) for x in rows[0]]

        if slide_role != "DETAIL" and ttype in ("MAIN_CHANGE_TABLE", "DETAIL_CHANGE_TABLE", "RISK_TABLE"):
            flat = _flat_cells(rows)
            discarded.append({
                "why": "non_detail_slide_for_change_table",
                "slide": tb.get("slide"),
                "table_id": tb.get("table_id"),
                "sample_text": " | ".join(flat[:6]) if flat else "",
            })
            continue

        if ttype == "MAIN_CHANGE_TABLE":
            c_i = next((i for i, h in enumerate(hdr) if ("구분" in h or "CATEGORY" in h)), 0)
            n_i = next((i for i, h in enumerate(hdr) if "NO" in h), 1)
            p_i = next((i for i, h in enumerate(hdr) if ("PART" in h or "부품" in h)), 2)
            d_i = next((i for i, h in enumerate(hdr) if ("변경내역" in h or "CHANGE" in h)), 3)
            r_i = next((i for i, h in enumerate(hdr) if ("변경사유" in h or "REASON" in h)), 4)
            g_i = next((i for i, h in enumerate(hdr) if ("걱정" in h or "CONCERN" in h)), 5)
            cur_cat = ""
            for r in rows[1:]:
                cat = (r[c_i] if c_i < len(r) else "").strip() or cur_cat
                cur_cat = cat or cur_cat
                no = (r[n_i] if n_i < len(r) else "").strip()
                part = (r[p_i] if p_i < len(r) else "").strip()
                detail = (r[d_i] if d_i < len(r) else "").strip()
                reason = (r[r_i] if r_i < len(r) else "").strip()
                concern = (r[g_i] if g_i < len(r) else "").strip()
                if not part and not detail:
                    discarded.append({
                        "why": "empty_part_and_detail_row",
                        "slide": tb.get("slide"),
                        "table_id": tb.get("table_id"),
                        "sample_text": " | ".join([str(x) for x in r[:6]]),
                    })
                    continue
                n_val: Any = no
                try:
                    n_val = int(re.sub(r"[^0-9]", "", no)) if re.search(r"[0-9]", no) else no
                except Exception:
                    n_val = no
                detailed_changes.append({
                    "category": cat or "정보 없음",
                    "discipline": cat or "정보 없음",
                    "no": n_val,
                    "part": part or "정보 없음",
                    "change_detail": detail or "정보 없음",
                    "change_reason": reason or "정보 없음",
                    "concern": concern or "내용 없음",
                    "tags": _auto_tags(part, detail),
                    "_src": {"slide": tb.get("slide"), "table_id": tb.get("table_id")},
                })
            continue

        if ttype == "DETAIL_CHANGE_TABLE":
            m_i = next((i for i, h in enumerate(hdr) if ("MODULE" in h or "모듈" in h)), 0)
            b_i = next((i for i, h in enumerate(hdr) if "BASE" in h), 1)
            n_i = next((i for i, h in enumerate(hdr) if "NEW" in h), 2)
            rm_i = next((i for i, h in enumerate(hdr) if ("REMARK" in h or "비고" in h)), min(3, len(hdr) - 1))
            for r in rows[1:]:
                mod = (r[m_i] if m_i < len(r) else "").strip()
                base_txt = (r[b_i] if b_i < len(r) else "").strip()
                new_txt = (r[n_i] if n_i < len(r) else "").strip()
                remark = (r[rm_i] if rm_i < len(r) else "").strip()
                if not mod and not base_txt and not new_txt:
                    continue

                mod_up = mod.upper()
                chk_desc = re.sub(r"\s+", "", f"{new_txt or base_txt}").upper()
                if "과전압" in mod_up or chk_desc in {"O", "X", "(O,X)", "OX"}:
                    discarded.append({
                        "why": "detail_row_blacklist_or_checkbox",
                        "slide": tb.get("slide"),
                        "table_id": tb.get("table_id"),
                        "sample_text": f"{mod} | {base_txt} | {new_txt}",
                    })
                    continue

                base_pnos = _extract_part_no_candidates(base_txt)
                new_pnos = _extract_part_no_candidates(new_txt)
                mix_txt = f"{new_txt} {remark}".lower()
                is_shared = any(k in mix_txt for k in ["공용", "공용화", "수평전개", "동일 적용"])
                sw = ""
                if is_shared:
                    m_sw = re.search(r"(\d{2}\s*인치\s*[A-Z0-9가-힣 ]+|[A-Z0-9가-힣 ]+\s*공용)", f"{new_txt} {remark}", flags=re.IGNORECASE)
                    if m_sw:
                        sw = re.sub(r"\s+", " ", m_sw.group(1)).strip()
                module_details.append({
                    "module": mod or "정보 없음",
                    "sub_module": mod or "정보 없음",
                    "base": {
                        "description": _strip_part_no_from_desc(base_txt, base_pnos) or (base_txt or "정보 없음"),
                        "part_no": base_pnos[0] if base_pnos else "TBD",
                    },
                    "new": {
                        "description": _strip_part_no_from_desc(new_txt, new_pnos) or (new_txt or "정보 없음"),
                        "part_no": new_pnos[0] if new_pnos else "TBD",
                        "is_shared": bool(is_shared),
                        "shared_with": sw,
                    },
                    "remark": remark or "정보 없음",
                    "_src": {"slide": tb.get("slide"), "table_id": tb.get("table_id")},
                })
            continue

        if ttype == "MASTER_BOM_CHANGE_TABLE":
            lev_i = next((i for i, h in enumerate(hdr) if ("LEV" in h or "레벨" in h)), None)
            b_i = next((i for i, h in enumerate(hdr) if "BASE" in h), None)
            n_i = next((i for i, h in enumerate(hdr) if "NEW" in h), None)
            pn_i = next((i for i, h in enumerate(hdr) if ("PART NAME" in h or "부품명" in h or "PART" in h)), None)
            cp_i = next((i for i, h in enumerate(hdr) if ("변경점" in h or "CHANGE" in h)), None)

            def _cv(r: list[str], idx: int | None) -> str:
                return r[idx].strip() if (idx is not None and idx < len(r)) else ""

            _UNCHANGED = {"←", "<-", "-", "→", "", "동일", "SAME"}
            for r in rows[1:]:
                base_txt = _cv(r, b_i)
                new_txt = _cv(r, n_i)
                pname = _cv(r, pn_i)
                cp = _cv(r, cp_i)
                lev = _cv(r, lev_i)
                base_pnos = _extract_part_no_candidates(base_txt)
                new_pnos = _extract_part_no_candidates(new_txt)
                base_pno = base_pnos[0] if base_pnos else (base_txt if base_txt and base_txt not in ("-",) else "")
                new_raw = new_txt.strip()
                new_pno = new_pnos[0] if new_pnos else (new_raw if new_raw and new_raw.upper() not in ("-",) else "")
                cp_clean = "" if cp in ("정보 없음", "정보없음") else cp

                # 변경 판정: ① 실제 상세 변경점 있음, 또는 ② New P/No가 변경을 의미
                # (새 번호로 교체 / TBD=발번대기 신규). New='←'(동일)·빈칸 + 변경점 없음 = 미변경 이월 → 제외.
                new_unchanged = new_raw.upper() in {u.upper() for u in _UNCHANGED}
                has_detail = bool(cp_clean)
                is_new_pno = (not new_unchanged) and (
                    new_pno.upper() == "TBD" or (bool(new_pno) and new_pno.upper() != base_pno.upper())
                )
                has_identity = bool(base_pno or (new_pno and not new_unchanged) or pname)
                if not ((has_detail or is_new_pno) and has_identity):
                    discarded.append({
                        "why": "master_bom_unchanged_or_empty",
                        "slide": tb.get("slide"), "table_id": tb.get("table_id"),
                        "sample_text": " | ".join([str(x) for x in r[:6]]),
                    })
                    continue

                # New P/No 정규화: 동일마커(←)인데 스펙만 변경된 행은 같은 번호(=base) 유지.
                if new_unchanged:
                    new_out = base_pno or "TBD"
                else:
                    new_out = new_pno or "TBD"

                # 중복 제거(표 간 포함) — (base, new, 부품명정규화, 변경점).
                dkey = (base_pno.upper(), new_out.upper(),
                        re.sub(r"\s+", "", pname.upper()), re.sub(r"\s+", "", cp_clean.upper()))
                if dkey in seen_master_bom:
                    discarded.append({
                        "why": "master_bom_duplicate",
                        "slide": tb.get("slide"), "table_id": tb.get("table_id"),
                        "sample_text": " | ".join([str(x) for x in r[:6]]),
                    })
                    continue
                seen_master_bom.add(dkey)

                detailed_changes.append({
                    "category": "개발부품 Master",
                    "discipline": "개발부품 Master",
                    "no": "",
                    "part": pname or "정보 없음",
                    "change_detail": cp_clean or "정보 없음",
                    "change_reason": cp_clean or "정보 없음",  # 별도 사유 컬럼 없음 → 변경점으로 대용(검색 신호).
                    "concern": "내용 없음",
                    "tags": _auto_tags(pname, cp_clean),
                    "base_part_no": base_pno or "TBD",
                    "new_part_no": new_out,
                    "bom_level": lev,
                    "_src": {"slide": tb.get("slide"), "table_id": tb.get("table_id"), "type": "master_bom"},
                })
            continue

        if ttype == "RISK_TABLE":
            c_i = next((i for i, h in enumerate(hdr) if ("구분" in h or "CATEGORY" in h)), 0)
            o_i = next((i for i, h in enumerate(hdr) if ("우려" in h or "CONCERN" in h)), 1)
            m_i = next((i for i, h in enumerate(hdr) if ("대책" in h or "COUNTER" in h)), 2)
            a_i = next((i for i, h in enumerate(hdr) if ("첨부" in h or "ATTACH" in h)), min(3, len(hdr) - 1))
            for r in rows[1:]:
                cat = (r[c_i] if c_i < len(r) else "").strip() or "정보 없음"
                concern = (r[o_i] if o_i < len(r) else "").strip() or "내용 없음"
                cm = (r[m_i] if m_i < len(r) else "").strip() or "내용 없음"
                at = (r[a_i] if a_i < len(r) else "").strip() or "내용 없음"
                if cat == "정보 없음" and concern == "내용 없음" and cm == "내용 없음":
                    continue
                risk_review.append({
                    "category": cat,
                    "concern": concern,
                    "countermeasure": cm,
                    "attachment": at,
                })

    # 모듈요약(주요 변경점)은 '유첨 상세표가 없는 PPT 대응' fallback이다(위 MODULE_SUMMARY 주석).
    # part·변경사유 컬럼을 갖춘 상세 변경표(MAIN_CHANGE_TABLE) 행이 하나라도 있으면, part/사유가
    # 비는 저품질 모듈요약 항목은 detailed_changes에서 제거한다 — 상세표가 진실의 출처.
    _detail_dc = [d for d in detailed_changes if (d.get("_src") or {}).get("type") != "module_summary"]
    if _detail_dc:
        detailed_changes = _detail_dc

    mod_map: dict[str, list[str]] = {}
    for dc in detailed_changes:
        mod = str(dc.get("category") or "정보 없음").strip()
        msg = str(dc.get("change_detail") or "").strip()
        if not msg:
            continue
        mod_map.setdefault(mod, [])
        if msg not in mod_map[mod]:
            mod_map[mod].append(msg)
    module_summary = [
        {"no": str(i + 1), "module": k, "changes": v}
        for i, (k, v) in enumerate(mod_map.items())
    ]

    def _norm_txt(s: Any) -> str:
        return re.sub(r"\s+", " ", str(s or "").strip().upper())

    def _rule_based_module_from_change(dc: dict[str, Any]) -> str:
        scope = _norm_txt(f"{dc.get('part') or ''} {dc.get('change_detail') or ''} {dc.get('change_reason') or ''}")
        if not scope:
            return ""
        rules = {
            "Door Assembly": ["DOOR", "CAMERA", "HARNESS", "HINGE", "TRAY", "FRAME"],
            "Cavity": ["CAVITY", "CONV", "FAN", "MOTOR", "BLADE", "INLET", "OUTLET", "AIR"],
            "Controller Assembly": ["CONTROLLER", "LCD", "PANEL", "PCB", "UI", "OS", "S/W", "SW"],
        }
        best_mod = ""
        best_score = 0
        for mod, kws in rules.items():
            score = 0
            for kw in kws:
                if kw in scope:
                    score += 2
            if mod == "Door Assembly" and any(k in scope for k in ["HARNESS", "TRAY", "HINGE", "CAMERA"]):
                score += 2
            if mod == "Cavity" and any(k in scope for k in ["CONV", "FAN", "MOTOR", "BLADE"]):
                score += 2
            if mod == "Controller Assembly" and any(k in scope for k in ["LCD", "PCB", "PANEL", "UI", "OS"]):
                score += 2
            if score > best_score:
                best_mod = mod
                best_score = score
        return best_mod if best_score >= 3 else ""

    def _pick_module_from_summary(dc: dict[str, Any]) -> str:
        part = _norm_txt(dc.get("part"))
        detail = _norm_txt(dc.get("change_detail"))
        stop_tokens = {"ASSY", "ASSEMBLY", "PART", "MODULE", "변경", "신규", "추가", "기구", "제어"}
        weak_tokens = {"COVER", "구조", "적용", "변경", "추가", "형상", "HOLE", "SIZE"}
        part_toks = [x for x in re.split(r"[^A-Z0-9가-힣]+", part) if len(x) >= 3 and x not in stop_tokens and x not in weak_tokens]
        detail_toks = [x for x in re.split(r"[^A-Z0-9가-힣]+", detail) if len(x) >= 4 and x not in stop_tokens and x not in weak_tokens]
        toks = part_toks + detail_toks
        best_mod = ""
        best_score = 0
        for it in module_summary_hints:
            mod = str(it.get("module") or "").strip()
            txt = _norm_txt(it.get("text"))
            if not mod or not txt:
                continue
            score = 0
            for tk in toks:
                if tk in txt:
                    score += 2
            if score > best_score:
                best_mod = mod
                best_score = score
        return best_mod if best_score >= 2 and len(toks) > 0 else ""

    def _pick_best_detail(dc: dict[str, Any], preferred_module: str = "") -> dict[str, Any] | None:
        part = _norm_txt(dc.get("part"))
        detail = _norm_txt(dc.get("change_detail"))
        scope = f"{part} {detail}"
        best = None
        best_score = -1
        stop_tokens = {"ASSY", "ASSEMBLY", "PART", "MODULE", "변경", "신규", "추가", "기구", "제어"}
        part_toks = [x for x in re.split(r"[^A-Z0-9가-힣]+", part) if len(x) >= 2 and x not in stop_tokens]
        for md in module_details:
            md_module = _norm_txt(md.get("module"))
            blob = " ".join([
                md_module,
                _norm_txt(md.get("sub_module")),
                _norm_txt((md.get("base") or {}).get("description")),
                _norm_txt((md.get("new") or {}).get("description")),
                _norm_txt(md.get("remark")),
            ])
            score = 0
            if preferred_module and _norm_txt(preferred_module) == md_module:
                score += 4
            mod_toks = [x for x in re.split(r"[^A-Z0-9가-힣]+", md_module) if len(x) >= 3 and x not in stop_tokens]
            for mt in mod_toks:
                if mt in scope:
                    score += 2
            for tok in part_toks:
                if tok in blob:
                    score += (3 if len(tok) >= 3 else 1)
            detail_toks = [x for x in re.split(r"[^A-Z0-9가-힣]+", detail) if len(x) >= 3 and x not in stop_tokens]
            for tok in detail_toks:
                if tok in blob:
                    score += 1
            if score > best_score:
                best = md
                best_score = score
        return best if best_score >= 1 else None

    def _infer_change_type(detail_text: Any, reason_text: Any, part_text: Any) -> str:
        blob = " ".join([str(detail_text or ""), str(reason_text or ""), str(part_text or "")]).upper()
        if any(k in blob for k in ["삭제", "제거", "미적용", "DELETED", "REMOVE"]):
            return "삭제"
        if any(k in blob for k in ["추가", "신규", "ADD", "NEW"]):
            return "NEW"
        return "Changing"

    change_points_for_bom: list[dict[str, Any]] = []
    for i, dc in enumerate(detailed_changes, 1):
        rule_mod = _rule_based_module_from_change(dc)
        summary_mod = _pick_module_from_summary(dc)
        preferred_mod = rule_mod or summary_mod
        md = _pick_best_detail(dc, preferred_module=preferred_mod)
        part = str(dc.get("part") or "정보 없음")
        detail = str(dc.get("change_detail") or "정보 없음")
        reason = str(dc.get("change_reason") or "정보 없음")
        concern = str(dc.get("concern") or "내용 없음")

        base_pno = "TBD"
        new_pno = "TBD"
        is_shared = False
        shared_src = ""
        related_parts: list[str] = []
        remark = ""
        discipline = str(dc.get("discipline") or dc.get("category") or "정보 없음").strip() or "정보 없음"
        module_name = "정보 없음"

        if preferred_mod:
            module_name = preferred_mod

        if md:
            md_module = str(md.get("module") or "").strip()
            if md_module:
                module_name = md_module
            base_pno = str((md.get("base") or {}).get("part_no") or "TBD")
            new_pno = str((md.get("new") or {}).get("part_no") or "TBD")
            is_shared = bool((md.get("new") or {}).get("is_shared"))
            shared_src = str((md.get("new") or {}).get("shared_with") or "")
            remark = str(md.get("remark") or "")
            rp = str(md.get("sub_module") or "").strip()
            if rp and _norm_txt(rp) != _norm_txt(part):
                related_parts.append(rp)

        # 개발부품 Master/BOM 표는 행 자체에 base/new 품번이 있음(module_details 매칭 불필요).
        if base_pno == "TBD" and dc.get("base_part_no"):
            base_pno = str(dc.get("base_part_no"))
        if new_pno == "TBD" and dc.get("new_part_no"):
            new_pno = str(dc.get("new_part_no"))
        # 모듈명 비면 부품명을 모듈로(카드 브레드크럼 가독성).
        if module_name in ("", "정보 없음") and (dc.get("_src") or {}).get("type") == "master_bom":
            module_name = part

        if module_name in ("", "정보 없음"):
            module_name = discipline

        desc = f"{part}: {detail}" if part and detail else (detail or part or "정보 없음")
        ctype = _infer_change_type(detail, reason, part)
        tags = list(dc.get("tags") or [])
        if is_shared and "공용" not in tags:
            tags.append("공용")

        change_points_for_bom.append({
            "id": i,
            "description": desc,
            "module": module_name,
            "discipline": discipline,
            "type": ctype,
            "base_part_no": base_pno,
            "new_part_no": new_pno,
            "is_shared_part": is_shared,
            "shared_source": shared_src,
            "related_parts": related_parts or ["정보 없음"],
            "concerns": [concern] if concern else ["내용 없음"],
            "tags": tags,
            "evidence": {
                "part": part,
                "change_detail": detail,
                "change_reason": reason,
                "detail_src": dc.get("_src") or {},
                "discipline_src": "main_change_table.category",
                "module_detail_src": (md or {}).get("_src") if md else {},
            },
        })

    cp_filtered: list[dict[str, Any]] = []
    for r in change_points_for_bom:
        mod = str(r.get("module") or "")
        desc = re.sub(r"\s+", "", str(r.get("description") or "").upper())
        if "과전압" in mod:
            discarded.append({
                "why": "cp_drop_blacklist_module",
                "slide": None,
                "table_id": "final_cp",
                "sample_text": f"{mod} | {r.get('description', '')}",
            })
            continue
        if desc in {"O", "X", "(O,X)", "OX"}:
            discarded.append({
                "why": "cp_drop_checkbox_token",
                "slide": None,
                "table_id": "final_cp",
                "sample_text": f"{mod} | {r.get('description', '')}",
            })
            continue
        cp_filtered.append(r)

    return {
        "project_meta": project_meta,
        "module_summary": module_summary,
        "detailed_changes": detailed_changes,
        "module_details": module_details,
        "risk_review": risk_review,
        "change_points_for_bom": cp_filtered,
        "attachment_presence": image_presence,
        "slide_roles": slide_role_details,
        "discarded": discarded,
    }


# --- 에이전트용 정규화 ------------------------------------------------------


@dataclass
class ChangeItem:
    """L1로 흘릴 단위 변경항목. ``search_text``는 변경내용+변경사유.

    part_name·base/new 품번은 2026-06-10 개정으로 검색의 parts 채널 매칭에 함께 쓰인다
    (propose_from_items가 intent_from_change로 전달 — [[feedback-search-by-reason-not-id]]).
    module은 표기용.
    """

    change_detail: str
    change_reason: str
    part_name: str = ""
    module: str = ""
    change_type: str = ""
    base_part_no: str = ""
    new_part_no: str = ""
    concerns: list[str] = field(default_factory=list)
    src: dict[str, Any] = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        """검색용 텍스트 = 변경내용 + 변경사유 (식별자 제외)."""
        parts = [p for p in (self.change_detail, self.change_reason) if p and p not in ("정보 없음", "내용 없음")]
        return " ".join(parts).strip()

    @property
    def display(self) -> str:
        head = self.part_name if self.part_name and self.part_name != "정보 없음" else self.module
        return f"{head}: {self.change_detail}".strip(": ")


def change_items_from_extraction(extraction: dict[str, Any]) -> list[ChangeItem]:
    """추출 dict → ChangeItem 리스트. change_points_for_bom 우선, 없으면 detailed_changes."""
    items: list[ChangeItem] = []
    cps = extraction.get("change_points_for_bom") or []
    if cps:
        for cp in cps:
            ev = cp.get("evidence") or {}
            items.append(
                ChangeItem(
                    change_detail=str(ev.get("change_detail") or cp.get("description") or "").strip(),
                    change_reason=str(ev.get("change_reason") or "").strip(),
                    part_name=str(ev.get("part") or "").strip(),
                    module=str(cp.get("module") or "").strip(),
                    change_type=str(cp.get("type") or "").strip(),
                    base_part_no=str(cp.get("base_part_no") or "").strip(),
                    new_part_no=str(cp.get("new_part_no") or "").strip(),
                    concerns=[str(c) for c in (cp.get("concerns") or []) if str(c).strip()],
                    src=dict(ev.get("detail_src") or {}),
                )
            )
        return items

    for dc in extraction.get("detailed_changes") or []:
        items.append(
            ChangeItem(
                change_detail=str(dc.get("change_detail") or "").strip(),
                change_reason=str(dc.get("change_reason") or "").strip(),
                part_name=str(dc.get("part") or "").strip(),
                module=str(dc.get("category") or "").strip(),
                concerns=[str(dc.get("concern"))] if dc.get("concern") else [],
                src=dict(dc.get("_src") or {}),
            )
        )
    return items
