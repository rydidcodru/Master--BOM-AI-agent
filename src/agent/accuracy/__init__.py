"""정확도 채점 모듈 — 산출 통합 master xlsx vs 정답지 비교.

독립·자기완결(DB/LLM/네트워크 없음). 산출물(produced)과 정답지(oracle)를
변경 유니버스(New/Change/Delete) 한정으로 1:1 매칭 후 게이팅/진단 컬럼을 채점한다.

상세: src/agent/accuracy/scorer.py
"""

from src.agent.accuracy.scorer import (
    MasterRow,
    load_master_rows,
    score,
    score_rows,
)

__all__ = ["MasterRow", "load_master_rows", "score", "score_rows"]
