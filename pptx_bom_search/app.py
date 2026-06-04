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
    "change_points": [],        # STEP 1 결과
    "search_results": [],       # (레거시 호환)
    "bom_explore_results": [],  # STEP 3 결과: [{change_point, bom_parts}]
    "confirmed_parts": [],      # STEP 3 확정 부품 [{...part, change_point_ref}]
    "history_results": [],      # STEP 4 결과: [{confirmed_part, histories}]
    "selections": {},           # STEP 4 선택: {idx: {confirmed_part, selected_history, custom_reason}}
    "master_rows": [],          # STEP 5 결과
    "master_excel": None,       # STEP 5 Excel bytes
    "new_model": "",
    "base_model": "",
    "base_bom_bytes": None,
    "base_bom_name": None,
    "upload_key": 0,
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
    _step_label(3, "변경 부품 확정")
    _step_label(4, "이력 조회 & 후보 선택")
    _step_label(5, "Master BOM 작성")

    st.divider()

    if st.session_state.get("base_bom_name"):
        st.caption(f"📂 Base BOM: {st.session_state.base_bom_name}")

    if st.session_state.step >= 3 and st.session_state.bom_explore_results:
        total = sum(len(r["bom_parts"]) for r in st.session_state.bom_explore_results)
        sel_cnt = sum(1 for v in st.session_state.selections.values() if v)
        st.metric("탐색 부품", total)
        st.metric("이력 선택됨", sel_cnt)

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
            "🔍 변경 부품 탐색 →",
            type="primary",
            use_container_width=True,
            disabled=not st.session_state.get("base_bom_bytes"),
            help="base_bom.xlsx를 업로드해야 활성화됩니다.",
        )
        if search_btn:
            # 편집 내용 반영
            updated_cps = edited_df.to_dict("records")
            for cp_orig, row in zip(st.session_state.change_points, updated_cps):
                cp_orig.update(row)
            for cp in st.session_state.change_points:
                if not cp.get("aliases"):
                    cp["aliases"] = [cp.get("part", "")]

            from runner import step3_explore_bom

            with st.status("🔄 base_bom 탐색 중...", expanded=True) as status:
                st.write(f"{len(st.session_state.change_points)}개 변경점 탐색...")
                explore_results = step3_explore_bom(
                    st.session_state.change_points,
                    st.session_state.base_bom_bytes,
                )
                st.session_state.bom_explore_results = explore_results
                total = sum(len(r["bom_parts"]) for r in explore_results)
                status.update(label=f"✅ 탐색 완료 — 총 {total}개 부품 발견", state="complete")

            st.session_state.step = 3
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 3 : base_bom 탐색 결과 확인 & 변경 부품 확정
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 3:
    st.title("STEP 3 — 변경 부품 확정")
    st.markdown(
        "base_bom에서 찾은 변경 대상 부품 목록입니다.  \n"
        "불필요한 부품은 체크를 해제하고 **변경 부품 확정** 버튼을 눌러주세요."
    )

    explore_results = st.session_state.bom_explore_results
    if not explore_results:
        st.warning("탐색 결과가 없습니다. STEP 2로 돌아가세요.")
        if st.button("← STEP 2로"):
            st.session_state.step = 2
            st.rerun()
        st.stop()

    # 모듈별 탭 분리
    modules = list(dict.fromkeys(
        r["change_point"].get("module", "기타") for r in explore_results
        if r.get("bom_parts")
    ))
    tabs = st.tabs(modules) if len(modules) > 1 else [st.container()]

    confirmed_map: dict[str, bool] = {}  # part_no → checked

    for tab, module in zip(tabs, modules):
        with tab:
            module_results = [
                r for r in explore_results
                if r["change_point"].get("module") == module and r.get("bom_parts")
            ]
            for er_idx, er in enumerate(module_results):
                cp = er["change_point"]
                bom_parts = er["bom_parts"]

                with st.container(border=True):
                    st.markdown(
                        f"**{cp.get('module','')} › {cp.get('part','')}**  \n"
                        f"🔄 {cp.get('change_detail','')}  \n"
                        f"💡 {cp.get('change_reason','')}"
                    )
                    st.caption(f"base_bom 하위 부품 {len(bom_parts)}개")
                    st.divider()

                    for p in bom_parts:
                        pno = p["part_no"]
                        key = f"bom_chk_{er_idx}_{pno}"
                        prev = st.session_state.get(key, True)
                        chk_col, info_col = st.columns([0.05, 0.95])
                        with chk_col:
                            checked = st.checkbox("", value=prev, key=key,
                                                  label_visibility="collapsed")
                        with info_col:
                            st.markdown(
                                f"`{p['lvl']:8}` **{p['description']}**  "
                                f"<span style='color:#888;font-size:0.8em'>"
                                f"part_no={pno} | {p['type']}</span>",
                                unsafe_allow_html=True,
                            )
                        confirmed_map[pno] = checked

    # 빈 결과 처리
    no_parts = [r for r in explore_results if not r.get("bom_parts")]
    if no_parts:
        with st.expander(f"⚠️ base_bom 매칭 실패 {len(no_parts)}건"):
            for r in no_parts:
                cp = r["change_point"]
                st.markdown(f"- {cp.get('module')} › **{cp.get('part')}**")

    st.divider()
    sel_cnt = sum(1 for v in confirmed_map.values() if v)
    c_left, c_right = st.columns([3, 1])
    with c_left:
        st.markdown(f"**{sel_cnt}개** 부품이 선택되어 있습니다.")
    with c_right:
        confirm_btn = st.button(
            "✅ 변경 부품 확정 →",
            disabled=sel_cnt == 0,
            type="primary",
            use_container_width=True,
        )

    if confirm_btn:
        # 확정 부품 조립: 체크된 것만 + change_point_ref 추가
        confirmed: list[dict] = []
        for er in explore_results:
            cp = er["change_point"]
            for p in er.get("bom_parts", []):
                if confirmed_map.get(p["part_no"], True):
                    confirmed.append({**p, "change_point_ref": cp})

        from runner import step4_search_history
        st.session_state.confirmed_parts = confirmed

        with st.status(f"🔄 {len(confirmed)}개 부품 Neo4j 이력 조회 중...", expanded=True) as status:
            history_results = step4_search_history(confirmed)
            st.session_state.history_results = history_results
            status.update(label="✅ 이력 조회 완료", state="complete")

        st.session_state.step = 4
        st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 4 : 이력 조회 결과 확인 & 후보 선택
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 4:
    st.title("STEP 4 — 이력 조회 & 후보 선택")
    st.markdown(
        "각 확정 부품마다 Neo4j에서 찾은 과거 변경 이력입니다.  \n"
        "참고할 이력을 선택하거나, 이력이 없으면 변경사유를 직접 입력하세요."
    )

    history_results = st.session_state.history_results
    if not history_results:
        st.warning("이력 조회 결과가 없습니다. STEP 3으로 돌아가세요.")
        if st.button("← STEP 3으로"):
            st.session_state.step = 3
            st.rerun()
        st.stop()

    selections: dict = st.session_state.selections

    for idx, hr in enumerate(history_results):
        part      = hr["confirmed_part"]
        histories = hr["histories"]
        cp_ref    = part.get("change_point_ref", {})

        with st.container(border=True):
            h_col, m_col = st.columns([3, 1])
            with h_col:
                st.markdown(
                    f"**{part.get('lvl','')}** &nbsp; **{part.get('description','')}**  \n"
                    f"`{part.get('part_no','')}` &nbsp;|&nbsp; {part.get('type','')}"
                )
                st.caption(
                    f"변경점: {cp_ref.get('change_detail','')}  |  "
                    f"출처: {cp_ref.get('module','')} › {cp_ref.get('part','')}"
                )
            with m_col:
                st.caption(f"과거 이력: {len(histories)}건")

            st.divider()

            cur_sel = selections.get(idx, {})

            if not histories:
                st.info("관련 과거 이력이 없습니다. 변경사유를 직접 입력하세요.")
                custom = st.text_input(
                    "변경사유 직접 입력",
                    value=cur_sel.get("custom_reason", ""),
                    key=f"custom_{idx}",
                    placeholder="예) 제품 치수 변경에 따른 Size 변경",
                )
                selections[idx] = {
                    "confirmed_part": part,
                    "selected_history": None,
                    "custom_reason": custom,
                }
                continue

            st.markdown("**📋 과거 이력 — 참고할 항목을 선택하세요 (선택 안 해도 됩니다)**")

            sel_change_id = cur_sel.get("selected_history", {}).get("changeId") if cur_sel.get("selected_history") else None

            new_hist_sel = None
            for h_idx, h in enumerate(histories):
                is_sel = (h.get("changeId") == sel_change_id)
                hchk_col, hcard_col = st.columns([0.05, 0.95])
                with hchk_col:
                    checked = st.checkbox(
                        "", value=is_sel,
                        key=f"hist_{idx}_{h_idx}",
                        label_visibility="collapsed",
                    )
                with hcard_col:
                    with st.expander(
                        f"**{h.get('modelName','?')}** | {h.get('changingPoint','')[:40]}",
                        expanded=is_sel,
                    ):
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**변경점**: {h.get('changingPoint','')}")
                            st.markdown(f"**변경사유**: {h.get('changingReason','')}")
                        with c2:
                            base = h.get("basePartNoRaw") or "-"
                            new  = h.get("newPartNoRaw")  or "-"
                            st.markdown(f"**P/No**: `{base}` → `{new}`")
                            st.markdown(f"**분류**: {h.get('classification','')}")
                        related = h.get("relatedParts") or []
                        if related:
                            st.caption(f"관련 변경 부품: {', '.join(str(r) for r in related[:8])}")
                if checked:
                    new_hist_sel = h

            selections[idx] = {
                "confirmed_part":   part,
                "selected_history": new_hist_sel,
                "custom_reason":    "",
            }

    st.session_state.selections = selections

    st.divider()
    c_left, c_right = st.columns([3, 1])
    with c_left:
        sel_cnt = sum(1 for v in selections.values() if v)
        st.markdown(f"**{len(history_results)}개** 부품 확인 완료")
    with c_right:
        next_btn = st.button(
            "📝 Master 작성 →",
            type="primary",
            use_container_width=True,
        )

    if next_btn:
        st.session_state.step = 5
        st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 5 : Master BOM 작성 & Excel 다운로드
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 5:
    st.title("STEP 5 — Master BOM 작성")
    st.markdown("확정된 부품과 선택한 이력을 바탕으로 Master BOM을 생성합니다.")

    selections = st.session_state.selections
    if not selections:
        st.warning("선택 정보가 없습니다. STEP 4로 돌아가세요.")
        if st.button("← STEP 4로"):
            st.session_state.step = 4
            st.rerun()
        st.stop()

    with st.container(border=True):
        st.markdown("**모델 정보**")
        mc1, mc2 = st.columns(2)
        with mc1:
            base_model = st.text_input(
                "Base Model/Grade",
                value=st.session_state.base_model,
                placeholder="예) WSED7613S / A",
            )
        with mc2:
            new_model = st.text_input(
                "New Model/Grade",
                value=st.session_state.new_model,
                placeholder="예) WS7D4731S / A",
            )
        st.session_state.base_model = base_model
        st.session_state.new_model  = new_model

    st.divider()

    gen_col, _ = st.columns([1, 2])
    with gen_col:
        gen_btn = st.button("📝 Master BOM 생성", type="primary", use_container_width=True)

    if gen_btn:
        from runner import step5_build_master
        confirmed_selections = list(selections.values())
        with st.status("🔄 Master BOM 작성 중...", expanded=True) as status:
            rows, excel_bytes = step5_build_master(
                confirmed_selections,
                new_model=new_model,
                base_model=base_model,
            )
            st.session_state.master_rows  = rows
            st.session_state.master_excel = excel_bytes
            status.update(label=f"✅ Master BOM {len(rows)}행 생성 완료", state="complete")

    if st.session_state.master_rows:
        import pandas as pd
        rows = st.session_state.master_rows
        PREVIEW_COLS = {
            "No": "No", "BOM_Level": "BOM Level", "Part_Type": "Part Type",
            "Base_PNo": "Base P/No", "New_PNo": "New P/No",
            "Class_Desc": "부품명", "Changing_Point": "변경점",
            "Changing_Reason": "변경사유", "Supplier": "양산처",
        }
        df = pd.DataFrame(rows)[list(PREVIEW_COLS.keys())].rename(columns=PREVIEW_COLS)
        st.markdown(f"**Master BOM 미리보기 — {len(rows)}행**")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()
        dl_col, back_col = st.columns([1, 1])
        with dl_col:
            st.download_button(
                label="⬇️ Excel 다운로드 (Master_BOM.xlsx)",
                data=st.session_state.master_excel,
                file_name="Master_BOM.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
            )
        with back_col:
            if st.button("← STEP 4로 돌아가기", use_container_width=True):
                st.session_state.step = 4
                st.rerun()
