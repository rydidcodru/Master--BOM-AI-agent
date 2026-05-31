# BOM Knowledge Graph

개발/변경 부품(BOM) Excel 파일들을 Neo4j 지식 그래프로 적재하는 ETL 모듈과 배포 구성이다.
파이프라인은 **Excel → CSV → Neo4j(LOAD CSV)** 3단계로 동작한다.

## 폴더 구조

```text
.
├── bom_graph_etl/                # ETL 파이썬 패키지
│   ├── cli.py                    # CLI 진입점 (export-csv / write-cypher / run-cypher)
│   ├── excel_to_csv.py           # Excel → Neo4j 적재용 CSV 변환 (pandas)
│   └── csv_to_neo4j.py           # LOAD CSV Cypher 생성 / 드라이버 실행
├── build_bom_tree.py             # (독립 스크립트) 단일 Master를 HTML 트리로 시각화
│                                 #   ※ 내부 경로가 Windows로 하드코딩됨 — 배포 흐름과 무관
├── cypher/
│   └── constraints_indexes.cypher  # 제약조건 + 인덱스 (존재 제약은 Enterprise 전용이라 주석 처리)
├── data/                         # 입력 Excel/xlsm BOM 파일들
├── docs/
│   ├── neo4j_bom_schema.md       # 그래프 스키마 설계
│   └── current_excel_csv_design.md # Excel→CSV 매핑 설계
├── outputs/
│   ├── neo4j_csv/                # 생성된 CSV (적재 대상)
│   ├── neo4j_load/load_all.cypher  # 생성된 LOAD CSV 스크립트
│   └── bom_tree_visualization.html
├── docker-compose.yml            # 개발용 (수동 적재, ~/neo4j 에 bind mount)
├── docker-compose.deploy.yml     # 배포용 (up 한 번으로 자동 적재, named volume)
├── load_to_neo4j.sh              # 배포 loader 스크립트
└── .env.example                  # 배포용 환경변수 템플릿
```

## 데이터 모델

노드 8종 / 관계 15종. 자세한 정의는 [docs/neo4j_bom_schema.md](docs/neo4j_bom_schema.md) 참고.

- **노드**: `ReviewCase`, `SourceDocument`, `SourceColumn`, `CanonicalField`, `BomLine`, `Part`, `RawPart`, `ChangeRecord`
- **주요 관계**: `CONTAINS_LINE`, `HAS_CHILD`(BOM 트리), `REPRESENTS_RAW`, `RESOLVES_TO`, `MAPS_TO`, `HAS_CHANGE_RECORD`, `DIRECTLY_CHANGES`, `IMPACTS_LINE`, `REPLACED_BY` 등

기준 적재 규모: 노드 약 20,990개 / 관계 약 53,134개.

## 실행 방법

### 1) 배포용 — 자동 적재 (권장)
data폴더에 엑셀파일을 넣는다
`outputs/neo4j_csv`에 이미 생성된 CSV를 Neo4j에 자동 적재한다. 컨테이너가 떠서 healthy 해지면
loader가 제약조건/인덱스를 적용하고 데이터를 올린 뒤 종료한다.

```bash
cp .env.example .env          # NEO4J_PASSWORD 수정
docker compose -f docker-compose up -d
docker compose -f docker-compose.yml logs -f loader   # 진행 상황
```

- 데이터는 **named volume**(`neo4j_data` 등)에 저장된다.
- 적재는 **멱등**하다 — 이미 데이터가 있으면 loader가 건너뛴다.
- 완전 초기화 후 재적재: `docker compose -f docker-compose down -v` 후 다시 `up`.
- 접속: Browser `http://<host>:7474`, Bolt `bolt://<host>:7687` (계정 `neo4j` / `.env`의 비밀번호).
- 포트가 겹치면 `.env`의 `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT`를 바꾼다.

### 2) 개발용 — 수동 적재

`docker-compose.yml`은 `~/neo4j`에 bind mount 한다. 컨테이너만 띄우고 cypher는 직접 실행한다.

```bash
docker compose up -d
sudo cp outputs/neo4j_csv/*.csv ~/neo4j/import/
cat cypher/constraints_indexes.cypher  | docker exec -i neo4j-graphrag cypher-shell -u neo4j -p 0000
cat outputs/neo4j_load/load_all.cypher | docker exec -i neo4j-graphrag cypher-shell -u neo4j -p 0000
```

### 3) CSV 재생성 (Excel이 바뀐 경우)

`data/`의 Excel을 다시 파싱해 CSV/Cypher를 재생성한다. 자세한 옵션은
[README_BOM_GRAPH_ETL.md](README_BOM_GRAPH_ETL.md) 참고. (`pandas`, `openpyxl` 필요)

```bash
python -m bom_graph_etl.cli export-csv  --data-dir data --docs-dir docs --output-dir outputs/neo4j_csv
python -m bom_graph_etl.cli write-cypher --csv-dir outputs/neo4j_csv --output outputs/neo4j_load/load_all.cypher --base-url file:///
```

## 주의사항

- **Neo4j 5.26 Community** 기준. 속성 존재(`IS NOT NULL`)·node key 제약은 Enterprise 전용이라
  `cypher/constraints_indexes.cypher`에서 주석 처리되어 있다 (고유성 제약 + 인덱스만 적용).
- CSV 셀에 JSON 문자열이 들어있어 백슬래시 이스케이프가 포함된다. Neo4j의 기본
  `db.import.csv.legacy_quote_escaping`(=true)와 충돌하므로 두 compose 모두 **false**로 설정해 둔다.
- `LOAD CSV FROM 'file:/...'`는 **서버 측 import 디렉토리** 기준 상대경로다 — CSV가 그 디렉토리에
  있어야 한다(배포용 loader가 자동으로 복사·권한 처리).
- apoc 플러그인은 최초 기동 시 다운로드되므로 인터넷이 필요하다.
