"""BOM Agent Streamlit UI — PostgreSQL backend.

통합 진입 ``main.py``가 두 페이지를 라우팅한다: ``inspect_app``(정형화·검색·BOM 전개 검수)
+ ``agent_app``(L1~L4 HITL 영향분석 — PPT/수동 입력 → 후보 검색 → 확정 → 전개 + New BOM).
어댑터는 ``agent_client``(dev_part_master + Ollama bge-m3 + src.db.retrieve).

실행:
    .venv/Scripts/python.exe -m src.cli app run

또는 직접:
    streamlit run src/ui/main.py
"""
