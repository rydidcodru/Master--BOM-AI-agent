"""한국어 인과/목적 연결어 상수 — L0 추출기와 L1 변경점 유도가 공유.

기존 ``src/agent/ppt/extractor.py``에 흩어져 있던 인과 연결어 상수를 모은 모듈
(extractor는 import로 전환 — 동작 불변 리팩터). ``derive_change_slots``(L1 변경점
유도 v3)의 R2 마커 분리에 ``PURPOSE_MARKERS``/``CAUSE_MARKERS``를 사용한다.

순수 상수 모듈 — LLM/DB import 0회(결정론 경로 공유).
"""

from __future__ import annotations

# ── L0 (ppt/extractor) — '주요 변경점' 셀에서 변경사유(원인/목적) 분리용 ──────
# 'A에 따른 B' / 'A(으)로 인한 B' 꼴의 원인 연결어.
CAUSE_CONN = [
    "에 따른", "에 따라", "으로 인한", "로 인한", "으로 인해", "로 인해",
    "때문에", "에 의한", "에 의해",
]
# 명사+(으)로 인과: 'Door 무게 변경으로 …'.
NOMINAL_CAUSE = ["변경으로", "적용으로", "삭제로", "수정으로", "반영으로", "축소로"]
# 'A (을/를) 위해 B' 꼴의 목적 연결어.
PURPOSE_CONN = ["위하여", "위해", "위한"]

# ── L1 derive (변경점 유도 v3) — R2 마커 분리용 ─────────────────────────────
# 사유 문장 "원인/목적 <마커> 변경절"에서 **최우측** 마커 기준 우측 절을 변경절로 추출.
PURPOSE_MARKERS = ["위하여", "위해서", "위해", "하고자", "목적으로", "차원에서"]
CAUSE_MARKERS = ["으로 인하여", "으로 인해", "로 인해", "때문에", "에 따라",
                 "에 따른", "발생하여", "되어", "하여", "이므로", "으로", "로"]
# "(으)로"는 모호 마커 → 우선순위 최하위 + 행위절 검증 통과 시에만 채택.
AMBIGUOUS_MARKERS = ["으로", "로"]

__all__ = [
    "AMBIGUOUS_MARKERS",
    "CAUSE_CONN",
    "CAUSE_MARKERS",
    "NOMINAL_CAUSE",
    "PURPOSE_CONN",
    "PURPOSE_MARKERS",
]
