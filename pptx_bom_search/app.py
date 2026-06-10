from __future__ import annotations

"""BOM 심의회 PPTX 분석 — 5단계 UI

  STEP 1: PPTX + Base BOM 업로드 & 파싱
  STEP 2: 파싱 결과(change_points) 확인 / 편집
  STEP 3: Base BOM 매칭 결과 확인 / 수정
  STEP 4: 과거 이력 후보 조회 + 연동 부품 선택
  STEP 5: 변경부품리스트.xlsx 생성 & 다운로드
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

st.set_page_config(
    page_title="BOM 변경점 분석",
    page_icon="🔍",
    layout="wide",
)

st.markdown("""
<style>
.step-header { font-size: 1.3rem; font-weight: 700; padding: 6px 0 2px 0; margin-bottom: 4px; }
.step-inactive { color: #aaa; }
.step-active   { color: #1f77b4; }
.step-done     { color: #28a745; }

.match-card {
    border: 1px solid #ddd; border-radius: 8px;
    padding: 10px 14px; margin-bottom: 8px; background: #fafafa;
}
.match-card.matched  { border-color: #28a745; background: #f0fff4; }
.match-card.unmatched { border-color: #dc3545; background: #fff5f5; }
.match-card.new-part { border-color: #ffc107; background: #fffdf0; }

.badge {
    display: inline-block; border-radius: 10px;
    padding: 2px 9px; font-size: 0.76em; font-weight: 600; margin-right: 5px;
}
.b-high   { background: #d4edda; color: #155724; }
.b-medium { background: #fff3cd; color: #856404; }
.b-low    { background: #f8d7da; color: #721c24; }
.b-new    { background: #fff3cd; color: #856404; }
.b-change { background: #d4edda; color: #155724; }
.b-delete { background: #f8d7da; color: #721c24; }
</style>
""", unsafe_allow_html=True)

# ── 세션 상태 초기화 ─────────────────────────────────────────
_DEFAULTS: dict = {
    "step": 1,
    "tmp_pptx_paths": [],
    "base_bom_path": "",
    "base_bom_name": "",
    "change_points": [],
    "upload_key": 0,
    "history_searched": False,
    "selected_linked": [],   # STEP 4에서 선택된 연동 부품 목록
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _reset():
    for k, v in _DEFAULTS.items():
        st.session_state[k] = v
    st.session_state["upload_key"] += 1


def _step_label(n: int, title: str) -> None:
    cur = st.session_state.step
    css  = "step-active" if cur == n else ("step-done" if cur > n else "step-inactive")
    icon = "✅" if cur > n else ("▶" if cur == n else "○")
    st.markdown(f'<div class="step-header {css}">{icon} STEP {n}. {title}</div>',
                unsafe_allow_html=True)


def _type_badge(t: str) -> str:
    mapping = {"Changing": "b-change", "NEW": "b-new", "삭제": "b-delete"}
    css = mapping.get(t, "b-new")
    return f'<span class="badge {css}">{t or "?"}</span>'


def _conf_badge(c: str) -> str:
    mapping = {"high": "b-high", "medium": "b-medium", "low": "b-low"}
    css = mapping.get(c, "b-medium")
    label = {"high": "높음", "medium": "보통", "low": "낮음"}.get(c, c)
    return f'<span class="badge {css}">신뢰도 {label}</span>'


# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.title("🔍 BOM 변경점 분석")
    st.markdown("심의회 PPTX + Base BOM을 업로드하면\n변경점을 자동으로 분석합니다.")
    st.divider()

    _step_label(1, "업로드 & 파싱")
    _step_label(2, "파싱 결과 확인")
    _step_label(3, "Base BOM 매칭 확인")
    _step_label(4, "과거 이력 후보 조회")
    _step_label(5, "변경부품리스트 생성")

    st.divider()

    if st.session_state.base_bom_name:
        st.caption(f"📂 Base BOM: {st.session_state.base_bom_name}")
    if st.session_state.change_points:
        cps = st.session_state.change_points
        st.metric("변경점 수", len(cps))
        if st.session_state.step >= 3:
            matched = sum(1 for cp in cps if cp.get("bom_matched"))
            st.metric("BOM 매칭", f"{matched}/{len(cps)}")

    if st.session_state.step > 1:
        if st.button("🔄 처음부터 다시", use_container_width=True):
            _reset()
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 1 : 업로드 & 파싱
# ════════════════════════════════════════════════════════════
if st.session_state.step == 1:
    st.title("STEP 1 — PPTX + Base BOM 업로드 & 파싱")
    st.markdown(
        "개발 유형 및 등급 확정 심의회 PPTX와 Base BOM 파일을 업로드하세요.  \n"
        "**파싱 시작** 버튼을 누르면 GPT-4o가 변경점을 추출합니다."
    )

    col_pptx, col_bom = st.columns(2)

    with col_pptx:
        uploaded_pptx = st.file_uploader(
            "PPTX 파일 선택 (복수 가능)",
            type=["pptx"],
            accept_multiple_files=True,
            key=f"uploader_pptx_{st.session_state.upload_key}",
        )
        if uploaded_pptx:
            st.success(f"{len(uploaded_pptx)}개: {', '.join(f.name for f in uploaded_pptx)}")

    with col_bom:
        uploaded_bom = st.file_uploader(
            "Base BOM 파일 선택 (xlsx)",
            type=["xlsx"],
            accept_multiple_files=False,
            key=f"uploader_bom_{st.session_state.upload_key}",
        )
        if uploaded_bom:
            st.success(f"Base BOM: {uploaded_bom.name}")

    parse_btn = st.button(
        "🚀 파싱 시작",
        disabled=not uploaded_pptx,
        type="primary",
        use_container_width=True,
    )

    if parse_btn and uploaded_pptx:
        from runner import save_uploads, save_upload, step1_parse

        with st.status("🔄 PPTX 파싱 중...", expanded=True) as status:
            st.write("임시 파일 저장 중...")
            tmp_pptx_paths = save_uploads(uploaded_pptx)
            st.session_state.tmp_pptx_paths = tmp_pptx_paths

            if uploaded_bom:
                bom_path = save_upload(uploaded_bom)
                st.session_state.base_bom_path = bom_path
                st.session_state.base_bom_name = uploaded_bom.name
            else:
                st.session_state.base_bom_path = ""
                st.session_state.base_bom_name = ""

            st.write(f"GPT-4o로 변경점 추출 중 ({len(tmp_pptx_paths)}개 파일)...")
            change_points = step1_parse(tmp_pptx_paths)
            st.session_state.change_points = change_points

            status.update(
                label=f"✅ 파싱 완료 — {len(change_points)}개 변경점 추출",
                state="complete",
            )

        st.session_state.step = 2
        st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 2 : 파싱 결과 확인 / 편집
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 2:
    st.title("STEP 2 — 파싱 결과 확인")
    st.markdown(
        "GPT-4o가 추출한 변경점 목록입니다.  \n"
        "불필요한 항목을 삭제하거나 내용을 수정한 뒤 **다음** 버튼을 눌러주세요."
    )

    cps = st.session_state.change_points
    if not cps:
        st.warning("추출된 변경점이 없습니다. STEP 1로 돌아가 다시 파싱하세요.")
        if st.button("← STEP 1으로"):
            st.session_state.step = 1
            st.rerun()
        st.stop()

    st.info(f"총 **{len(cps)}개** 변경점 추출됨")

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
        width="stretch",
        num_rows="dynamic",
        column_config={
            "source_pptx":    st.column_config.TextColumn("파일명",   width="medium"),
            "module":         st.column_config.TextColumn("모듈",     width="small"),
            "part":           st.column_config.TextColumn("부품명",   width="medium"),
            "change_detail":  st.column_config.TextColumn("변경내역", width="large"),
            "change_reason":  st.column_config.TextColumn("변경사유", width="large"),
            "discipline":     st.column_config.SelectboxColumn(
                                  "분야", options=["기구", "제어", "ThinQ", "기타"], width="small"),
            "type":           st.column_config.SelectboxColumn(
                                  "유형", options=["Changing", "NEW", "삭제"], width="small"),
            "concern":        st.column_config.TextColumn("걱정점",   width="medium"),
            "evidence_slide": st.column_config.NumberColumn("슬라이드#", width="small"),
        },
        key="cp_editor",
    )

    st.divider()
    col_back, col_next = st.columns([1, 2])
    with col_back:
        if st.button("← STEP 1으로", use_container_width=True):
            st.session_state.step = 1
            st.rerun()
    with col_next:
        has_bom = bool(st.session_state.base_bom_path)
        next_label = "Base BOM 매칭 시작 →" if has_bom else "매칭 없이 다음 단계 →"
        next_btn = st.button(
            next_label,
            type="primary",
            use_container_width=True,
        )

    if next_btn:
        # 편집 내용 반영
        updated_cps = edited_df.to_dict("records")
        for cp_orig, row in zip(st.session_state.change_points, updated_cps):
            cp_orig.update(row)

        if has_bom:
            from runner import step3_bom_match
            with st.status("🔄 Base BOM 매칭 중...", expanded=True) as status:
                st.write(f"{len(st.session_state.change_points)}개 변경점 매칭 중...")
                matched_cps = step3_bom_match(
                    st.session_state.change_points,
                    st.session_state.base_bom_path,
                )
                st.session_state.change_points = matched_cps
                matched_cnt = sum(1 for cp in matched_cps if cp.get("bom_matched"))
                status.update(
                    label=f"✅ 매칭 완료 — {matched_cnt}/{len(matched_cps)}개 매칭됨",
                    state="complete",
                )
        else:
            # Base BOM 없이 넘어가는 경우 — bom_matched 기본값만 채움
            for cp in st.session_state.change_points:
                cp.setdefault("base_part_no", "")
                cp.setdefault("bom_matched", False)
                cp.setdefault("match_confidence", "")
                cp.setdefault("match_reason", "")
                cp.setdefault("matched_subtree", [])
                cp.setdefault("history_candidates", [])

        st.session_state.step = 3
        st.rerun()

    if not has_bom:
        st.info("Base BOM 파일 없이도 다음 단계로 진행할 수 있습니다. (매칭 결과 없이 STEP 3으로 이동)")


# ════════════════════════════════════════════════════════════
# STEP 3 : Base BOM 매칭 결과 확인 / 수정
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 3:
    st.title("STEP 3 — Base BOM 매칭 결과 확인")
    st.markdown(
        "LLM이 각 변경점을 Base BOM 부품에 매칭한 결과입니다.  \n"
        "매칭이 잘못된 경우 **Base P/No** 를 직접 수정하세요."
    )

    cps = st.session_state.change_points
    if not cps:
        st.warning("변경점이 없습니다.")
        st.stop()

    matched   = sum(1 for cp in cps if cp.get("bom_matched"))
    unmatched = sum(1 for cp in cps if not cp.get("bom_matched") and cp.get("type") != "NEW")
    new_parts = sum(1 for cp in cps if cp.get("type") == "NEW" and not cp.get("bom_matched"))

    c1, c2, c3 = st.columns(3)
    c1.metric("✅ 매칭됨",     matched)
    c2.metric("🆕 신규 부품",  new_parts)
    c3.metric("⚠️ 매칭 실패", unmatched)

    st.divider()

    # PPTX 파일별 탭
    pptx_names = list(dict.fromkeys(
        cp.get("source_pptx", "기타") for cp in cps
    ))
    tabs = st.tabs([f"📄 {n}" for n in pptx_names]) if len(pptx_names) > 1 else [st.container()]

    for tab, pptx_name in zip(tabs, pptx_names):
        with tab:
            file_cps = [(i, cp) for i, cp in enumerate(cps)
                        if cp.get("source_pptx") == pptx_name]

            for cp_idx, cp in file_cps:
                is_matched  = cp.get("bom_matched", False)
                is_new      = cp.get("type") == "NEW"
                card_cls    = "matched" if is_matched else ("new-part" if is_new else "unmatched")

                with st.container(border=True):
                    h1, h2 = st.columns([3, 1])
                    with h1:
                        st.markdown(
                            f"### {cp.get('module','')} › **{cp.get('part','')}**  "
                            + _type_badge(cp.get("type", ""))
                            + (f" {_conf_badge(cp.get('match_confidence',''))}" if is_matched else ""),
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f"🔄 **변경내역**: {cp.get('change_detail','')}  \n"
                            f"💡 **변경사유**: {cp.get('change_reason','')}"
                        )
                    with h2:
                        status_txt = "✅ 매칭됨" if is_matched else ("🆕 신규 부품" if is_new else "⚠️ 매칭 실패")
                        st.markdown(f"**{status_txt}**")
                        if is_matched:
                            st.caption(f"근거: {cp.get('match_reason','')}")

                    st.divider()

                    # 매칭 결과 편집
                    col_pno, col_desc = st.columns([1, 2])
                    with col_pno:
                        new_pno = st.text_input(
                            "Base P/No",
                            value=cp.get("base_part_no", ""),
                            key=f"pno_{cp_idx}",
                            placeholder="매칭된 품번 (없으면 빈칸)",
                        )
                        # 편집 즉시 반영
                        if new_pno != cp.get("base_part_no", ""):
                            st.session_state.change_points[cp_idx]["base_part_no"] = new_pno

                    with col_desc:
                        if is_matched:
                            st.markdown(
                                f"**부품명**: {cp.get('part_type','')} | "
                                f"**Level**: {cp.get('bom_level','')} | "
                                f"**Qty**: {cp.get('qty','')} | "
                                f"**Maker**: {cp.get('supplier','')}"
                            )

                    # 하위 트리 표시
                    subtree = cp.get("matched_subtree", [])
                    if subtree:
                        with st.expander(f"📋 하위 부품 트리 ({len(subtree)}행)", expanded=False):
                            import pandas as pd
                            tree_df = pd.DataFrame([{
                                "Part No":     r["part_no"],
                                "Level":       r["lvl"],
                                "Description": r["description"],
                                "Part Type":   r["part_type"],
                                "Qty":         r["qty"],
                                "Maker":       r["maker"],
                                "Supply Type": r["supply_type"],
                            } for r in subtree])
                            st.dataframe(tree_df, width="stretch", hide_index=True)
                    elif is_new:
                        st.caption("🆕 신규 부품 — Base BOM에 없는 부품입니다.")
                    elif not is_matched:
                        st.warning("Base BOM에서 해당 부품을 찾지 못했습니다. Base P/No를 직접 입력하세요.")

    st.divider()
    col_back, col_next = st.columns([1, 2])
    with col_back:
        if st.button("← STEP 2로", use_container_width=True):
            st.session_state.step = 2
            st.rerun()
    with col_next:
        matched_cnt = sum(1 for cp in cps if cp.get("bom_matched"))
        if st.button(
            f"과거 이력 조회 → ({matched_cnt}개 매칭 부품 대상)",
            type="primary",
            use_container_width=True,
        ):
            from runner import step4_history_search
            with st.status("🔄 과거 이력 조회 중...", expanded=True) as status:
                st.write(f"{matched_cnt}개 매칭 부품의 과거 변경이력 조회 중...")
                result_cps = step4_history_search(st.session_state.change_points)
                st.session_state.change_points = result_cps
                st.session_state.history_searched = True
                total_hist = sum(
                    len(cp.get("history_candidates", []))
                    for cp in result_cps
                )
                status.update(
                    label=f"✅ 조회 완료 — 총 {total_hist}개 과거이력 후보 수집",
                    state="complete",
                )
            st.session_state.step = 4
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 4 : 과거 이력 후보 조회
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 4:
    st.title("STEP 4 — 과거 유사 이력 후보 조회")
    st.markdown(
        "Base BOM 매칭 부품을 기준으로 과거 변경이력에서 유사 케이스 Top 5를 조회했습니다.  \n"
        "각 이력에서 **현재 변경점에 누락되었을 가능성이 있는 연동 부품**을 확인하세요."
    )

    cps = st.session_state.change_points
    if not cps:
        st.warning("변경점이 없습니다.")
        st.stop()

    # 요약 메트릭
    matched_cps  = [cp for cp in cps if cp.get("bom_matched")]
    hist_cps     = [cp for cp in cps if cp.get("history_candidates")]
    total_linked = sum(
        len(lp)
        for cp in cps
        for hc in cp.get("history_candidates", [])
        for lp in [hc.get("linked_parts", [])]
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("BOM 매칭 부품", len(matched_cps))
    c2.metric("이력 조회 완료", len(hist_cps))
    c3.metric("연동 부품 후보 합계", total_linked)

    st.divider()

    # 변경점별 렌더링
    for i, cp in enumerate(cps):
        is_matched  = cp.get("bom_matched", False)
        is_new      = cp.get("type") == "NEW"
        candidates  = cp.get("history_candidates", [])

        with st.container(border=True):
            h1, h2 = st.columns([3, 1])
            with h1:
                st.markdown(
                    f"### {cp.get('module','')} › **{cp.get('part','')}**  "
                    + _type_badge(cp.get("type", ""))
                    + (f" {_conf_badge(cp.get('match_confidence',''))}" if is_matched else ""),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"🔄 **변경내역**: {cp.get('change_detail','')}  \n"
                    f"📌 **Base P/No**: `{cp.get('base_part_no','') or '없음'}`"
                )
            with h2:
                if not is_matched:
                    st.markdown("**⬜ 매칭 없음**")
                    st.caption("과거이력 조회 불가 (Base P/No 없음)")
                elif not candidates:
                    st.markdown("**📭 이력 없음**")
                    st.caption("관련 과거 케이스 없음")
                else:
                    st.markdown(f"**📚 이력 {len(candidates)}건**")
                    linked_cnt = sum(len(c.get("linked_parts", [])) for c in candidates)
                    if linked_cnt:
                        st.markdown(f"⚠️ **연동 부품 후보 {linked_cnt}개**")

            # 후보 이력 카드
            if candidates:
                st.divider()
                for hc in candidates:
                    rank        = hc.get("rank", "?")
                    model       = hc.get("model_name", "")
                    base_model  = hc.get("base_model", "")
                    reason      = hc.get("select_reason", "")
                    linked      = hc.get("linked_parts", [])

                    model_label = f"{base_model} → {model}" if base_model and base_model != model else model

                    with st.expander(
                        f"{'★' * min(rank, 5)}{'☆' * max(0, 5 - rank)}  "
                        f"[{rank}위] {model_label}  "
                        + (f"| ⚠ 연동 부품 {len(linked)}개" if linked else "| 연동 부품 없음"),
                        expanded=(rank == 1 and bool(linked)),
                    ):
                        st.caption(f"케이스 ID: `{hc.get('case_id','')}`")
                        st.markdown(f"**선정 이유**: {reason}")

                        if linked:
                            st.markdown("**연동 부품 후보** (현재 변경점 목록에 없는 부품)")
                            import pandas as pd
                            linked_df = pd.DataFrame([{
                                "Part No":    lp.get("part_no", ""),
                                "부품명":      lp.get("part_name", ""),
                                "변경유형":    lp.get("change_type", ""),
                                "관련성 근거": lp.get("relevance_reason", ""),
                            } for lp in linked])
                            st.dataframe(
                                linked_df,
                                width="stretch",
                                hide_index=True,
                                column_config={
                                    "Part No":    st.column_config.TextColumn(width="small"),
                                    "부품명":      st.column_config.TextColumn(width="medium"),
                                    "변경유형":    st.column_config.TextColumn(width="small"),
                                    "관련성 근거": st.column_config.TextColumn(width="large"),
                                },
                            )
                        else:
                            st.caption("이 케이스에서 누락 가능한 연동 부품은 없습니다.")

            elif is_new:
                st.caption("🆕 신규 부품 — Base BOM에 없어 과거이력 조회 대상이 아닙니다.")
            elif not is_matched:
                st.caption("⚠️ Base BOM 미매칭 — Base P/No를 STEP 3에서 직접 입력하면 조회 가능합니다.")

    # ── 연동 부품 선택 섹션 ───────────────────────────────────
    st.divider()
    st.subheader("📌 연동 부품 선택")
    st.markdown(
        "변경부품리스트에 포함할 연동 부품을 체크하세요.  \n"
        "과거 이력에서 발굴된 후보 중 이번 변경에 필요하다고 판단되는 부품만 선택하면 됩니다."
    )

    # 모든 linked_parts 후보 수집 (중복 제거: part_no 기준)
    all_linked_map: dict[str, dict] = {}   # part_no → LinkedPart
    for cp in cps:
        for hc in cp.get("history_candidates", []):
            for lp in hc.get("linked_parts", []):
                pno = lp.get("part_no", "")
                if pno and pno not in all_linked_map:
                    all_linked_map[pno] = lp

    if all_linked_map:
        # 기존 선택 상태 로드
        prev_selected: set[str] = {
            lp.get("part_no", "") for lp in st.session_state.selected_linked
        }

        import pandas as pd
        linked_rows = []
        for pno, lp in all_linked_map.items():
            linked_rows.append({
                "선택":       pno in prev_selected,
                "Part No":    pno,
                "부품명":      lp.get("part_name", ""),
                "변경유형":    lp.get("change_type", ""),
                "관련성 근거": lp.get("relevance_reason", ""),
            })

        linked_df = pd.DataFrame(linked_rows)
        edited_linked = st.data_editor(
            linked_df,
            width="stretch",
            hide_index=True,
            column_config={
                "선택":       st.column_config.CheckboxColumn("포함", width="small"),
                "Part No":    st.column_config.TextColumn(width="small"),
                "부품명":      st.column_config.TextColumn(width="medium"),
                "변경유형":    st.column_config.TextColumn(width="small"),
                "관련성 근거": st.column_config.TextColumn(width="large"),
            },
            disabled=["Part No", "부품명", "변경유형", "관련성 근거"],
            key="linked_editor",
        )

        selected_pnos: set[str] = set(
            row["Part No"] for _, row in edited_linked.iterrows() if row["선택"]
        )
        selected_linked_list = [
            all_linked_map[pno] for pno in selected_pnos if pno in all_linked_map
        ]
        selected_cnt = len(selected_linked_list)
    else:
        st.info("발굴된 연동 부품 후보가 없습니다. 변경점 부품만으로 리스트를 생성합니다.")
        selected_linked_list = []
        selected_cnt = 0

    st.divider()
    col_back, col_next = st.columns([1, 2])
    with col_back:
        if st.button("← STEP 3으로", use_container_width=True):
            st.session_state.step = 3
            st.rerun()
    with col_next:
        total_rows = len(cps) + selected_cnt
        if st.button(
            f"변경부품리스트 생성 → ({total_rows}개 부품)",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.selected_linked = selected_linked_list
            st.session_state.step = 5
            st.rerun()


# ════════════════════════════════════════════════════════════
# STEP 5 : 변경부품리스트.xlsx 생성 & 다운로드
# ════════════════════════════════════════════════════════════
elif st.session_state.step == 5:
    st.title("STEP 5 — 변경부품리스트.xlsx 생성")
    st.markdown(
        "메타정보를 입력하고 **Excel 생성** 버튼을 눌러 파일을 다운로드하세요."
    )

    cps     = st.session_state.change_points
    linked  = st.session_state.get("selected_linked", [])

    if not cps:
        st.warning("변경점이 없습니다. STEP 1로 돌아가 다시 시작하세요.")
        st.stop()

    # ── 메타정보 입력 ─────────────────────────────────────────
    st.subheader("📋 메타정보")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        base_model = st.text_input(
            "Base Model / Grade",
            value=st.session_state.get("base_bom_name", "").replace(".xlsx", ""),
            placeholder="예: WSED7613S / B",
        )
    with col_b:
        new_model = st.text_input(
            "New Model / Grade",
            value="TBD",
            placeholder="예: WSED7620S / B",
        )
    with col_c:
        event = st.text_input(
            "Event",
            value="CP DV",
            placeholder="예: CP DV PV PreMP",
        )

    st.divider()

    # ── 미리보기 테이블 ───────────────────────────────────────
    from nodes.export_excel import build_export_rows
    import pandas as pd

    preview_rows = build_export_rows(cps, linked)
    preview_df   = pd.DataFrame([{
        "No.":         r["no"],
        "BOM Lvl":     r["bom_level"],
        "Part Type":   r["part_type"],
        "Base P/No":   r["base_pno"],
        "부품명":        r["part_name"],
        "변경점":        r["change_point"],
        "변경사유":       r["change_reason"],
        "양산처":        r["supplier"],
        "구분":          r["classification"],
        "출처":          "연동부품" if r["source"] == "linked_part" else "변경점",
    } for r in preview_rows])

    st.subheader(f"📄 미리보기 ({len(preview_rows)}행)")
    st.dataframe(
        preview_df,
        width="stretch",
        hide_index=True,
        column_config={
            "No.":       st.column_config.NumberColumn(width="small"),
            "BOM Lvl":   st.column_config.TextColumn(width="small"),
            "Part Type": st.column_config.TextColumn(width="small"),
            "Base P/No": st.column_config.TextColumn(width="medium"),
            "부품명":      st.column_config.TextColumn(width="large"),
            "변경점":      st.column_config.TextColumn(width="large"),
            "변경사유":    st.column_config.TextColumn(width="large"),
            "양산처":      st.column_config.TextColumn(width="medium"),
            "구분":        st.column_config.TextColumn(width="small"),
            "출처":        st.column_config.TextColumn(width="small"),
        },
    )

    st.divider()

    # ── 생성 & 다운로드 ───────────────────────────────────────
    col_back, col_dl = st.columns([1, 2])
    with col_back:
        if st.button("← STEP 4로", use_container_width=True):
            st.session_state.step = 4
            st.rerun()
    with col_dl:
        from runner import step5_export
        xlsx_bytes = step5_export(
            change_points=cps,
            selected_linked=linked,
            base_model=base_model,
            new_model=new_model,
            event=event,
        )
        st.download_button(
            label="⬇️ 변경부품리스트.xlsx 다운로드",
            data=xlsx_bytes,
            file_name="변경부품리스트.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )