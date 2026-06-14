"""사이드바 환경 상태 패널 — 현재 .env 세팅(LLM/임베딩/DB/검색 게이트)을 한눈에.

통합 진입점(main.py)의 사이드바에서 호출한다. 외부 핑(Ollama/Postgres)은
``st.cache_data(ttl=60)``로 캐시해 rerun마다 느려지지 않게 한다. 표시 전용 —
어떤 상태도 변경하지 않는다(키 값은 절대 노출하지 않음).
"""

from __future__ import annotations

import os

import streamlit as st


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


@st.cache_data(ttl=60, show_spinner=False)
def _ollama_models() -> list[str] | None:
    """Ollama 모델 목록. 서버 다운이면 None."""
    import requests

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        r = requests.get(f"{host}/api/tags", timeout=2)
        r.raise_for_status()
        return [str(m.get("name", "")) for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001 — 상태 표시용, 실패=다운
        return None


@st.cache_data(ttl=60, show_spinner=False)
def _db_status() -> tuple[bool, str, int]:
    """Postgres 연결 + 코퍼스 요약. (ok, detail, bom_file_count)."""
    try:
        from sqlalchemy import text

        from src.db.engine import make_engine, session_factory

        with session_factory(make_engine())() as s:
            n_ev = s.execute(text("SELECT count(*) FROM change_event")).scalar()
            n_bom = s.execute(
                text("SELECT count(DISTINCT file_id) FROM bom_edge")
            ).scalar()
        return True, f"change_event {n_ev}건 · BOM 파일 {n_bom}개", int(n_bom or 0)
    except Exception as exc:  # noqa: BLE001
        return False, f"연결 실패 — {str(exc)[:60]}", 0


def _llm_line() -> str:
    from src.agent.llm.client import current_provider, llm_enabled

    prov = current_provider()
    if prov == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        key_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
        icon = "✅" if (llm_enabled() and key_ok) else "⚠️"
        tail = "" if key_ok else " · API 키 미설정(.env)"
        return f"{icon} **LLM** `anthropic` / `{model}`{tail}"
    model = os.environ.get("LLM_MODEL", "qwen2.5:32b")
    icon = "✅" if llm_enabled() else "⚠️"
    tail = "" if llm_enabled() else " · ENABLE_LLM=1 필요"
    return f"{icon} **LLM** `{prov}` / `{model}`{tail}"


def _embedding_lines() -> list[str]:
    if not _flag("ENABLE_EMBEDDING", False):
        return ["⚠️ **임베딩** 꺼짐 — lexical/parts 채널만 (semantic 검색 제외)"]
    embed_model = os.environ.get("EMBED_MODEL", "bge-m3")
    models = _ollama_models()
    if models is None:
        return [
            "⚠️ **임베딩** Ollama 다운 → sentence-transformers 폴백",
            "&nbsp;&nbsp;첫 검색 시 모델 로드 ~1분 소요",
        ]
    if any(embed_model in m for m in models):
        return [f"✅ **임베딩** Ollama `{embed_model}`"]
    return [
        f"⚠️ **임베딩** Ollama에 `{embed_model}` 없음 → ST 폴백(첫 검색 ~1분)",
        f"&nbsp;&nbsp;`ollama pull {embed_model}` 하면 빨라집니다",
    ]


def render() -> None:
    """사이드바 환경 패널 (expander, 기본 접힘)."""
    with st.sidebar.expander("⚙️ 환경 상태 (.env)", expanded=False):
        st.markdown(_llm_line())
        for line in _embedding_lines():
            st.markdown(line)
        ok, detail, n_bom = _db_status()
        st.markdown(f"{'✅' if ok else '⛔'} **Postgres** {detail}")

        struct_on = _flag("SEARCH_INCLUDE_STRUCT", False)
        gates = [
            f"- parts 채널(부품명·품번): `{'on' if _flag('SEARCH_INCLUDE_PARTS', True) else 'off'}`",
            f"- 변경점 유도 DERIVE_CP: `{'on' if _flag('DERIVE_CP', True) else 'off'}` "
            + f"(LLM 2차: `{'on' if _flag('DERIVE_CP_LLM', False) else 'off'}`)",
            "- 구조 부스트 SEARCH_INCLUDE_STRUCT: "
            + (f"`on` (w={os.environ.get('STRUCT_WEIGHT', '1.0')})" if struct_on else "`off`"),
            f"- 구제 재검색 RESCUE_SEARCH: `{'on' if _flag('RESCUE_SEARCH', False) else 'off'}` "
            + f"(low_conf<{os.environ.get('SEARCH_LOW_CONF', '0.30')})",
        ]
        st.markdown("**검색 게이트**\n" + "\n".join(gates))
        # P7.4: ①.5 "켰는데 안 도는" 진단 — 구조 부스트 on인데 BOM이 없으면 절대 안 됨.
        if struct_on and ok and n_bom == 0:
            st.warning("⚠️ 구조 부스트 ON인데 bom_edge 적재 0 — ①.5가 동작하지 않습니다. "
                       "`db bom-edges`로 BOM을 적재하세요.")
        elif struct_on:
            st.caption(f"①.5 닻 매칭은 분석 시 모듈명 기준(BOM 파일 {n_bom}개에서 해석).")
        excl = os.environ.get("SEARCH_EXCLUDE_FILE_IDS", "").strip()
        if excl:
            st.caption(f"검색 제외 file_id: {excl}")
        if st.button("상태 새로고침", key="_env_refresh", use_container_width=True):
            _ollama_models.clear()
            _db_status.clear()
            st.rerun()


__all__ = ["render"]
