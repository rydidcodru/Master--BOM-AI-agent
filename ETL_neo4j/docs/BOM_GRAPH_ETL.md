# BOM Graph ETL 사용법

이 모듈은 `data` 폴더의 Excel BOM 파일들을 읽어 Neo4j 적재용 CSV로 변환하고, CSV를 Neo4j에 `LOAD CSV`로 적재하는 Cypher 스크립트를 생성한다.

## 1. Excel -> CSV

```powershell
& 'C:\Users\Study\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m bom_graph_etl.cli export-csv --data-dir data --docs-dir docs --output-dir outputs\neo4j_csv
```

생성 위치:

```text
outputs/neo4j_csv/
```

주요 파일:

- `bom_lines.csv`
- `bom_line_edges.csv`
- `change_records.csv`
- `impact_edges.csv`
- `parts.csv`
- `raw_parts.csv`
- `export_manifest.json`

## 2. CSV -> Neo4j LOAD CSV Cypher 생성

Neo4j import directory에 CSV를 복사해서 사용할 경우:

```powershell
& 'C:\Users\Study\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m bom_graph_etl.cli write-cypher --csv-dir outputs\neo4j_csv --output outputs\neo4j_load\load_all.cypher --base-url file:///
```

생성 위치:

```text
outputs/neo4j_load/load_all.cypher
```

Neo4j Desktop 또는 서버에서 `file:///bom_lines.csv` 같은 경로가 동작하려면 CSV 파일들이 Neo4j import directory에 있어야 한다.

## 3. 제약조건/인덱스 적용

먼저 아래 파일을 Neo4j Browser나 cypher-shell에서 실행한다.

```text
cypher/constraints_indexes.cypher
```

그 다음 `outputs/neo4j_load/load_all.cypher`를 실행한다.

## 4. Python driver로 직접 실행

`neo4j` Python driver가 설치된 환경에서는 아래 명령을 사용할 수 있다.

```powershell
& 'C:\Users\Study\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m bom_graph_etl.cli run-cypher --cypher outputs\neo4j_load\load_all.cypher --uri bolt://localhost:7687 --user neo4j --password YOUR_PASSWORD
```

현재 번들 Python 환경에 `neo4j` driver가 없으면, 생성된 Cypher 파일을 Neo4j Browser 또는 cypher-shell에서 실행하면 된다.
