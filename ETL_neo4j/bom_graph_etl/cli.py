from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

from .csv_to_neo4j import execute_cypher_file, write_load_script
from .excel_to_csv import export_excel_dir


def main() -> None:
    warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
    parser = argparse.ArgumentParser(description="BOM graph ETL CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export-csv", help="Export Excel files to Neo4j-ready CSV files")
    export_parser.add_argument("--data-dir", default=Path("data"), type=Path)
    export_parser.add_argument("--docs-dir", default=Path("docs"), type=Path)
    export_parser.add_argument("--output-dir", default=Path("outputs/neo4j_csv"), type=Path)

    cypher_parser = subparsers.add_parser("write-cypher", help="Write Neo4j LOAD CSV Cypher script")
    cypher_parser.add_argument("--csv-dir", default=Path("outputs/neo4j_csv"), type=Path)
    cypher_parser.add_argument("--output", default=Path("outputs/neo4j_load/load_all.cypher"), type=Path)
    cypher_parser.add_argument("--base-url", default="file:///")

    run_parser = subparsers.add_parser("run-cypher", help="Execute generated Cypher through the neo4j Python driver")
    run_parser.add_argument("--cypher", default=Path("outputs/neo4j_load/load_all.cypher"), type=Path)
    run_parser.add_argument("--uri", default="bolt://localhost:7687")
    run_parser.add_argument("--user", default="neo4j")
    run_parser.add_argument("--password", required=True)
    run_parser.add_argument("--database", default="neo4j")

    args = parser.parse_args()
    if args.command == "export-csv":
        result = export_excel_dir(args.data_dir, args.docs_dir, args.output_dir)
    elif args.command == "write-cypher":
        result = write_load_script(args.csv_dir, args.output, args.base_url)
    elif args.command == "run-cypher":
        execute_cypher_file(args.cypher, args.uri, args.user, args.password, args.database)
        result = {"executed": str(args.cypher)}
    else:
        parser.error(f"unknown command: {args.command}")
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
