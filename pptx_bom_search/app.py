from __future__ import annotations

"""BOM 심의회 PPTX 분석 — 4단계 데모 UI

  STEP 1: PPTX 업로드 & 파싱
  STEP 2: 파싱 결과(change_points) 확인 / 편집
  STEP 3: 유사 이력 후보 검색 & 사용자 선정
  STEP 4: Master BOM 작성 & Excel 다운로드
"""

import sys
from pathlib import Path

# pptx_bom_search 디렉토리를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

st.set_page_config(
    page_title="BOM 변경점 이력 검색",
    page_icon="🔍",
    layout="wide",
)

# ── 전역 CSS ────────────────────────────────────────────────
st.markdown("""
<style>
/* 스텝 헤더 */
.step-header {
    font-size: 1.3rem;
    font-weight: 700;
    padding: 6px 0 2px 0;
    margin-bottom: 4px;
}
.step-inactive { color: #aaa; }
.step-active   { color: #1f77b4; }
.step-done     { color: #28a745; }

/* 후보 카드 */
.cand-card {
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 6px;
    background: #fafafa;
}
.cand-card.sel {
    border-color: #1f77b4;
    background: #e8f4fd;
}
.badge {
    display: inline-block;
    border-radius: 10px;
    padding: 2px 9px;
    font-size: 0.76em;
    font-weight: 600;
    margin-right: 5px;
}
.b-rank  { background:#1f77b4; color:#fff; }
.b-lv    { background:#555;    color:#fff; }
.b-new   { background:#d4edda; color:#155724; }
.b-chg   { background:#fff3cd; color:#856404; }
.b-del   { background:#f8d7da; color:#721c24; }
.b-etc   { background:#e2e3e5; color:#383d41; }
</style>
""", unsafe_allow_html=True)


# ── 세션 상태 초기화 ─────────────────────────────────────────
_DEFAULTS: dict = {
    "step": 1,
    "tmp_paths": [],
    "change_points": [],
    "search_results": [],
    "selections": {},       # {sr_idx: [candidate_dict, ...]}
    "master_rows": [],      # STEP 4: 조립된 Master BOM 행
    "master_excel": None,   # STEP 4: Excel bytes
    "new_model": "",        # STEP 4: New Model/Grade
    "base_model": "",       # STEP 4: Base Model/Grade
    "base_bom_bytes": None, # STEP 1: base_bom.xlsx bytes
    "base_bom_name": None,  # STEP 1: base_bom.xlsx 파일명
    "upload_key": 0,        # 업로더 리셋용
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── 유틸 ────────────────────────────────────────────────────
def _cls_badge(cls: str) -> str:
    mapping = {"New": "b-new", "Change": "b-chg", "Delete": "b-del"}
    css = mapping.get(cls, "b-etc")
    return f'<span class="badge {css}">{cls or "?"}</span>'


def _render_candidate(c: dict, checked: bool, key: str) -> bool:
    rank = c.get("rank", "?")
    level = c.get("level", "")
    cls = c.get("classification") or ""
    part = c.get("partName", "")
    model = c.get("modelName", "")

    badge_html = (
        f'<span class="badge b-rank">{rank}위</span>'
        f'<span class="badge b-lv">Lv.{level}</span>'
        f'{_cls_badge(cls)}'
        f'<strong>{part}</strong>'
        f'&nbsp;|&nbsp;{model}'
    )
    card_cls = "cand-card sel" if checked else "cand-card"
    st.markdown(f'<div class="{card_cls}">{badge_html}</div>', unsafe_allow_html=True)
    with st.expander("상세", expanded=checked):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**변경점** : {c.get('changingPoint','')}")
            st.markdown(f"**변경사유**: {c.get('changingReason','')}")
        with c2:
            base = c.get("basePartNoRaw") or "-"
            new  = c.get("newPartNoRaw")  or "-"
            st.markdown(f"**P/No** : `{base}` → `{new}`")
            if c.get("partType"):
                st.markdown(f"**PartType**: {c.get('partType')}")
        sim = c.get("similarity_reason", "")
        if sim:
            st.caption(f"📌 {sim}")
        elif c.get("caseSummary"):
            st.caption(f"사례 요약: {c.get('caseSummary')}")

    return st.checkbox("이 후보 선택", value=checked, key=key)


def _step_label(n: int, title: str) -> None:
    cur = st.session_state.step
    css = "step-active" if cur == n else ("step-done" if cur > n else "step-inactive")
    icon = "✅" if cur > n else ("▶" if cur == n else "○")
    st.markdown(f'<div class="step-header {css}">{icon} STEP {n}. {title}</div>',
                unsafe_allow_html=True)


def _reset():
    for k, v in _DEFAULTS.items():
        st.session_state[k] = v
    st.session_state["upload_key"] += 1


# ════════════════════════════════════════════════════════════
# 사이드바 — 스텝 네비게이터 & 리셋
# ════════════════════════════════════════════════════════════
with st.sidebar:
    st.title("🔍 BOM 이력 검색")
    st.markdown("심의회 PPTX를 업로드하면 과거 유사 변경 이력을 찾아드립니다.")
    st.divider()

    _step_label(1, "PPTX 업로드 & 파싱")
    _step_label(2, "파싱 결과 확인")
    _step_label(3, "후보 선정")
    _step_label(4, "Master BOM 작성")

    st.divider()

    if st.session_state.get("base_bom_name"):
        st.caption(f"📂 Base BOM: {st.session_state.base_bom_name}")

    if st.session_state.step >= 3 and st.session_state.search_results:
        total = len(st.session_state.search_results)
        sel_cnt = sum(1 for v in st.session_state.selections.values() if v)
        st.metric("분석 항목", total)
        st.metric("후보 선택됨", sel_cnt)

    if st.session_state.step > 1:
        if st.button("🔄 처음부터 다시", use_container_width=True):
            _reset()
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 1 : PPTX 업로드 & 파싱
# ════════════════════════════════════════════════════════════
if st.session_state.step == 1:
    st.title("STEP 1 — PPTX 업로드 & 파싱")
    st.markdown(
        "개발 유형 및 등급 확정 심의회 PPTX 파일을 업로드하세요.  \n"
        "업로드 후 **파싱 시작** 버튼을 누르면 GPT-4o가 변경점 목록을 추출합니다."
    )

    col_pptx, col_bom = st.columns(2)

    with col_pptx:
        uploaded = st.file_uploader(
            "PPTX 파일 선택 (복수 가능)",
            type=["pptx"],
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.upload_key}",
        )
        if uploaded:
            st.success(f"{len(uploaded)}개 파일: {', '.join(f.name for f in uploaded)}")

    with col_bom:
        base_bom_file = st.file_uploader(
            "Base BOM 파일 선택 (선택)",
            type=["xlsx"],
            accept_multiple_files=False,
            key=f"bom_uploader_{st.session_state.upload_key}",
        )
        if base_bom_file:
            st.success(f"Base BOM: {base_bom_file.name}")

    parse_btn = st.button(
        "🚀 파싱 시작",
        disabled=not uploaded,
        type="primary",
        use_container_width=True,
    )

    if parse_btn and uploaded:
        from runner import save_uploads, step1_parse

        with st.status("🔄 PPTX 파싱 중...", expanded=True) as status:
            st.write("임시 파일 저장 중...")
            tmp_paths = save_uploads(uploaded)
            st.session_state.tmp_paths = tmp_paths

            # base_bom 파일 bytes 저장
            if base_bom_file:
                st.session_state.base_bom_bytes = base_bom_file.read()
                st.session_state.base_bom_name  = base_bom_file.name
            else:
                st.session_state.base_bom_bytes = None
                st.session_state.base_bom_name  = None

            st.write(f"GPT-4o로 변경점 추출 중 ({len(tmp_paths)}개 파일)...")
            change_points = step1_parse(tmp_paths)
            st.session_state.change_points = change_points

            status.update(label=f"✅ 파싱 완료 — {len(change_points)}개 변경점 추출", state="complete")

        st.session_state.step = 2
        st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 2 : 파싱 결과 확인 / 편집
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 2:
    st.title("STEP 2 — 파싱 결과 확인")
    st.markdown(
        "GPT-4o가 추출한 변경점 목록입니다.  \n"
        "불필요한 항목을 삭제하거나 내용을 수정한 뒤 **이력 검색 시작** 버튼을 눌러주세요."
    )

    cps = st.session_state.change_points
    if not cps:
        st.warning("추출된 변경점이 없습니다. STEP 1로 돌아가 다시 파싱하세요.")
        if st.button("← STEP 1으로"):
            st.session_state.step = 1
            st.rerun()
        st.stop()

    st.info(f"총 **{len(cps)}개** 변경점 추출됨")

    # 편집 가능한 데이터프레임
    import pandas as pd
    DISPLAY_COLS = ["source_pptx", "module", "part",
                    "change_detail", "change_reason", "discipline", "type",
                    "concern", "evidence_slide"]
    df = pd.DataFrame(cps)
    for col in DISPLAY_COLS:
        if col not in df.columns:
            df[col] = ""
    df = df[DISPLAY_COLS]

    edited_df = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "source_pptx":    st.column_config.TextColumn("파일명", width="medium"),
            "module":         st.column_config.TextColumn("모듈", width="small"),
            "part":           st.column_config.TextColumn("부품명", width="medium"),
            "change_detail":  st.column_config.TextColumn("변경내역", width="large"),
            "change_reason":  st.column_config.TextColumn("변경사유", width="large"),
            "discipline":     st.column_config.SelectboxColumn(
                                  "분야", options=["기구", "제어", "ThinQ", "기타"], width="small"),
            "type":           st.column_config.SelectboxColumn(
                                  "유형", options=["Changing", "NEW", "삭제"], width="small"),
            "concern":        st.column_config.TextColumn("걱정점", width="medium"),
            "evidence_slide": st.column_config.NumberColumn("슬라이드#", width="small"),
        },
        key="cp_editor",
    )

    col_norm, col_search = st.columns([1, 2])

    with col_norm:
        norm_btn = st.button(
            "🔤 부품명 재정규화",
            help="편집된 부품명 기준으로 canonical_part / aliases를 다시 생성합니다.",
            use_container_width=True,
        )
        if norm_btn:
            from runner import step2_normalize
            updated_cps = edited_df.to_dict("records")
            for cp_orig, row in zip(st.session_state.change_points, updated_cps):
                cp_orig.update(row)
            with st.spinner("부품명 정규화 중..."):
                normalized = step2_normalize(st.session_state.change_points)
            st.session_state.change_points = normalized
            st.success("정규화 완료!")
            st.rerun()

    with col_search:
        search_btn = st.button(
            "🔍 이력 검색 시작 →",
            type="primary",
            use_container_width=True,
        )
        if search_btn:
            # 편집 내용 반영
            updated_cps = edited_df.to_dict("records")
            for cp_orig, row in zip(st.session_state.change_points, updated_cps):
                cp_orig.update(row)
            # aliases 없는 항목 보정
            for cp in st.session_state.change_points:
                if not cp.get("aliases"):
                    cp["aliases"] = [cp.get("part", "")]

            from runner import step3_search_and_rank

            with st.status("🔄 Neo4j 검색 & LLM 유사도 판단 중...", expanded=True) as status:
                st.write(f"{len(st.session_state.change_points)}개 항목 병렬 검색...")
                results = step3_search_and_rank(st.session_state.change_points)
                st.session_state.search_results = results
                status.update(
                    label=f"✅ 검색 완료 — {len(results)}개 결과",
                    state="complete",
                )

            st.session_state.step = 3
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 3 : 유사 이력 후보 선정
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 3:
    st.title("STEP 3 — 유사 이력 후보 선정")
    st.markdown(
        "각 변경점마다 LLM이 추천한 유사 이력 후보가 표시됩니다.  \n"
        "참고할 항목을 체크박스로 선택하세요."
    )

    results = st.session_state.search_results
    if not results:
        st.warning("검색 결과가 없습니다. STEP 2로 돌아가세요.")
        if st.button("← STEP 2로"):
            st.session_state.step = 2
            st.rerun()
        st.stop()

    # PPTX 파일별 탭 분리
    pptx_names = list(dict.fromkeys(
        sr["change_point"].get("source_pptx", "기타") for sr in results
    ))
    tabs = st.tabs([f"📄 {n}" for n in pptx_names]) if len(pptx_names) > 1 else [st.container()]

    for tab, pptx_name in zip(tabs, pptx_names):
        with tab:
            file_items = [
                (i, sr) for i, sr in enumerate(results)
                if sr["change_point"].get("source_pptx") == pptx_name
            ]

            for sr_idx, sr in file_items:
                cp = sr["change_point"]
                ranked = sr.get("ranked", [])
                pool_size = len(sr.get("candidates", []))

                with st.container(border=True):
                    # 변경점 헤더
                    h_col, m_col = st.columns([3, 1])
                    with h_col:
                        st.markdown(
                            f"### {cp.get('module','')} &nbsp;›&nbsp; **{cp.get('part','')}**"
                        )
                        st.markdown(
                            f"🔄 **변경내역** : {cp.get('change_detail','')}  \n"
                            f"💡 **변경사유** : {cp.get('change_reason','')}"
                        )
                    with m_col:
                        st.caption(f"Neo4j 후보풀: {pool_size}건")
                        st.caption(f"LLM 추천: {len(ranked)}건")
                        retry = sr.get("retry_count", 0)
                        if retry:
                            st.caption(f"검색 시도: {retry+1}회")

                    st.divider()

                    candidates = sr.get("candidates", [])
                    using_fallback = not ranked and bool(candidates)

                    if not ranked and not candidates:
                        st.warning("유사한 과거 이력을 찾지 못했습니다.")
                        continue

                    if using_fallback:
                        st.warning(
                            f"LLM이 유사 후보를 선정하지 못했습니다. "
                            f"Neo4j 후보풀 {len(candidates)}건을 직접 확인하세요."
                        )
                        display_list = candidates
                        st.markdown("**📋 Neo4j 원본 후보풀 — 직접 선택하세요**")
                    else:
                        display_list = ranked
                        st.markdown("**📋 유사 이력 후보 — 참고할 항목을 선택하세요**")

                    cur_sel = st.session_state.selections.get(sr_idx, [])
                    sel_ids = {c.get("changeId") for c in cur_sel}

                    new_sel = []
                    for idx_c, c in enumerate(display_list):
                        # 폴백 후보는 rank 필드가 없으므로 순번 부여
                        if using_fallback and "rank" not in c:
                            c = {**c, "rank": idx_c + 1}
                        cid = c.get("changeId", "")
                        is_sel = cid in sel_ids
                        chk_col, card_col = st.columns([0.05, 0.95])
                        with chk_col:
                            checked = st.checkbox(
                                "",
                                value=is_sel,
                                key=f"chk_{sr_idx}_{cid}_{idx_c}",
                                label_visibility="collapsed",
                            )
                        with card_col:
                            _render_candidate(c, checked, key=f"sel_{sr_idx}_{cid}_{idx_c}")
                        if checked:
                            new_sel.append(c)

                    st.session_state.selections[sr_idx] = new_sel
                    if new_sel:
                        st.success(f"✅ {len(new_sel)}건 선택됨")

    # ── 하단 확정 버튼 ────────────────────────────────────────
    st.divider()
    total_sel = sum(1 for v in st.session_state.selections.values() if v)
    c_left, c_right = st.columns([3, 1])
    with c_left:
        st.markdown(f"현재 **{total_sel}개** 항목에 후보가 선택되어 있습니다.")
    with c_right:
        confirm_btn = st.button(
            "✅ 선택 확정",
            disabled=total_sel == 0,
            type="primary",
            use_container_width=True,
        )

    if confirm_btn:
        st.session_state.step = 4
        st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 4 : Master BOM 작성 & Excel 다운로드
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 4:
    st.title("STEP 4 — Master BOM 작성")
    st.markdown(
        "선정된 후보를 바탕으로 Master BOM을 작성합니다.  \n"
        "변경사유는 과거 이력 문체를 참고해 GPT-4o가 자동 생성합니다."
    )

    results = st.session_state.search_results
    selections = st.session_state.selections

    # ── 모델명 입력 ───────────────────────────────────────────
    with st.container(border=True):
        st.markdown("**모델 정보**")
        mc1, mc2 = st.columns(2)
        with mc1:
            base_model = st.text_input(
                "Base Model/Grade",
                value=st.session_state.base_model,
                placeholder="예) WSED7612B.AKSQEUR",
            )
        with mc2:
            new_model = st.text_input(
                "New Model/Grade",
                value=st.session_state.new_model,
                placeholder="예) WSED7612B.AKSZLSL / D",
            )
        st.session_state.base_model = base_model
        st.session_state.new_model  = new_model

    st.divider()

    # ── Master 생성 버튼 ──────────────────────────────────────
    gen_col, _ = st.columns([1, 2])
    with gen_col:
        gen_btn = st.button(
            "📝 Master BOM 생성",
            type="primary",
            use_container_width=True,
            disabled=not any(v for v in selections.values()),
        )

    if gen_btn:
        from runner import step4_build_master
        with st.status("🔄 Master BOM 작성 중... (GPT-4o 변경사유 생성)", expanded=True) as status:
            rows, excel_bytes = step4_build_master(
                results, selections,
                new_model=new_model,
                base_model=base_model,
            )
            st.session_state.master_rows  = rows
            st.session_state.master_excel = excel_bytes
            status.update(label=f"✅ Master BOM {len(rows)}행 생성 완료", state="complete")

    # ── 미리보기 & 다운로드 ───────────────────────────────────
    if st.session_state.master_rows:
        import pandas as pd

        rows = st.session_state.master_rows

        # 표시용 DataFrame
        PREVIEW_COLS = {
            "No":               "No",
            "BOM_Level":        "BOM Level",
            "Part_Type":        "Part Type",
            "Base_PNo":         "Base P/No",
            "New_PNo":          "New P/No",
            "Class_Desc":       "부품명",
            "Qty_Base":         "Qty Base",
            "Qty_New":          "Qty New",
            "Changing_Point":   "변경점",
            "Changing_Reason":  "변경사유",
            "Supplier":         "양산처",
            "Classification":   "신규/변경",
        }
        df = pd.DataFrame(rows)[list(PREVIEW_COLS.keys())].rename(columns=PREVIEW_COLS)

        st.markdown(f"**Master BOM 미리보기 — {len(rows)}행**")
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "No":       st.column_config.NumberColumn(width="small"),
                "BOM Level":st.column_config.TextColumn(width="small"),
                "Part Type":st.column_config.TextColumn(width="medium"),
                "Base P/No":st.column_config.TextColumn(width="medium"),
                "New P/No": st.column_config.TextColumn(width="medium"),
                "부품명":   st.column_config.TextColumn(width="large"),
                "변경점":   st.column_config.TextColumn(width="large"),
                "변경사유": st.column_config.TextColumn(width="large"),
            },
        )

        st.divider()
        dl_col, back_col = st.columns([1, 1])
        with dl_col:
            st.download_button(
                label="⬇️ Excel 다운로드 (Master BOM.xlsx)",
                data=st.session_state.master_excel,
                file_name="Master_BOM.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
            )
        with back_col:
            if st.button("← 후보 선정으로 돌아가기", use_container_width=True):
                st.session_state.step = 3
                st.rerun()
