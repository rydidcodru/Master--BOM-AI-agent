"""Master(ms) 코퍼스 tagged import — ms etl_dev dev_part_master → lg, ``source_project='ms'``.

스크래치 ``_migrate_master.py``의 정식 대체. 차이:
  - ``source_project`` 태깅 → production 검색에 ms 선례를 더하되, 'lg' vs 'ms'를 필터로 분리해
    깨끗한 A/B 비교·출처표기 유지(이전 master_import는 무태그 → 56% 조용한 오염).
  - 행 원본 전체를 ``raw_json``으로 보존(ms BomLine.rawJson 차용).
  - file_hash 기준 멱등(기존 import 삭제 후 재적재). change_event는 load_change_events로
    빌드되며 ``source_project`` 전파.

검색 코퍼스 합치기 동기: clean 비교에서 ms(720)가 lg(309)를 압도 — ms 선례가 recall을 올림.
``search_events(..., source_project='lg'|'ms')``로 비교는 계속 분리 가능.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.db.load_change_events import load_change_events, update_event_embeddings
from src.db.models import ChangeEvent, ChangeLine, DevPartMaster, FormRegistry, SourceFile
from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_FILE_HASH = "ms_etl_import_v1"
DEFAULT_FILE_NAME = "ms_etl_import.xlsx"
DEFAULT_FORM_ID = "ms_import"

# ms etl_dev dev_part_master에서 읽는 컬럼 순서(= dict 키).
MS_COLUMNS = [
    "sheet_name", "source_row", "region", "base_model", "new_model", "event",
    "bom_level_raw", "bom_depth", "part_type", "part_no_base", "part_no_new", "part_name",
    "qty_base", "qty_new", "change_point_raw", "change_reason_raw", "supplier",
    "classification", "extra_fields", "embedding_text",
]

# DevPartMaster에 그대로 매핑되는 스칼라 컬럼(extra_fields/embedding_text/raw_json은 별도).
_DPM_SCALAR = [
    "sheet_name", "source_row", "region", "base_model", "new_model", "event",
    "bom_level_raw", "bom_depth", "part_type", "part_no_base", "part_no_new", "part_name",
    "qty_base", "qty_new", "change_point_raw", "change_reason_raw", "supplier", "classification",
]


@dataclass
class ImportResult:
    """tagged import 결과."""

    file_id: int
    dpm_inserted: int = 0
    events_inserted: int = 0
    lines_inserted: int = 0
    embedded: int = 0


def _purge_existing(session: Session, file_hash: str) -> None:
    """같은 file_hash의 기존 import(dpm + change_event/line + source_file) 제거 — 멱등."""
    old = session.execute(
        select(SourceFile).where(SourceFile.file_hash == file_hash)
    ).scalar_one_or_none()
    if old is None:
        return
    ev_ids = session.execute(
        select(ChangeEvent.event_id).where(ChangeEvent.file_id == old.file_id)
    ).scalars().all()
    if ev_ids:
        session.execute(delete(ChangeLine).where(ChangeLine.event_id.in_(ev_ids)))
        session.execute(delete(ChangeEvent).where(ChangeEvent.event_id.in_(ev_ids)))
    session.execute(delete(DevPartMaster).where(DevPartMaster.file_id == old.file_id))
    session.delete(old)
    session.commit()


def _purge_all_ms(session: Session) -> int:
    """모든 ms tagged import 제거 — 구 합성(ms_etl_import_v1) + per-file(``ms:%``). 멱등 재적재용."""
    sfs = session.execute(
        select(SourceFile).where(
            (SourceFile.file_hash == DEFAULT_FILE_HASH) | (SourceFile.file_hash.like("ms:%"))
        )
    ).scalars().all()
    for sf in sfs:
        ev_ids = session.execute(
            select(ChangeEvent.event_id).where(ChangeEvent.file_id == sf.file_id)
        ).scalars().all()
        if ev_ids:
            session.execute(delete(ChangeLine).where(ChangeLine.event_id.in_(ev_ids)))
            session.execute(delete(ChangeEvent).where(ChangeEvent.event_id.in_(ev_ids)))
        session.execute(delete(DevPartMaster).where(DevPartMaster.file_id == sf.file_id))
        session.delete(sf)
    session.commit()
    return len(sfs)


def import_dpm_rows(
    session: Session,
    rows: list[dict[str, Any]],
    *,
    source_project: str = "ms",
    file_name: str = DEFAULT_FILE_NAME,
    file_hash: str = DEFAULT_FILE_HASH,
    form_id: str = DEFAULT_FORM_ID,
    region: str | None = None,
    embed: bool = False,
) -> ImportResult:
    """행 dict 리스트 → lg dev_part_master(tagged) + change_event/line(전파). 멱등.

    Args:
        rows: 각 dict는 :data:`MS_COLUMNS` 키. 원본 행 전체가 ``raw_json``으로 보존됨.
        source_project: 출처 태그(기본 'ms').
        file_name/file_hash/form_id: import 식별(멱등 키 = file_hash).
        embed: True면 reason_embedding까지 backfill(ENABLE_EMBEDDING=1, Postgres 전용).
    """
    _purge_existing(session, file_hash)

    if not session.get(FormRegistry, form_id):
        session.add(FormRegistry(form_id=form_id, description=f"{source_project} tagged import"))
        session.flush()

    sf = SourceFile(file_name=file_name, file_hash=file_hash, region=region)
    session.add(sf)
    session.flush()
    fid = sf.file_id

    # ms 원본 source_row는 (sheet, row) 비고유(ms 자체 키가 part_no 포함) → uq_dpm_source 충돌.
    # 임포트 시퀀스로 합성 고유 source_row를 부여하고, 원본 source_row는 raw_json에 보존(손실 0).
    for i, row in enumerate(rows, start=1):
        scalars = {k: row.get(k) for k in _DPM_SCALAR if k != "source_row"}
        session.add(
            DevPartMaster(
                file_id=fid,
                form_id=form_id,
                source_project=source_project,
                source_row=i,  # 합성 고유 시퀀스 (원본은 raw_json['source_row']에 보존)
                extra_fields=row.get("extra_fields"),
                embedding_text=row.get("embedding_text"),
                raw_json=dict(row) or None,  # 원본 행 전체 보존 (ms rawJson 차용)
                **scalars,
            )
        )
    session.commit()

    ev = load_change_events(session, file_ids=[fid])  # source_project='ms' 전파
    embedded = 0
    if embed:
        embedded = update_event_embeddings(session)

    log.info(
        "db.import_ms.done",
        source_project=source_project,
        dpm=len(rows),
        events=ev.events_inserted,
        lines=ev.lines_inserted,
        embedded=embedded,
    )
    return ImportResult(
        file_id=fid,
        dpm_inserted=len(rows),
        events_inserted=ev.events_inserted,
        lines_inserted=ev.lines_inserted,
        embedded=embedded,
    )


def import_ms_corpus(
    session: Session,
    *,
    host: str = "localhost",
    port: int = 15432,
    user: str = "etl_user",
    password: str = "etl_pass",
    dbname: str = "etl_dev",
    source_project: str = "ms",
    embed: bool = False,
) -> ImportResult:
    """ms etl_dev(15432)를 읽어 **진짜 파일명별**로 tagged import — 출처 추적성 복원.

    이전 버전은 2,860행을 합성 단일 파일(ms_etl_import.xlsx)로 묶어 출처가 가짜였다. 이제
    ms ``source_files``를 조인해 **행마다 진짜 원본 파일명**을 부여하고, lg에 파일별 source_file
    (``file_hash='ms:<name>'``)을 만든다 → change_event.source_ref가 진짜 파일을 가리킨다.
    ms 원본 source_row는 비고유라 dev_part_master.source_row엔 합성 시퀀스(파일 내), 원본은
    raw_json에 보존. psycopg2 필요(live read).
    """
    import psycopg2  # 지역 import — 모듈 import만으로 네트워크/드라이버 의존 없음

    conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
    try:
        cur = conn.cursor()
        cols = ", ".join(f"d.{c}" for c in MS_COLUMNS)  # 컬럼명은 상수(고정)
        cur.execute(
            f"SELECT sf.file_name, {cols} FROM dev_part_master d "
            "JOIN source_files sf ON sf.file_id = d.file_id"
        )
        raw = cur.fetchall()
    finally:
        conn.close()

    # 진짜 파일명별 그룹핑
    by_file: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        fname = r[0] or "ms_unknown_source.xlsx"
        by_file.setdefault(fname, []).append(dict(zip(MS_COLUMNS, r[1:], strict=True)))
    log.info("db.import_ms.read", rows=len(raw), files=len(by_file))

    _purge_all_ms(session)
    if not session.get(FormRegistry, DEFAULT_FORM_ID):
        session.add(FormRegistry(form_id=DEFAULT_FORM_ID, description=f"{source_project} tagged import"))
        session.flush()

    file_ids: list[int] = []
    total = 0
    for fname, rows in by_file.items():
        sf = SourceFile(file_name=fname, file_hash=f"ms:{fname}", region=None)
        session.add(sf)
        session.flush()
        for i, row in enumerate(rows, start=1):
            scalars = {k: row.get(k) for k in _DPM_SCALAR if k != "source_row"}
            session.add(
                DevPartMaster(
                    file_id=sf.file_id,
                    form_id=DEFAULT_FORM_ID,
                    source_project=source_project,
                    source_row=i,  # 파일 내 합성 고유 시퀀스 (ms 원본 source_row는 raw_json 보존)
                    extra_fields=row.get("extra_fields"),
                    embedding_text=row.get("embedding_text"),
                    raw_json=dict(row) or None,
                    **scalars,
                )
            )
        file_ids.append(sf.file_id)
        total += len(rows)
    session.commit()

    ev = load_change_events(session, file_ids=file_ids)  # source_ref = 진짜 파일명, source_project='ms'
    embedded = 0
    if embed:
        embedded = update_event_embeddings(session)
    log.info("db.import_ms.done", source_project=source_project, files=len(file_ids),
             dpm=total, events=ev.events_inserted, lines=ev.lines_inserted, embedded=embedded)
    return ImportResult(
        file_id=file_ids[0] if file_ids else -1,
        dpm_inserted=total,
        events_inserted=ev.events_inserted,
        lines_inserted=ev.lines_inserted,
        embedded=embedded,
    )


__all__ = ["ImportResult", "import_dpm_rows", "import_ms_corpus", "MS_COLUMNS"]
