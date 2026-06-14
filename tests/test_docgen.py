"""Phase 5 — L4 Document Generator 테스트.

검증: 3종 산출 + 모든 행 [SRC], NEW=<발번대기>, 검증 룰(임의 품번/출처 없음 → fail),
doc_id→SourceRef 해소.
"""

from __future__ import annotations

import pytest

from src.agent.docgen import (
    PNO_PLACEHOLDER,
    DocItem,
    DocRow,
    DocValidationError,
    GeneratedDoc,
    SourceRef,
    assert_valid,
    generate,
    source_ref_for,
    validate_doc,
)
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import DevPartMaster, SourceFile


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _src() -> SourceRef:
    return SourceRef(doc_id=1, file_name="개발변경부품Master Best 221226.xlsx", sheet_name="변경부품 list", source_row=5)


def test_generate_three_sections_with_src():
    items = [
        DocItem("AGG74419321", is_new=False, action="MODIFY", tier="CORE", source=_src(), reason="재질 변경"),
        DocItem(None, is_new=True, action="ADD", tier="CORE", source=_src(), relation="seed"),
        DocItem("MFZ67394702", is_new=False, action="CHECK", tier="CASCADE", source=_src(), relation="child"),
        DocItem("WSED7667M", is_new=False, action="KEEP", tier="CASCADE", source=_src(), relation="child"),
    ]
    doc = generate(items)
    assert len(doc.changed_parts) == 4
    # 모든 행에 [SRC]
    assert all(r.src.startswith("[SRC ") for r in doc.changed_parts)
    # NEW → placeholder
    new_rows = [r for r in doc.changed_parts if r.is_new]
    assert new_rows and all(r.pno_display == PNO_PLACEHOLDER for r in new_rows)
    # dev_master_rows = ADD/MODIFY 만
    assert {r.action for r in doc.dev_master_rows} <= {"ADD", "MODIFY"}
    # bom_diff = child/parent & action!=KEEP
    assert all(r.relation in ("child", "parent") and r.action != "KEEP" for r in doc.bom_diff)
    # checklist = CHECK
    assert len(doc.checklist) == 1 and doc.checklist[0].startswith("[ ]")


def test_generate_valid_doc_passes():
    items = [DocItem("AGG74419321", is_new=False, action="MODIFY", tier="CORE", source=_src())]
    doc = generate(items)
    assert validate_doc(doc) == []
    assert_valid(doc)  # raises 안 함


def test_validate_catches_arbitrary_new_pno():
    bad = GeneratedDoc(
        changed_parts=[
            DocRow(pno_display="REAL999", is_new=True, action="ADD", tier="CORE",
                   src="[SRC f/s/1]", valid_source=True, detail="")
        ]
    )
    violations = validate_doc(bad)
    assert any("임의 품번" in v for v in violations)
    with pytest.raises(DocValidationError):
        assert_valid(bad)


def test_validate_catches_missing_source():
    bad = GeneratedDoc(
        changed_parts=[
            DocRow(pno_display="AGG74419321", is_new=False, action="MODIFY", tier="CORE",
                   src="[SRC ?/?/?]", valid_source=False, detail="")
        ]
    )
    violations = validate_doc(bad)
    assert any("출처 없는 행" in v for v in violations)


def test_source_ref_for_resolves_from_db(session):
    sf = SourceFile(file_name="best.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    dpm = DevPartMaster(
        file_id=sf.file_id, form_id="changing_parts_list_96",
        part_no_new="AGG74419321", sheet_name="변경부품 list", source_row=7,
    )
    session.add(dpm)
    session.commit()

    ref = source_ref_for(session, dpm.doc_id)
    assert ref.valid
    assert ref.file_name == "best.xlsx"
    assert ref.sheet_name == "변경부품 list"
    assert ref.source_row == 7
    assert ref.tag() == "[SRC best.xlsx/변경부품 list/7]"
