from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PARSER_VERSION = "bom_graph_etl.v0.1"
DEFAULT_FORMAT_TYPE = "auto_bom_excel"
CHANGE_CLASSES = {"Change", "New", "Delete"}
VALID_CLASSES = {"Common", "Change", "New", "Delete"}
SYMBOLS = {"", "-", "X", "x", "←", "nan", "None"}


CANONICAL_FIELDS = [
    ("row_no", "Row No", "Source row number", "integer"),
    ("bom_level", "BOM Level", "Parsed BOM hierarchy level", "integer"),
    ("part_type", "Part Type", "Source part category", "string"),
    ("base_part_no", "Base P/No", "Base part number", "string"),
    ("new_part_no", "New P/No", "New part number or source symbol", "string"),
    ("part_name", "Part Name", "Part name or class description", "string"),
    ("quantity", "Quantity", "Quantity or source quantity symbol", "string"),
    ("changing_point", "Changing Point", "Change point text", "string"),
    ("changing_reason", "Changing Reason", "Change reason text", "string"),
    ("supplier_name", "Supplier", "Supplier name", "string"),
    ("classification", "Classification", "Common Change New Delete", "string"),
]


CSV_HEADERS = {
    "review_cases.csv": [
        "caseId",
        "modelName",
        "baseModel",
        "newModel",
        "event",
        "status",
        "summary",
        "searchText",
        "createdAt",
    ],
    "source_documents.csv": [
        "documentId",
        "fileName",
        "filePath",
        "sheetName",
        "formatType",
        "importedAt",
        "parserVersion",
        "rawMetadata",
    ],
    "document_case_edges.csv": ["documentId", "caseId"],
    "canonical_fields.csv": ["key", "displayName", "description", "dataType"],
    "source_columns.csv": [
        "sourceColumnId",
        "documentId",
        "documentFormat",
        "sheetName",
        "rawColumnName",
        "columnIndex",
        "canonicalFieldKey",
        "confidence",
        "method",
        "parserVersion",
    ],
    "bom_lines.csv": [
        "lineId",
        "documentId",
        "caseId",
        "rowNo",
        "level",
        "rawLevel",
        "partType",
        "basePartNoRaw",
        "newPartNoRaw",
        "effectivePartNo",
        "partNameRaw",
        "qtyRaw",
        "effectiveQty",
        "supplierRaw",
        "classification",
        "changingPoint",
        "changingReason",
        "rawJson",
        "searchText",
    ],
    "bom_line_edges.csv": ["parentLineId", "childLineId", "parentRowNo", "childRowNo"],
    "raw_parts.csv": [
        "rawPartId",
        "rawPartNo",
        "rawPartName",
        "rawPartType",
        "sourceRole",
        "sourceDocumentId",
        "sourceLineId",
        "sourceColumnKey",
    ],
    "parts.csv": ["partId", "canonicalPartNo", "canonicalName", "partType", "status", "searchText"],
    "raw_part_resolutions.csv": ["rawPartId", "partId", "confidence", "method", "reviewedBy", "reviewedAt"],
    "bom_line_part_resolutions.csv": ["lineId", "partId", "role", "method"],
    "change_records.csv": [
        "changeId",
        "caseId",
        "lineId",
        "classification",
        "basePartNoRaw",
        "newPartNoRaw",
        "effectivePartNo",
        "changingPoint",
        "changingReason",
        "searchText",
    ],
    "change_record_edges.csv": [
        "changeId",
        "lineId",
        "directPartId",
        "directPartRole",
        "basePartId",
        "newPartId",
    ],
    "impact_edges.csv": ["changeId", "impactedLineId", "distance", "impactType"],
    "replacement_edges.csv": [
        "fromPartId",
        "toPartId",
        "caseId",
        "documentId",
        "lineId",
        "classification",
        "changingPoint",
        "changingReason",
    ],
}


@dataclass
class ParsedSheet:
    path: Path
    sheet_name: str
    header_row: int
    metadata: dict[str, str]
    dataframe: pd.DataFrame
    columns: dict[str, str | None]


def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:length]


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", clean(value).replace("\n", " ")).strip().lower()


def normalize_part_no(value: str) -> str:
    return re.sub(r"\s+", "", clean(value)).upper()


def valid_part_no(value: str) -> bool:
    text = clean(value)
    return bool(text) and text not in SYMBOLS


def part_id(part_no: str) -> str:
    normalized = normalize_part_no(part_no)
    return f"part:{stable_hash(normalized, 20)}"


def bom_level(value: Any) -> int | None:
    text = clean(value)
    if not text:
        return None
    match = re.search(r"(\d+)$", text)
    if match:
        return int(match.group(1))
    dots = text.count(".")
    return dots if dots else None


def normalize_classification(value: Any) -> str:
    text = clean(value)
    key = text.lower()
    if "common" in key or "공통" in key:
        return "Common"
    if "change" in key or "변경" in key:
        return "Change"
    if "delete" in key or "삭제" in key:
        return "Delete"
    if key == "new" or "신규" in key or "추가" in key:
        return "New"
    return text if text in VALID_CLASSES else text


def parse_qty(value: Any) -> str:
    text = clean(value)
    if not text or text in {"←", "-"}:
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return str(number)


def effective_part_no(base_raw: str, new_raw: str, classification: str) -> str:
    base = clean(base_raw)
    new = clean(new_raw)
    if new == "←":
        return base if valid_part_no(base) else ""
    if new.upper() == "X":
        return ""
    if classification in {"Change", "New"} and valid_part_no(new):
        return new
    if valid_part_no(new) and not valid_part_no(base):
        return new
    if valid_part_no(base):
        return base
    return ""


def row_to_json(row: pd.Series) -> str:
    payload = {str(k): clean(v) for k, v in row.items()}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def search_text(*parts: Any) -> str:
    return " | ".join(clean(part) for part in parts if clean(part))


def find_header_row(raw: pd.DataFrame) -> int | None:
    for idx, row in raw.head(40).iterrows():
        values = [normalize_key(v) for v in row.tolist()]
        joined = " ".join(values)
        has_level = (
            ("bom" in joined and "level" in joined)
            or any(value in {"level", "lvl"} for value in values)
            or any("bom level" in value for value in values)
        )
        has_part_no = (
            "p/no" in joined
            or "p/no." in joined
            or "part no" in joined
            or "part no." in joined
            or "part number" in joined
        )
        has_part = has_part_no or "part" in joined or "부품" in joined
        if has_level and has_part:
            return int(idx)
    return None


def parse_metadata(raw: pd.DataFrame, header_row: int) -> dict[str, str]:
    metadata: dict[str, str] = {}
    aliases = {
        "baseModel": ["base model/grade", "base model", "base"],
        "newModel": ["new model/grade", "new model"],
        "event": ["event"],
    }
    for _, row in raw.head(header_row).iterrows():
        cells = [clean(v) for v in row.tolist()]
        normalized = [normalize_key(v) for v in cells]
        for out_key, labels in aliases.items():
            if metadata.get(out_key):
                continue
            for pos, value in enumerate(normalized):
                if any(label in value for label in labels):
                    for next_value in cells[pos + 1 :]:
                        if next_value:
                            metadata[out_key] = next_value
                            break
                    break
    metadata.setdefault("baseModel", "")
    metadata.setdefault("newModel", "")
    metadata.setdefault("event", "DV")
    return metadata


def find_col(columns: list[str], *needles: str) -> str | None:
    normalized = [(col, normalize_key(col)) for col in columns]
    for col, key in normalized:
        if all(needle.lower() in key for needle in needles):
            return col
    return None


def detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    columns = [str(c) for c in df.columns]
    base_pno = (
        find_col(columns, "p/no")
        or find_col(columns, "p/no.")
        or find_col(columns, "part no")
        or find_col(columns, "part no.")
        or find_col(columns, "part number")
    )
    new_pno = None
    if base_pno is not None:
        base_idx = columns.index(base_pno)
        if base_idx + 1 < len(columns):
            next_col = columns[base_idx + 1]
            next_key = normalize_key(next_col)
            if "new" in next_key or "unnamed" in next_key:
                new_pno = next_col

    return {
        "row_no": find_col(columns, "no") or (columns[1] if len(columns) > 1 else None),
        "level": find_col(columns, "bom", "level") or find_col(columns, "level") or find_col(columns, "lvl"),
        "part_type": find_col(columns, "part", "type"),
        "base_part_no": base_pno,
        "new_part_no": new_pno,
        "part_name": (
            find_col(columns, "part name")
            or find_col(columns, "class desc")
            or find_col(columns, "desc")
            or find_col(columns, "부품명")
        ),
        "quantity": find_col(columns, "q'ty") or find_col(columns, "qty") or find_col(columns, "quantity"),
        "changing_point": find_col(columns, "changing point") or find_col(columns, "변경점"),
        "changing_reason": find_col(columns, "changing reason") or find_col(columns, "변경사유"),
        "supplier": find_col(columns, "supplier") or find_col(columns, "양산처"),
        "classification": find_col(columns, "classification") or find_col(columns, "신규") or find_col(columns, "부품 대상"),
    }


def read_design_docs(docs_dir: Path) -> dict[str, Any]:
    docs = {}
    if not docs_dir.exists():
        return docs
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        docs[path.name] = {
            "path": str(path),
            "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
            "bytes": len(text.encode("utf-8")),
        }
    return docs


def discover_sheets(path: Path) -> list[ParsedSheet]:
    parsed: list[ParsedSheet] = []
    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:
        raise RuntimeError(f"workbook open failed: {exc}") from exc

    for sheet_name in xl.sheet_names:
        try:
            raw = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=50)
        except Exception:
            continue
        header_row = find_header_row(raw)
        if header_row is None:
            continue
        try:
            df = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
        except Exception:
            continue
        columns = detect_columns(df)
        if not columns.get("level"):
            continue
        parsed.append(
            ParsedSheet(
                path=path,
                sheet_name=sheet_name,
                header_row=header_row,
                metadata=parse_metadata(raw, header_row),
                dataframe=df,
                columns=columns,
            )
        )
    return parsed


def append_unique(target: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
    if key not in target:
        target[key] = row


def build_rows(data_dir: Path, docs_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in CSV_HEADERS}
    part_rows: dict[str, dict[str, Any]] = {}
    raw_part_rows: dict[str, dict[str, Any]] = {}
    raw_resolutions: dict[str, dict[str, Any]] = {}
    bom_resolutions: dict[str, dict[str, Any]] = {}
    replacement_edges: dict[str, dict[str, Any]] = {}
    manifest = {
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "parserVersion": PARSER_VERSION,
        "docs": read_design_docs(docs_dir),
        "files": [],
    }

    for key, display, description, data_type in CANONICAL_FIELDS:
        buckets["canonical_fields.csv"].append(
            {"key": key, "displayName": display, "description": description, "dataType": data_type}
        )

    excel_paths = sorted(
        [
            path
            for pattern in ("*.xlsx", "*.xlsm")
            for path in data_dir.glob(pattern)
            if not path.name.startswith("~$")
        ]
    )

    for path in excel_paths:
        file_report = {"file": str(path), "sheets": [], "status": "ok", "errors": []}
        try:
            sheets = discover_sheets(path)
        except Exception as exc:
            file_report["status"] = "failed"
            file_report["errors"].append(str(exc))
            manifest["files"].append(file_report)
            continue

        if not sheets:
            file_report["status"] = "skipped"
            file_report["errors"].append("no BOM-like sheet found")
            manifest["files"].append(file_report)
            continue

        for sheet in sheets:
            sheet_report = {"sheet": sheet.sheet_name, "rows": 0, "changes": 0, "headerRow": sheet.header_row}
            document_id = "doc:" + stable_hash(f"{path.resolve()}|{sheet.sheet_name}", 24)
            base_model = sheet.metadata.get("baseModel", "")
            new_model = sheet.metadata.get("newModel", "")
            event = sheet.metadata.get("event", "DV") or "DV"
            case_id = "case:" + stable_hash(f"{base_model}|{new_model}|{event}|{document_id}", 24)
            imported_at = datetime.now(timezone.utc).isoformat()

            buckets["review_cases.csv"].append(
                {
                    "caseId": case_id,
                    "modelName": new_model or base_model or path.stem,
                    "baseModel": base_model,
                    "newModel": new_model,
                    "event": event,
                    "status": "imported",
                    "summary": f"{path.name} / {sheet.sheet_name}",
                    "searchText": search_text(path.name, sheet.sheet_name, base_model, new_model, event),
                    "createdAt": imported_at,
                }
            )
            buckets["source_documents.csv"].append(
                {
                    "documentId": document_id,
                    "fileName": path.name,
                    "filePath": str(path.resolve()),
                    "sheetName": sheet.sheet_name,
                    "formatType": DEFAULT_FORMAT_TYPE,
                    "importedAt": imported_at,
                    "parserVersion": PARSER_VERSION,
                    "rawMetadata": json.dumps(sheet.metadata, ensure_ascii=False, sort_keys=True),
                }
            )
            buckets["document_case_edges.csv"].append({"documentId": document_id, "caseId": case_id})

            for idx, col in enumerate(sheet.dataframe.columns):
                raw_col = str(col)
                canonical = canonical_for_column(raw_col, idx, sheet.columns)
                source_column_id = f"{document_id}:col:{idx}"
                buckets["source_columns.csv"].append(
                    {
                        "sourceColumnId": source_column_id,
                        "documentId": document_id,
                        "documentFormat": DEFAULT_FORMAT_TYPE,
                        "sheetName": sheet.sheet_name,
                        "rawColumnName": raw_col,
                        "columnIndex": idx,
                        "canonicalFieldKey": canonical,
                        "confidence": "1.0" if canonical else "",
                        "method": "auto_header_match" if canonical else "",
                        "parserVersion": PARSER_VERSION,
                    }
                )

            stack: list[dict[str, Any]] = []
            parent_by_line: dict[str, str] = {}
            lines_by_id: dict[str, dict[str, Any]] = {}
            line_order: list[str] = []

            for df_index, row in sheet.dataframe.iterrows():
                raw_level = get(row, sheet.columns["level"])
                level = bom_level(raw_level)
                if level is None or level < 1:
                    continue
                row_no_raw = get(row, sheet.columns["row_no"])
                row_no = row_no_raw if row_no_raw else str(int(df_index) + sheet.header_row + 2)
                line_id = f"{document_id}:line:{row_no}"
                part_type = get(row, sheet.columns["part_type"])
                base_raw = get(row, sheet.columns["base_part_no"])
                new_raw = get(row, sheet.columns["new_part_no"])
                part_name = get(row, sheet.columns["part_name"])
                qty_raw = parse_qty(get(row, sheet.columns["quantity"]))
                supplier = get(row, sheet.columns["supplier"])
                classification = normalize_classification(get(row, sheet.columns["classification"]))
                changing_point = get(row, sheet.columns["changing_point"])
                changing_reason = get(row, sheet.columns["changing_reason"])
                effective_no = effective_part_no(base_raw, new_raw, classification)
                raw_json = row_to_json(row)

                bom_row = {
                    "lineId": line_id,
                    "documentId": document_id,
                    "caseId": case_id,
                    "rowNo": row_no,
                    "level": level,
                    "rawLevel": clean(raw_level),
                    "partType": part_type,
                    "basePartNoRaw": base_raw,
                    "newPartNoRaw": new_raw,
                    "effectivePartNo": effective_no,
                    "partNameRaw": part_name,
                    "qtyRaw": qty_raw,
                    "effectiveQty": qty_raw if qty_raw not in {"←", "-"} else "",
                    "supplierRaw": supplier,
                    "classification": classification,
                    "changingPoint": changing_point,
                    "changingReason": changing_reason,
                    "rawJson": raw_json,
                    "searchText": search_text(
                        path.name,
                        sheet.sheet_name,
                        base_model,
                        new_model,
                        event,
                        row_no,
                        level,
                        part_type,
                        base_raw,
                        new_raw,
                        effective_no,
                        part_name,
                        supplier,
                        classification,
                        changing_point,
                        changing_reason,
                    ),
                }
                buckets["bom_lines.csv"].append(bom_row)
                lines_by_id[line_id] = bom_row
                line_order.append(line_id)
                sheet_report["rows"] += 1

                while len(stack) >= level:
                    stack.pop()
                if stack:
                    parent = stack[-1]
                    parent_by_line[line_id] = parent["lineId"]
                    buckets["bom_line_edges.csv"].append(
                        {
                            "parentLineId": parent["lineId"],
                            "childLineId": line_id,
                            "parentRowNo": parent["rowNo"],
                            "childRowNo": row_no,
                        }
                    )
                stack.append({"lineId": line_id, "rowNo": row_no, "level": level})

                role_values = {
                    "base": base_raw,
                    "new": new_raw,
                    "effective": effective_no,
                }
                for role, raw_part_no in role_values.items():
                    if not valid_part_no(raw_part_no):
                        continue
                    raw_part_id = f"{line_id}:rawpart:{role}"
                    pid = part_id(raw_part_no)
                    append_unique(
                        raw_part_rows,
                        raw_part_id,
                        {
                            "rawPartId": raw_part_id,
                            "rawPartNo": raw_part_no,
                            "rawPartName": part_name,
                            "rawPartType": part_type,
                            "sourceRole": role,
                            "sourceDocumentId": document_id,
                            "sourceLineId": line_id,
                            "sourceColumnKey": f"{role}_part_no",
                        },
                    )
                    append_unique(
                        part_rows,
                        pid,
                        {
                            "partId": pid,
                            "canonicalPartNo": normalize_part_no(raw_part_no),
                            "canonicalName": part_name,
                            "partType": part_type,
                            "status": "active",
                            "searchText": search_text(normalize_part_no(raw_part_no), part_name, part_type),
                        },
                    )
                    raw_resolutions[raw_part_id] = {
                        "rawPartId": raw_part_id,
                        "partId": pid,
                        "confidence": "1.0",
                        "method": "exact_part_no",
                        "reviewedBy": "",
                        "reviewedAt": "",
                    }
                    bom_resolutions[f"{line_id}|{role}|{pid}"] = {
                        "lineId": line_id,
                        "partId": pid,
                        "role": role,
                        "method": "auto",
                    }

                if classification in CHANGE_CLASSES:
                    change_id = f"{line_id}:change"
                    direct_no = direct_part_for_change(classification, base_raw, new_raw, effective_no)
                    direct_pid = part_id(direct_no) if valid_part_no(direct_no) else ""
                    base_pid = part_id(base_raw) if valid_part_no(base_raw) else ""
                    new_pid = part_id(new_raw) if valid_part_no(new_raw) else ""
                    buckets["change_records.csv"].append(
                        {
                            "changeId": change_id,
                            "caseId": case_id,
                            "lineId": line_id,
                            "classification": classification,
                            "basePartNoRaw": base_raw,
                            "newPartNoRaw": new_raw,
                            "effectivePartNo": effective_no,
                            "changingPoint": changing_point,
                            "changingReason": changing_reason,
                            "searchText": search_text(
                                classification,
                                base_raw,
                                new_raw,
                                effective_no,
                                part_name,
                                changing_point,
                                changing_reason,
                            ),
                        }
                    )
                    buckets["change_record_edges.csv"].append(
                        {
                            "changeId": change_id,
                            "lineId": line_id,
                            "directPartId": direct_pid,
                            "directPartRole": direct_role_for_change(classification),
                            "basePartId": base_pid,
                            "newPartId": new_pid,
                        }
                    )
                    for distance, impacted_line_id in enumerate(ancestor_chain(line_id, parent_by_line)):
                        buckets["impact_edges.csv"].append(
                            {
                                "changeId": change_id,
                                "impactedLineId": impacted_line_id,
                                "distance": distance,
                                "impactType": impact_type(distance),
                            }
                        )
                    if classification == "Change" and base_pid and new_pid and base_pid != new_pid:
                        replacement_edges[f"{base_pid}|{new_pid}|{change_id}"] = {
                            "fromPartId": base_pid,
                            "toPartId": new_pid,
                            "caseId": case_id,
                            "documentId": document_id,
                            "lineId": line_id,
                            "classification": classification,
                            "changingPoint": changing_point,
                            "changingReason": changing_reason,
                        }
                    sheet_report["changes"] += 1

            file_report["sheets"].append(sheet_report)
        manifest["files"].append(file_report)

    buckets["parts.csv"].extend(part_rows.values())
    buckets["raw_parts.csv"].extend(raw_part_rows.values())
    buckets["raw_part_resolutions.csv"].extend(raw_resolutions.values())
    buckets["bom_line_part_resolutions.csv"].extend(bom_resolutions.values())
    buckets["replacement_edges.csv"].extend(replacement_edges.values())
    return buckets, manifest


def canonical_for_column(raw_col: str, index: int, detected: dict[str, str | None]) -> str:
    mapping = {
        "row_no": "row_no",
        "level": "bom_level",
        "part_type": "part_type",
        "base_part_no": "base_part_no",
        "new_part_no": "new_part_no",
        "part_name": "part_name",
        "quantity": "quantity",
        "changing_point": "changing_point",
        "changing_reason": "changing_reason",
        "supplier": "supplier_name",
        "classification": "classification",
    }
    for role, col in detected.items():
        if col == raw_col:
            return mapping.get(role, "")
    return ""


def get(row: pd.Series, col: str | None) -> str:
    if col is None:
        return ""
    if col not in row:
        return ""
    return clean(row[col])


def direct_part_for_change(classification: str, base_raw: str, new_raw: str, effective_no: str) -> str:
    if classification == "Delete":
        return base_raw
    if classification in {"Change", "New"} and valid_part_no(new_raw):
        return new_raw
    return effective_no


def direct_role_for_change(classification: str) -> str:
    if classification == "Delete":
        return "base"
    if classification in {"Change", "New"}:
        return "new"
    return "effective"


def ancestor_chain(line_id: str, parent_by_line: dict[str, str]) -> list[str]:
    chain = [line_id]
    current = line_id
    while current in parent_by_line:
        current = parent_by_line[current]
        chain.append(current)
    return chain


def impact_type(distance: int) -> str:
    if distance == 0:
        return "direct"
    if distance == 1:
        return "parent"
    return "ancestor"


def write_csvs(rows: dict[str, list[dict[str, Any]]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, headers in CSV_HEADERS.items():
        path = output_dir / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows.get(filename, []):
                writer.writerow({header: row.get(header, "") for header in headers})


def export_excel_dir(data_dir: Path, docs_dir: Path, output_dir: Path) -> dict[str, Any]:
    rows, manifest = build_rows(data_dir, docs_dir)
    write_csvs(rows, output_dir)
    summary = {
        "csvDir": str(output_dir),
        "files": len(manifest["files"]),
        "reviewCases": len(rows["review_cases.csv"]),
        "sourceDocuments": len(rows["source_documents.csv"]),
        "bomLines": len(rows["bom_lines.csv"]),
        "bomEdges": len(rows["bom_line_edges.csv"]),
        "parts": len(rows["parts.csv"]),
        "rawParts": len(rows["raw_parts.csv"]),
        "changeRecords": len(rows["change_records.csv"]),
        "impactEdges": len(rows["impact_edges.csv"]),
        "skippedFiles": [f for f in manifest["files"] if f["status"] != "ok"],
    }
    manifest["summary"] = summary
    (output_dir / "export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    parser = argparse.ArgumentParser(description="Export BOM Excel files to Neo4j-ready CSV files.")
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--docs-dir", default="docs", type=Path)
    parser.add_argument("--output-dir", default=Path("outputs/neo4j_csv"), type=Path)
    args = parser.parse_args()
    summary = export_excel_dir(args.data_dir, args.docs_dir, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
