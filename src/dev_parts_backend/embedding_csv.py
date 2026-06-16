import csv
from pathlib import Path
from typing import Any

from .embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts, embedding_json
from .loader import text


EMBEDDING_COLUMNS = [
    "changing_reason_embedding",
    "changing_point_embedding",
    "part_name_embedding",
    "combined_embedding",
]


def source_texts(row: dict[str, Any]) -> dict[str, str]:
    changing_reason = text(row.get("changing_reason"))
    changing_point = text(row.get("changing_point"))
    part_name = text(row.get("part_name"))
    part_type = text(row.get("part_type"))
    classification = text(row.get("classification"))
    # 과거 데이터는 변경사유/변경점이 절반 이상 비어 있다. combined 텍스트에
    # part_type/classification까지 넣어, 변경 텍스트가 없는 행도 부품 정체성으로
    # 검색되도록 보강한다. (효과는 embed-csv 재생성 후 반영됨)
    combined_parts = []
    if part_name:
        combined_parts.append(f"부품명: {part_name}")
    if part_type:
        combined_parts.append(f"부품유형: {part_type}")
    if classification:
        combined_parts.append(f"분류: {classification}")
    if changing_point:
        combined_parts.append(f"변경점: {changing_point}")
    if changing_reason:
        combined_parts.append(f"변경내역: {changing_reason}")
    return {
        "changing_reason_embedding": changing_reason,
        "changing_point_embedding": changing_point,
        "part_name_embedding": part_name,
        "combined_embedding": "\n".join(combined_parts),
    }


def has_embedding(value: Any) -> bool:
    raw = text(value)
    return bool(raw and raw != "[]")


def read_rows(path: str | Path) -> tuple[list[str], list[dict[str, Any]]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, restkey="_extra_columns")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for column in EMBEDDING_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)
    return fieldnames, rows


def write_rows(path: str | Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def embed_csv(
    input_csv: str | Path,
    output_csv: str | Path,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 96,
    force: bool = False,
) -> dict[str, int | str]:
    fieldnames, rows = read_rows(input_csv)

    jobs: list[tuple[int, str]] = []
    for row_index, row in enumerate(rows):
        for column, source in source_texts(row).items():
            if not source:
                row[column] = "[]"
                continue
            if force or not has_embedding(row.get(column)):
                jobs.append((row_index, column))

    embedded = 0
    for start in range(0, len(jobs), batch_size):
        batch_jobs = jobs[start : start + batch_size]
        batch_inputs = [source_texts(rows[row_index])[column] for row_index, column in batch_jobs]
        vectors = embed_texts(batch_inputs, model=model)
        for job, vector in zip(batch_jobs, vectors):
            row_index, column = job
            rows[row_index][column] = embedding_json(vector)
            embedded += 1

    write_rows(output_csv, fieldnames, rows)
    return {
        "input_csv": str(input_csv),
        "output_csv": str(output_csv),
        "rows": len(rows),
        "embedding_jobs": len(jobs),
        "embedded": embedded,
        "model": model,
    }
