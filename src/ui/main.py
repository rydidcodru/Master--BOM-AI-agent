"""통합 Streamlit 진입점 — 검수(정형화·검색·BOM 전개) + L1~L4 영향분석 라우터.

    streamlit run src/ui/main.py     (또는  python -m src.cli app run)

사이드바에서 화면을 고르면 해당 페이지의 ``render()``를 호출한다. set_page_config는
이 파일에서 한 번만 호출하고, 각 페이지(inspect_app/agent_app)는 본문만 그린다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트(src.* 임포트용) + src/ui(레거시 sibling bare import용 — agent_app의
# ``from agent_client import``)를 둘 다 path 앞에 둔다. streamlit이 페이지 파일을 직접
# 실행할 때와 동일한 import 환경을 재현 → 페이지를 top-level 모듈로 임포트한다.
_PKG_ROOT = Path(__file__).resolve().parents[2]
_UI_DIR = Path(__file__).resolve().parent
_pre = [str(_PKG_ROOT), str(_UI_DIR)]
sys.path = _pre + [p for p in sys.path if p not in _pre]

import streamlit as st  # noqa: E402

st.set_page_config(page_title="LG BOM 변경영향 분석", page_icon="🧭", layout="wide")

# .env(LLM_PROVIDER/ANTHROPIC_API_KEY) 로드 — 진입점에서만(agent_client는 import 부작용 없음).
from src.ui.agent_client import load_agent_env  # noqa: E402

load_agent_env()

import agent_app  # noqa: E402  (top-level — src/ui on path)
import env_panel  # noqa: E402
import inspect_app  # noqa: E402

_PAGES = {
    "🧭 L1~L4 영향분석 (HITL·PPT·텍스트)": agent_app.render,
    "🔎 정형화·검색·BOM 전개 (검수)": inspect_app.render,
}


def main() -> None:
    st.sidebar.title("🧭 LG BOM Agent")
    choice = st.sidebar.radio("화면", list(_PAGES), label_visibility="collapsed")
    env_panel.render()  # 현재 .env 세팅(LLM/임베딩/DB/게이트) 한눈에
    st.sidebar.divider()
    _PAGES[choice]()


main()
