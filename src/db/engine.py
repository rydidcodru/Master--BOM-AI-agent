"""D-012 — SQLAlchemy engine + dev_part_master schema bootstrap.

``make_engine`` reads connection settings from env (or accepts an explicit URL,
useful for tests with ``sqlite:///:memory:``). ``init_db`` creates the ORM
tables and — on Postgres only — applies ``schema_dev_part_master.sql`` for
pgvector / pg_trgm extensions and indexes.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

from src.db.models import Base
from src.utils.logging import get_logger

log = get_logger(__name__)

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema_dev_part_master.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _split_sql_statements(sql: str) -> list[str]:
    """``--`` 주석 라인 제거 후 ``;`` 기준 statement 분리."""
    no_comment_lines = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in no_comment_lines.split(";") if s.strip()]


def _apply_sql(conn: Connection, sql: str) -> None:
    for stmt in _split_sql_statements(sql):
        conn.execute(text(stmt))


def _forward_migrations() -> list[Path]:
    """이름순 forward 마이그레이션 (``*.rollback.sql`` 제외, 수동 실행 전용)."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql") if not p.name.endswith(".rollback.sql")
    )


def database_url() -> str:
    """Return the configured Postgres URL from the ``POSTGRES_*`` env vars."""
    user = os.environ.get("POSTGRES_USER", "lg")
    password = os.environ.get("POSTGRES_PASSWORD", "lg_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "lg_bom")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def make_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy Engine for the given URL (or env-derived default).

    SQLite는 기본적으로 FK 제약을 무시 — ``PRAGMA foreign_keys=ON``으로 활성화해야
    ON DELETE CASCADE가 동작 (rollback_file 의존).
    """
    engine = create_engine(url or database_url())

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(
            dbapi_conn: DBAPIConnection, _record: ConnectionPoolEntry
        ) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


_FORM_REGISTRY_SEED = [
    ("changing_parts_list_91", "변경부품 list 91컬럼"),
    ("changing_parts_list_95", "변경부품 list 95컬럼"),
    ("changing_parts_list_96", "변경부품 list 96컬럼"),
    ("changing_parts_list_97", "변경부품 list 97컬럼"),
    ("new_parts_list_75", "신규부품리스트 75컬럼"),
    ("base_master_24", "구버전 24컬럼"),
    ("uae_dev_list", "UAE 신규개발리스트"),
    ("bom_ag_grid_36", "BOM ag-grid 36컬럼"),
    ("v1_2_template_59", "v1.2 통합 마스터 (빈 템플릿)"),
]


def init_db(engine: Engine) -> None:
    """Create all tables + Postgres extensions/indexes via raw SQL.

    On Postgres: ``schema_dev_part_master.sql``이 모든 테이블 / 확장 / 인덱스 +
    form_registry seed까지 한 번에 처리.

    SQLite (단위 테스트): ORM ``create_all`` + form_registry seed 별도 INSERT
    (Vector → JSON variant, FK 활성화는 make_engine에서).
    """
    from src.db.models import FormRegistry

    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            for form_id, description in _FORM_REGISTRY_SEED:
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO form_registry (form_id, description) "
                        "VALUES (:fid, :desc)"
                    ),
                    {"fid": form_id, "desc": description},
                )
        log.info("db.init.sqlite_only", dialect=engine.dialect.name)
        return

    # base schema(4테이블) → forward 마이그레이션(가산 보조 테이블) 순서로 적용.
    # 둘 다 idempotent(IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)이라 재실행 안전.
    with engine.begin() as conn:
        _apply_sql(conn, SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        for mig in _forward_migrations():
            _apply_sql(conn, mig.read_text(encoding="utf-8"))
            log.info("db.migration.applied", file=mig.name)
    log.info("db.init.done", dialect=engine.dialect.name)
