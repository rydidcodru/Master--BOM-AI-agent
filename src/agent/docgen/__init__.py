"""L4 — Document Generator. 변경리스트 / 개발마스터 행 / BOM diff + 체크리스트.

절대 원칙: NEW 품번 = `<발번대기>` placeholder (품번 무생성), 모든 행에 [SRC],
출처 없는 행 출력 금지. LLM은 자연어 설명에만(게이트).
"""

from src.agent.docgen.generator import (
    PNO_PLACEHOLDER,
    DocItem,
    DocRow,
    DocValidationError,
    GeneratedDoc,
    SourceRef,
    assert_valid,
    generate,
    source_ref_for,
    source_ref_for_pno,
    validate_doc,
)

__all__ = [
    "PNO_PLACEHOLDER",
    "DocItem",
    "DocRow",
    "DocValidationError",
    "GeneratedDoc",
    "SourceRef",
    "assert_valid",
    "generate",
    "source_ref_for",
    "source_ref_for_pno",
    "validate_doc",
]
