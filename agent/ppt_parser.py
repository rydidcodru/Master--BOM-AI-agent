"""심의회 PPTX 파일 파싱 및 데이터 추출 유틸리티 모듈."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ppt_text_from_shape(shape) -> str:
    """PPT 도형에서 텍스트를 추출하며, 취소선이 그어진 부분은 제외합니다."""
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


def _norm_header(h: str) -> str:
    return re.sub(r"[^A-Z0-9가-힣]", "", str(h or "").upper())


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


def _extract_product_type_from_page1(text_p1: str) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in str(text_p1 or "").splitlines() if str(x or "").strip()]
    cands: list[str] = []

    # 1) key:value 또는 key|value 패턴 우선
    p1 = [
        r"(?:^|\|)\s*제품군\s*(?:\||[:：])\s*([^|\n\r]+)",
        r"(?:^|\|)\s*PRODUCT(?:\s*TYPE)?\s*(?:\||[:：])\s*([^|\n\r]+)",
    ]
    for p in p1:
        for m in re.finditer(p, text_p1, flags=re.IGNORECASE):
            v = _sanitize_product_type(m.group(1))
            if v:
                cands.append(v)

    # 2) 테이블 행 형태: ... | 제품군 | 값 | ...
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

    # 2-1) 인접 라인 패턴: "제품군" 다음 줄이 값인 경우
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

    # 인접 라인 패턴 허용: KEY 라인 다음 라인 값
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


def _infer_product_type_from_model(model_text: str) -> str:
    m = str(model_text or "").strip().upper()
    if m.startswith("W"):
        return "Built-in Oven"
    if m.startswith("M"):
        return "Microwave Oven"
    return "Oven/Microwave"


def _split_lines_text(v: str) -> list[str]:
    txt = str(v or "")
    parts = re.split(r"[\n\r•·]+", txt)
    out = []
    for p in parts:
        s = re.sub(r"\s+", " ", p).strip(" -\t")
        if s:
            out.append(s)
    return out


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
    found = []
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


def _classify_slide_role(slide_lines: list[str], table_headers: list[str]) -> dict:
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


def _extract_project_meta(lines: list[str], ctx: dict, page1_lines: list[str] | None = None) -> dict:
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


def extract_change_review_from_pptx_bytes(pptx_bytes: bytes, ctx: dict | None = None) -> dict:
    """PPTX 바이너리 데이터를 파싱하여 주요 변경 내역 및 메타정보를 추출합니다."""
    ctx = ctx or {}
    try:
        from pptx import Presentation
    except Exception as e:
        raise RuntimeError("python-pptx 패키지가 필요합니다. 설치 후 다시 시도하세요.") from e

    prs = Presentation(io.BytesIO(pptx_bytes))

    discarded: list[dict] = []
    all_lines: list[str] = []
    all_table_text_lines: list[str] = []
    slide_lines_map: dict[int, list[str]] = {}
    slide_table_headers_map: dict[int, list[str]] = {}
    slide_table_text_map: dict[int, list[str]] = {}
    table_rows: list[dict] = []

    def _flat_cells(rows: list[list[str]]) -> list[str]:
        out = []
        for r in rows or []:
            for c in r or []:
                t = re.sub(r"\s+", " ", str(c or "")).strip()
                if t:
                    out.append(t)
        return out

    def _classify_table_candidate(rows: list[list[str]], slide_lines: list[str], slide: int, table_id: int) -> dict:
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

        # MAIN_CHANGE_TABLE
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

        # DETAIL_CHANGE_TABLE
        has_base = _has_any(hraw_join, ["BASE"])
        has_new = _has_any(hraw_join, ["NEW"])
        has_mod_or_remark = _has_any(hraw_join, ["MODULE", "모듈", "REMARK", "비고"])
        if has_base and has_new and has_mod_or_remark:
            return {"type": "DETAIL_CHANGE_TABLE", "why": "whitelist_detail_change"}

        # RISK_TABLE
        has_risk = _has_any(hraw_join, ["우려", "걱정", "CONCERN"]) or _has_any_compact(hraw_compact, ["우려", "걱정"])
        has_counter = _has_any(hraw_join, ["대책", "대응", "검증", "방안", "COUNTER"]) or _has_any_compact(hraw_compact, ["대책", "대응", "검증방안"])
        if has_risk and has_counter:
            return {"type": "RISK_TABLE", "why": "whitelist_risk"}

        return {"type": "IGNORE", "why": "not_whitelisted"}

    for s_idx, slide in enumerate(prs.slides, 1):
        slide_lines = []
        slide_table_headers: list[str] = []
        slide_table_texts: list[str] = []
        table_seq = 0
        for sh in slide.shapes:
            if getattr(sh, "has_text_frame", False):
                txt = _ppt_text_from_shape(sh)
                if txt:
                    ls = _split_lines_text(txt)
                    slide_lines.extend(ls)
                    all_lines.extend(ls)
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

    slide_roles: dict[int, str] = {}
    slide_role_details: list[dict] = []
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

    module_summary: list[dict] = []
    detailed_changes: list[dict] = []
    module_details: list[dict] = []
    risk_review: list[dict] = []
    module_summary_hints: list[dict] = []

    for tb in table_rows:
        rows = tb["rows"]
        slide_no = int(tb.get("slide") or 0)
        slide_role = slide_roles.get(slide_no, "UNKNOWN")

        if slide_role == "REFERENCE":
            flat = _flat_cells(rows)
            discarded.append({
                "why": "reference_slide_scope",
                "slide": tb.get("slide"),
                "table_id": tb.get("table_id"),
                "sample_text": " | ".join(flat[:6]) if flat else "",
            })
            continue

        cls = _classify_table_candidate(rows, tb.get("slide_lines") or [], tb.get("slide"), tb.get("table_id"))
        ttype = cls.get("type", "IGNORE")

        if ttype == "IGNORE":
            if slide_role == "DETAIL" and rows and rows[0]:
                hdr0 = [_norm_header(x) for x in rows[0]]
                has_mod_col = any(("MODULE" in h) or ("모듈" in h) for h in hdr0)
                has_change_col = any(("주요변경점" in h) or ("변경내역" in h) or ("CHANGE" in h) for h in hdr0)
                if has_mod_col and has_change_col:
                    m_i = next((i for i, h in enumerate(hdr0) if ("MODULE" in h or "모듈" in h)), 0)
                    c_i = next((i for i, h in enumerate(hdr0) if ("주요변경점" in h or "변경내역" in h or "CHANGE" in h)), min(1, len(hdr0)-1))
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
            rm_i = next((i for i, h in enumerate(hdr) if ("REMARK" in h or "비고" in h)), min(3, len(hdr)-1))
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

        if ttype == "RISK_TABLE":
            c_i = next((i for i, h in enumerate(hdr) if ("구분" in h or "CATEGORY" in h)), 0)
            o_i = next((i for i, h in enumerate(hdr) if ("우려" in h or "CONCERN" in h)), 1)
            m_i = next((i for i, h in enumerate(hdr) if ("대책" in h or "COUNTER" in h)), 2)
            a_i = next((i for i, h in enumerate(hdr) if ("첨부" in h or "ATTACH" in h)), min(3, len(hdr)-1))
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

    return {
        "project_meta": project_meta,
        "module_summary": module_summary,
        "detailed_changes": detailed_changes,
        "module_details": module_details,
        "risk_review": risk_review,
    }


def parse_pptx_file(file_path: str | Path, ctx: dict | None = None) -> dict:
    """PPTX 파일 경로를 전달받아 데이터를 파싱합니다."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PPTX 파일이 존재하지 않습니다: {file_path}")
    return extract_change_review_from_pptx_bytes(path.read_bytes(), ctx=ctx)
