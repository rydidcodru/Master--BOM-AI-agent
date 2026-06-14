"""L4 문서 생성 + 출처/품번 검증.

산출 3종: 변경 부품 리스트 / 개발마스터 행 초안 / New BOM diff + 검토 체크리스트.
모든 행에 ``[SRC 파일/시트/행]``. NEW 부품 품번은 항상 ``<발번대기>``(무생성).

validate_doc / assert_valid: NEW 행에 placeholder가 아닌 임의 품번 → 실패,
출처 없는 행 → 실패. LLM은 설명(detail) 문장 생성에만, 게이트(없으면 결정론 reason).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.impact.models import Action, Tier
from src.agent.llm.client import LlmClient
from src.db.models import DevPartMaster, SourceFile

PNO_PLACEHOLDER = "<발번대기>"


class DocValidationError(Exception):
    """출처 누락 또는 NEW 임의 품번 등 출력 규칙 위반."""


@dataclass
class SourceRef:
    doc_id: int | None = None
    file_name: str | None = None
    sheet_name: str | None = None
    source_row: int | None = None

    @property
    def valid(self) -> bool:
        return (
            self.doc_id is not None
            or bool(self.file_name)
            or bool(self.sheet_name)
            or self.source_row is not None
        )

    def tag(self) -> str:
        fname = self.file_name or "?"
        sheet = self.sheet_name or "?"
        row = str(self.source_row) if self.source_row is not None else "?"
        return f"[SRC {fname}/{sheet}/{row}]"


@dataclass
class DocItem:
    """L4 입력 1건 (부품 + 영향 판정 + 출처)."""

    part_no: str | None
    is_new: bool
    action: Action
    tier: Tier
    source: SourceRef
    relation: str = "seed"
    part_name: str | None = None
    model: str | None = None
    reason: str = ""


@dataclass
class DocRow:
    pno_display: str
    is_new: bool
    action: Action
    tier: Tier
    src: str
    valid_source: bool
    detail: str
    relation: str = "seed"


@dataclass
class GeneratedDoc:
    changed_parts: list[DocRow] = field(default_factory=list)
    dev_master_rows: list[DocRow] = field(default_factory=list)
    bom_diff: list[DocRow] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)


def _explain(item: DocItem, llm: LlmClient | None) -> str:
    base = item.reason or f"{item.action} ({item.tier})"
    if llm is None:
        return base
    try:
        out = llm.complete_json(
            f"다음 영향 판정을 한 문장으로 설명: 품번={item.part_no} action={item.action} 사유={item.reason}",
            system="반드시 JSON 객체 {\"explanation\": string} 만 반환한다.",
        )
        exp = out.get("explanation")
        return exp if isinstance(exp, str) and exp.strip() else base
    except Exception:  # noqa: BLE001 — 설명 실패는 결정론 reason으로 fallback
        return base


def _to_row(item: DocItem, llm: LlmClient | None) -> DocRow:
    pno = PNO_PLACEHOLDER if item.is_new else (item.part_no or PNO_PLACEHOLDER)
    return DocRow(
        pno_display=pno,
        is_new=item.is_new,
        action=item.action,
        tier=item.tier,
        src=item.source.tag(),
        valid_source=item.source.valid,
        detail=_explain(item, llm),
        relation=item.relation,
    )


def generate(
    items: list[DocItem],
    *,
    llm: LlmClient | None = None,
    info_rows: list[object] | None = None,
) -> GeneratedDoc:
    """DocItem 리스트 → 3종 문서. NEW=placeholder, 모든 행 [SRC].

    ``info_rows``(P5 — 과거 동반변경 :class:`~src.agent.impact.cascade.InfoRow`)는
    **체크리스트 말미 "참고: 과거 동반 변경" 섹션에만** 덧붙인다. 개발마스터 행
    (``dev_master_rows``)·New BOM(``bom_diff``)·변경부품(``changed_parts``)은 오직
    ``items``에서만 만들어진다 — 정보행은 BOM/마스터 행으로 절대 나가지 않는다(v1 원칙).
    """
    rows = [_to_row(i, llm) for i in items]
    checklist = [f"[ ] {r.pno_display} — {r.detail} {r.src}" for r in rows if r.action == "CHECK"]
    if info_rows:
        checklist.append("── 참고: 과거 동반 변경 ──")
        checklist.extend(f"(참고) {getattr(ir, 'text', str(ir))}" for ir in info_rows)
    return GeneratedDoc(
        changed_parts=rows,
        dev_master_rows=[r for r in rows if r.action in ("ADD", "MODIFY")],
        bom_diff=[r for r in rows if r.relation in ("child", "parent") and r.action != "KEEP"],
        checklist=checklist,
    )


def validate_doc(doc: GeneratedDoc) -> list[str]:
    """출력 규칙 위반 목록 반환 (빈 리스트 = 통과)."""
    violations: list[str] = []
    sections = (
        ("changed_parts", doc.changed_parts),
        ("dev_master_rows", doc.dev_master_rows),
        ("bom_diff", doc.bom_diff),
    )
    for name, rows in sections:
        for r in rows:
            if not r.valid_source:
                violations.append(f"{name}: 출처 없는 행 ({r.pno_display})")
            if r.is_new and r.pno_display != PNO_PLACEHOLDER:
                violations.append(f"{name}: NEW 행 임의 품번 ({r.pno_display})")
    return violations


def assert_valid(doc: GeneratedDoc) -> None:
    violations = validate_doc(doc)
    if violations:
        raise DocValidationError("; ".join(violations))


def source_ref_for(session: Session, doc_id: int) -> SourceRef:
    """doc_id → SourceRef (dev_part_master.sheet_name/source_row + source_files.file_name)."""
    row = session.get(DevPartMaster, doc_id)
    if row is None:
        return SourceRef(doc_id=doc_id)
    sf = session.get(SourceFile, row.file_id)
    return SourceRef(
        doc_id=doc_id,
        file_name=sf.file_name if sf else None,
        sheet_name=row.sheet_name,
        source_row=row.source_row,
    )


def source_ref_for_pno(session: Session, part_no: str) -> SourceRef:
    """part_no_new로 dev_part_master 행을 찾아 SourceRef 해소 (없으면 invalid)."""
    row = (
        session.execute(
            select(DevPartMaster).where(DevPartMaster.part_no_new == part_no).limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        return SourceRef()
    return source_ref_for(session, row.doc_id)
