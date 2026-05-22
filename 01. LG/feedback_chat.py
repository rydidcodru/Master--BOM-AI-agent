from __future__ import annotations
import re, datetime
import streamlit as st
import pandas as pd
from streamlit_float import float_init, float_css_helper


def init_feedback_chat():
    ss = st.session_state
    ss.setdefault("fb_messages", [])
    ss.setdefault("fb_open", False)
    ss.setdefault("fb_history", [])
    ss.setdefault("fb_input_counter", 0)
    ss.setdefault("pending_recheck", None)
    ss.setdefault("feedback_log", [])
    ss.setdefault("question_queue", [])
    ss.setdefault("question_queue_idx", 0)
    ss.setdefault("review_active", False)
    ss.setdefault("review_intro_sent", False)
    ss.setdefault("review_last_qid", "")
    ss.setdefault("review_queue_signature", "")


INTENT_EXCLUDE  = "EXCLUDE"
INTENT_ADD      = "ADD"
INTENT_SOURCING = "SOURCING"
INTENT_MOVE     = "MOVE"
INTENT_BULK     = "BULK"
INTENT_QUERY    = "QUERY"
INTENT_RECHECK  = "RECHECK"
INTENT_UNKNOWN  = "UNKNOWN"

_INTENT_RULES = [
    (INTENT_EXCLUDE,  ["제외", "빼줘", "빼", "삭제해", "없애", "제거"]),
    (INTENT_ADD,      ["추가해", "넣어줘", "넣어", "빠졌", "누락"]),
    (INTENT_SOURCING, ["공용으로", "공용검토", "신규로", "채번", "참고품번"]),
    (INTENT_MOVE,     ["하위로", "옮겨", "이동해", "밑으로"]),
    (INTENT_BULK,     ["다 추가", "나머지 확정", "전부 추가", "일괄", "전체 확정",
                       "다 변경", "나머지 추가"]),
    (INTENT_QUERY,    ["뭐야", "왜", "어떤 모델", "알려줘", "확인해", "검색해",
                       "어디서", "출처"]),
    (INTENT_RECHECK,  ["재검토", "다시 찾아", "다르게 찾아", "다른 걸로", "대안", "다시 검토", "다시 추천"]),
]


def classify_intent(text):
    t = (text or "").strip().lower()
    if not t:
        return INTENT_UNKNOWN
    for intent, keywords in _INTENT_RULES:
        if intent == INTENT_BULK:
            for kw in keywords:
                if kw in t:
                    return INTENT_BULK
    for intent, keywords in _INTENT_RULES:
        if intent == INTENT_BULK:
            continue
        for kw in keywords:
            if kw in t:
                return intent
    return INTENT_UNKNOWN


def _norm(s):
    return re.sub(r'[^A-Z0-9가-힣]', '', (s or '').strip().upper())


def _norm_space(s):
    return re.sub(r'\s+', ' ', str(s or '').strip())


def _norm_pno(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').strip().upper())


def _extract_model_size_inch(text):
    t = str(text or "")
    m = re.search(r"(\d{2})\s*인치", t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    m = re.search(r"\b(24|30|27|36)\b", t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


MAX_QUESTIONS = 5


def _make_flag_label(part_name, flag_type, flag_keyword):
    return flag_type or "확인 필요"

def _extract_core9(model_text):
    t = str(model_text or "").upper().strip()
    m = re.search(r"\b[KC]?([WLMCH][A-Z0-9]{8})\b", t)
    if not m:
        return ""
    return m.group(1)


def infer_oven_size_inch(capacity_code, region, product, category):
    try:
        val = int(capacity_code) if str(capacity_code or "").isdigit() else 0
    except Exception:
        val = 0
    region_up = str(region or "").upper()
    product = str(product or "").upper()
    category = str(category or "").upper()

    if product == "W" and category in ("S", "D", "C"):
        if any(x in region_up for x in ["EU", "유럽", "EUROPE", "GLOBAL", "글로벌"]):
            if 65 <= val <= 80:
                return "24"
            return "UNKNOWN"
        if val <= 30:
            return "24"
        if val <= 60:
            return "30"
        return "30+"

    if product == "M":
        if val <= 25:
            return "SMALL"
        if val <= 32:
            return "MEDIUM"
        return "LARGE"

    if product in ("C", "H"):
        return str(val) if val else "UNKNOWN"

    return "UNKNOWN"


def auto_detect_model_diff(target_core9, source_core9, region):
    """
    모델 코어9을 비교하고 차이 감지 → "trigger" 역할만 함 (어떤 부품 영향인지는 판단 안함)
    v4.0: gap 레벨 계산 포함 (small <= 5, medium 5-15, large > 15)
    """
    diffs = []
    t = _extract_core9(target_core9)
    s = _extract_core9(source_core9)
    if len(t) != 9 or len(s) != 9:
        return diffs

    if t[0] != s[0]:
        diffs.append({"type": "PRODUCT", "desc": f"제품군 다름 ({t[0]} vs {s[0]})"})
    if t[1] != s[1]:
        diffs.append({"type": "INSTALL", "desc": f"설치방식 다름 ({t[1]} vs {s[1]})"})
    if t[2] != s[2]:
        diffs.append({"type": "SERIES", "desc": f"시리즈 다름 ({t[2]}00 vs {s[2]}00)"})

    t_cap = t[4:6]
    s_cap = s[4:6]
    if t_cap != s_cap:
        # Gap 계산 (용량/폭 수치)
        try:
            t_num = int(t_cap) if t_cap.isdigit() else 0
            s_num = int(s_cap) if s_cap.isdigit() else 0
            gap_abs = abs(t_num - s_num) if t_num and s_num else 0
        except Exception:
            gap_abs = 0

        # Gap 레벨 분류: small <= 5, medium 5-15, large > 15
        if gap_abs <= 5 and gap_abs > 0:
            gap_level = "small"
        elif gap_abs <= 15:
            gap_level = "medium"
        else:
            gap_level = "large"

        t_inch = infer_oven_size_inch(t_cap, region, t[0], t[1])
        s_inch = infer_oven_size_inch(s_cap, region, s[0], s[1])
        if t_inch != "UNKNOWN" and s_inch != "UNKNOWN":
            size_desc = f"사이즈 다름 ({t_inch}인치 vs {s_inch}인치)"
        else:
            size_desc = f"용량/폭 다름 ({t_cap} vs {s_cap})"

        diffs.append({
            "type": "SIZE",
            "desc": size_desc,
            "gap": gap_level,
            "gap_abs": gap_abs
        })

    if t[6] != s[6]:
        diffs.append({"type": "FEATURE", "desc": f"디자인/기능 다름 ({t[6]} vs {s[6]})"})

    return diffs


def _active_types_from_diffs(detected_diffs):
    active_types = set()
    for d in detected_diffs or []:
        dt = d.get("type")
        if dt in ("SIZE", "INSTALL", "HEAT", "CONVECTION"):
            active_types.add(dt)
    return list(active_types)


def _detected_diffs_text(detected_diffs):
    picked = [d.get("desc", "") for d in (detected_diffs or []) if d.get("type") in ("SIZE", "INSTALL", "HEAT") and d.get("desc")]
    return ", ".join(picked)


def _part_source_model(prop):
    refs = prop.get("ref_models") or []
    if refs:
        return str(refs[0])
    src_docs = prop.get("source_docs") or []
    if src_docs:
        return str(src_docs[0])
    return ""


def _find_past_exclude_reason(part_name, feedback_log):
    n = _norm(part_name)
    if not n:
        return ""
    for item in reversed(feedback_log or []):
        if item.get("action") != "제외":
            continue
        prev = _norm(item.get("part_name") or "")
        if not prev:
            continue
        if n == prev or n in prev or prev in n:
            return item.get("reason") or "유사 부품 과거 제외 이력"
    return ""


def _queue_signature(proposals, target_model, region="", detected_diffs=None):
    parts_n = 0
    pids = []
    for p in proposals or []:
        pids.append(str(p.get("proposal_id") or ""))
        parts_n += len(p.get("changed_parts") or []) + len(p.get("indirect_parts") or [])
    return f"{target_model}|{_norm_space(region)}|{_detected_diffs_text(detected_diffs)}|{len(proposals or [])}|{parts_n}|{'|'.join(sorted(pids))}"


def _add_review_tag(pt, tag):
    if not tag:
        return
    cur = str(pt.get("review_tag") or "").strip()
    tags = [x.strip() for x in cur.split("|") if x.strip()]
    if tag not in tags:
        tags.append(tag)
    pt["review_tag"] = " | ".join(tags)


def _extract_change_target_objects(change_text):
    """
    변경점 텍스트에서 대상 객체 추출 (v4.0)
    예: "트레이, 도어 변경" → ["tray", "door"]
    """
    t = (change_text or "").lower()
    objs = []
    mapping = {
        "door": ["door", "도어"],
        "cavity": ["cavity", "캐비티", "캐비"],
        "panel": ["panel", "패널"],
        "camera": ["camera", "카메라"],
        "rack": ["rack", "랙"],
        "tray": ["tray", "트레이"],
        "control": ["control", "제어", "조작"],
        "motor": ["motor", "모터", "bldc"],
        "steam": ["steam", "스팀"],
        "hinge": ["hinge", "힌지"],
        "harness": ["harness", "하네스", "wire", "배선"],
    }
    found_objs = set()
    for eng, vals in mapping.items():
        if any(v in t for v in vals):
            found_objs.add(eng)
    return list(found_objs)


def _prioritize_by_change_context(queue, change_items):
    """
    변경점 관련성으로 우선순위 재정렬 (v4.0)
    - bom_path 일치: 우선순위 0
    - part_name 일치: 우선순위 1
    - 기타: 우선순위 2
    """
    change_text = " ".join(change_items or "").lower()
    target_objects = _extract_change_target_objects(change_text)

    def _get_priority(item):
        bom_path = (item.get("bom_path") or item.get("apply_bom_path") or item.get("lvl1_desc") or "").lower()
        part_name = (item.get("part_name") or "").lower()
        
        for obj in target_objects:
            if obj in bom_path:
                return 0
        for obj in target_objects:
            if obj in part_name:
                return 1
        return 2

    return sorted(queue, key=lambda x: (_get_priority(x), x.get("proposal_idx", 99), x.get("part_idx", 99)))


def build_llm_review_prompt(queue_items, target_model, detected_diffs, change_items):
    """
    LLM이 추가 검증이 필요한 부품을 판정하기 위한 프롬프트 생성 (v4.0)
    - gap 레벨별 톤 조절
    - 변경점 컨텍스트 포함
    - 최대 5건 규칙명시
    """
    diff_descriptions = []
    gap_level = "unknown"
    
    for d in (detected_diffs or []):
        if d.get("type") == "SIZE":
            gap_level = d.get("gap", "unknown")
            diff_descriptions.append(f"{d.get('desc', '')} [gap: {gap_level}]")
        else:
            diff_descriptions.append(d.get("desc", ""))
    
    diff_text = "\n".join(diff_descriptions) if diff_descriptions else "자동 감지된 주요 차이 없음"

    # 부품 목록 포맷팅
    parts_list = "\n".join([
        f"[{i}] {item.get('part_name', '')} (품번: {item.get('part_no', '(미확정)')})"
        for i, item in enumerate(queue_items)
    ])

    # Gap 레벨별 톤 조절
    if gap_level == "large":
        tone = "적극적으로 확인하고 모델 차이가 영향을 미칠 수 있는 부품만 선택"
    elif gap_level == "medium":
        tone = "중요한 부품만 선택"
    else:
        tone = "거의 모든 부품이 호환되겠지만, 확실하지 않은 것만 선택"

    change_text = ", ".join(change_items or []) if change_items else "정보없음"

    prompt = f"""당신은 가전제품(오븐/전자레인지) BOM 전문가입니다.

## 대상 모델
{target_model}

## 변경점
{change_text}

## 모델 비교 결과
{diff_text}

## 검증 필요 부품 목록
{parts_list}

## 지시사항
모델 차이({gap_level})나 변경점({change_text})으로 인해 실제로 적용이 안 되거나 교체가 필요할 가능성이 높은 부품을 선택하세요.

{tone}.

## 규칙
- 최대 5건만 선택
- 확실한 것만 선택 (의심스러운 부품은 제외)
- 변경점과 직접 관련된 부품을 우선

## 출력 형식
선택된 부품의 인덱스(0-based)를 JSON 배열로 반환
예) [0, 2, 4]

배열만 반환하세요. 다른 설명 없음."""

    return prompt.strip()


def _llm_refine_candidates(candidates, target_model, region, detected_diffs, change_items, max_n=MAX_QUESTIONS):
    """
    LLM을 사용해 검증 필요 부품 선별 (v4.0)
    - OpenAI API 호출
    - 부품 인덱스 배열 추출
    - 최대 max_n건 반환
    """
    if not candidates:
        return []

    # 프롬프트 생성
    prompt = build_llm_review_prompt(candidates, target_model, detected_diffs, change_items)

    try:
        # LLM_PROVIDER 환경변수에 따라 Azure / Ollama 자동 분기
        from graph_rag.config import llm_openai_client
        client, model_name = llm_openai_client()

        # OpenAI API 호출 (chat.completions)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "당신은 BOM 검토 전문가입니다. 사용자의 지시에 따라 JSON 배열만 반환하세요."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0,
        )
        
        # 응답 파싱
        text = response.choices[0].message.content.strip() if response.choices else ""
        
        # JSON 배열 추출
        import json
        m = re.search(r"\[\s*[0-9\s,]*\]", text)
        if not m:
            return candidates[:max_n]
        
        try:
            arr = json.loads(m.group(0))
            if not isinstance(arr, list):
                return candidates[:max_n]
        except Exception:
            return candidates[:max_n]
        
        # 선택된 부품 추출
        picked = []
        for idx in arr:
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                picked.append(candidates[idx])
        
        return picked[:max_n] if picked else candidates[:max_n]
    
    except Exception as e:
        # LLM 호출 실패 시 상위 max_n개 반환
        return candidates[:max_n]


def build_question_queue(proposals, target_model, feedback_log, region="", detected_diffs=None):
    """
    LLM 기반 검증 큐 구성 (v4.0)
    - 모든 부품을 후보에 포함
    - LLM으로 선별
    - 변경점 관련성으로 재정렬
    """
    queue_candidates = []
    seen = set()

    for p_idx, prop in enumerate(proposals or []):
        source_model = _part_source_model(prop)
        conf = float(prop.get("confidence") or 0.0)
        
        for list_key in ("changed_parts", "indirect_parts"):
            for part_idx, pt in enumerate(prop.get(list_key) or []):
                pt["review_tag"] = ""

                part_name = pt.get("part_name") or pt.get("desc") or ""
                part_no = pt.get("part_no") or pt.get("display_pno") or ""
                in_base = bool(pt.get("in_base", False))

                # 리뷰 태그 추가 (시각 가이드)
                if conf < 0.5:
                    _add_review_tag(pt, "⚠️ 낮은 확신도")
                if not in_base:
                    _add_review_tag(pt, "🆕 신규")

                # 과거 제외 이력 체크
                prev_reason = _find_past_exclude_reason(part_name, feedback_log)
                if prev_reason:
                    _add_review_tag(pt, "🚫 " + prev_reason[:20])

                key = (_norm_pno(part_no), _norm(part_name))
                if key in seen:
                    continue
                seen.add(key)

                queue_candidates.append({
                    "proposal_idx": p_idx,
                    "part_list": list_key,
                    "part_idx": part_idx,
                    "part_no": part_no,
                    "part_name": part_name,
                    "source_model": source_model,
                    "target_model": target_model,
                    "detected_diffs": auto_detect_model_diff(target_model, source_model, region) if source_model else (detected_diffs or []),
                    "lvl1_desc": (prop.get("lvl1") or {}).get("desc") or "",
                    "bom_path": pt.get("bom_path") or pt.get("apply_bom_path") or "",
                    "confidence": conf,
                    "in_base": in_base,
                    "answered": False,
                })

    if not queue_candidates:
        return []

    # LLM 프롬프트 생성 및 호출
    llm_prompt = build_llm_review_prompt(
        queue_candidates,
        target_model,
        detected_diffs,
        st.session_state.get("change_items") or [],
    )

    queue_candidates_llm = _llm_refine_candidates(
        queue_candidates,
        target_model,
        region,
        detected_diffs,
        st.session_state.get("change_items") or [],
        MAX_QUESTIONS,
    )

    # 변경점 관련성으로 재정렬
    queue_candidates_llm = _prioritize_by_change_context(
        queue_candidates_llm,
        st.session_state.get("change_items") or [],
    )

    # 최대 5건으로 제한 + flag_type 추가 (시각 가이드)
    queue = []
    for i, item in enumerate(queue_candidates_llm[:MAX_QUESTIONS]):
        q = dict(item)
        q["qid"] = f"Q-{i+1:03d}"
        q["num"] = i + 1
        
        # 시각 가이드를 위한 flag_type 설정
        if float(q.get("confidence", 0.0)) < 0.5:
            q["flag_type"] = "낮은 확신도"
        elif not q.get("in_base", False):
            q["flag_type"] = "신규"
        else:
            q["flag_type"] = "모델 차이"
        
        q["flag_keyword"] = ""  # LLM 판정이므로 keyword 없음
        queue.append(q)

    return queue


def _append_chat_once(role, content):
    msgs = st.session_state.setdefault("fb_messages", [])
    if msgs and msgs[-1].get("role") == role and msgs[-1].get("content") == content:
        return
    msgs.append({"role": role, "content": content})


def _active_question():
    ss = st.session_state
    queue = ss.get("question_queue") or []
    idx = int(ss.get("question_queue_idx") or 0)
    if not queue:
        return None
    if idx <= 0:
        return queue
    return None


def _render_question_text(queue):
    lines = []
    for q in queue:
        pno = str(q.get("part_no") or "").strip()
        pno_disp = pno if pno and pno.lower() != "nan" else "품번 미확정"
        label = _make_flag_label(q.get("part_name"), q.get("flag_type"), q.get("flag_keyword"))
        lines.append(
            f"{q.get('num')}. 📏 {q.get('part_name')} ({pno_disp}) - {label}"
        )
    body = "\n".join(lines)
    total_parts = sum(len(p.get("changed_parts") or []) + len(p.get("indirect_parts") or []) for p in (st.session_state.get("proposals") or []))
    detected_text = _detected_diffs_text(st.session_state.get("detected_diffs") or [])
    intro = (
        f"추천 {total_parts}건 중, **{detected_text}** 차이 때문에 확인이 필요한 부품 {len(queue)}건이 있어:"
        if detected_text else
        f"추천 {total_parts}건 중 모델명 자동 비교로 확인이 필요한 부품 {len(queue)}건이 있어:"
    )
    return (
        f"{intro}\n\n"
        f"{body}\n\n"
        "그대로 쓸 수 있는 거 있어?\n"
        "(예: `다 유지` / `2번 빼` / `1,3번 빼고 나머지 유지`)"
    )


def _ensure_active_review_queue():
    ss = st.session_state
    proposals = ss.get("proposals") or []
    if not proposals:
        return

    region = ss.get("region") or ""
    detected_diffs = ss.get("detected_diffs") or []
    sig = _queue_signature(proposals, ss.get("target_model") or "", region, detected_diffs)
    if sig == ss.get("review_queue_signature"):
        return

    queue = build_question_queue(
        proposals,
        ss.get("target_model") or "",
        ss.get("feedback_log") or [],
        region,
        detected_diffs,
    )
    ss["question_queue"] = queue
    ss["question_queue_idx"] = 0
    ss["review_active"] = bool(queue)
    ss["review_intro_sent"] = False
    ss["review_last_qid"] = ""
    ss["review_queue_signature"] = sig

    total_parts = sum(len(p.get('changed_parts') or []) + len(p.get('indirect_parts') or []) for p in proposals)
    if queue:
        _append_chat_once("assistant", _render_question_text(queue))
        ss["review_last_qid"] = "GROUP_SENT"
    else:
        _append_chat_once("assistant", f"추천 결과 {total_parts}건 전부 모델 차이 영향이 낮은 부품이야. 바로 테이블에서 확인하고 확정해줘!")
        ss["review_last_qid"] = "NO_QUESTION"


def prepare_active_review():
    """앱 본문 렌더링 전에 질문 큐/태그를 준비한다."""
    _ensure_active_review_queue()


def _append_next_question_if_needed():
    return


def parse_user_feedback(user_text, question):
    # 그룹 질문 파싱
    if isinstance(question, list):
        queue = question
        n = len(queue)
        if n == 0:
            return None

        t = _norm_space(user_text)
        low = t.lower()

        def _parse_nums(text):
            nums = []
            for m in re.finditer(r"(\d+)\s*번", text):
                v = int(m.group(1))
                if 1 <= v <= n and v not in nums:
                    nums.append(v)
            if nums:
                return nums
            for m in re.finditer(r"([\d\s,]+)", text):
                chunk = m.group(1)
                if not re.search(r"\d", chunk):
                    continue
                for p in re.split(r"[^0-9]+", chunk):
                    if p.isdigit():
                        v = int(p)
                        if 1 <= v <= n and v not in nums:
                            nums.append(v)
            return nums

        nums = _parse_nums(t)

        replace_map = {}
        for m in re.finditer(r"(\d+)번.*?([A-Z]{2,3}\d{8,12}).*?(바꿔|교체|대체|변경)", t, re.I):
            num = int(m.group(1))
            if 1 <= num <= n:
                replace_map[num] = m.group(2).upper()

        decisions = {i: "유지" for i in range(1, n + 1)}

        if any(k in low for k in ["다 빼", "전부 제외", "다 필요없", "전부 빼", "모두 제외"]):
            decisions = {i: "제외" for i in range(1, n + 1)}
        elif any(k in low for k in ["다 유지", "다 괜찮", "ㅇㅇ 다", "모두 유지", "전부 유지"]):
            decisions = {i: "유지" for i in range(1, n + 1)}
        elif nums:
            if "만 유지" in low:
                decisions = {i: ("유지" if i in nums else "제외") for i in range(1, n + 1)}
            elif ("나머지 유지" in low) and any(k in low for k in ["빼", "제외", "삭제"]):
                decisions = {i: ("제외" if i in nums else "유지") for i in range(1, n + 1)}
            elif any(k in low for k in ["빼", "제외", "삭제"]):
                decisions = {i: ("제외" if i in nums else "유지") for i in range(1, n + 1)}
            elif any(k in low for k in ["유지", "그대로", "맞아"]):
                decisions = {i: ("유지" if i in nums else "제외") for i in range(1, n + 1)}
            else:
                return None
        else:
            return None

        for num, new_pno in replace_map.items():
            decisions[num] = "교체"

        return {
            "mode": "group",
            "decisions": decisions,
            "replace": replace_map,
            "replace_with": None,
            "replace_name": "",
            "reason": "사용자 그룹 검토 응답",
        }

    t = _norm_space(user_text)
    if not t:
        return None

    low = t.lower()
    if any(k in low for k in ["유지", "그대로", "맞아", "ㅇㅇ", "ok", "오케이"]):
        return {"action": "유지", "reason": "사용자 유지 판단", "replace_with": None, "replace_name": ""}
    if any(k in low for k in ["제외", "빼", "필요없", "삭제"]):
        return {"action": "제외", "reason": "사용자 제외 판단", "replace_with": None, "replace_name": ""}

    m = re.search(r"(?:대신|교체|바꿔|변경)\s*([A-Z0-9가-힣\-_/ ]{2,})", t, re.I)
    if m:
        raw = m.group(1).strip()
        pno_m = re.search(r"([A-Z0-9]{5,})", raw.upper())
        rpno = pno_m.group(1) if pno_m else ""
        return {
            "action": "교체",
            "reason": "사용자 대체 요청",
            "replace_with": rpno,
            "replace_name": raw if not rpno else "",
        }

    pno_only = re.search(r"\b([A-Z0-9]{5,})\b", t.upper())
    if pno_only and any(k in low for k in ["로", "바꿔", "대신"]):
        return {"action": "교체", "reason": "사용자 대체 요청", "replace_with": pno_only.group(1), "replace_name": ""}

    return None


def apply_feedback_to_table(feedback):
    all_edited = st.session_state.get("proposal_review_all") or {}
    target_pno = _norm_pno(feedback.get("part_no") or "")
    target_name = _norm(feedback.get("part_name") or "")
    reason = feedback.get("reason") or ""
    action = feedback.get("action") or ""
    replace_with = feedback.get("replace_with") or ""
    replace_name = feedback.get("replace_name") or ""

    matched = False
    for g_idx, edited_df in all_edited.items():
        if not isinstance(edited_df, pd.DataFrame) or len(edited_df) == 0:
            continue

        pno_col = "품번" if "품번" in edited_df.columns else None
        name_col = "부품명" if "부품명" in edited_df.columns else None
        note_col = "비고" if "비고" in edited_df.columns else None
        type_col = "변경유형" if "변경유형" in edited_df.columns else None

        mask = pd.Series([False] * len(edited_df))
        if pno_col and target_pno:
            mask = mask | edited_df[pno_col].astype(str).apply(lambda x: _norm_pno(x) == target_pno)
        if name_col and target_name:
            mask = mask | edited_df[name_col].astype(str).apply(lambda x: _norm(x) == target_name)

        if not mask.any():
            continue

        if type_col:
            if action == "제외":
                edited_df.loc[mask, type_col] = "제외"
            elif action == "교체":
                edited_df.loc[mask, type_col] = "제외"

        if note_col:
            edited_df.loc[mask, note_col] = edited_df.loc[mask, note_col].astype(str).apply(
                lambda x: (x + " | " if x and x != "nan" else "") + f"[챗봇 검토] {reason}"
            )

        if action == "교체":
            base_row = edited_df[mask].head(1).copy()
            if len(base_row) > 0:
                if type_col:
                    base_row[type_col] = "추가"
                if pno_col:
                    base_row[pno_col] = replace_with if replace_with else "(채번 필요)"
                if name_col and replace_name:
                    base_row[name_col] = replace_name
                if note_col:
                    base_row[note_col] = f"[챗봇 검토] 대체 추가"
                edited_df = pd.concat([edited_df, base_row], ignore_index=True)

        all_edited[g_idx] = edited_df
        matched = True
        break

    st.session_state["proposal_review_all"] = all_edited
    return matched


def _apply_feedback_to_proposals(feedback):
    proposals = st.session_state.get("proposals") or []
    q = feedback.get("question") or {}
    p_idx = int(q.get("proposal_idx", -1))
    list_key = q.get("part_list") or ""
    part_idx = int(q.get("part_idx", -1))
    if p_idx < 0 or p_idx >= len(proposals) or list_key not in ("changed_parts", "indirect_parts"):
        return
    parts = proposals[p_idx].get(list_key) or []
    if part_idx < 0 or part_idx >= len(parts):
        return

    pt = parts[part_idx]
    action = feedback.get("action")
    if action == "제외":
        pt["action"] = "EXCLUDE"
    elif action == "유지":
        pass
    elif action == "교체":
        pt["action"] = "EXCLUDE"
        new_part = {
            "action": "ADD",
            "part_name": feedback.get("replace_name") or pt.get("part_name") or "",
            "part_no": feedback.get("replace_with") or "",
            "display_pno": feedback.get("replace_with") or "(채번 필요)",
            "lvl": pt.get("lvl") or "",
            "qty": pt.get("qty") or "1",
            "base_type": pt.get("base_type") or "",
            "rsn": "챗봇 대체",
            "tier": "USER",
            "source_doc": "chatbot_review",
        }
        proposals[p_idx].setdefault(list_key, []).append(new_part)

    st.session_state["proposals"] = proposals


def _record_feedback_log(feedback):
    log = st.session_state.setdefault("feedback_log", [])
    q = feedback.get("question") or {}
    log.append({
        "part_no": q.get("part_no") or "",
        "part_name": q.get("part_name") or "",
        "action": feedback.get("action") or "",
        "reason": feedback.get("reason") or "",
        "replace_with": feedback.get("replace_with") or None,
        "flag_type": q.get("flag_type") or "",
        "source": "chatbot_review",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })


def _process_active_review_answer(user_text):
    ss = st.session_state
    queue = _active_question()
    if not queue:
        return None

    parsed = parse_user_feedback(user_text, queue)
    if not parsed:
        return "이해 못 했어 😅 번호로 알려줘. 예: `1,3번 빼` 또는 `다 유지`"

    decisions = parsed.get("decisions") or {}
    replace_map = parsed.get("replace") or {}
    result_lines = []
    detected_text = _detected_diffs_text(ss.get("detected_diffs") or [])

    for q in queue:
        num = int(q.get("num") or 0)
        action = decisions.get(num, "유지")
        feedback = {
            "action": action,
            "reason": (f"{detected_text} 차이로 미적용" if (action == "제외" and detected_text) else (parsed.get("reason") or "사용자 검토 응답")),
            "replace_with": replace_map.get(num) if action == "교체" else None,
            "replace_name": parsed.get("replace_name") or "",
            "question": q,
            "part_no": q.get("part_no") or "",
            "part_name": q.get("part_name") or "",
        }
        _apply_feedback_to_proposals(feedback)
        apply_feedback_to_table(feedback)
        _record_feedback_log(feedback)
        q["answered"] = True

        if action == "교체" and replace_map.get(num):
            result_lines.append(f"- {num}. {q.get('part_name')} -> 제외 (대체: {replace_map.get(num)})")
        else:
            result_lines.append(f"- {num}. {q.get('part_name')} -> {action}")

    _log("ACTIVE_REVIEW", result_lines)
    ss["question_queue"] = queue
    ss["question_queue_idx"] = len(queue)
    ss["review_active"] = False
    ss["review_last_qid"] = "GROUP_DONE"

    total_parts = sum(len(p.get("changed_parts") or []) + len(p.get("indirect_parts") or []) for p in (ss.get("proposals") or []))
    remain = max(total_parts - len(queue), 0)
    return "OK!\n" + "\n".join(result_lines) + f"\n\n나머지 {remain}건은 사이즈 무관/저위험으로 두었어. 테이블 확인하고 확정해줘!"


def _tokenize(s):
    s = (s or "").upper()
    return [w for w in re.split(r'[\s,/\-\(\)]+', s) if len(w) >= 2]


def find_target_parts(text, proposals):
    t_up = (text or "").upper()
    t_norm = _norm(text)
    matches = []
    for p_idx, prop in enumerate(proposals or []):
        for list_key in ("changed_parts", "indirect_parts"):
            parts = prop.get(list_key) or []
            for pt_idx, pt in enumerate(parts):
                pname = pt.get("part_name") or pt.get("desc") or ""
                pno = pt.get("part_no") or pt.get("display_pno") or ""
                pno_n = _norm(pno)
                if pno_n and len(pno_n) >= 5 and pno_n in t_norm:
                    matches.append(dict(proposal_idx=p_idx, part_list=list_key,
                                        part_idx=pt_idx, part=pt, match_type="part_no"))
                    continue
                for tok in _tokenize(pname):
                    if tok in t_up and tok not in ("ASS", "ASSY", "THE", "FOR"):
                        matches.append(dict(proposal_idx=p_idx, part_list=list_key,
                                            part_idx=pt_idx, part=pt, match_type="part_name"))
                        break
        lvl1 = prop.get("lvl1") or {}
        l1_desc = lvl1.get("desc") or ""
        for tok in _tokenize(l1_desc):
            if tok in t_up and tok not in ("ASS", "ASSY", "THE", "FOR"):
                matches.append(dict(proposal_idx=p_idx, part_list="_assembly",
                                    part_idx=-1, part=lvl1, match_type="assembly"))
                break
    return matches


def _log(action_type, details):
    ss = st.session_state
    ss.setdefault("fb_history", [])
    ss["fb_history"].append({
        "ts": datetime.datetime.now().strftime("%H:%M:%S"),
        "action": action_type,
        "items": details,
    })


def _handle_exclude(text, proposals, targets):
    if not targets:
        return ("제외할 부품을 특정할 수 없어요. "
                "부품명이나 품번을 함께 입력해 주세요.\n\n"
                "예) Shelf 제외해줘, EAD66035701 제외")
    excluded = []
    for t in targets:
        if t["part_list"] == "_assembly":
            continue
        pt = proposals[t["proposal_idx"]][t["part_list"]][t["part_idx"]]
        pt["action"] = "EXCLUDE"
        name = pt.get("part_name") or pt.get("desc") or pt.get("part_no") or "?"
        excluded.append(name)
    if excluded:
        _log("EXCLUDE", excluded)
        return "✅ **{}건 제외** 처리했습니다:\n".format(len(excluded)) + "\n".join("- " + n for n in excluded)
    return "어셈블리 전체가 아닌 개별 부품을 지정해 주세요."


def _handle_add(text, proposals, targets):
    m = re.search(r'(.+?)\s*(?:하위에|밑에|아래에)\s*(.+?)\s*(?:추가|넣어)', text)
    if m:
        parent_hint = m.group(1).strip()
        part_name = m.group(2).strip()
    else:
        m2 = re.search(r'(.+?)\s*(?:추가해|넣어줘|추가로|넣어)', text)
        part_name = m2.group(1).strip() if m2 else ""
        parent_hint = ""
    if not part_name:
        return "추가할 부품명을 입력해 주세요.\n\n예) Door 하위에 Gasket 추가해줘"
    target_idx = 0
    if parent_hint and targets:
        for t in targets:
            if t["match_type"] == "assembly":
                target_idx = t["proposal_idx"]
                break
    if target_idx >= len(proposals):
        target_idx = 0
    new_part = {
        "action": "ADD", "part_name": part_name, "part_no": "",
        "display_pno": "(채번 필요)", "lvl": "", "qty": "1",
        "base_type": "", "rsn": "사용자 추가", "tier": "USER",
        "sourcing": "신규", "sourcing_reason": "", "source_doc": "사용자 피드백",
    }
    proposals[target_idx].setdefault("changed_parts", []).append(new_part)
    parent_desc = (proposals[target_idx].get("lvl1") or {}).get("desc") or "(최상위)"
    _log("ADD", [part_name + " -> " + parent_desc])
    return "✅ **" + part_name + "** -> `" + parent_desc + "` 하위에 추가했습니다.\n분류: 🔴 신규(채번 필요)"


def _handle_sourcing(text, proposals, targets):
    t_low = text.lower()
    new_src = ""
    if "공용" in t_low:
        new_src = "공용검토"
    elif "신규" in t_low or "채번" in t_low:
        new_src = "신규"
    if not new_src:
        return "**공용검토** / **신규** 중 어떤 분류로 변경할지 명시해 주세요.\n\n예) 하네스 공용으로 바꿔줘"
    if not targets:
        return "대상 부품을 특정할 수 없어요. 부품명이나 품번을 함께 입력해 주세요."
    changed = []
    for t in targets:
        if t["part_list"] == "_assembly":
            continue
        pt = proposals[t["proposal_idx"]][t["part_list"]][t["part_idx"]]
        old_src = pt.get("sourcing") or "(미분류)"
        pt["sourcing"] = new_src
        if new_src == "공용검토":
            orig_pno = pt.get("part_no") or ""
            if orig_pno and orig_pno not in ("(채번 필요)", ""):
                pt["display_pno"] = orig_pno
            pt["sourcing_reason"] = ""
        elif new_src == "신규":
            orig_pno = pt.get("part_no") or ""
            if orig_pno and orig_pno not in ("(채번 필요)", ""):
                pt["sourcing_reason"] = "참고: " + orig_pno
            pt["display_pno"] = "(채번 필요)"
        name = pt.get("part_name") or pt.get("desc") or "?"
        tag = "🔵" if new_src == "공용검토" else "🔴"
        changed.append(name + ": " + old_src + " -> " + tag + " **" + new_src + "**")
    if changed:
        _log("SOURCING", changed)
        return "✅ **{}건 분류 변경**:\n".format(len(changed)) + "\n".join("- " + c for c in changed)
    return "변경할 부품을 찾지 못했습니다."


def _handle_bulk(text, proposals):
    t_low = text.lower()
    target_action = "ADD"
    if "변경" in t_low or "시방" in t_low:
        target_action = "MODIFY"
    elif "삭제" in t_low:
        target_action = "REMOVE"
    count = 0
    for prop in proposals:
        for lk in ("changed_parts", "indirect_parts"):
            for pt in (prop.get(lk) or []):
                act = (pt.get("action") or "").upper()
                if act in ("CHECK", "PENDING", ""):
                    pt["action"] = target_action
                    count += 1
    label = {"ADD": "추가", "MODIFY": "변경(시방)", "REMOVE": "삭제"}.get(target_action, target_action)
    if count > 0:
        _log("BULK", [str(count) + "건 -> " + label])
        return "✅ 미확정 **" + str(count) + "건**을 모두 **" + label + "**으로 확정했습니다."
    return "미확정 부품이 없습니다. 이미 모두 확정된 상태예요."


def _handle_move(text, proposals, targets):
    return "부품 경로 이동은 현재 테이블에서 직접 수정해 주세요.\n향후 업데이트에서 지원 예정입니다. 🚧"


def _handle_query(text, proposals, targets):
    if not targets:
        return "해당 부품을 찾지 못했습니다. 품번이나 부품명을 정확히 입력해 주세요."
    seen = set()
    lines = []
    for t in targets:
        pt = t["part"]
        name = pt.get("part_name") or pt.get("desc") or "?"
        if name in seen:
            continue
        seen.add(name)
        pno = pt.get("part_no") or pt.get("display_pno") or "(없음)"
        src = pt.get("sourcing") or "(미분류)"
        act = pt.get("action") or "(미정)"
        doc = pt.get("source_doc") or "(없음)"
        rsn = pt.get("rsn") or ""
        info = "**" + name + "**\n"
        info += "  - 품번: `" + pno + "`\n"
        info += "  - 변경유형: " + act + "\n"
        info += "  - 분류: " + src + "\n"
        info += "  - 변경사유: " + (rsn if rsn else "(없음)") + "\n"
        info += "  - 출처: " + doc
        lines.append(info)
    return "\n\n".join(lines)


def _handle_recheck(text, proposals, targets):
    if not targets:
        return (
            "재검토할 부품을 특정할 수 없어요. 부품명이나 품번을 함께 입력해 주세요.\n\n"
            "예) `Door assy의 LED 다시 찾아줘`, `EAD66035701 재검토해줘`"
        )

    target = None
    for t in targets:
        if t["part_list"] != "_assembly":
            target = t
            break
    if target is None:
        return "어셈블리 전체가 아니라 재검토할 개별 부품을 지정해 주세요."

    proposal = proposals[target["proposal_idx"]]
    part = target["part"]
    lvl1 = proposal.get("lvl1") or {}
    req = {
        "proposal_idx": target["proposal_idx"],
        "part_list": target["part_list"],
        "part_idx": target["part_idx"],
        "part_name": part.get("part_name") or part.get("desc") or "",
        "part_no": part.get("part_no") or part.get("display_pno") or "",
        "lvl1_desc": lvl1.get("desc") or "",
        "change_summary": proposal.get("change_summary") or "",
        "user_text": text,
    }
    st.session_state["pending_recheck"] = req
    name = req["part_name"] or req["part_no"] or "대상 부품"
    parent = req["lvl1_desc"] or "해당 Assy"
    return (
        f"🔄 **{parent} > {name}** 재검토 요청을 접수했습니다.\n"
        "기존 변경사유와 Assy 문맥을 유지한 채 다른 근거를 다시 찾아볼게요."
    )


_HELP_MSG = (
    "아래와 같이 입력해 주세요:\n\n"
    "- **제외**: `Shelf 제외해줘`\n"
    "- **분류 변경**: `Camera Module 공용으로 바꿔줘`\n"
    "- **부품 추가**: `Door 하위에 Gasket 추가해줘`\n"
    "- **재검토**: `Door assy의 LED 다시 찾아줘`\n"
    "- **일괄 확정**: `나머지 다 추가로 확정`\n"
    "- **질문**: `EAD66035701 어떤 모델 거야?`"
)


def handle_feedback(user_text):
    ss = st.session_state
    proposals = ss.get("proposals") or []
    if not proposals:
        return "아직 추천 결과가 없습니다. 먼저 추천을 실행해 주세요."

    if ss.get("review_active") and _active_question() is not None:
        ans = _process_active_review_answer(user_text)
        if ans:
            return ans

    intent = classify_intent(user_text)
    targets = find_target_parts(user_text, proposals)
    if intent == INTENT_EXCLUDE:
        return _handle_exclude(user_text, proposals, targets)
    if intent == INTENT_ADD:
        return _handle_add(user_text, proposals, targets)
    if intent == INTENT_SOURCING:
        return _handle_sourcing(user_text, proposals, targets)
    if intent == INTENT_BULK:
        return _handle_bulk(user_text, proposals)
    if intent == INTENT_MOVE:
        return _handle_move(user_text, proposals, targets)
    if intent == INTENT_QUERY:
        return _handle_query(user_text, proposals, targets)
    if intent == INTENT_RECHECK:
        return _handle_recheck(user_text, proposals, targets)
    if targets:
        names_set = set()
        for t in targets[:5]:
            n = t["part"].get("part_name") or t["part"].get("desc") or "?"
            names_set.add(n)
        names = ", ".join(names_set)
        return "'" + names + "' 관련 부품을 찾았어요. 어떤 작업을 원하시나요?\n\n" + _HELP_MSG
    return "무엇을 도와드릴까요?\n\n" + _HELP_MSG


def render_floating_chat():
    ss = st.session_state
    proposals = ss.get("proposals") or []
    if not proposals:
        return

    _ensure_active_review_queue()
    _append_next_question_if_needed()

    # ── 1) FAB 버튼 ──
    fab = st.container()
    with fab:
        if st.button("🤖", key="fb_float_toggle"):
            ss["fb_open"] = not ss.get("fb_open", False)
            st.rerun()

    # 컨테이너 자체를 동그라미로 (float가 확실히 적용됨)
    fab.float(
        "position: fixed; bottom: 32px; right: 32px; left: auto !important; "
        "z-index: 99991; width: 76px; height: 76px; padding: 0 !important; "
        "background: linear-gradient(135deg, #87CEEB 0%, #5BB0CC 100%); "
        "border-radius: 50%; overflow: hidden; "
        "border: 2px solid rgba(255,255,255,0.4); "
        "box-shadow: 0 4px 18px rgba(91,176,204,0.5); "
        "cursor: pointer;"
    )

    # ── 2) 내부 요소 전부 투명화 (99991 = 유니크 마커) ──
    st.markdown("""<style>
    div[style*="99991"] {
        background: linear-gradient(135deg, #87CEEB 0%, #5BB0CC 100%) !important;
        border-radius: 50% !important;
        border: 2px solid rgba(255,255,255,0.4) !important;
        box-shadow: 0 4px 18px rgba(91,176,204,0.5) !important;
        overflow: hidden !important;
    }
    div[style*="99991"]:hover {
        box-shadow: 0 6px 24px rgba(91,176,204,0.7) !important;
    }
    div[style*="99991"] > div,
    div[style*="99991"] > div > div,
    div[style*="99991"] > div > div > div,
    div[style*="99991"] [data-testid] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
        margin: 0 !important;
        gap: 0 !important;
        min-height: 0 !important;
    }
    div[style*="99991"] button {
        all: unset !important;
        width: 76px !important;
        height: 76px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        cursor: pointer !important;
        background: transparent !important;
    }
    div[style*="99991"] button:hover {
        background: rgba(0,0,0,0.08) !important;
    }
    div[style*="99991"] button p {
        font-size: 34px !important;
        margin: 0 !important;
        line-height: 1 !important;
    }
    </style>""", unsafe_allow_html=True)

    if not ss.get("fb_open", False):
        return

    # ── 3) 채팅 패널 ──
    panel = st.container()
    with panel:
        hcol1, hcol2 = st.columns([0.85, 0.15])
        with hcol1:
            st.markdown("#### 🤖 피드백 챗봇")
        with hcol2:
            if st.button("✕", key="fb_close"):
                ss["fb_open"] = False
                st.rerun()
        st.caption("추천 결과를 수정하거나 질문할 수 있어요.")
        st.divider()

        msg_box = st.container(height=300)
        with msg_box:
            msgs = ss.get("fb_messages") or []
            if not msgs:
                st.info(
                    "피드백을 입력해 주세요 💡\n\n"
                    "예시:\n"
                    "- `Shelf 제외해줘`\n"
                    "- `Camera Module 공용으로 변경`\n"
                    "- `Door 하위에 Gasket 추가`\n"
                    "- `Door assy의 LED 다시 찾아줘`\n"
                    "- `나머지 다 추가로 확정`"
                )
            else:
                for msg in msgs:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

        input_key = "fb_input_" + str(ss.get("fb_input_counter", 0))
        icol, bcol = st.columns([0.8, 0.2])
        with icol:
            user_input = st.text_input(
                "피드백", key=input_key,
                placeholder="예: Shelf 제외해줘",
                label_visibility="collapsed",
            )
        with bcol:
            send = st.button("전송", key="fb_send", use_container_width=True)

        if send and user_input:
            ss.setdefault("fb_messages", []).append(
                {"role": "user", "content": user_input}
            )
            response = handle_feedback(user_input)
            ss["fb_messages"].append(
                {"role": "assistant", "content": response}
            )
            ss["fb_input_counter"] = ss.get("fb_input_counter", 0) + 1
            st.rerun()

        history = ss.get("fb_history") or []
        if history:
            with st.expander("📝 수정 이력 (" + str(len(history)) + "건)"):
                for h in reversed(history):
                    st.caption("🕐 " + h["ts"] + " | **" + h["action"] + "**")
                    for item in h.get("items", []):
                        st.text("  → " + str(item))

    panel.float(
        "position: fixed; bottom: 116px; right: 32px; left: auto !important; "
        "z-index: 99990; width: 460px; max-height: 560px; overflow-y: auto; "
        "background: white; border: 1px solid #e0e0e0; border-radius: 16px; "
        "box-shadow: 0 8px 24px rgba(0,0,0,0.18); padding: 20px;"
    )