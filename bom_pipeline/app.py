"""
BOM 변경 파이프라인 Streamlit 앱.

실행: streamlit run bom_pipeline/app.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

# ── 환경변수 로드 ─────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── 페이지 설정 ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BOM 변경 파이프라인",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 세션 상태 초기화 ──────────────────────────────────────────────────────
for key, default in {
    "step":            1,
    "change_points":   [],
    "human_selections": [],
    "bom_df":          None,
    "bom_path":        None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1 — 파일 업로드 & 파이프라인 실행
# ═══════════════════════════════════════════════════════════════════════════

def render_step1():
    st.header("Step 1 — 파일 업로드")

    col1, col2 = st.columns(2)
    with col1:
        pptx_file = st.file_uploader("변경점 PPTX", type=["pptx"], key="pptx_upload")
    with col2:
        bom_file = st.file_uploader("Base BOM (Excel)", type=["xlsx", "xls"], key="bom_upload")

    use_fixture = st.checkbox("Fixture JSON 사용 (테스트용)", value=False)
    fixture_choice = None
    if use_fixture:
        fixture_choice = st.radio(
            "Fixture 선택",
            ["퀵존레인지", "Compact Oven"],
            horizontal=True,
        )

    st.divider()

    if st.button("파이프라인 실행", type="primary", disabled=not (use_fixture or pptx_file)):
        _run_pipeline(pptx_file, bom_file, use_fixture, fixture_choice)


def _run_pipeline(pptx_file, bom_file, use_fixture: bool, fixture_choice: str | None):
    from nodes.parse_pptx import parse_pptx_node
    from nodes.bom_match import bom_match_node
    from nodes.history_search import history_search_node

    progress = st.progress(0, text="파이프라인 준비 중...")

    # ── 변경점 확보 ──────────────────────────────────────────────────────
    if use_fixture:
        fixture_map = {
            "퀵존레인지": "fixtures/quickzone_change_points.json",
            "Compact Oven": "fixtures/compact_oven_change_points.json",
        }
        fixture_path = Path(__file__).parent / fixture_map[fixture_choice]
        change_points = json.loads(fixture_path.read_text(encoding="utf-8"))
        progress.progress(33, text="Fixture 로드 완료")
    else:
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            f.write(pptx_file.read())
            tmp_pptx = f.name
        progress.progress(10, text="PPTX 파싱 중...")
        state = parse_pptx_node({"pptx_path": tmp_pptx})
        change_points = state.get("change_points", [])
        os.unlink(tmp_pptx)
        progress.progress(33, text=f"PPTX 파싱 완료 — {len(change_points)}개 변경점")

    # ── Base BOM 매핑 ────────────────────────────────────────────────────
    bom_path = ""
    if bom_file:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(bom_file.read())
            bom_path = f.name
        st.session_state["bom_path"] = bom_path

    progress.progress(40, text="Base BOM 매핑 중...")
    state2 = bom_match_node({"change_points": change_points, "base_bom_path": bom_path})
    change_points = state2.get("change_points", change_points)
    progress.progress(66, text="BOM 매핑 완료")

    # ── 이력 검색 ────────────────────────────────────────────────────────
    progress.progress(70, text="과거 이력 검색 중 (시간이 걸릴 수 있습니다)...")
    state3 = history_search_node({"change_points": change_points})
    change_points = state3.get("change_points", change_points)
    progress.progress(100, text="완료!")

    st.session_state["change_points"] = change_points
    st.session_state["step"] = 2
    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2 — Human Review (케이스 선택 + 연동부품 확정)
# ═══════════════════════════════════════════════════════════════════════════

def render_step2():
    st.header("Step 2 — 변경점 검토")
    change_points = st.session_state["change_points"]

    st.caption(f"총 {len(change_points)}개 변경점 — 참고 케이스를 선택하고 연동부품을 확정하세요.")

    selections: list[dict] = []

    for idx, cp in enumerate(change_points):
        candidates = cp.get("history_candidates", [])

        with st.expander(
            f"[{idx+1}] {cp.get('part','')}  |  {cp.get('change_detail','')[:60]}",
            expanded=(idx == 0),
        ):
            # 변경점 요약
            c1, c2, c3 = st.columns(3)
            c1.metric("Base P/No", cp.get("base_part_no") or "(없음)")
            c2.metric("New P/No",  cp.get("new_part_no")  or "(없음)")
            c3.metric("유형", cp.get("change_type", ""))
            st.markdown(f"**변경사유:** {cp.get('change_reason','')}")
            st.divider()

            if not candidates:
                st.info("참고 이력 없음")
                selections.append(_empty_selection(idx, cp))
                continue

            # 케이스별 탭
            tab_labels = [f"케이스 #{c['rank']}" for c in candidates]
            tabs = st.tabs(tab_labels)

            selected_case_keys: list[str] = []
            linked_part_keys: list[dict] = []

            for tab, c in zip(tabs, candidates):
                with tab:
                    path_tag = "🔵 직접이력" if "직접이력" in c.get("select_reason","") else "🟡 유사패턴"
                    st.markdown(f"**{path_tag}** &nbsp; `master_id={c['master_id']}` &nbsp; "
                                f"`{c.get('base_model','')} → {c.get('new_model','')}`")
                    st.caption(c.get("select_reason",""))

                    # 케이스 변경부품 테이블
                    case_parts = c.get("case_parts", [])
                    if case_parts:
                        df = pd.DataFrame(case_parts)[
                            ["bom_level","part_name","base_part_no","new_part_no","changing_point"]
                        ].rename(columns={
                            "bom_level":     "레벨",
                            "part_name":     "부품명",
                            "base_part_no":  "Base P/No",
                            "new_part_no":   "New P/No",
                            "changing_point":"변경내역",
                        })
                        st.dataframe(df, use_container_width=True, hide_index=True)

                    # 케이스 선택 체크박스
                    checked = st.checkbox(
                        "이 케이스 참고",
                        value=True,
                        key=f"case_{idx}_{c['master_id']}",
                    )
                    if checked:
                        selected_case_keys.append(c["master_id"])

                    # 연동부품 제안
                    linked = c.get("linked_parts", [])
                    if linked:
                        st.markdown("**연동부품 제안:**")
                        for lp in linked:
                            col_a, col_b = st.columns([3, 1])
                            col_a.markdown(
                                f"`[{lp.get('change_type','')}]` **{lp.get('part_name','')}** "
                                f"— {lp.get('relevance_reason','')}"
                            )
                            add = col_b.checkbox(
                                "추가",
                                value=False,
                                key=f"linked_{idx}_{c['master_id']}_{lp.get('part_name','')}",
                            )
                            if add:
                                linked_part_keys.append(lp)

            selections.append({
                "change_point_idx":   idx,
                "part":               cp.get("part", ""),
                "base_part_no":       cp.get("base_part_no", ""),
                "new_part_no":        cp.get("new_part_no", ""),
                "change_detail":      cp.get("change_detail", ""),
                "change_reason":      cp.get("change_reason", ""),
                "change_type":        cp.get("change_type", ""),
                "discipline":         cp.get("discipline", ""),
                "bom_level":          cp.get("bom_level", ""),
                "selected_cases":     selected_case_keys,
                "added_linked_parts": [
                    {
                        "part_name":    lp.get("part_name", ""),
                        "base_part_no": lp.get("base_part_no", ""),
                        "new_part_no":  lp.get("new_part_no", ""),
                        "change_type":  lp.get("change_type", ""),
                    }
                    for lp in linked_part_keys
                ],
                "skipped": False,
            })

    st.divider()
    col_back, col_next = st.columns([1, 5])
    with col_back:
        if st.button("← 처음으로"):
            st.session_state["step"] = 1
            st.rerun()
    with col_next:
        if st.button("BOM 편집 화면으로 →", type="primary"):
            st.session_state["human_selections"] = selections
            _build_bom_df()
            st.session_state["step"] = 3
            st.rerun()


def _empty_selection(idx: int, cp: dict) -> dict:
    return {
        "change_point_idx":   idx,
        "part":               cp.get("part", ""),
        "base_part_no":       cp.get("base_part_no", ""),
        "new_part_no":        cp.get("new_part_no", ""),
        "change_detail":      cp.get("change_detail", ""),
        "change_reason":      cp.get("change_reason", ""),
        "change_type":        cp.get("change_type", ""),
        "discipline":         cp.get("discipline", ""),
        "bom_level":          cp.get("bom_level", ""),
        "selected_cases":     [],
        "added_linked_parts": [],
        "skipped":            True,
    }


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3 — BOM 편집 화면
# ═══════════════════════════════════════════════════════════════════════════

_CHANGE_COL = "_변경상태"   # 내부용 마킹 컬럼
_NOTE_COL   = "_변경내역"


def _build_bom_df():
    """human_selections + change_points → 편집용 DataFrame 생성."""
    change_points  = st.session_state["change_points"]
    selections     = st.session_state["human_selections"]

    # Base BOM이 있으면 로드, 없으면 change_points만으로 구성
    bom_path = st.session_state.get("bom_path")
    if bom_path and Path(bom_path).exists():
        from nodes.bom_match import load_base_bom
        rows, _ = load_base_bom(bom_path)
        base_df = pd.DataFrame([r.to_dict() for r in rows])
    else:
        base_df = pd.DataFrame(columns=[
            "part_no","lvl","parent_no","description","qty","uom",
            "maker","part_type","supply_type","ckd",
        ])

    base_df[_CHANGE_COL] = ""
    base_df[_NOTE_COL]   = ""

    # 변경 행 마킹
    pno_to_sel: dict[str, dict] = {}
    for sel in selections:
        if sel.get("skipped"):
            continue
        pno = sel.get("base_part_no", "")
        if pno:
            pno_to_sel[pno] = sel

    if not base_df.empty and "part_no" in base_df.columns:
        for pno, sel in pno_to_sel.items():
            mask = base_df["part_no"] == pno
            new_pno = sel.get("new_part_no", "TBD") or "TBD"
            base_df.loc[mask, _CHANGE_COL] = "변경"
            base_df.loc[mask, _NOTE_COL]   = (
                f"{sel.get('change_detail','')} → New P/No: {new_pno}"
            )

    # 연동부품 추가 행 생성
    linked_rows = []
    for sel in selections:
        if sel.get("skipped"):
            continue
        for lp in sel.get("added_linked_parts", []):
            linked_rows.append({
                "part_no":     lp.get("base_part_no", "TBD"),
                "lvl":         "",
                "parent_no":   sel.get("base_part_no", ""),
                "description": lp.get("part_name", ""),
                "qty":         "",
                "uom":         "",
                "maker":       "",
                "part_type":   "",
                "supply_type": "",
                "ckd":         "",
                _CHANGE_COL:   lp.get("change_type", "추가"),
                _NOTE_COL:     f"연동부품 추가 — New P/No: {lp.get('new_part_no','TBD') or 'TBD'}",
            })

    if linked_rows:
        linked_df = pd.DataFrame(linked_rows)
        base_df = pd.concat([base_df, linked_df], ignore_index=True)

    # 변경 있는 행만 없으면 전체, 있으면 변경행 + 인접 컨텍스트
    st.session_state["bom_df"] = base_df


def _highlight_bom(row):
    status = row.get(_CHANGE_COL, "")
    if status == "변경":
        return ["background-color: #fff3cd"] * len(row)
    if status in ("추가", "NEW"):
        return ["background-color: #d4edda"] * len(row)
    if status == "삭제":
        return ["background-color: #f8d7da"] * len(row)
    return [""] * len(row)


def render_step3():
    st.header("Step 3 — BOM 편집 및 확정")

    df: pd.DataFrame = st.session_state.get("bom_df")
    if df is None or df.empty:
        st.warning("BOM 데이터가 없습니다.")
        return

    # 변경행 필터 토글
    show_all = st.toggle("전체 BOM 표시", value=False)
    changed_mask = df[_CHANGE_COL] != ""

    display_df = df if show_all else df[changed_mask].copy()

    st.caption(
        f"전체 {len(df)}행 중 변경 {changed_mask.sum()}행 표시 중"
        if not show_all else f"전체 {len(df)}행"
    )

    # 컬럼 표시명 매핑
    col_rename = {
        "part_no":     "Part No",
        "lvl":         "레벨",
        "parent_no":   "Parent No",
        "description": "부품명",
        "qty":         "수량",
        "uom":         "단위",
        "maker":       "메이커",
        "part_type":   "유형",
        _CHANGE_COL:   "변경상태",
        _NOTE_COL:     "변경내역",
    }
    display_cols = [c for c in col_rename if c in display_df.columns]
    display_df = display_df[display_cols].rename(columns=col_rename)

    # 편집 가능한 데이터 에디터
    edited_df = st.data_editor(
        display_df.style.apply(_highlight_bom, axis=1),
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "변경상태": st.column_config.SelectboxColumn(
                options=["", "변경", "추가", "삭제", "NEW"],
                width="small",
            ),
            "변경내역": st.column_config.TextColumn(width="large"),
            "Part No":  st.column_config.TextColumn(width="medium"),
        },
        key="bom_editor",
    )

    st.divider()

    # 범례
    col_l1, col_l2, col_l3, _ = st.columns([1,1,1,3])
    col_l1.markdown("🟡 변경")
    col_l2.markdown("🟢 추가/NEW")
    col_l3.markdown("🔴 삭제")

    st.divider()

    col_back, col_dl = st.columns([1, 5])
    with col_back:
        if st.button("← 검토로 돌아가기"):
            st.session_state["step"] = 2
            st.rerun()
    with col_dl:
        if st.button("✅ 확정 및 xlsx 다운로드", type="primary"):
            xlsx_bytes = _export_xlsx(df, changed_mask)
            st.download_button(
                label="📥 new_bom.xlsx 다운로드",
                data=xlsx_bytes,
                file_name="new_bom.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def _export_xlsx(df: pd.DataFrame, changed_mask) -> bytes:
    """변경 내역이 반영된 xlsx 생성."""
    import openpyxl
    from openpyxl.styles import PatternFill

    col_rename = {
        "part_no": "Part No", "lvl": "레벨", "parent_no": "Parent No",
        "description": "부품명", "qty": "수량", "uom": "단위",
        "maker": "메이커", "part_type": "유형",
        _CHANGE_COL: "변경상태", _NOTE_COL: "변경내역",
    }
    export_cols = [c for c in col_rename if c in df.columns]
    out_df = df[export_cols].rename(columns=col_rename)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "new_bom"

    # 헤더
    for col_idx, col_name in enumerate(out_df.columns, 1):
        ws.cell(row=1, column=col_idx, value=col_name)

    fill_map = {
        "변경": PatternFill("solid", fgColor="FFF3CD"),
        "추가": PatternFill("solid", fgColor="D4EDDA"),
        "NEW":  PatternFill("solid", fgColor="D4EDDA"),
        "삭제": PatternFill("solid", fgColor="F8D7DA"),
    }

    status_col_idx = list(out_df.columns).index("변경상태") + 1 if "변경상태" in out_df.columns else None

    for row_idx, (_, row) in enumerate(out_df.iterrows(), 2):
        status = str(row.get("변경상태", ""))
        fill = fill_map.get(status)
        for col_idx, val in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            if fill:
                cell.fill = fill

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# 메인 라우터
# ═══════════════════════════════════════════════════════════════════════════

def main():
    st.title("BOM 변경 파이프라인")

    # 상단 스텝 표시
    step = st.session_state["step"]
    cols = st.columns(3)
    labels = ["1. 파일 업로드", "2. 변경점 검토", "3. BOM 편집"]
    for i, (col, label) in enumerate(zip(cols, labels), 1):
        if i == step:
            col.markdown(f"**▶ {label}**")
        elif i < step:
            col.markdown(f"✅ {label}")
        else:
            col.markdown(f"○ {label}")

    st.divider()

    if step == 1:
        render_step1()
    elif step == 2:
        render_step2()
    elif step == 3:
        render_step3()


if __name__ == "__main__":
    main()
