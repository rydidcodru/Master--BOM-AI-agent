"""②.5 후보-BOM 매핑 — 과거 change_line 세트를 모듈 하위트리 S에 사상.

순수 결정론 — LLM 0, DB 쓰기 0. ScopeIndex는 ①.5(Phase 2) 인스턴스를 재사용.
"""

from src.agent.mapping.mapper import map_event_to_scope
from src.agent.mapping.models import MappedEvent, MappedLine, MatchEvidence, NodeRef

__all__ = [
    "MappedEvent",
    "MappedLine",
    "MatchEvidence",
    "NodeRef",
    "map_event_to_scope",
]
