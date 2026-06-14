"""정형화·검색 검수 UI (별도 페이지).

입력 소스:
- **심의회 PPT(.pptx)**: 결정론 추출기로 base_model + 변경항목(변경내역/변경사유)을 뽑아 폼에 채움.
- **베이스 BOM(.xlsx/.xlsm)**: 트리 스냅샷 파싱 — 후보 부품이 베이스 BOM에 있는지 표시.
- 수기 입력(변경내역/변경사유).

처리: ① 질문 정형화(ChangeIntent; Claude 보강) → ② 과거 change_event hybrid 검색(semantic+lexical)
후보·점수·트레이스를 검수.

실행:
    cd lg_data_pipeline
    # .env (gitignored): LLM_PROVIDER=anthropic / ANTHROPIC_API_KEY=sk-ant-... / ANTHROPIC_MODEL=claude-sonnet-4-6
    # ENABLE_EMBEDDING=1 + Ollama(bge-m3) 시 hybrid, 아니면 lexical-only로 폴백.
    streamlit run src/ui/inspect_app.py

설계 메모:
- **검색은 reason-only 쿼리만**(절대원칙: 검색 키=변경내역+변경사유). PPT의 ChangeItem도
  search_text=변경내용+변경사유만 사용(식별자 제외). Claude 재작성은 표시·비교 전용.
- 정형화(LLM)+검색은 **제출 시 1회**만 수행해 캐시(검수는 read-only, tool_call_log 미기록).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── src 패키지 import 경로 보정 — 중첩 패키지 루트를 항상 sys.path 맨 앞으로 ──────────
_PKG_ROOT = Path(__file__).resolve().parents[2]  # .../lg_data_pipeline
_pkg_root_s = str(_PKG_ROOT)
sys.path = [_pkg_root_s] + [p for p in sys.path if p != _pkg_root_s]

try:
    from dotenv import load_dotenv

    load_dotenv(_PKG_ROOT / ".env")
except ModuleNotFoundError:
    pass

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.agent.basebom.parser import parse_base_bom, walk_base_bom_subtree  # noqa: E402
from src.agent.inspect import (  # noqa: E402
    BackendStatus,
    build_llm_from_env,
    detect_backend,
    expand_bom_db,
    formalize,
    run_search,
)
from src.agent.intent.models import ChangeIntent  # noqa: E402
from src.agent.ppt.extractor import (  # noqa: E402
    change_items_from_extraction,
    extract_change_review_from_pptx_bytes,
)

_EXAMPLES = [
    ("외관 변경 (STS → BK STS)", "중아향 전용 Controller 외관 변경", ""),
    ("카탈루냐어 추가", "스페인 규제 대응을 위한 카탈루냐어 추가", ""),
    ("에너지 라벨 양식으로 변경", "국가별 에너지 규제 양식 다름", ""),
    ("Assembly 하위 품번 변경", "외관 변경(STS → BK Glass)에 따른 하위 전개", ""),
]


@st.cache_resource(show_spinner=False)
def _session_factory():
    try:
        from src.db.engine import make_engine, session_factory

        return session_factory(make_engine())
    except Exception:  # noqa: BLE001
        return None


def _norm_pno(p: str) -> str:
    return re.sub(r"\s+", "", str(p or "")).upper()


def _intent_table(intent: ChangeIntent) -> pd.DataFrame:
    # 변경점 유도 v3(DERIVE_CP) 자동요약 — 슬롯이 있을 때만 의미 있는 값.
    slots = getattr(intent, "derived_change_slots", []) or []
    derived = " · ".join(
        " ".join(p for p in (s.target, s.attribute, s.action) if p) for s in slots
    )
    src = {"det": "결정론", "llm": "LLM"}.get(
        getattr(intent, "derived_cp_source", None) or "", ""
    )
    return pd.DataFrame(
        {
            "필드": [
                "raw_text", "change_attribute(속성)", "change_direction(방향)",
                "intent_summary(요약)", "자동요약(변경점 유도)", "confidence", "source",
                "part_nos", "models", "module_names", "region", "base_model",
            ],
            "값": [
                intent.raw_text,
                intent.change_attribute or "-",
                intent.change_direction or "-",
                intent.intent_summary or "-",
                (f"{derived} ({src})" if derived else "-"),
                f"{intent.confidence:.2f}",
                intent.source,
                ", ".join(intent.part_nos) or "-",
                ", ".join(intent.models) or "-",
                ", ".join(getattr(intent, "module_names", []) or []) or "-",
                intent.region or "-",
                intent.base_model or "-",
            ],
        }
    )


def _sidebar(backend: BackendStatus) -> object | None:
    st.sidebar.header("상태")
    llm, llm_err = build_llm_from_env()
    if llm is not None:
        st.sidebar.success(
            f"LLM: **{getattr(llm, 'provider', '?')}** / `{getattr(llm, 'model', '?')}`"
        )
    else:
        st.sidebar.warning(f"LLM 비활성 — 결정론 정형화만 표시\n\n{llm_err}")
    key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    st.sidebar.caption(
        f"ANTHROPIC_API_KEY: {'설정됨 ✅' if key_set else '미설정 ⚠️'} · "
        f"LLM_PROVIDER=`{os.environ.get('LLM_PROVIDER', 'ollama')}`"
    )
    icon = {"ok": "✅", "no_data": "⚠️", "no_backend": "⛔"}.get(backend.status, "⛔")
    st.sidebar.write(f"검색 백엔드: {icon} `{backend.status}`")
    st.sidebar.caption(f"{backend.detail}\n\nmode: `{backend.mode}`")
    from src.agent.intent.derive import env_flag

    st.sidebar.caption(
        "게이트: parts=`{}` · DERIVE_CP=`{}` · STRUCT=`{}`".format(
            "on" if env_flag("SEARCH_INCLUDE_PARTS", default=True) else "off",
            "on" if env_flag("DERIVE_CP", default=True) else "off",
            "on" if env_flag("SEARCH_INCLUDE_STRUCT", default=False) else "off",
        )
    )
    if backend.status != "ok":
        st.sidebar.info(
            "검색을 켜려면: `docker compose up -d` → `python -m src.cli db init` → "
            "`db load --embed` → change_event 적재. (임베딩 없으면 lexical-only)"
        )
    return llm


def _parse_ppt(data: bytes):
    """심의회 PPT bytes → (ChangeItem 리스트, project_meta, error)."""
    try:
        ext = extract_change_review_from_pptx_bytes(data)
        items = change_items_from_extraction(ext)
        meta = ext.get("project_meta") or {}
        if not items:
            return [], meta, "PPT에서 변경항목을 찾지 못했습니다 (유첨 변경표 슬라이드 확인)."
        return items, meta, None
    except Exception as e:  # noqa: BLE001
        return [], {}, f"PPT 파싱 실패: {type(e).__name__}: {e}"


def _render_sources() -> None:
    """입력 소스: PPT 업로드 / 베이스 BOM 업로드 / 예시. ex_* 세션값을 채워 폼에 반영."""
    with st.expander("입력 소스 — 심의회 PPT / 베이스 BOM 업로드 · 예시", expanded=True):
        c1, c2 = st.columns(2)
        # ── PPT ──
        with c1:
            st.markdown("**① 심의회 PPT (.pptx)**")
            up = st.file_uploader("PPT", type=["pptx"], key="ppt_up", label_visibility="collapsed")
            if up is not None and st.session_state.get("ppt_name") != up.name:
                items, meta, err = _parse_ppt(up.getvalue())
                st.session_state["ppt_name"] = up.name
                st.session_state["ppt_items"] = items
                st.session_state["ppt_meta"] = meta
                st.session_state["ppt_err"] = err
            if st.session_state.get("ppt_err"):
                st.error(st.session_state["ppt_err"])
            items = st.session_state.get("ppt_items") or []
            meta = st.session_state.get("ppt_meta") or {}
            if items:
                st.caption(f"추출: 변경항목 **{len(items)}건** · base_model=`{meta.get('base_model', '-')}`")
                labels = [f"{i + 1}. {(it.display or it.change_detail)[:55]}" for i, it in enumerate(items)]
                sel = st.selectbox("변경항목", range(len(items)), format_func=lambda i: labels[i], key="ppt_sel")
                if st.button("선택 항목으로 입력 채우기", key="ppt_fill", width="stretch"):
                    it = items[sel]
                    st.session_state.ex_log = it.change_detail if it.change_detail != "정보 없음" else ""
                    st.session_state.ex_reason = it.change_reason if it.change_reason != "정보 없음" else ""
                    bm = str(meta.get("base_model") or "")
                    if bm and bm != "정보 없음":
                        st.session_state.ex_model = bm
        # ── 베이스 BOM ──
        with c2:
            st.markdown("**② 베이스 BOM (.xlsx/.xlsm)**")
            bup = st.file_uploader("BOM", type=["xlsx", "xlsm"], key="bom_up", label_visibility="collapsed")
            if bup is not None and st.session_state.get("bom_name") != bup.name:
                try:
                    st.session_state["base_bom"] = parse_base_bom(bup.getvalue(), file_name=bup.name)
                    st.session_state["bom_name"] = bup.name
                    st.session_state["bom_err"] = None
                except Exception as e:  # noqa: BLE001
                    st.session_state["bom_err"] = f"BOM 파싱 실패: {type(e).__name__}: {e}"
            if st.session_state.get("bom_err"):
                st.error(st.session_state["bom_err"])
            bb = st.session_state.get("base_bom")
            if bb is not None:
                st.caption(f"BOM: model=`{bb.model or '-'}` · **{len(bb.rows)}행** (후보 부품 BOM 존재 여부 ②에 표시)")
                if bb.model and st.button("베이스모델로 채우기", key="bom_fill", width="stretch"):
                    st.session_state.ex_model = bb.model
                with st.expander("BOM 트리 (상위 300행)"):
                    st.dataframe(
                        pd.DataFrame(
                            [{"lvl": r.lvl, "depth": r.depth, "part_no": r.part_no,
                              "part_name": r.part_name, "path": r.bom_path} for r in bb.rows[:300]]
                        ),
                        hide_index=True, width="stretch",
                    )
        # ── 예시 ──
        st.markdown("**예시 (적재 데이터 매칭)**")
        ecols = st.columns(len(_EXAMPLES))
        for i, (lg, rs, md) in enumerate(_EXAMPLES):
            if ecols[i].button(f"예시 {i + 1}", key=f"ex{i}", width="stretch"):
                st.session_state.ex_log, st.session_state.ex_reason, st.session_state.ex_model = lg, rs, md


def _render_formalization(fv) -> None:
    st.subheader("① 질문 정형화 (Formalization)")
    st.caption(
        "의미 인덱스는 **변경내역+변경사유**(reason_embedding)가 정답. 부품명/품번을 입력하면 "
        "**parts 채널**(부품명·품번·모델 word_similarity)이 RRF에 더해집니다(2026-06-10 개정). "
        "구분/걱정점은 검색 미사용. Claude 재작성도 검색에 함께 쓰되 환각 식별자는 sanitize."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**결정론 (사유 + 부품 보강) — 검색에 사용**")
        st.dataframe(_intent_table(fv.reason_intent), hide_index=True, width="stretch")
        st.markdown("검색 쿼리(사유 기준):")
        for q in fv.reason_queries:
            st.code(q, language=None)
    with col_b:
        if fv.llm_intent is not None:
            st.markdown(f"**Claude 보강** (`{fv.llm_provider}` / `{fv.llm_model}`)")
            if fv.llm_intent.source != "regex+llm":
                st.warning(
                    f"⚠️ LLM 슬롯 미반영 — 결정론 폴백(source=`{fv.llm_intent.source}`). "
                    "네트워크/키/모델 응답을 확인하세요."
                )
            st.dataframe(_intent_table(fv.llm_intent), hide_index=True, width="stretch")
            st.markdown("Claude 재작성 쿼리 (검색에 함께 사용):")
            for q in fv.llm_queries:
                st.code(q, language=None)
            st.caption(f"→ 실제 검색에 투입된 쿼리 {len(fv.search_queries)}개 (reason + Claude 재작성, 식별자 sanitize)")
        elif fv.llm_error:
            st.markdown("**Claude 보강**")
            st.error(f"LLM 호출 실패 — 결정론 경로 사용\n\n{fv.llm_error}")
        else:
            st.info("LLM 미설정 — 결정론 정형화만 표시 중. .env에 Claude 키를 넣으면 보강됩니다.")


def _render_search(sv, queries: list[str], base_bom) -> None:
    st.subheader("② 변경점·변경사항 검색 (change_event 회수)")
    if sv is None:
        st.info("아직 검색하지 않았습니다 — 위에서 '정형화 + 검색 실행'을 누르세요.")
        return
    st.caption(
        f"backend: `{sv.mode}` · 사용 쿼리 {len(queries)}개(reason+Claude 재작성) · "
        f"reflection {sv.reflections}회 · 검수 모드(tool_call_log 미기록)"
    )
    if sv.status != "ok":
        if sv.status in ("no_backend", "no_data"):
            st.warning(f"검색 백엔드 미가용 (`{sv.status}`): {sv.detail}")
        else:
            st.error(f"검색 실패 (`{sv.status}`): {sv.detail}")
        return
    if not sv.candidates:
        st.warning("후보 0건 — 쿼리/적재 데이터를 확인하세요.")

    # 베이스 BOM 인덱스(있으면) — 후보 부품이 BOM에 존재하는지 표시.
    bom_idx: dict[str, str] | None = None
    if base_bom is not None:
        bom_idx = {}
        for r in base_bom.rows:
            k = _norm_pno(r.part_no)
            if k and k not in bom_idx:
                bom_idx[k] = r.bom_path or r.part_name or r.part_no

    for c in sv.candidates:
        score = c.score_rrf or 0.0
        title = (
            f"#{c.rank}  RRF={score:.4f}  [{c.base_model or '?'} → {c.new_model or '?'}]  "
            f"{(c.change_reason or c.change_log or '')[:50]}"
        )
        with st.expander(title, expanded=(c.rank == 1)):
            st.markdown(
                f"- **변경내역(change_log)**: {c.change_log or '-'}\n"
                f"- **변경사유(change_reason)**: {c.change_reason or '-'}\n"
                f"- **출처(source_ref)**: `{c.source_ref or '-'}`\n"
                f"- 점수 — semantic: `{c.score_semantic}` · lexical: `{c.score_lexical}` · "
                f"sparse: `{c.score_sparse}` · parts(부품명/품번): `{c.score_parts}` · "
                f"RRF: `{c.score_rrf}`"
            )
            if c.lines:
                rows = []
                for ln in c.lines:
                    row = dict(ln)
                    if bom_idx is not None:
                        hit = bom_idx.get(_norm_pno(ln.get("new_pno") or "")) or bom_idx.get(
                            _norm_pno(ln.get("base_pno") or "")
                        )
                        row["base_bom"] = ("✓ " + hit[:28]) if hit else ""
                    rows.append(row)
                st.markdown(f"**함께 바뀐 부품 라인 {len(c.lines)}건**" + ("  ·  ✓=베이스 BOM 존재" if bom_idx is not None else ""))
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.caption("이 사유의 부품 라인(change_line) 없음.")

    if sv.trace:
        with st.expander("도구 트레이스 (tool_call_log 미기록 — 화면 표시용)"):
            st.dataframe(pd.DataFrame(sv.trace), hide_index=True, width="stretch")


def _bom_tree_lines(nodes, anchor: str) -> list[str]:
    """walk_subtree 노드(from_pno/pno/depth) → 들여쓰기 트리 텍스트 라인."""
    kids: dict[str, list] = {}
    for nd in nodes:
        kids.setdefault(nd.from_pno, []).append(nd)
    lines: list[str] = []

    def walk(pno: str, depth: int) -> None:
        for nd in sorted(kids.get(pno, []), key=lambda x: x.pno):
            nm = (nd.part_name or "")[:26]
            qty = "" if nd.qty is None else f" ×{nd.qty:g}"
            lvl = "" if nd.bom_level is None else f"  [L{nd.bom_level}]"
            leaf = "" if kids.get(nd.pno) else "  ·leaf"
            lines.append(f"{'│  ' * (depth - 1)}├─ {nd.pno:<14} {nm}{qty}{lvl}{leaf}")
            walk(nd.pno, depth + 1)

    walk(anchor, 1)
    return lines


def _changed_part_candidates(sv) -> list[str]:
    """검색 후보(change_line)에서 변경 부품 P/No 후보 목록."""
    out: list[str] = []
    if sv is not None and sv.status == "ok":
        for c in sv.candidates:
            for ln in c.lines:
                for p in (ln.get("new_pno"), ln.get("base_pno")):
                    if p and p.strip() and p.strip() not in out:
                        out.append(p.strip())
    return out


def _render_bom_expansion(sv, base_bom, backend) -> None:
    st.subheader("③ BOM 하위 전개 (유저 지정 레벨)")

    db_ok = backend is not None and backend.status == "ok" and backend.factory is not None
    sources = (["코퍼스 BOM (DB·bom_edge)"] if db_ok else []) + (["업로드 BOM"] if base_bom is not None else [])
    if not sources:
        st.info("코퍼스 BOM 미적재(또는 백엔드 미가용) + 업로드 BOM 없음 — 베이스 BOM(.xlsx) 업로드 또는 "
                "`python -m src.cli db bom-edges`로 엣지를 적재하세요.")
        return
    source = st.radio("BOM 소스", sources, horizontal=True, key="mx_src")
    cand_pnos = _changed_part_candidates(sv)

    if source.startswith("코퍼스"):
        col1, col2, col3 = st.columns([3, 1, 1])
        options = cand_pnos + ["(직접 입력)"]
        pick = col1.selectbox("전개할 부품(anchor)", options, key="mx_anchor_sel",
                              help="검색 후보의 변경 부품(change_line), 또는 직접 입력")
        anchor = col1.text_input("부품 P/No 직접 입력", value="", key="mx_anchor_txt") if pick == "(직접 입력)" else pick
        direction = col2.radio("방향", ["하위(자식)", "상위(부모)"], key="mx_dir")
        depth = col3.slider("레벨(depth)", 1, 4, 2, key="mx_depth")
        if not anchor:
            return
        dirc = "down" if direction.startswith("하위") else "up"
        view = expand_bom_db(anchor, direction=dirc, max_depth=depth, session_factory=backend.factory)
        if not view.nodes:
            st.warning(f"`{anchor}` — {view.detail or '전개할 하위/상위 노드 없음(리프이거나 bom_edge에 미존재)'}")
            return
        st.caption(f"`{anchor}` {direction} **{depth}레벨** — {len(view.nodes)}개 노드  ·  파일: {view.file_name}")
        st.code("\n".join(_bom_tree_lines(view.nodes, anchor)) or "(없음)", language="text")
        st.dataframe(
            pd.DataFrame([
                {"depth": n.depth, "from": n.from_pno, "part_no": n.pno,
                 "part_name": n.part_name, "qty": n.qty, "bom_level": n.bom_level}
                for n in view.nodes
            ]),
            hide_index=True, width="stretch",
        )
        return

    # ── 업로드 BOM 경로(파일 스냅샷) ──
    bom_keys = {_norm_pno(r.part_no) for r in base_bom.rows}
    up_cands = [p for p in cand_pnos if _norm_pno(p) in bom_keys]
    col1, col2 = st.columns([3, 1])
    pick = col1.selectbox("전개할 부품(anchor)", ["(직접 입력)"] + up_cands, key="mx_up_sel",
                          help="검색 후보 중 업로드 BOM에 존재하는 부품, 또는 직접 입력")
    anchor = col1.text_input("부품 P/No 직접 입력", value="", key="up_anchor") if pick == "(직접 입력)" else pick
    depth = col2.slider("하위 레벨(depth)", 1, 6, 2, key="up_depth")
    if not anchor:
        return
    sub = walk_base_bom_subtree(base_bom, anchor, max_depth=depth)
    if not sub:
        st.warning(f"`{anchor}` 가 업로드 BOM에 없거나 하위 부품이 없습니다.")
        return
    st.caption(f"`{anchor}` 기준 **하위 {depth}레벨** — {len(sub)}개 부품")
    st.dataframe(
        pd.DataFrame([
            {"레벨": s.rel_level, "part_no": s.part_no, "part_name": s.part_name,
             "lvl": s.lvl, "qty": s.qty, "bom_path": s.bom_path}
            for s in sub
        ]),
        hide_index=True, width="stretch",
    )


def _render_confirm_expand(sv, backend) -> None:
    """③ HITL — 후보의 변경 부품을 골라 확정하면, 그 부품 기준으로 BOM 하위 트리를 전개."""
    st.markdown("##### 변경 부품 선택 → ✅ 확정 → BOM 트리 전개")
    st.caption("후보(②)의 변경 부품 중 전개할 것을 고르고 **선택 완료**를 누르면, 확정 부품 기준으로 "
               "BOM 트리를 결정론(walk_subtree)으로 펼칩니다. (절대원칙: 확정 후 전개)")
    if sv is None or getattr(sv, "status", None) != "ok" or not sv.candidates:
        st.info("먼저 ② 검색에서 후보를 회수하세요.")
        return

    # 후보별로 변경내역/변경사유를 '보면서' 그 후보의 부품을 고른다(맥락 가시 선택).
    selected: list[str] = []
    name_of: dict[str, str] = {}
    for c in sv.candidates:
        lp: dict[str, str] = {}
        for ln in c.lines:
            pno = (ln.get("new_pno") or ln.get("base_pno") or "").strip()
            if not pno:
                continue
            nm = (ln.get("part_name") or "").strip()
            name_of[pno] = nm
            lbl = f"{pno}  {nm[:22]}"
            lp.setdefault(lbl, pno)
        with st.container(border=True):
            st.markdown(
                f"**#{c.rank}**  ·  RRF `{(c.score_rrf or 0):.4f}`  ·  "
                f"`{c.base_model or '?'} → {c.new_model or '?'}`"
            )
            st.markdown(
                f"- **변경내역**: {c.change_log or '-'}\n"
                f"- **변경사유**: {c.change_reason or '-'}"
            )
            if lp:
                chosen = st.multiselect(
                    f"#{c.rank} 전개할 부품 (이 변경의 부품 {len(lp)}개 중)",
                    list(lp.keys()), key=f"ce_sel_{c.rank}",
                )
                selected.extend(lp[x] for x in chosen)
            else:
                st.caption("이 후보엔 부품 라인(change_line)이 없습니다.")

    selected = list(dict.fromkeys(selected))  # 순서 유지 dedup
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.markdown(f"**선택된 부품: {len(selected)}개**"
                + (f"\n\n`{'`, `'.join(selected[:8])}`" if selected else " — 위에서 골라주세요"))
    direction = c2.radio("방향", ["하위(자식)", "상위(부모)"], key="ce_dir")
    depth = c3.slider("레벨(depth)", 1, 4, 3, key="ce_depth")
    if st.button("✅ 선택 완료 — BOM 전개", type="primary", disabled=not selected, key="ce_go"):
        st.session_state["confirmed_expand"] = {
            "pnos": selected,
            "dir": "down" if direction.startswith("하위") else "up",
            "depth": int(depth),
        }

    conf = st.session_state.get("confirmed_expand")
    if not conf:
        st.caption("부품을 고르고 '선택 완료'를 누르세요.")
        return
    dir_kr = "하위(자식)" if conf["dir"] == "down" else "상위(부모)"
    st.divider()
    st.success(f"확정 {len(conf['pnos'])}건 — {dir_kr} {conf['depth']}레벨 전개")
    total = 0
    for pno in conf["pnos"]:
        view = expand_bom_db(pno, direction=conf["dir"], max_depth=conf["depth"],
                             session_factory=backend.factory)
        total += len(view.nodes)
        # 레벨별 노드 수 요약
        by_lvl: dict[int, int] = {}
        for n in view.nodes:
            by_lvl[n.depth] = by_lvl.get(n.depth, 0) + 1
        lvl_txt = " · ".join(f"L{d}:{cnt}" for d, cnt in sorted(by_lvl.items())) or "-"
        head = f"🌳 {pno}  {name_of.get(pno, '')[:20]}  —  {len(view.nodes)}개 노드 ({lvl_txt})  ·  {view.file_name or '—'}"
        with st.expander(head, expanded=True):
            if not view.nodes:
                st.warning(view.detail or "전개할 하위/상위 노드 없음 (리프이거나 bom_edge 미존재).")
                continue
            st.code("\n".join(_bom_tree_lines(view.nodes, pno)) or "(없음)", language="text")
            st.dataframe(
                pd.DataFrame([
                    {"depth": n.depth, "부모": n.from_pno, "part_no": n.pno,
                     "part_name": n.part_name, "qty": n.qty, "bom_level": n.bom_level}
                    for n in view.nodes
                ]),
                hide_index=True, width="stretch",
            )
    st.caption(f"확정 부품 {len(conf['pnos'])}건 → 전개 노드 합계 {total}개  ·  depth는 결정론 walk_subtree 기준(≤4)")


def render() -> None:
    """페이지 본문 — set_page_config는 호출부(통합 main.py 또는 standalone) 책임."""
    st.title("🔎 BOM 변경 영향 분석 — 정형화 · 검색 · 전개")
    st.caption("변경내역+변경사유(+부품명·품번) → ① 정형화 → ② 과거 change_event 하이브리드 검색"
               "(사유 + parts 채널) → ③ 변경 부품 선택·확정 → BOM 하위 전개")
    backend = detect_backend(_session_factory())
    llm = _sidebar(backend)

    for k in ("ex_log", "ex_reason", "ex_model", "ex_part"):
        st.session_state.setdefault(k, "")

    _render_sources()

    with st.form("inspect"):
        change_log = st.text_area("변경내역 (change_log)", value=st.session_state.ex_log, height=90)
        change_reason = st.text_area("변경사유 (change_reason)", value=st.session_state.ex_reason, height=90)
        p1, p2 = st.columns(2)
        part_name = p1.text_input("부품명 (검색·매칭 반영, 선택)", value=st.session_state.ex_part)
        part_nos_raw = p2.text_input("품번 (쉼표 구분, 선택)", value="")
        c1, c2, c3 = st.columns(3)
        base_model = c1.text_input("베이스모델 (표시·필터용, 선택)", value=st.session_state.ex_model)
        region = c2.text_input("region (선택)", value="")
        top_k = c3.slider("top_k", 1, 10, 5)
        submitted = st.form_submit_button("정형화 + 검색 실행", type="primary")

    if submitted:
        if not (change_log.strip() or change_reason.strip()):
            st.error("변경내역 또는 변경사유 중 하나는 입력하세요.")
            return
        part_nos = [p.strip() for p in part_nos_raw.split(",") if p.strip()]
        fv = formalize(
            change_log=change_log,
            change_reason=change_reason,
            base_model=base_model or None,
            region=region or None,
            part_name=part_name or None,
            part_nos=part_nos or None,
            llm=llm,
        )
        with st.spinner("검색 중..."):
            # 검색 = reason + Claude 재작성(sanitize) 합집합. write_log=False(read-only).
            sv = run_search(fv.reason_intent, fv.search_queries, backend, top_k=top_k, write_log=False)
        st.session_state["fv"] = fv
        st.session_state["sv"] = sv
        st.session_state.pop("confirmed_expand", None)  # 새 검색 → 이전 확정 초기화

    fv = st.session_state.get("fv")
    if fv is None:
        st.info("위에 변경내역/변경사유를 입력하고 **정형화 + 검색 실행**을 누르세요.")
        return
    sv = st.session_state.get("sv")
    tab1, tab2, tab3 = st.tabs(["① 정형화", "② 검색 후보", "③ 선택 → 하위 전개"])
    with tab1:
        _render_formalization(fv)
    with tab2:
        _render_search(sv, fv.search_queries, st.session_state.get("base_bom"))
    with tab3:
        _render_confirm_expand(sv, backend)
        with st.expander("고급 — 직접 부품 P/No 입력 · 업로드 BOM 전개"):
            _render_bom_expansion(sv, st.session_state.get("base_bom"), backend)


if __name__ == "__main__":
    st.set_page_config(page_title="정형화·검색 검수", page_icon="🔎", layout="wide")
    render()
