"""Streamlit 페이지 — BOM 변경 영향 분석 에이전트 (L1~L4).

입력 두 가지: ① 심의회 PPT 업로드(권장) → 변경사유/변경내용 + 베이스 모델 추출 후
변경항목별 분석, ② 자유텍스트 단발. 데이터 계층은 app.py와 공유.
실행: ``streamlit run src/ui/agent_app.py`` 또는 ``python -m src.cli app agent``.

intra-ui 임포트는 app.py와 동일하게 bare(``from agent_client import ...``).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from agent_client import (
    bom_diff_change_list,
    build_master_from_diff_rows,
    build_master_xlsx,
    change_list_from_result,
    confirm_change_item,
    confirm_items,
    export_rows_preview,
    extract_base_bom,
    extract_ppt,
    load_agent_env,
    master_template_path,
    propose_change_item,
    propose_items,
    score_master_xlsx,
)

try:  # 화이트 모드 카드형 후보 검토 뷰 (sibling 모듈, bare import 관례).
    from card_view import inject_card_css, render_card_tab
except ImportError:
    from src.ui.card_view import inject_card_css, render_card_tab

# .env(LLM_PROVIDER/ANTHROPIC_API_KEY) 로드 — 직접 실행(streamlit run agent_app.py) 대비.
# 진입점에서만 호출(import 부작용 없는 load_agent_env). main.py 경유 시엔 idempotent(override 안 함).
load_agent_env()

from src.agent.confirm.models import CHANGE_POINTS, CLASSIFICATION_KO_OPTIONS


def _status_badge(status: str) -> str:
    return {"ADD": "🟢 ADD", "MODIFY": "🟡 MODIFY", "DELETE": "🔴 DELETE"}.get(status, status)


# ②.5 매핑 status 뱃지 — 구조 스코프 게이트(SEARCH_INCLUDE_STRUCT) on일 때만 표시됨.
_MAP_BADGE = {"matched": "🟢", "ambiguous": "🟡", "add_proposal": "🆕", "dropped": "⚪"}


def _mapping_label(ln: dict[str, Any]) -> str:
    """직렬화 라인 → '🟢matched→MODIFY P100' 꼴 한 줄 라벨. 매핑 없으면 빈 문자열."""
    status = ln.get("mapping_status")
    if not status:
        return ""
    parts = [f"{_MAP_BADGE.get(status, '')}{status}"]
    if ln.get("mapping_action"):
        parts.append(f"→{ln['mapping_action']}")
    if ln.get("mapping_target"):
        parts.append(f" {ln['mapping_target']}")
    return "".join(parts)


def _derived_summary(intent: dict[str, Any]) -> str:
    """intent dict → 자동요약(변경점 유도 v3) 라벨. 슬롯 없으면 빈 문자열."""
    slots = intent.get("derived_change_slots") or []
    if not slots:
        return ""
    txt = " · ".join(
        " ".join(p for p in (s.get("target"), s.get("attribute"), s.get("action")) if p)
        for s in slots
    )
    src = {"det": "결정론", "llm": "LLM"}.get(intent.get("derived_cp_source") or "", "")
    return f"🧩 자동요약(변경점 유도{', ' + src if src else ''}): {txt}"


def _with_optional_cols(base_cols: list[str], rows: list[dict[str, Any]]) -> list[str]:
    """매핑/구조정합 컬럼은 값이 하나라도 있을 때만 노출 (게이트 off면 기존과 동일)."""
    cols = list(base_cols)
    anchor = cols.index("변경점")
    for col in ("구조정합", "매핑 제안"):  # 변경점 바로 앞에 매핑→구조정합 순으로
        if any(r.get(col) for r in rows):
            cols.insert(anchor, col)
    return cols


def _gate_column_config() -> dict[str, Any]:
    """게이트/항목 에디터 공용 컬럼 설정 — 회수값은 읽기전용, 사용자 입력만 편집 가능."""
    ro = st.column_config.TextColumn
    return {
        "채택": st.column_config.CheckboxColumn("채택", help="확정 닻으로 채택"),
        "후보": st.column_config.NumberColumn(
            "후보", disabled=True, width="small",
            help="사유 묶음(과거 한 이벤트) 그룹 번호 — 같은 번호 = 함께 바뀐 부품 세트",
        ),
        "부품명": ro("부품명", disabled=True),
        "과거 base P/No": ro("과거 base P/No", disabled=True, help="과거 개발마스터 변경 전 품번(change_line.base_pno)"),
        "과거 new P/No(제안)": ro("과거 new P/No(제안)", disabled=True,
                              help="과거 변경 후 품번(change_line.new_pno) — 회수한 기존 품번(새로 생성한 게 아님)"),
        "출처(개발마스터)": ro("출처(개발마스터)", disabled=True, help="이 후보가 나온 원본 엑셀(source_ref)"),
        "선례 변경점": ro("선례 변경점", disabled=True),
        "사유": ro("사유", disabled=True),
        "변경점": st.column_config.SelectboxColumn("변경점", options=["", *CHANGE_POINTS],
                                                help="이번 변경의 변경점 지정(사용자)"),
        "신규 P/No 입력": ro("신규 P/No 입력", help="이번 변경의 새 품번(미입력 시 <발번대기> — 시스템 무생성)"),
        "매핑 제안": ro("매핑 제안", disabled=True,
                    help="②.5 후보-BOM 매핑(SEARCH_INCLUDE_STRUCT=1일 때, 참고용 AI 제안)"),
        "구조정합": ro("구조정합", disabled=True,
                   help="①.5 구조 스코프 정합 점수(0~1) — SEARCH_INCLUDE_STRUCT=1일 때만"),
    }


def _to_csv(rows: list[dict[str, Any]]) -> bytes:
    import csv
    import io

    if not rows:
        return b""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _doc_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.write("(없음)")
        return
    st.dataframe(
        [
            {
                "품번": r["pno"],
                "action": r["action"],
                "tier": r["tier"],
                "출처": r["src"],
                "설명": r["detail"],
            }
            for r in rows
        ],
        use_container_width=True,
    )


def _render_newbom(nb: dict[str, Any]) -> None:
    st.markdown(
        f"**New BOM** · 모델 `{nb['model']}` · 전체 {nb['total_rows']}행 · "
        f"변경 {len(nb['changed'])}건" + (f" · ⚠️ 미매칭 {nb['unmatched']}" if nb["unmatched"] else "")
    )
    violations = nb.get("violations") or []
    if violations:
        st.error("New BOM 출력 검증 위반:\n" + "\n".join(f"- {v}" for v in violations))
    else:
        st.success("New BOM 출력 검증 OK — 모든 변경/신규 행 출처 보유, NEW 품번=<발번대기>")
    changed = [{**r, "status": _status_badge(r["status"])} for r in nb["changed"]]
    st.markdown("**변경 부품 리스트 (base BOM 대비)**")
    st.dataframe(changed or [{"(변경 없음)": ""}], use_container_width=True)

    st.markdown("**개발 master 행 (ADD/MODIFY)**")
    st.dataframe(
        [{**r, "status": _status_badge(r["status"])} for r in nb["dev_master"]] or [{"(없음)": ""}],
        use_container_width=True,
    )

    with st.expander(f"전체 New BOM 트리 ({nb['total_rows']}행)"):
        st.dataframe(
            [{**r, "status": _status_badge(r["status"])} for r in nb["full"]],
            use_container_width=True,
            height=400,
        )
    cdl1, cdl2 = st.columns(2)
    cdl1.download_button(
        "New BOM CSV 다운로드",
        data=_to_csv(nb["full"]),
        file_name=f"new_bom_{nb['model'] or 'model'}.csv",
        mime="text/csv",
    )
    if nb.get("xlsx_b64"):
        import base64
        cdl2.download_button(
            "서식보존 New BOM xlsx 다운로드 (원본 양식 유지)",
            data=base64.b64decode(nb["xlsx_b64"]),
            file_name=f"new_bom_{nb['model'] or 'model'}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    elif nb.get("xlsx_error"):
        cdl2.caption(f"⚠️ 서식보존 xlsx 생성 실패(CSV 사용): {nb['xlsx_error'][:80]}")


# ════════════════════════════════════════════════════════════════
# HITL 확정 게이트 (절대원칙 #4 — 확정이 트리 전개보다 먼저).
# Streamlit은 재실행마다 stateless라 propose 결과를 session_state에 보관하고,
# 사용자 확정(채택/거절·변경점·신규 P/No) 후 confirm_change_item으로 전개한다.
# ════════════════════════════════════════════════════════════════

# 컬럼 = 과거 개발마스터 변경행(change_line)에서 회수한 값 + 사용자 입력란.
#   과거 base P/No → 과거 new P/No(제안): 그 변경이 바꾼 before→after 품번(둘 다 change_line 컬럼).
#   '제안' = 새로 생성한 번호가 아니라 과거에 실재했던 new_pno를 회수해 제시한 것.
#   출처(개발마스터) = 그 라인이 나온 원본 엑셀(source_ref).
#   신규 P/No 입력 = 이번 변경의 새 품번(사용자 입력; 미입력 시 <발번대기>).
_GATE_COLS = [
    "채택",
    "후보",              # 사유 묶음(event) 그룹 번호 — 같은 번호 = 함께 바뀐 부품 세트
    "부품명",
    "과거 base P/No",     # change_line.base_pno (변경 전)
    "과거 new P/No(제안)", # change_line.new_pno (변경 후 — 회수한 기존 품번)
    "출처(개발마스터)",    # change_line.source_ref (참고한 원본 엑셀)
    "선례 변경점",        # 과거 변경점(change_log)
    "사유",
    "변경점",            # 사용자 지정(입력)
    "신규 P/No 입력",
]


def _gate_propose(change_detail: str, change_reason: str, base_model: str, region: str) -> None:
    with st.spinner("과거 master 검색 중 (후보 부품 세트)..."):
        try:
            st.session_state["gate_proposal"] = propose_change_item(
                change_detail, change_reason,
                base_model=base_model or None, region=region or None,
            )
        except Exception as exc:  # noqa: BLE001 — UI에 오류 표시
            st.error(f"후보 검색 실패: {exc}")
            return
    st.session_state.pop("gate_result", None)
    st.session_state.pop("gate_rows", None)


def _cand_rows(candidates: list[dict[str, Any]], *, item_index: int | None = None) -> list[dict[str, Any]]:
    """후보 세트 → data_editor 평면 행. 후보(사유 묶음)별 그룹 번호 부여, 출처 포함.

    채택 기본 False(기본 통과 금지). 매핑 제안/구조정합은 SEARCH_INCLUDE_STRUCT on일 때만
    값이 있고 그때만 컬럼 노출(_with_optional_cols). ``item_index``가 주어지면 행에 부착(PPT).
    """
    rows: list[dict[str, Any]] = []
    for g, cand in enumerate(candidates, 1):
        struct = cand.get("struct_score")
        merged = cand.get("merged_event_ids") or []
        src_note = (cand.get("source_ref") or "")
        if merged:
            src_note = f"{src_note} (+{len(merged)}개 모델 변형 병합)"
        for ln in cand.get("lines", []):
            row = {
                "채택": False,
                "후보": g,
                "부품명": ln.get("part_name") or "",
                "과거 base P/No": ln.get("part_no_base") or "",
                "과거 new P/No(제안)": ln.get("part_no_new_suggested") or "",
                "출처(개발마스터)": ln.get("source_ref") or src_note,
                "선례 변경점": cand.get("change_log") or "",
                "사유": cand.get("change_reason") or "",
                "매핑 제안": _mapping_label(ln),
                "구조정합": f"{struct:.2f}" if struct is not None else "",
                "변경점": "",
                "신규 P/No 입력": "",
                "_event_id": ln.get("event_id"),
                "_classification": ln.get("classification"),
                "_source_ref": ln.get("source_ref"),
            }
            if item_index is not None:
                row["_item_index"] = item_index
            rows.append(row)
    return rows


def _gate_editor_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _cand_rows(candidates)


def _gate_selections(edited: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "accept": bool(r.get("채택")),
            "part_no_base": r.get("과거 base P/No") or None,
            "part_no_new_suggested": r.get("과거 new P/No(제안)") or None,
            "change_point": (r.get("변경점") or None),
            "new_pno_input": (r.get("신규 P/No 입력") or None),
            "classification": r.get("_classification"),
            "source_ref": r.get("_source_ref"),
            "event_id": r.get("_event_id"),
            "manual_added": bool(r.get("_manual_added")),  # P8 미회수율 측정용
        }
        for r in edited
    ]


# PPT 항목별 토글(expander) 안에서 쓰는 컬럼 — 항목은 헤더에 있으므로 '항목' 컬럼 제거.
_PPT_GATE_COLS = [c for c in _GATE_COLS]


def _item_rows(p: dict[str, Any]) -> list[dict[str, Any]]:
    """한 변경항목(proposal)의 후보 → 행. _item_index 부착(확정 시 항목 매핑)."""
    return _cand_rows(p.get("candidates", []), item_index=int(p.get("item_index", 0)))


def _ppt_selections(edited: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """확정 에디터 행 → confirm_items용 선택. _gate_selections + _item_index 운반."""
    out = _gate_selections(edited)
    for sel, r in zip(out, edited):
        sel["_item_index"] = int(r.get("_item_index", 0))
    return out


def _render_gate_result(res: dict[str, Any]) -> None:
    st.subheader("④ 확정 닻 + 하위 전개 (L3·L4)")
    if res.get("violations"):
        st.error("출력 규칙 위반: " + "; ".join(res["violations"]))
    else:
        st.success("출력 검증 통과 — 모든 행 출처 보유, NEW 품번=<발번대기>.")
    st.markdown("**확정 닻**")
    st.dataframe(res.get("anchors", []) or [{"(없음)": ""}], use_container_width=True)
    if res.get("tree"):
        st.markdown("**하위 BOM 트리 (확정 닻 한정)**")
        st.dataframe(res["tree"], use_container_width=True)
    if res.get("verdicts"):
        st.markdown("**영향 판정 (L3)**")
        st.dataframe(
            [{**v, "rules": ", ".join(v["rules"])} for v in res["verdicts"]],
            use_container_width=True,
        )
    doc = res.get("doc", {})
    st.markdown("**문서 (L4)**")
    t1, t2, t3, t4 = st.tabs(["변경 부품", "개발마스터 행", "영향 전개 부품(하위/상위)", "체크리스트"])
    with t1:
        _doc_table(doc.get("changed_parts", []))
    with t2:
        _doc_table(doc.get("dev_master_rows", []))
    with t3:
        _doc_table(doc.get("bom_diff", []))
    with t4:
        for item in doc.get("checklist", []):
            st.write(item)


def _render_gate() -> None:
    """② 후보 → ③ 확정 게이트 → ④ 결과. 확정이 트리 전개보다 먼저."""
    prop = st.session_state.get("gate_proposal")
    if not prop:
        return
    ci = prop["intent"]
    st.caption(
        f"의도: source=`{ci.get('source')}` · base_model=`{prop.get('base_model') or '-'}` · "
        f"쿼리={ci.get('rewritten_queries')}"
    )
    if (ds := _derived_summary(ci)):
        st.caption(ds)
    rmeta = prop.get("retrieval_meta") or {}
    if rmeta.get("low_confidence"):
        st.warning(
            "🔎 검색 신뢰 낮음 — 선례가 없거나 검색이 못 찾았을 수 있습니다. "
            "아래 후보를 신중히 확인하고, 필요하면 키워드로 직접 보강 검색하세요."
        )
    candidates = prop.get("candidates", [])
    if not candidates:
        st.info("과거 사례 후보가 없습니다 (Predict). 신규 P/No를 직접 입력해 확정하세요.")
        return
    st.markdown(
        f"**② 후보 부품 세트** {len(candidates)}개 사유 묶음 · "
        f"tool_calls={prop.get('tool_calls')} · reflections={prop.get('reflections')} "
        "— AI 제안, 확인 필요"
    )

    if "gate_rows" not in st.session_state:
        st.session_state["gate_rows"] = _gate_editor_rows(candidates)

    # P7.2(c) — 자동 검색이 못 가져온 정답을 사람이 키워드로 보강 회수(입력 1 + 버튼 1).
    with st.expander("🔎 후보 보강 검색 (자동 검색이 놓쳤을 때)"):
        mk = st.text_input("키워드", key="_gate_manual_kw",
                           placeholder="예: 하네스 커넥터 / Decor Panel")
        if st.button("보강 검색 추가", key="_gate_manual_btn") and mk.strip():
            from agent_client import manual_search_candidates
            added = manual_search_candidates(mk, base_model=prop.get("base_model") or None)
            new_rows = _gate_editor_rows(added)
            for r in new_rows:
                r["항목"] = "🔎수동"
                r["_manual_added"] = True  # P8 기록용(미회수율 측정 원천)
            st.session_state["gate_rows"] = list(st.session_state["gate_rows"]) + new_rows
            st.caption(f"{len(new_rows)}개 라인 추가됨 (manual_added). 아래 표에서 채택하세요.")

    st.markdown("**③ 확정** — '후보'(사유 묶음)별로 채택할 행 체크 + 변경점/신규 P/No 지정 (기본=거절)")
    st.caption("과거 base P/No → 과거 new P/No(제안)은 **과거 개발마스터 변경행에서 회수한 값**입니다"
               "(새로 생성한 추천 번호 아님). 출처 컬럼에서 참고한 원본 엑셀을 확인하세요.")
    edited = st.data_editor(
        st.session_state["gate_rows"],
        key="gate_editor",
        use_container_width=True,
        hide_index=True,
        column_order=_with_optional_cols(_GATE_COLS, st.session_state["gate_rows"]),
        column_config=_gate_column_config(),
    )

    if st.button("확정 후 하위 전개", key="gate_confirm", type="primary"):
        selections = _gate_selections(list(edited))
        if not any(s["accept"] for s in selections):
            st.warning("최소 한 행을 채택하세요. (확정 없이 트리 전개 안 함 — 절대원칙 #4)")
        else:
            with st.spinner("확정 닻 하위 전개 + 영향 판정 + 문서 생성 중..."):
                try:
                    st.session_state["gate_result"] = confirm_change_item(
                        prop["intent"], selections,
                        candidates=prop.get("candidates"),
                        retrieval_meta=prop.get("retrieval_meta"),
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"확정/전개 실패: {exc}")

    res = st.session_state.get("gate_result")
    if res:
        st.divider()
        _render_gate_result(res)


def _render_gate_tab() -> None:
    st.caption(
        "변경내용+사유 → 후보 부품 세트 검색(정지) → **사용자 확정**(채택/거절·변경점·신규 P/No) "
        "→ 확정 닻만 하위 전개. 확정이 트리 전개보다 먼저(절대원칙 #4). 신규 품번은 <발번대기>."
    )
    detail = st.text_area("변경 내용", height=90, placeholder="예: AC 모터를 BLDC 모터로 변경", key="_g_detail")
    reason = st.text_area("변경 사유", height=70, placeholder="예: 소음 저감 및 효율 개선", key="_g_reason")
    c1, c2 = st.columns(2)
    base_model = c1.text_input(
        "베이스 모델 (선택)", "", key="_g_bm",
        help="표기용 + 구조 부스트(SEARCH_INCLUDE_STRUCT=1) 시 BOM 파일 해석 키. 검색어엔 미주입.",
    )
    region = c2.text_input("지역 (선택, 필터)", "", key="_g_region")
    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("후보 검색", key="_g_propose", type="primary"):
        if detail.strip() or reason.strip():
            _gate_propose(detail, reason, base_model, region)
        else:
            st.warning("변경 내용 또는 사유를 입력하세요.")
    if b2.button("초기화", key="_g_reset"):
        for k in ("gate_proposal", "gate_result", "gate_rows", "gate_editor"):
            st.session_state.pop(k, None)

    _render_gate()


def _clear_ppt_state(*, keep_proposals: bool) -> None:
    """PPT 확정 세션 상태 정리 — 행 캐시 + 항목별 data_editor 위젯 키(동적)까지."""
    keys = ["ppt_rows_by_item", "ppt_result", "ppt_master_rows"]
    if not keep_proposals:
        keys.append("ppt_proposals")
    keys += [k for k in list(st.session_state) if str(k).startswith("_ppt_ed_")]
    for k in keys:
        st.session_state.pop(k, None)


def _render_ppt_tab() -> None:
    st.caption(
        "심의회 PPT(.pptx)에서 **변경사유·변경내용·베이스 모델**을 추출 → **검색할 변경항목을 골라** "
        "과거 master 후보를 찾고 → 확정 → (베이스 BOM 올리면) 베이스 트리에 적용해 New BOM을 만듭니다. "
        "한 번에 전 항목을 검색하지 않습니다 — 필요한 항목만 선택하세요."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        up = st.file_uploader("① 심의회 PPT", type=["pptx"], key="_agent_ppt")
    with col_b:
        bom_up = st.file_uploader("② 베이스 BOM", type=["xlsx"], key="_agent_basebom")

    if up is None:
        st.info("PPT를 업로드하면 추출값을 확인합니다. New BOM 생성에는 베이스 BOM도 필요합니다.")
        return

    file_key = f"{up.name}:{up.size}"
    if st.session_state.get("_ppt_key") != file_key:
        with st.spinner("PPT 추출 중 (LLM 0회, 결정론)..."):
            try:
                st.session_state["_ppt_extracted"] = extract_ppt(up.getvalue())
                st.session_state["_ppt_key"] = file_key
            except Exception as exc:  # noqa: BLE001 — UI에 오류 표시
                st.error(f"PPT 추출 실패: {exc}")
                return

    extracted = st.session_state.get("_ppt_extracted") or {}
    pm = extracted.get("project_meta") or {}
    items = extracted.get("items") or []

    bom_model = ""
    if bom_up is not None:
        bom_key = f"{bom_up.name}:{bom_up.size}"
        if st.session_state.get("_bom_key") != bom_key:
            with st.spinner("베이스 BOM 파싱 중..."):
                try:
                    st.session_state["_bom_extracted"] = extract_base_bom(
                        bom_up.getvalue(), file_name=bom_up.name
                    )
                    st.session_state["_bom_key"] = bom_key
                except Exception as exc:  # noqa: BLE001
                    st.error(f"베이스 BOM 파싱 실패: {exc}")
                    return
        bom_model = str((st.session_state.get("_bom_extracted") or {}).get("model") or "")

    st.subheader("추출 결과 확인/수정")
    ppt_bm = str(extracted.get("base_model") or "")
    st.caption(f"베이스 모델 추출값 — PPT: `{ppt_bm or '-'}` · BOM: `{bom_model or '-'}`")
    c1, c2 = st.columns(2)
    with c1:
        base_model = st.text_input(
            "베이스 모델 (최종 확정)", value=str(ppt_bm or bom_model or ""),
            help="PPT/BOM 추출값 중 맞는 것을 고르거나 직접 수정하세요.",
        )
        st.text_input("제품군", value=str(pm.get("product_type") or ""), disabled=True)
    with c2:
        st.text_input("프로젝트명", value=str(pm.get("project_name") or ""), disabled=True)
        st.text_input("출시 국가", value=str(pm.get("target_country") or ""), disabled=True)

    if not items:
        st.warning("변경항목을 찾지 못했습니다. PPT의 '유첨1. 개발 변경점 상세' 슬라이드 구조를 확인하세요.")
        return

    # ── ① 변경항목 선택 — 한 번에 전부가 아니라 **검색할 항목만 고른다** ──
    st.markdown(f"**① 변경항목 {len(items)}개 — 검색할 항목 선택**")
    st.caption("‘검색’ 체크 → 고른 항목만 과거 master 후보를 찾습니다 (전 항목 일괄 검색 안 함).")
    item_rows = [
        {
            "검색": False,
            "#": i + 1,
            "모듈": it["module"],
            "변경내용": it["change_detail"],
            "변경사유": it["change_reason"],
            "유형": it["change_type"],
            "base P/N": it["base_part_no"],
            "new P/N": it["new_part_no"],
        }
        for i, it in enumerate(items)
    ]
    edited_items = st.data_editor(
        item_rows, key="_ppt_item_select", use_container_width=True, hide_index=True,
        column_config={
            "검색": st.column_config.CheckboxColumn("검색", help="이 항목만 후보 검색"),
            "#": st.column_config.NumberColumn("#", disabled=True, width="small"),
            "모듈": st.column_config.TextColumn("모듈", disabled=True),
            "변경내용": st.column_config.TextColumn("변경내용", disabled=True),
            "변경사유": st.column_config.TextColumn("변경사유", disabled=True),
            "유형": st.column_config.TextColumn("유형", disabled=True, width="small"),
            "base P/N": st.column_config.TextColumn("base P/N", disabled=True),
            "new P/N": st.column_config.TextColumn("new P/N", disabled=True),
        },
    )
    selected_idx = {int(r["#"]) - 1 for r in edited_items if r.get("검색")}

    # ── HITL 단일 흐름: ① 선택 항목 검색(정지) → ③ 확정 → ④ 전개 + New BOM (절대원칙 #4) ──
    bm = base_model.strip() or None
    c1, c2, _ = st.columns([1.6, 1, 3])
    if c1.button(f"① 선택 항목 후보 검색 ({len(selected_idx)}개 · HITL 정지)",
                 type="primary", key="_ppt_propose", disabled=not selected_idx):
        with st.spinner(f"선택 {len(selected_idx)}개 항목 과거 master 후보 검색 중 (트리 전개 없음)..."):
            try:
                st.session_state["ppt_proposals"] = propose_items(
                    items, base_model=bm, select_indices=selected_idx
                )
            except Exception as exc:  # noqa: BLE001 — UI에 오류 표시
                st.error(f"후보 검색 실패: {exc}")
                return
        _clear_ppt_state(keep_proposals=True)
    if not selected_idx:
        st.caption("⬆️ 검색할 변경항목을 한 개 이상 선택하세요.")
    if c2.button("초기화", key="_ppt_reset"):
        _clear_ppt_state(keep_proposals=False)

    proposals = st.session_state.get("ppt_proposals")
    if not proposals:
        st.info("‘후보 검색’을 누르면 변경사유+내용만으로 과거 master 후보를 찾습니다 (확정 전 트리/New BOM 전개 없음).")
        return

    total_cands = sum(len(p.get("candidates", [])) for p in proposals)
    st.markdown(
        f"**② 후보 부품 세트** — 선택 {len(proposals)}개 항목 · 사유 묶음 {total_cands}개 "
        "— AI 제안, 확인 필요."
    )
    # P7.2 — 저신뢰 항목(선례 없거나 검색이 못 찾았을 수 있음) 배지.
    low_items = [
        f"#{int(p.get('item_index', 0)) + 1}"
        for p in proposals if (p.get("retrieval_meta") or {}).get("low_confidence")
    ]
    if low_items:
        st.warning(
            f"🔎 검색 신뢰 낮음: 항목 {', '.join(low_items)} — 선례가 없거나 검색이 못 찾았을 수 "
            "있습니다. 아래에서 신중히 확인하고, 필요하면 키워드로 보강 검색하세요."
        )
    derived = [
        f"#{int(p.get('item_index', 0)) + 1} {ds}"
        for p in proposals
        if (ds := _derived_summary(p.get("intent") or {}))
    ]
    if derived:
        with st.expander(f"🧩 자동요약 — 변경점 유도 v3 ({len(derived)}건, 검색 보강용)"):
            for line in derived:
                st.caption(line)
    if total_cands == 0:
        st.warning(
            "과거 사례 후보가 없습니다 (Predict). `db change-events --embed`로 change_event 적재를 "
            "확인하거나, 채택 후 신규 P/No를 직접 입력해 확정하세요."
        )

    # ── ③ 확정 — 변경항목별 토글(expander) 안에 그 항목의 후보들을 펼쳐 보여준다 ──
    st.markdown("**③ 확정** — 변경항목을 펼쳐 '후보'(사유 묶음)별로 채택 (기본=거절)")
    st.caption("과거 base P/No → 과거 new P/No(제안)은 **과거 개발마스터에서 회수한 값**(새 추천 번호 아님)이며, "
               "출처 컬럼에 참고한 원본 엑셀이 표시됩니다.")
    # 항목별 행을 세션에 1회 구성(편집 상태는 키별 data_editor가 보존).
    if "ppt_rows_by_item" not in st.session_state:
        st.session_state["ppt_rows_by_item"] = {
            int(p["item_index"]): _item_rows(p) for p in proposals
        }
    rows_by_item = st.session_state["ppt_rows_by_item"]

    cfg = _gate_column_config()
    all_edited: list[dict[str, Any]] = []
    for p in proposals:
        idx = int(p.get("item_index", 0))
        item = p.get("item", {})
        cands = p.get("candidates", [])
        low = bool((p.get("retrieval_meta") or {}).get("low_confidence"))
        head = (f"#{idx + 1} [{item.get('module', '') or '-'}] "
                f"{(item.get('change_detail') or '')[:36]} · 후보 {len(cands)}묶음"
                + (" · 🔎저신뢰" if low else ""))
        with st.expander(head, expanded=(len(proposals) == 1)):
            if (ds := _derived_summary(p.get("intent") or {})):
                st.caption(ds)
            rows_i = rows_by_item.get(idx, [])
            if not rows_i:
                st.info("후보 없음 (Predict) — 이 항목은 선례가 없습니다. "
                        "수동 입력 탭에서 신규 P/No로 직접 확정하세요.")
                continue
            edited_i = st.data_editor(
                rows_i,
                key=f"_ppt_ed_{idx}",
                use_container_width=True,
                hide_index=True,
                column_order=_with_optional_cols(_PPT_GATE_COLS, rows_i),
                column_config=cfg,
            )
            all_edited.extend(edited_i)

    newbom_help = "" if bom_up is not None else "New BOM 트리복제에는 베이스 BOM 업로드 필요(전개·판정은 가능)"
    if st.button("④ 확정 후 하위 전개 + New BOM", type="primary", key="_ppt_confirm", help=newbom_help):
        selections = _ppt_selections(all_edited)
        if not any(s["accept"] for s in selections):
            st.warning("최소 한 행을 채택하세요. (확정 없이 트리/New BOM 전개 안 함 — 절대원칙 #4)")
        else:
            with st.spinner("확정 닻 하위 전개 + 영향 판정 + 베이스 BOM 적용(New BOM) 중..."):
                try:
                    st.session_state["ppt_result"] = confirm_items(
                        proposals=proposals,
                        selections=selections,
                        base_bom_bytes=(bom_up.getvalue() if bom_up is not None else None),
                        base_bom_name=(bom_up.name if bom_up is not None else "base_bom.xlsx"),
                        base_model=bm,
                    )
                    # 새 확정 → 이전 변경리스트 편집 캐시 무효화(새 new_bom로 다시 구성).
                    st.session_state.pop("ppt_master_rows", None)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"확정/전개 실패: {exc}")

    result = st.session_state.get("ppt_result")
    if not result:
        return
    st.divider()
    new_bom = result.get("new_bom")
    if new_bom:
        st.subheader("New BOM (베이스 트리 복제 + 변경 적용)")
        _render_newbom(new_bom)
    elif bom_up is None:
        st.info("New BOM(트리복제)을 생성하려면 베이스 BOM(.xlsx)을 업로드한 뒤 다시 확정하세요.")
    st.markdown("---")
    st.subheader("통합 개발부품 Master 검수 + 내보내기 (Compact v1.1)")
    _render_master_export(result, key_prefix="ppt")
    st.markdown("---")
    st.subheader("변경항목별 확정 전개 (하위 트리 + 영향 판정 + 문서)")
    for r in result.get("items", []):
        item = r.get("item", {})
        head = f"#{int(r.get('item_index', 0)) + 1} [{item.get('module', '')}] {(item.get('change_detail') or '')[:40]}"
        with st.expander(head, expanded=False):
            _render_gate_result(r["confirmed"])


# ════════════════════════════════════════════════════════════════
# 통합 개발부품 Master 검수/편집 + 내보내기 (Compact v1.1 형식).
# 확정 결과(new_bom)에서 변경 리스트를 만들어 사용자가 변경사유/분류를 편하게 편집한 뒤
# v1.1 서식 xlsx로 내보낸다. 분류 한글 셀렉트(신규/변경/기존/삭제)→영어(New/Change/Common/
# Delete) 매핑은 내보내기 시 적용(agent_client). 절대원칙: 신규 무번호 → <발번대기>.
# ════════════════════════════════════════════════════════════════

# 변경 리스트 에디터 컬럼 — 식별 컬럼은 읽기전용, 변경내용/변경사유/분류만 편집 가능.
_MASTER_EDIT_COLS = [
    "BOM Level", "Part Type", "부품명", "Base P/No", "New P/No", "Q'ty",
    "변경내용", "변경사유", "분류",
]


def _master_editor_config() -> dict[str, Any]:
    ro = st.column_config.TextColumn
    return {
        "BOM Level": ro("BOM Level", disabled=True, width="small"),
        "Part Type": ro("Part Type", disabled=True, width="small"),
        "부품명": ro("부품명", disabled=True),
        "Base P/No": ro("Base P/No", disabled=True, help="베이스 BOM 품번(읽기전용)"),
        "New P/No": ro("New P/No", disabled=True, help="확정 New 품번 — 신규 무번호는 <발번대기>"),
        "Q'ty": ro("Q'ty", disabled=True, width="small"),
        "변경내용": st.column_config.TextColumn(
            "변경내용", help="이 변경의 내용(자유 편집)"
        ),
        "변경사유": st.column_config.TextColumn(
            "변경사유", help="변경사유 — 자유롭게 편집하세요(내보내기 K열)"
        ),
        "분류": st.column_config.SelectboxColumn(
            "분류", options=list(CLASSIFICATION_KO_OPTIONS),
            help="신규/변경/기존/삭제 → 내보내기 시 New/Change/Common/Delete로 변환",
        ),
    }


def _render_master_export(result: dict[str, Any], *, key_prefix: str) -> None:
    """확정 결과 → 변경 리스트 검수/편집 → 통합 master(v1.1) 내보내기 + (선택) 정확도.

    편집 상태는 session_state[``{key_prefix}_master_rows``]에 보관해 재실행에도 보존한다.
    new_bom이 없으면(베이스 BOM 미업로드) 조용히 안내만 한다.
    """
    rows_key = f"{key_prefix}_master_rows"
    if rows_key not in st.session_state:
        st.session_state[rows_key] = change_list_from_result(result, changed_only=True)
    base_rows = st.session_state[rows_key]
    if not base_rows:
        st.info(
            "내보낼 변경 리스트가 없습니다. 베이스 BOM(.xlsx)을 업로드하고 확정하면 "
            "변경 부품 기반 통합 master를 편집/내보낼 수 있습니다."
        )
        return

    st.markdown("**검수/편집** — 변경사유는 자유롭게, 분류는 셀렉트(신규/변경/기존/삭제)로 고치세요.")
    st.caption("부품명·Base/New P/No는 읽기전용(확정값). 편집은 재실행에도 보존됩니다. "
               "신규 부품에 번호가 없으면 내보내기 시 `<발번대기>`로 표기됩니다(시스템 무생성).")
    edited = st.data_editor(
        base_rows,
        key=f"{key_prefix}_master_editor",
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_order=_MASTER_EDIT_COLS,
        column_config=_master_editor_config(),
    )
    # 편집 결과를 세션에 되써서 다음 재실행/내보내기에 반영.
    st.session_state[rows_key] = list(edited)

    with st.expander("내보낼 행 미리보기 (Compact v1.1 매핑)", expanded=False):
        try:
            st.dataframe(export_rows_preview(list(edited)), use_container_width=True)
        except Exception as exc:  # noqa: BLE001
            st.caption(f"미리보기 생성 실패: {exc}")

    cols = st.columns([2, 2, 3])
    xlsx_bytes: bytes | None = None
    try:
        xlsx_bytes = build_master_xlsx(list(edited))
    except Exception as exc:  # noqa: BLE001 — 템플릿 부재 등
        cols[0].caption(f"⚠️ 내보내기 생성 실패: {exc}")
    if xlsx_bytes is not None:
        cols[0].download_button(
            "통합 개발부품 Master 내보내기",
            data=xlsx_bytes,
            file_name="compact_oven_master.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key=f"{key_prefix}_master_dl",
        )

    # ── (선택) 정확도 패널 — 정답지(v1.1)가 있으면 방금 산출물을 채점. 절대 크래시 금지. ──
    with st.expander("정확도(정답지 대비) — 선택", expanded=False):
        st.caption(f"정답지(oracle): `{master_template_path()}`")
        if st.button("방금 내보낸 master 채점", key=f"{key_prefix}_score_btn",
                     disabled=(xlsx_bytes is None)):
            try:
                rep = score_master_xlsx(xlsx_bytes or b"")
                gating = rep.get("gating", {})
                rowm = rep.get("row_matching", {})
                st.json({
                    "scope": rep.get("scope"),
                    "row_matching": rowm,
                    "gating": {
                        "base_pno": gating.get("base_pno"),
                        "new_pno": gating.get("new_pno"),
                        "classification": gating.get("classification"),
                        "overall_gating_acc": gating.get("overall_gating_acc"),
                    },
                    "common_carryover_coverage": rep.get("common_carryover_coverage"),
                })
            except Exception as exc:  # noqa: BLE001 — 정답지 부재/형식 차이 등
                st.caption(f"채점 불가(정답지 부재 또는 형식 차이): {exc}")


# ════════════════════════════════════════════════════════════════
# 통합 Master — base BOM ↔ new BOM diff 기반 (결정론 가지치기 + 사유 보강).
# 후보-확정 경로와 **별개**다. base/new BOM(+선택 PPT)을 올리면 집합차로 변경 리스트를 만들고
# (confidence + 사유 초안), 사용자가 사유/분류를 편집한 뒤 고신뢰만 필터링해 v1.1 xlsx로
# 내보낸다. 세션 키는 ``bomdiff_`` 접두로 기존 키와 충돌 방지. 절대원칙: 신규 무번호→<발번대기>.
# ════════════════════════════════════════════════════════════════

# 변경행 에디터 컬럼 — 식별/신뢰도 읽기전용, 변경사유 자유편집, 분류 셀렉트.
_BOMDIFF_EDIT_COLS = [
    "bom_level", "part_type", "part_name", "base_pno", "new_pno", "qty",
    "confidence", "classification", "changing_reason",
]


def _bomdiff_editor_config() -> dict[str, Any]:
    ro = st.column_config.TextColumn
    return {
        "bom_level": ro("BOM Level", disabled=True, width="small"),
        "part_type": ro("Part Type", disabled=True, width="small"),
        "part_name": ro("부품명", disabled=True),
        "base_pno": ro("Base P/No", disabled=True, help="베이스 BOM 품번(읽기전용)"),
        "new_pno": ro("New P/No", disabled=True, help="new BOM 매칭 품번 — 신규 무번호는 <발번대기>"),
        "qty": ro("Q'ty", disabled=True, width="small"),
        "confidence": ro(
            "신뢰도", disabled=True, width="small",
            help="high=고유 부품명 번호교체 / medium=중복명 교체·삭제 / low=신규(base 대응 없음)",
        ),
        "classification": st.column_config.SelectboxColumn(
            "분류", options=["New", "Change", "Delete", "Common"],
            help="diff 영어 라벨(New/Change/Delete) — 셀렉트로 수정. 내보내기 시 그대로 사용.",
        ),
        "changing_reason": st.column_config.TextColumn(
            "변경사유", help="PPT 보강 초안 — 자유롭게 편집하세요(내보내기 K열).",
        ),
    }


def _bomdiff_ppt_bytes() -> bytes | None:
    """PPT 탭에서 이미 올린 PPT bytes를 session_state에서 회수(사유 보강용). 없으면 None."""
    up = st.session_state.get("_agent_ppt")
    try:
        return up.getvalue() if up is not None else None
    except Exception:  # noqa: BLE001
        return None


def _render_bomdiff_tab() -> None:
    st.caption(
        "베이스 BOM과 New BOM을 올리면 **집합차(diff)** 로 변경 부품을 자동 추출하고, 가지치기로 "
        "**신뢰도**(high/medium/low)를 매깁니다. PPT를 올려두었으면 변경사유 초안을 자동 채웁니다. "
        "사유/분류를 편집한 뒤 **고신뢰만 보기**로 정밀도 높은 집합을 골라 v1.1 서식으로 내보내세요. "
        "LLM/DB 0회 · 후보-확정 경로와 무관."
    )
    c_a, c_b = st.columns(2)
    with c_a:
        base_up = st.file_uploader("① 베이스 BOM (.xlsx)", type=["xlsx"], key="bomdiff_base")
    with c_b:
        new_up = st.file_uploader("② New BOM (.xlsx)", type=["xlsx"], key="bomdiff_new")

    ppt_bytes = _bomdiff_ppt_bytes()
    st.caption(
        ("🧩 PPT 사유 보강: 사용 가능 (PPT 탭에 업로드된 PPT 재사용)" if ppt_bytes
         else "ℹ️ 사유 보강용 PPT 없음 — PPT 탭에서 .pptx를 올리면 변경사유 초안이 자동 채워집니다.")
    )

    if base_up is None or new_up is None:
        st.info("베이스 BOM과 New BOM을 모두 업로드하면 변경 리스트를 만듭니다.")
        return

    if st.button("변경 리스트 만들기 (diff + 가지치기 + 사유 보강)", type="primary",
                 key="bomdiff_build"):
        with st.spinner("base↔new BOM diff + 가지치기 + 사유 보강 중 (결정론)..."):
            try:
                st.session_state["bomdiff_result"] = bom_diff_change_list(
                    base_up.getvalue(), base_up.name,
                    new_up.getvalue(), new_up.name,
                    ppt_bytes=ppt_bytes,
                )
                st.session_state.pop("bomdiff_rows", None)  # 새 결과 → 편집 캐시 무효화
            except Exception as exc:  # noqa: BLE001 — UI에 표시, 절대 크래시 금지
                st.error(f"변경 리스트 생성 실패: {exc}")
                return

    result = st.session_state.get("bomdiff_result")
    if not result:
        return

    summary = result.get("summary") or {}
    by_conf = summary.get("changed_by_confidence") or {}
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("변경 건수", summary.get("changed", 0))
    m2.metric("Common 이월", result.get("common_count", 0))
    m3.metric("high", by_conf.get("high", 0))
    m4.metric("medium", by_conf.get("medium", 0))
    m5.metric("low", by_conf.get("low", 0))

    high_only = st.checkbox(
        "고신뢰만 보기 (high+medium)", value=False, key="bomdiff_high_only",
        help="체크 시 신뢰도 high·medium 변경행만 표시·내보냅니다(low 신규 잡음 제외).",
    )
    keep = {"high", "medium"} if high_only else None

    # 편집 캐시 — 전체 변경행 보관(필터는 표시 시점에만 적용; 편집은 키별로 보존).
    if "bomdiff_rows" not in st.session_state:
        st.session_state["bomdiff_rows"] = [dict(r) for r in (result.get("rows") or [])]
    all_rows = st.session_state["bomdiff_rows"]
    if not all_rows:
        st.warning("변경 행이 없습니다 (base와 new가 동일하거나 모두 Common).")
        return

    view_rows = [r for r in all_rows if keep is None or (r.get("confidence") in keep)]
    st.markdown(
        f"**변경 리스트** — 표시 {len(view_rows)}행 / 전체 {len(all_rows)}행. "
        "변경사유는 자유 편집, 분류는 셀렉트로 수정하세요 (식별·신뢰도는 읽기전용)."
    )
    edited = st.data_editor(
        view_rows,
        key="bomdiff_editor",
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_order=_BOMDIFF_EDIT_COLS,
        column_config=_bomdiff_editor_config(),
    )

    # 편집분을 전체 캐시에 머지 — (base,new,name) 키로 되써서 필터 토글에도 편집 보존.
    def _k(r: dict[str, Any]) -> tuple[str, str, str]:
        return (str(r.get("base_pno") or "").upper(), str(r.get("new_pno") or "").upper(),
                str(r.get("part_name") or "").upper())
    edited_by_key = {_k(r): r for r in edited}
    merged: list[dict[str, Any]] = []
    for r in all_rows:
        e = edited_by_key.get(_k(r))
        if e is not None:
            rr = dict(r)
            rr["changing_reason"] = e.get("changing_reason", r.get("changing_reason"))
            rr["classification"] = e.get("classification", r.get("classification"))
            merged.append(rr)
        else:
            merged.append(r)
    st.session_state["bomdiff_rows"] = merged

    include_common = st.checkbox(
        "Common 이월행 포함 (v1.1 전체 형태)", value=True, key="bomdiff_include_common",
        help="끄면 변경행만 내보냅니다. 정답지 형태는 Common 이월 포함.",
    )

    xlsx_bytes: bytes | None = None
    try:
        xlsx_bytes = build_master_from_diff_rows(
            st.session_state["bomdiff_rows"],
            base_up.getvalue(), base_up.name,
            new_up.getvalue(), new_up.name,
            include_common=include_common,
            confidence_keep=keep,
        )
    except Exception as exc:  # noqa: BLE001 — 템플릿 부재 등
        st.caption(f"⚠️ 내보내기 생성 실패: {exc}")

    if xlsx_bytes is not None:
        st.download_button(
            "통합 Master 내보내기 (BOM diff)",
            data=xlsx_bytes,
            file_name="compact_oven_master_bomdiff.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="bomdiff_dl",
        )

    with st.expander("정확도(정답지 대비) — 선택", expanded=False):
        st.caption(f"정답지(oracle): `{master_template_path()}`")
        if st.button("방금 내보낸 master 채점", key="bomdiff_score_btn",
                     disabled=(xlsx_bytes is None)):
            try:
                rep = score_master_xlsx(xlsx_bytes or b"")
                gating = rep.get("gating", {})
                st.json({
                    "scope": rep.get("scope"),
                    "row_matching": rep.get("row_matching", {}),
                    "gating": {
                        "base_pno": gating.get("base_pno"),
                        "new_pno": gating.get("new_pno"),
                        "classification": gating.get("classification"),
                        "overall_gating_acc": gating.get("overall_gating_acc"),
                    },
                    "common_carryover_coverage": rep.get("common_carryover_coverage"),
                })
            except Exception as exc:  # noqa: BLE001
                st.caption(f"채점 불가(정답지 부재 또는 형식 차이): {exc}")

    if st.button("초기화", key="bomdiff_reset"):
        for k in ("bomdiff_result", "bomdiff_rows", "bomdiff_editor"):
            st.session_state.pop(k, None)


def render() -> None:
    """페이지 본문 — set_page_config는 호출부(통합 main.py 또는 standalone) 책임."""
    inject_card_css()  # 화이트 모드 카드 스타일(전역 1회)
    st.title("BOM 변경 영향 분석 에이전트 (L1~L4)")
    st.caption(
        "흐름: PPT/수동 입력 → **검색할 변경항목 선택** → 후보 검색(정지) → **확정**(채택·변경점·신규 P/No) "
        "→ 확정 닻만 하위 전개 + 베이스 트리 New BOM. 확정이 트리 전개보다 먼저(절대원칙 #4)."
    )
    card_tab, ppt_tab, manual_tab, bomdiff_tab = st.tabs(
        ["🔎 후보 검토 (카드 뷰)", "PPT 업로드", "수동 입력 (HITL)",
         "통합 Master — BOM diff 기반 (가지치기 + 사유 보강)"]
    )
    with card_tab:
        render_card_tab()
    with ppt_tab:
        _render_ppt_tab()
    with manual_tab:
        _render_gate_tab()
    with bomdiff_tab:
        _render_bomdiff_tab()


if __name__ == "__main__":
    st.set_page_config(page_title="BOM 변경 영향 분석 에이전트", layout="wide")
    render()
