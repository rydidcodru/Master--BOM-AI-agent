import sqlite3
from importlib import resources
from pathlib import Path


class Connection(sqlite3.Connection):
    """임의 속성 할당을 허용하는 Connection.

    sqlite3.Connection은 인스턴스 속성을 막아서 요청 범위 캐시
    (예: rag.search의 임베딩 캐시)를 붙일 수 없다. 서브클래스로 허용한다.
    """


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, factory=Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    schema = resources.files("dev_parts_backend").joinpath("schema.sql").read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema)
        ensure_detail_embedding_columns(conn)


def ensure_detail_embedding_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dev_part_detail)").fetchall()
    }
    columns = {
        "changing_reason_embedding": "TEXT",
        "changing_point_embedding": "TEXT",
        "part_name_embedding": "TEXT",
        "combined_embedding": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE dev_part_detail ADD COLUMN {name} {column_type}")


def reset_data(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM dev_part_detail_fts")
    conn.execute("DELETE FROM dev_part_detail")
    conn.execute("DELETE FROM dev_part_master")
    conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('dev_part_detail', 'dev_part_master')")


def refresh_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM dev_part_detail_fts")
    conn.execute(
        """
        INSERT INTO dev_part_detail_fts(
            detail_id,
            part_name,
            base_part_no,
            new_part_no,
            changing_point,
            changing_reason,
            supplier,
            classification
        )
        SELECT
            detail_id,
            COALESCE(part_name, ''),
            COALESCE(base_part_no, ''),
            COALESCE(new_part_no, ''),
            COALESCE(changing_point, ''),
            COALESCE(changing_reason, ''),
            COALESCE(supplier, ''),
            COALESCE(classification, '')
        FROM dev_part_detail
        """
    )
