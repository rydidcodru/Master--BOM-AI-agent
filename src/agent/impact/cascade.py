"""과거 동반변경 → 체크리스트 "정보행"(companion_info). 결정론, LLM 0.

P5(v2 패치): v1의 cascade 레벨창 전사(`transcribe`/`CASCADE_TPL`)는 **제거**됐다 —
"과거 트리의 상대 깊이가 현재 트리에서 ±1 보존된다"는 가정이 미검증이라 부속 복잡도가
과다했다. 같은 정보(②.5 ``MappedEvent``)를 **체크리스트 정보행**으로 강등해 표시한다
(신규 코드 최소화, BOM 행으로는 절대 안 나감 — v1 원칙 유지).

``companion_info``는 채택 이벤트에서 **사람이 채택하지 않은 나머지 라인**을 정보행으로
만든다(②.5 매핑 결과 재사용, 재계산 금지). 모든 행에 ``[SRC ...]``(v1 원칙 #3).

순수 결정론 — LLM/DB import 0회 (단언 테스트로 핀).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.agent.mapping.models import MappedEvent, MappedLine

# Delete 분류 부기 / 하위 전개 확인 신호 — 정보행 문구 보강용.
_DELETE = "delete"


@dataclass
class InfoRow:
    """체크리스트 말미 "참고: 과거 동반 변경" 정보행. BOM/개발마스터 행 아님."""

    text: str
    status: str
    source_ref: str


def _location_phrase(ml: MappedLine) -> str:
    """매핑 status → 현재 트리상 위치 문구."""
    if ml.status == "matched" and ml.target_node is not None:
        where = ml.target_node.path or ml.target_node.pno
        return f"현재 트리 {where}에 존재"
    if ml.status == "ambiguous":
        cand = ml.candidates[0].part_name if ml.candidates else "?"
        return f"현재 트리 후보 {cand} (확인 요)"
    if ml.status == "add_proposal":
        return "현재 트리 미존재, 추가 검토 후보 (<발번대기>)"
    return "현재 트리 미발견"  # dropped (또는 노드 없는 matched)


def _row_text(ml: MappedLine) -> str:
    ref = ml.line_ref or {}
    pname = str(ref.get("part_name") or ref.get("base_pno") or ref.get("new_pno") or "미상")
    cp = str(ref.get("changepoint") or "변경점 미상")
    src = str(ref.get("source_ref") or "미상")
    text = f"과거 동반변경: {pname} ({cp}) — {_location_phrase(ml)}"
    if str(ref.get("classification") or "").strip().lower() == _DELETE:
        text += " (과거엔 삭제됨)"
    return f"{text} [SRC {src}]"


def companion_info(
    adopted_mapping: MappedEvent, adopted_line_ids: set
) -> list[InfoRow]:
    """채택 이벤트에서 '사람이 채택하지 않은 나머지 라인'을 정보행으로.

    입력은 ②.5 ``MappedEvent`` 재사용(재계산 금지). ``adopted_line_ids``에 든 라인은
    이미 사람이 확정·전개한 부품이므로 정보행에서 제외한다. BOM 행 생성 금지 —
    반환은 docgen 체크리스트 말미 전용 :class:`InfoRow`.
    """
    rows: list[InfoRow] = []
    for ml in adopted_mapping.lines:
        lid = (ml.line_ref or {}).get("line_id")
        if lid is not None and lid in adopted_line_ids:
            continue
        rows.append(
            InfoRow(
                text=_row_text(ml),
                status=ml.status,
                source_ref=str((ml.line_ref or {}).get("source_ref") or ""),
            )
        )
    return rows


__all__ = ["InfoRow", "companion_info"]
