"""화이트 모드 카드형 후보 검토 뷰 — 참고 이미지(KakaoTalk_…463) 형식.

흐름: PPT 추출 → **변경항목 리스트(검수·편집 가능)** → 선택 항목 후보 검색 →
선택 항목별 **후보 카드**(랭크/레벨/상태 배지 + 펼침 상세 + P/No old→new + 유사 근거 +
선택 체크박스). 추출·검색 로직은 ``agent_client`` 재사용(신규 비즈니스 로직 없음).
"""
from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st

try:  # streamlit은 src/ui를 sys.path에 올려 bare import가 정답(agent_app과 동일 관례).
    from agent_client import extract_base_bom, extract_ppt, propose_items
except ImportError:  # 패키지 경로로 임포트되는 경우(테스트 등) 폴백.
    from src.ui.agent_client import extract_base_bom, extract_ppt, propose_items

_KO_CLS = ["신규", "변경", "기존", "삭제"]
_CLS_KO = {"new": "신규", "change": "변경", "changing": "변경", "common": "기존",
           "delete": "삭제", "신규": "신규", "변경": "변경", "기존": "기존", "삭제": "삭제"}

__all__ = ["render_card_tab", "inject_card_css"]


# ────────────────────────────────────────────────────────────────────────────
# 스타일 (화이트 모드, 참고 이미지 톤)
# ────────────────────────────────────────────────────────────────────────────
_CSS = """
<style>
:root { --ink:#1f2430; --muted:#6b7280; --line:#e9edf3; --blue:#2563eb; }
.block-container { padding-top: 1.4rem; max-width: 1080px; }
.card-title { font-size: 22px; font-weight: 800; color: var(--ink); margin: 2px 0 2px; }
.crumb { font-size: 20px; font-weight: 700; color: var(--ink); margin: 6px 0 4px; }
.crumb .sep { color:#c7cdd8; margin: 0 7px; font-weight: 400; }
.chg-row { font-size: 14px; color: var(--ink); margin: 3px 0; }
.chg-row .lbl { color: var(--muted); font-weight: 600; }
.sec-head { font-size: 15px; font-weight: 700; color: var(--ink);
            border-top: 1px solid var(--line); padding-top: 16px; margin: 14px 0 6px; }
.lgbadge { display:inline-block; padding:1px 9px; border-radius:7px; font-size:12px;
           font-weight:700; margin-right:6px; line-height:1.7; vertical-align:middle; }
.b-rank   { background:#e7f0ff; color:#2563eb; }
.b-lvl    { background:#eef1f5; color:#5b6472; }
.b-new    { background:#e6f7ed; color:#0f9d58; }
.b-change { background:#fef3c7; color:#b45309; }
.b-delete { background:#fde8e8; color:#dc2626; }
.b-common { background:#eef1f5; color:#5b6472; }
.cand-part  { font-weight:700; color:var(--ink); font-size:15px; vertical-align:middle; }
.cand-model { color:#9aa3b2; font-weight:600; font-size:14px; vertical-align:middle; }
.d-row { font-size:14px; color:var(--ink); margin:4px 0; }
.d-label { font-weight:700; color:#374151; }
.pno { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:13px; }
.pno-old { color:#6b7280; }
.pno-arrow { color:#c7cdd8; margin:0 7px; }
.pno-new { color:#dc2626; font-weight:700; }
.sim { color:#2563eb; font-size:13px; margin-top:8px; }
.sim .star { margin-right:5px; }
.meta-r { text-align:right; color:#9aa3b2; font-size:13px; line-height:1.7; }
div[data-testid="stExpander"] details summary p { font-size:13px; color:#5b6472; }
</style>
"""


def inject_card_css() -> None:
    """카드 CSS 1회 주입."""
    if not st.session_state.get("_card_css_done"):
        st.markdown(_CSS, unsafe_allow_html=True)
        st.session_state["_card_css_done"] = True


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _status_badge(cls: str) -> str:
    c = (cls or "").strip().lower()
    if c in ("new", "신규"):
        return '<span class="lgbadge b-new">New</span>'
    if c in ("change", "변경", "changing", "changing point"):
        return '<span class="lgbadge b-change">Change</span>'
    if c in ("delete", "삭제"):
        return '<span class="lgbadge b-delete">Delete</span>'
    if c in ("common", "기존"):
        return '<span class="lgbadge b-common">Common</span>'
    return f'<span class="lgbadge b-common">{_esc(cls)}</span>' if cls else ""


def _similarity_note(cand: dict[str, Any], current_reason: str) -> str:
    """유사 근거 한 줄(✦) — 후보 사유와 현재 변경사유의 검색 매칭을 설명(생성 아님, 표기)."""
    cr = str(cand.get("change_reason") or "").strip()
    if not cr:
        return ""
    cur = str(current_reason or "").strip()
    if cur:
        return f"과거 ‘{cr}’ 사례 — 현재 변경사유와 부품·사유 맥락이 유사"
    return f"과거 ‘{cr}’ 사례와 유사"


# ────────────────────────────────────────────────────────────────────────────
# 렌더링
# ────────────────────────────────────────────────────────────────────────────
def _render_change_header(item: dict[str, Any]) -> None:
    """변경항목 헤더 — 브레드크럼 제목 + 변경내역/변경사유."""
    module = str(item.get("module") or item.get("모듈") or "")
    detail = str(item.get("change_detail") or item.get("변경내용") or "")
    reason = str(item.get("change_reason") or item.get("변경사유") or "")
    crumbs = [c.strip() for c in re.split(r"[>›/]", module) if c.strip()] or [module or "-"]
    crumb_html = '<span class="sep">›</span>'.join(_esc(c) for c in crumbs)
    st.markdown(
        f'<div class="crumb">{crumb_html}</div>'
        f'<div class="chg-row">📋 <span class="lbl">변경내역</span> : {_esc(detail) or "-"}</div>'
        f'<div class="chg-row">💡 <span class="lbl">변경사유</span> : {_esc(reason) or "-"}</div>',
        unsafe_allow_html=True,
    )


def _render_candidate_card(cand: dict[str, Any], rank: int, kp: str, current_reason: str) -> bool:
    """후보 1건 카드 → 선택 여부 반환."""
    lines = cand.get("lines") or [{}]
    prim = lines[0] if lines else {}
    cls = str(prim.get("classification") or "")
    part_name = str(prim.get("part_name") or "-")
    model = str(cand.get("base_model") or cand.get("new_model") or "-")
    lvl = prim.get("bom_level")

    with st.container(border=True):
        c0, c1 = st.columns([0.5, 13])
        with c0:
            sel = st.checkbox("선택", key=f"{kp}_sel", label_visibility="collapsed")
        with c1:
            lvl_badge = f'<span class="lgbadge b-lvl">Lv.{_esc(lvl)}</span>' if lvl not in (None, "") else ""
            st.markdown(
                '<div>'
                f'<span class="lgbadge b-rank">{rank}위</span>'
                f'{lvl_badge}{_status_badge(cls)}'
                f'<span class="cand-part">{_esc(part_name)}</span>'
                f'<span class="cand-model"> | {_esc(model)}</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            with st.expander("상세"):
                for ln in lines:
                    cp = str(ln.get("changepoint") or cand.get("change_log") or "-")
                    reason = str(cand.get("change_reason") or "-")
                    base_p = str(ln.get("part_no_base") or "-")
                    new_p = str(ln.get("part_no_new_suggested") or "-")
                    ptype = str(ln.get("part_type") or ln.get("classification") or "-")
                    pname = str(ln.get("part_name") or "")
                    left, right = st.columns([1.25, 1])
                    with left:
                        nm = f'<div class="d-row"><span class="d-label">부품명</span> : {_esc(pname)}</div>' if len(lines) > 1 else ""
                        st.markdown(
                            nm
                            + f'<div class="d-row"><span class="d-label">변경점</span> : {_esc(cp)}</div>'
                            + f'<div class="d-row"><span class="d-label">변경사유</span> : {_esc(reason)}</div>',
                            unsafe_allow_html=True,
                        )
                    with right:
                        st.markdown(
                            '<div class="d-row"><span class="d-label">P/No</span> : '
                            f'<span class="pno pno-old">{_esc(base_p)}</span>'
                            '<span class="pno-arrow">→</span>'
                            f'<span class="pno pno-new">{_esc(new_p)}</span></div>'
                            f'<div class="d-row"><span class="d-label">PartType</span> : {_esc(ptype)}</div>',
                            unsafe_allow_html=True,
                        )
                sim = _similarity_note(cand, current_reason)
                if sim:
                    st.markdown(f'<div class="sim"><span class="star">✦</span>{_esc(sim)}</div>',
                                unsafe_allow_html=True)
    return bool(sel)


# ────────────────────────────────────────────────────────────────────────────
# 탭 본문
# ────────────────────────────────────────────────────────────────────────────
def _upload_section() -> tuple[bytes | None, Any]:
    """PPT + 베이스 BOM 업로더(나란히). 반환 (ppt_bytes, bom_uploaded_file)."""
    c1, c2 = st.columns(2)
    with c1:
        up = st.file_uploader("① 심의회 PPT (.pptx)", type=["pptx"], key="_card_ppt")
    with c2:
        bom = st.file_uploader("② 베이스 BOM (.xlsx, 선택)", type=["xlsx"], key="_card_bom")
    pb: bytes | None = None
    if up is not None:
        pb = up.getvalue()
    else:
        shared = st.session_state.get("_agent_ppt")
        if shared is not None:
            try:
                pb = shared.getvalue()
            except Exception:  # noqa: BLE001
                pb = None
    return pb, bom


def render_card_tab() -> None:
    """카드형 변경점 검토 & 후보 선택 탭 — PPT+BOM 업로드 → 후보 선택 → 선택 리스트(수정)."""
    inject_card_css()
    st.markdown('<div class="card-title">🔎 변경점 검토 &amp; 후보 선택</div>', unsafe_allow_html=True)
    st.caption("PPT(+베이스 BOM)를 올려 변경항목을 뽑고 → 검색할 항목 선택 → 후보 카드에서 채택 → "
               "**선택 목록을 리스트로 만들어 수정**하세요.")

    pb, bom_file = _upload_section()
    if pb is None:
        st.info("심의회 PPT(.pptx)를 올리면 변경항목을 추출합니다. 베이스 BOM(.xlsx)은 New BOM/Master 생성에 쓰입니다.")
        return

    # 베이스 BOM 파싱(선택) — 다운스트림(master/expand)용으로 세션 보관.
    if bom_file is not None:
        bkey = f"{bom_file.name}:{bom_file.size}"
        # NOTE: 위젯 키 '_card_bom'은 UploadedFile를 소유 → 파싱 결과는 별도 키에 보관.
        if st.session_state.get("_card_bom_key") != bkey:
            try:
                st.session_state["_card_bom_parsed"] = extract_base_bom(bom_file.getvalue(), file_name=bom_file.name)
                st.session_state["_card_bom_key"] = bkey
            except Exception as exc:  # noqa: BLE001
                st.warning(f"베이스 BOM 파싱 실패(무시하고 진행): {exc}")
        bmeta = st.session_state.get("_card_bom_parsed") or {}
        if isinstance(bmeta, dict) and bmeta:
            st.caption(f"✅ 베이스 BOM: `{bmeta.get('model', '-')}` · {len(bmeta.get('rows') or [])}행 (Master 생성에 사용)")

    key = f"card:{len(pb)}"
    if st.session_state.get("_card_ppt_key") != key:
        with st.spinner("PPT 추출 중 (결정론, LLM 0회)..."):
            try:
                st.session_state["_card_extracted"] = extract_ppt(pb)
                st.session_state["_card_ppt_key"] = key
                st.session_state.pop("_card_proposals", None)
            except Exception as exc:  # noqa: BLE001
                st.error(f"PPT 추출 실패: {exc}")
                return

    extracted = st.session_state.get("_card_extracted") or {}
    items = extracted.get("items") or []
    if not items:
        st.warning("변경항목을 찾지 못했습니다. PPT의 변경점/개발부품 Master 표 구조를 확인하세요.")
        return

    # ── ① 변경항목 리스트 — 검수/편집 가능 (변경내용·변경사유 수정 → 검색에 반영) ──
    st.markdown('<div class="sec-head">① 변경항목 — 검수·편집 후 검색할 항목을 선택하세요</div>',
                unsafe_allow_html=True)
    _bm = st.session_state.get("_card_bom_parsed")
    base_default = str(extracted.get("base_model") or (_bm.get("model") if isinstance(_bm, dict) else "") or "")
    base_model = st.text_input("베이스 모델", value=base_default,
                               help="후보 검색 표기/필터용. PPT/BOM 추출값을 고치거나 직접 입력.")

    rows = [
        {
            "검색": False, "#": i + 1, "모듈": it.get("module", ""),
            "변경내용": it.get("change_detail", ""), "변경사유": it.get("change_reason", ""),
            "유형": it.get("change_type", ""), "base P/N": it.get("base_part_no", ""),
            "new P/N": it.get("new_part_no", ""),
        }
        for i, it in enumerate(items)
    ]
    edited = st.data_editor(
        rows, key="_card_items", use_container_width=True, hide_index=True,
        column_config={
            "검색": st.column_config.CheckboxColumn("검색", help="이 항목만 후보 검색", width="small"),
            "#": st.column_config.NumberColumn("#", disabled=True, width="small"),
            "모듈": st.column_config.TextColumn("모듈", disabled=True),
            "변경내용": st.column_config.TextColumn("변경내용 ✏️", help="자유 편집 — 검색에 반영"),
            "변경사유": st.column_config.TextColumn("변경사유 ✏️", help="자유 편집 — 검색에 반영"),
            "유형": st.column_config.TextColumn("유형", disabled=True, width="small"),
            "base P/N": st.column_config.TextColumn("base P/N", disabled=True),
            "new P/N": st.column_config.TextColumn("new P/N", disabled=True),
        },
    )
    # 편집분을 items에 반영(검색에 사용).
    edited_items: list[dict[str, Any]] = []
    selected_idx: set[int] = set()
    for i, (it, r) in enumerate(zip(items, edited)):
        merged = dict(it)
        merged["change_detail"] = r.get("변경내용", it.get("change_detail", ""))
        merged["change_reason"] = r.get("변경사유", it.get("change_reason", ""))
        edited_items.append(merged)
        if r.get("검색"):
            selected_idx.add(i)

    c1, c2 = st.columns([1.6, 5])
    if c1.button(f"② 선택 항목 후보 검색 ({len(selected_idx)}개)", type="primary",
                 disabled=not selected_idx, key="_card_search"):
        bm = base_model.strip() or None
        with st.spinner(f"{len(selected_idx)}개 항목 과거 master 후보 검색 중..."):
            try:
                st.session_state["_card_proposals"] = propose_items(
                    edited_items, base_model=bm, select_indices=selected_idx
                )
                st.session_state["_card_items_snapshot"] = edited_items
            except Exception as exc:  # noqa: BLE001
                st.error(f"후보 검색 실패: {exc}")
                return
    if not selected_idx:
        c2.caption("⬆️ 검색할 변경항목을 한 개 이상 체크하세요.")

    proposals = st.session_state.get("_card_proposals")
    if not proposals:
        st.info("‘후보 검색’을 누르면 변경사유+내용으로 과거 유사 사례를 카드로 보여줍니다.")
        return

    snap = st.session_state.get("_card_items_snapshot") or edited_items
    # ── ② 항목별 후보 카드 (채택 선택) ──
    picks: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (변경항목, 채택 후보)
    for p in proposals:
        idx = int(p.get("item_index", 0))
        item = snap[idx] if 0 <= idx < len(snap) else {}
        cands = p.get("candidates") or []
        st.markdown('<div class="sec-head">📋 유사 이력 후보 — 참고할 항목을 선택하세요</div>',
                    unsafe_allow_html=True)
        hl, hr = st.columns([4, 1])
        with hl:
            _render_change_header(item)
        with hr:
            st.markdown(
                f'<div class="meta-r">베이스: {_esc(base_model or "-")}<br>'
                f'후보: {len(cands)}건</div>', unsafe_allow_html=True)
        if not cands:
            st.caption("이 항목에 대한 후보가 없습니다.")
            continue
        cur_reason = str(item.get("change_reason") or "")
        for rank, cand in enumerate(cands, 1):
            kp = f"card_{idx}_{rank}"
            if _render_candidate_card(cand, rank, kp, cur_reason):
                picks.append((item, cand))

    # ── ③ 선택한 후보 → 리스트화(수정 가능) ──
    st.markdown('<div class="sec-head">③ 선택한 후보 — 변경 리스트 (수정 가능)</div>',
                unsafe_allow_html=True)
    _render_selected_editor(picks)


def _render_selected_editor(picks: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    """채택한 후보를 변경 리스트로 모아 편집(신규 P/No·변경사유·분류). 편집은 세션에 영속."""
    if not picks:
        st.caption("위 카드의 체크박스로 후보를 채택하면 여기 변경 리스트로 모이고, 바로 수정할 수 있습니다.")
        return
    edits: dict[str, dict[str, str]] = st.session_state.setdefault("_card_edits", {})

    rows: list[dict[str, Any]] = []
    rids: list[str] = []  # 행 인덱스 ↔ 안정 id (data_editor는 행 순서 보존 → 인덱스 정렬 안전)
    for item, cand in picks:
        ln = (cand.get("lines") or [{}])[0]
        rid = f"{cand.get('event_id')}|{ln.get('part_no_base') or ''}|{ln.get('part_name') or ''}"
        rids.append(rid)
        e = edits.get(rid, {})
        rows.append({
            "변경항목": str(item.get("module") or ""),
            "부품명": str(ln.get("part_name") or ""),
            "과거 base P/No": str(ln.get("part_no_base") or ""),
            "과거 new P/No": str(ln.get("part_no_new_suggested") or ""),
            "신규 P/No": e.get("new_pno", ""),
            "변경사유": e.get("reason", str(item.get("change_reason") or cand.get("change_reason") or "")),
            "분류": e.get("cls", _CLS_KO.get(str(ln.get("classification") or "").strip().lower(), "변경")),
            "출처": str(cand.get("source_ref") or ""),
        })
    out = st.data_editor(
        rows, key="_card_selected", use_container_width=True, hide_index=True,
        column_config={
            "변경항목": st.column_config.TextColumn("변경항목", disabled=True),
            "부품명": st.column_config.TextColumn("부품명", disabled=True),
            "과거 base P/No": st.column_config.TextColumn("과거 base P/No", disabled=True),
            "과거 new P/No": st.column_config.TextColumn("과거 new P/No(참고)", disabled=True),
            "신규 P/No": st.column_config.TextColumn("신규 P/No ✏️", help="발번된 새 품번 입력(미입력 시 <발번대기>)"),
            "변경사유": st.column_config.TextColumn("변경사유 ✏️"),
            "분류": st.column_config.SelectboxColumn("분류 ✏️", options=_KO_CLS, width="small"),
            "출처": st.column_config.TextColumn("출처", disabled=True),
        },
    )
    # 편집분 영속(카드 재선택/필터에도 유지) — 인덱스로 rid 매핑(행 순서 보존).
    for i, r in enumerate(out):
        if i < len(rids):
            edits[rids[i]] = {"new_pno": str(r.get("신규 P/No") or ""),
                              "reason": str(r.get("변경사유") or ""),
                              "cls": str(r.get("분류") or "")}
    st.session_state["_card_selected_rows"] = list(out)
    st.caption(f"✅ {len(rows)}건 선택 — 신규 P/No·변경사유·분류를 바로 수정하세요. 수정 내용은 자동 저장됩니다.")
