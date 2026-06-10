# ============================================================
# chatbot_flow.py — 챗봇 입력 + BOM 업로드 + 디버그 사이드바
# ============================================================
from __future__ import annotations
import re, json
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────
# 0) 개발등급 드롭박스 (사이드바)
# ─────────────────────────────────────────
GRADE_OPTIONS = {
    "S": "S급",
    "A": "A급",
    "B":   "B급",
    "CA":  "Ca급",
    "CB":  "Cb급",
    "CSW": "Csw급",
    "D":   "D급",
}

def render_grade_selector():
    ss = st.session_state
    locked = ss.get("grade_locked", False)
    selected = st.sidebar.selectbox(
        "📋 개발등급 선택",
        options=list(GRADE_OPTIONS.keys()),
        format_func=lambda k: f"{k}  —  {GRADE_OPTIONS[k]}",
        index=0, disabled=locked, key="_grade_select",
    )
    ss["dev_grade"] = selected
    if locked:
        st.sidebar.caption(f"✅ 등급 확정: **{selected}**")
    return selected

# ─────────────────────────────────────────
# 1) 세션 초기화
# ─────────────────────────────────────────
def init_chat():
    ss = st.session_state
    ss.setdefault("chat_step",    "ASK_MODEL")
    ss.setdefault("dev_grade",    "B")
    ss.setdefault("grade_locked", False)
    ss.setdefault("target_model", "")
    ss.setdefault("change_items", [])
    ss.setdefault("ref_model",    "")
    ss.setdefault("messages",     [])
    ss.setdefault("bom_uploaded", False)
    ss.setdefault("base_df",      None)
    ss.setdefault("base_df_raw",  None)
    ss.setdefault("bom_model",    "")

def chat_add(role: str, content: str):
    st.session_state["messages"].append({"role": role, "content": content})

def render_chat():
    for m in st.session_state["messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

# ─────────────────────────────────────────
# 2) 모델코드 파서
# ─────────────────────────────────────────
_MODEL_RE = re.compile(r"(?i)\b[KC]?[WL][A-Z0-9]{7,14}\b")

def extract_model(text: str) -> str:
    for m in _MODEL_RE.finditer(text or ""):
        s = m.group(0).strip().upper()
        if len(s) >= 10 and s[0] in ("K","C") and s[1] in ("W","L"):
            s = s[1:]
        if len(s) > 9:
            s = s[:8] + s[-1]
        if len(s) == 9 and s[0] in ("W","L"):
            return s
    return ""

# ─────────────────────────────────────────
# 3) 봇 인사말 / 스킵 판정
# ─────────────────────────────────────────
_GREET = {
    "ASK_MODEL": (
        "안녕하세요! 개발부품 BOM 초안 생성 Agent입니다 🤖\n\n"
        "**신규 모델명**을 입력해 주세요.\n예) `KWS9D7687M`, `WSED7667M`"
    ),
    "ASK_CHANGES": (
        "감사합니다! 이제 **변경점**을 입력해 주세요.\n"
        "여러 건이면 줄바꿈으로 구분해 주세요.\n\n"
        "예)\n```\n도어에 카메라 추가\n컨트롤러 UI 변경\n```"
    ),
    "ASK_REF_MODEL": (
        "변경점을 확인했습니다!\n\n"
        "혹시 이 변경이 **이미 적용된 참고 모델**을 알고 계신가요?\n"
        "해당 모델의 변경이력을 **우선 참고**하여 추천 정확도를 높입니다.\n\n"
        "- 알고 있다면 모델명 입력 (예: `WSED7665B`)\n"
        "- 모르면 `없음` 또는 `스킵`"
    ),
    "UPLOAD_BOM": (
        "좋습니다! 마지막으로 **Base 모델의 BOM 파일**(.xlsx)을 업로드해 주세요.\n\n"
        "⬇️ 아래 파일 업로드 영역을 이용해 주세요."
    ),
    "DONE": "✅ 모든 입력이 완료되었습니다! 분석을 시작합니다.",
}
_SKIP = {"없음","스킵","skip","없어","모름","패스","pass","no","없습니다"}

def _is_skip(t: str) -> bool:
    return (t or "").strip().lower() in _SKIP or not t.strip()

# ─────────────────────────────────────────
# 4) 챗봇 플로우
# ─────────────────────────────────────────
def chat_flow():
    ss = st.session_state
    step = ss["chat_step"]
    if not ss["messages"]:
        g = ss.get("dev_grade","B")
        chat_add("assistant", f"📋 개발등급: **{g}** (사이드바에서 변경 가능)\n\n" + _GREET["ASK_MODEL"])
        ss["grade_locked"] = True
    render_chat()
    if step == "DONE":
        # ✅ BOM 미리보기 (업로드 완료 후 항상 표시)
        _bdf = ss.get("base_df")
        if isinstance(_bdf, pd.DataFrame) and len(_bdf) > 0:
            st.subheader("📋 BOM 미리보기 (상위 10행)")
            st.dataframe(_bdf.head(10), use_container_width=True)
        return
    if step == "UPLOAD_BOM":
        _handle_bom_upload()
        return
    # ── 스텝별 placeholder ──
    _placeholders = {
        "ASK_MODEL":     "신규 모델명을 입력하세요 (예: KWS9D7687M)",
        "ASK_CHANGES":   "변경점을 입력하세요 (예: 도어에 카메라 추가)",
        "ASK_REF_MODEL": "참고 모델명 또는 '없음' (예: WSED7665B)",
    }
    user = st.chat_input(_placeholders.get(step, "메시지를 입력하세요..."))
    if not user:
        return
    chat_add("user", user)
    with st.chat_message("user"):
        st.markdown(user)
    if   step == "ASK_MODEL":     _handle_model(user)
    elif step == "ASK_CHANGES":   _handle_changes(user)
    elif step == "ASK_REF_MODEL": _handle_ref_model(user)
    st.rerun()

# ─────────────────────────────────────────
# 5) 스텝 핸들러
# ─────────────────────────────────────────
def _handle_model(text):
    ss = st.session_state
    model = extract_model(text) or text.strip().upper()
    ss["target_model"] = model
    chat_add("assistant", f"신규 모델: **{model}**\n\n" + _GREET["ASK_CHANGES"])
    ss["chat_step"] = "ASK_CHANGES"

def _handle_changes(text):
    ss = st.session_state
    items = [l.strip() for l in text.splitlines() if l.strip()]
    if not items:
        items = [x.strip() for x in text.split(",") if x.strip()]
    if not items:
        chat_add("assistant", "변경점을 하나 이상 입력해 주세요.")
        return
    ss["change_items"] = items
    fmt = "\n".join([f"  {i+1}. {it}" for i, it in enumerate(items)])
    chat_add("assistant", f"변경점 **{len(items)}건** 확인:\n{fmt}\n\n" + _GREET["ASK_REF_MODEL"])
    ss["chat_step"] = "ASK_REF_MODEL"

def _handle_ref_model(text):
    ss = st.session_state
    if _is_skip(text):
        ss["ref_model"] = ""
        msg = "참고 모델 없이 진행합니다. 전체 DB에서 유사 이력을 검색할게요.\n\n"
    else:
        ref = extract_model(text) or text.strip().upper()
        ss["ref_model"] = ref
        msg = f"참고 모델: **{ref}** — 이 모델의 이력을 우선 검색합니다.\n\n"
    chat_add("assistant", msg + _GREET["UPLOAD_BOM"])
    ss["chat_step"] = "UPLOAD_BOM"

def _handle_bom_upload():
    ss = st.session_state
    uploaded = st.file_uploader("Base BOM 엑셀 (.xlsx)", type=["xlsx","xls"], key="bom_uploader")
    if uploaded is not None and not ss.get("bom_uploaded"):
        with st.spinner("BOM을 분석 중입니다..."):
            uploaded.seek(0)
            df_raw = pd.read_excel(uploaded, header=0, dtype=str)
            model_bom = _detect_model_from_bom(df_raw)
            if model_bom:
                st.info(f"📦 **{model_bom}** 의 BOM을 인식했습니다.")
            else:
                st.info("📦 BOM 파일을 인식했습니다.")
            df_filt = _filter_bom_base_rows(df_raw)
            ss["base_df"] = df_filt
            ss["base_df_raw"] = df_raw
            ss["bom_model"] = model_bom
            ss["bom_uploaded"] = True

            # ── 추가: DONE으로 전환 + 메시지 + rerun ──
            chat_add("assistant", _GREET["DONE"])
            ss["chat_step"] = "DONE"
            st.rerun()

            # ✅ BOM 미리보기 (상위 10행)
            st.subheader(f"📋 BOM 미리보기 (상위 10행)")
            st.dataframe(df_filt.head(10), use_container_width=True)
        chat_add("assistant",
            f"✅ BOM 업로드 완료!\n"
            f"- 전체 행: {len(df_raw)}건\n"
            f"- Base(B) 행: {len(df_filt)}건 (`*S*`/`*Q*` 제외)\n\n"
            + _GREET["DONE"])
        ss["chat_step"] = "DONE"
        st.rerun()

# ─────────────────────────────────────────
# 6) BOM 전처리 헬퍼
# ─────────────────────────────────────────
def _detect_model_from_bom(df):
    lc = _find_col(df, ["Lvl","LVL","Level","LEVEL"])
    if lc:
        for _, row in df.iterrows():
            if str(row.get(lc,"")).strip() == "0":
                for col in df.columns:
                    m = extract_model(str(row.get(col,"")))
                    if m: return m
    if len(df) > 0:
        for col in df.columns:
            m = extract_model(str(df.iloc[0].get(col,"")))
            if m: return m
    return ""

def _filter_bom_base_rows(df):
    if df is None or len(df) == 0: return df
    lc = _find_col(df, ["Lvl","LVL","Level","LEVEL"])
    if not lc: return df
    v = df[lc].astype(str).str.strip().str.upper()
    drop = v.str.startswith("*S*") | v.str.startswith("*Q*")
    return df[~drop].reset_index(drop=True)

def _find_col(df, cands):
    norm = {str(c).strip().upper(): str(c) for c in df.columns}
    for w in cands:
        if w.upper() in norm: return norm[w.upper()]
    return ""

# ─────────────────────────────────────────
# 헬퍼: 세션에서 안전하게 값 꺼내기 (DataFrame이면 or/if 터짐 방지)
# ─────────────────────────────────────────
def _safe(val, default=""):
    """세션값이 DataFrame이면 if/or 판정 불가 → 타입 체크로 우회"""
    if val is None:
        return default
    if isinstance(val, pd.DataFrame):
        return default  # DataFrame은 기본값으로 대체
    return val

def _safe_len(val):
    """DataFrame이면 len(), 아니면 0"""
    if val is None:
        return 0
    if isinstance(val, (pd.DataFrame, list)):
        return len(val)
    return 0

# ─────────────────────────────────────────
# 7) 상태 요약 패널
# ─────────────────────────────────────────
def render_status_panel():
    ss = st.session_state
    st.subheader("📌 현재 상태")
    st.write("**개발등급:**", _safe(ss.get("dev_grade"), "(미선택)"))
    st.write("**신규모델:**", _safe(ss.get("target_model"), "(미입력)"))

    changes = ss.get("change_items")
    if isinstance(changes, list) and len(changes) > 0:
        st.write("**변경점:**")
        for i, c in enumerate(changes, 1):
            st.write(f"  {i}. {c}")
    else:
        st.write("**변경점:** (미입력)")

    ref = _safe(ss.get("ref_model"), "")
    st.write(f"**참고모델:** {ref}" if ref else "**참고모델:** (없음)")

    bom_flag = ss.get("bom_uploaded")
    if bom_flag is True:
        bm = _safe(ss.get("bom_model"), "")
        nr = _safe_len(ss.get("base_df"))
        st.success(f"📦 BOM 업로드 완료 ({bm}, {nr}건)")
    else:
        st.write("**BOM:** (미업로드)")

# ─────────────────────────────────────────
# 8) 디버그 사이드바 (C안: 토글)
# ─────────────────────────────────────────
def render_debug_sidebar():
    ss = st.session_state
    show = st.sidebar.toggle("🧪 디버그 확인", value=False, key="_debug_toggle")
    if not show:
        return
    st.sidebar.divider()
    st.sidebar.markdown("### 🔍 디버그 정보")
    st.sidebar.json({
        "chat_step":       _safe(ss.get("chat_step"), ""),
        "dev_grade":       _safe(ss.get("dev_grade"), ""),
        "target_model":    _safe(ss.get("target_model"), ""),
        "ref_model":       _safe(ss.get("ref_model"), ""),
        "change_items":    _safe(ss.get("change_items"), []),
        "bom_model":       str(_safe(ss.get("bom_model"), "")),
        "bom_uploaded":    bool(ss.get("bom_uploaded") is True),
        "bom_rows_raw":    _safe_len(ss.get("base_df_raw")),
        "bom_rows_filtered": _safe_len(ss.get("base_df")),
    })
    st.sidebar.markdown("**검색 결과:**")
    st.sidebar.write(f"primary_docs: {_safe_len(ss.get('primary_docs'))}")
    st.sidebar.write(f"secondary_docs: {_safe_len(ss.get('secondary_docs'))}")
    st.sidebar.write(f"proposals: {_safe_len(ss.get('proposals'))}")
    props = ss.get("proposals")
    if isinstance(props, list) and len(props) > 0:
        st.sidebar.markdown("**Proposals 상세:**")
        for p in props:
            st.sidebar.json(p)
    errs = ss.get("debug_err")
    if isinstance(errs, dict) and len(errs) > 0:
        st.sidebar.markdown("**⚠️ 에러:**")
        st.sidebar.json(errs)                
    # Chroma 컬렉션 카운트
    try:
        from rag_client import get_collection
        legacy_cnt = get_collection().count()
        st.sidebar.write(f"- Legacy Chroma count: {legacy_cnt}")
    except Exception as e:
        st.sidebar.write(f"- Legacy Chroma error: {e}")
    struct_cnt = st.session_state.get("_chroma_struct_count", 0)
    st.sidebar.write(f"- Structured Chroma count: {struct_cnt}")