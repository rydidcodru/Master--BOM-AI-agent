"""D-012 — file_id 단위 DB 롤백.

이전 v2.0: run_id 단위 (preprocessing_runs 테이블).
신규 D-012: file_id 단위 — source_files 한 행 삭제 → CASCADE로
ingestion_log + dev_part_master 자동 삭제.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RollbackResult:
    file_id: int
    rows_deleted: dict[str, int] = field(default_factory=dict)


def rollback_file(session: Session, file_id: int) -> RollbackResult:
    """source_files.file_id 한 행 삭제 → CASCADE로 자식 row 자동 삭제.

    Raises:
        ValueError: 해당 file_id가 source_files에 없으면.
    """
    existing = session.get(SourceFile, file_id)
    if existing is None:
        raise ValueError(f"unknown file_id: {file_id}")

    # CASCADE 이전에 카운트 측정 (보고용)
    dpm_count = session.execute(
        select(func.count())
        .select_from(DevPartMaster)
        .where(DevPartMaster.file_id == file_id)
    ).scalar_one()
    log_count = session.execute(
        select(func.count())
        .select_from(IngestionLog)
        .where(IngestionLog.file_id == file_id)
    ).scalar_one()

    session.execute(delete(SourceFile).where(SourceFile.file_id == file_id))
    session.commit()

    deleted = {
        "source_files": 1,
        "ingestion_log": int(log_count),
        "dev_part_master": int(dpm_count),
    }
    log.info("db.rollback.done", file_id=file_id, deleted=deleted)
    return RollbackResult(file_id=file_id, rows_deleted=deleted)


# Legacy alias — 기존 import 호환용. run_id 기반은 더 이상 지원 X.
def rollback_run(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise NotImplementedError(
        "run_id-based rollback was removed in D-012. "
        "Use rollback_file(session, file_id) instead."
    )
