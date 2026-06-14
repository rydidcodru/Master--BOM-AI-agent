"""L1 경계 스키마 (Pydantic v2).

``ChangeIntent`` = structurizer 최종 출력. ``LlmSlots`` = LLM이 채우는 의미 슬롯의
스키마 경계 — 잘못된 모양(타입/구조)이면 ValidationError로 거부되어 structurizer가
결정론 fallback으로 떨어진다(설계 §3-2, "잘못된 JSON 거부").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

IntentSource = Literal["regex+llm", "regex", "raw_fallback", "ppt"]

_MAX_QUERIES = 4

# 변경점 유도 v3 — 행위는 닫힌 enum (config/changepoint_vocab.yaml actions와 1:1).
ChangeAction = Literal[
    "변경", "추가", "삭제", "적용", "축소", "확대", "통합", "분리", "보강", "교체"
]


class ChangeSlot(BaseModel):
    """변경점 유도 v3 슬롯 — "무엇을(target) 어떤 속성을(attribute) 어떻게(action)".

    target/attribute는 열린 슬롯(derive가 grounding 검증 — 사유/부품명에 실재하는
    토큰만), action은 닫힌 enum(타입 위반은 ValidationError → 슬롯 폐기). 검색 쿼리
    보강 전용 — 문서/BOM 출력 경로에 "사실"로 흘리지 않는다.
    """

    model_config = {"extra": "ignore"}

    target: str
    attribute: str | None = None
    action: ChangeAction


class LlmSlots(BaseModel):
    """LLM JSON 모드가 반환해야 하는 의미 슬롯. extra 키는 무시, 타입 위반은 거부."""

    model_config = {"extra": "ignore"}

    change_attribute: str | None = None
    change_direction: str | None = None
    intent_summary: str = ""
    rewritten_queries: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @field_validator("rewritten_queries")
    @classmethod
    def _trim_queries(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for q in v:
            q = q.strip()
            if q and q not in seen:
                seen.append(q)
        return seen[:_MAX_QUERIES]


class ChangeIntent(BaseModel):
    """L1 최종 산출물. 정규식 선추출(결정론) + LLM 의미 슬롯(게이트) 병합."""

    model_config = {"extra": "ignore"}

    raw_text: str
    part_nos: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    region: str | None = None
    # 변경 기준이 되는 베이스 모델 (PPT project_meta.base_model). 검색어가 아니라 표기/선택적
    # 필터용 — 항목마다 동일해 변별력이 없으므로 쿼리에 자동 주입하지 않는다. (부품명/품번은
    # 2026-06-10 개정으로 parts 채널 매칭에 사용 — [[feedback-search-by-reason-not-id]] 참고.)
    base_model: str | None = None
    change_attribute: str | None = None
    change_direction: str | None = None
    intent_summary: str = ""
    rewritten_queries: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source: IntentSource = "regex"
    # 변경점 유도 v3 (additive — 기본값 有 → change_intent JSONB 캐시 하위호환).
    # derived_cp_source는 추후 UI "자동요약" 라벨용 (이번엔 표시 안 함).
    derived_change_slots: list[ChangeSlot] = Field(default_factory=list)
    derived_cp_source: Literal["det", "llm"] | None = None
    # ①.5 구조 스코프용 모듈명 (additive) — PPT 경로에서 extractor의 module_details/
    # 항목 module로부터 플러밍(폼 경로는 빈 리스트). 검색어가 아니라 S(모듈 하위트리)
    # 닻 매칭용 — SEARCH_INCLUDE_STRUCT 게이트가 켜졌을 때만 사용.
    module_names: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp_conf(cls, v: float) -> float:
        return max(0.0, min(1.0, v))
