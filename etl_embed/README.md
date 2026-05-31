# ETL_PG: Excel -> PostgreSQL + pgvector

개발부품/변경부품 엑셀 파일을 읽어서 PostgreSQL에 표준화 저장하는 ETL 프로젝트입니다.

현재는 `input_files` 폴더의 `.xlsx`, `.xlsm` 파일을 자동 탐색하고, 여러 형태의 엑셀 시트에서 부품번호, 부품명, 변경점, 변경사유, 모델, 지역 정보를 추출해 `dev_part_master` 테이블에 적재합니다.

자세한 비전공자용 명세는 [docs/ETL_PG_PROJECT_SPEC.md](docs/ETL_PG_PROJECT_SPEC.md)를 참고하세요.

## 현재 상태

최신 적재 결과:

| 항목 | 값 |
|---|---:|
| source_files | 23 files |
| dev_part_master | 2,860 rows |
| ingestion_log | 120 logs |

현재 `input_files`에는 24개 엑셀 파일이 있으며, 그중 1개는 엑셀 내부 XML 문제로 `openpyxl`이 열지 못합니다.

실패 파일:

```text
240430 BDO30 SKS Transitional 개발부품Master List Ver.20200814 Part DMS v2.xlsx
```

오류 요약:

```text
#N/A is not a valid print titles definition
```

이 파일은 파서 매핑 문제가 아니라 엑셀 파일 내부의 인쇄 제목/워크북 XML 문제입니다.

## 폴더 구조

```text
etl_pg/
├── docker-compose.yml          # PostgreSQL + pgvector 컨테이너
├── pyproject.toml              # uv 의존성
├── .env                        # 로컬 DB/입력 폴더 설정
├── schema_postgres.sql         # DB 스키마와 form_registry seed
├── run.py                      # 실행 엔트리
├── input_files/                # 엑셀 파일 위치
├── docs/
│   └── ETL_PG_PROJECT_SPEC.md  # 프로젝트 상세 명세
└── parsers/
    ├── utils.py                # 해싱, 셀 정리, 숫자 변환 등 공통 유틸
    ├── db.py                   # psycopg3 DB 레이어
    ├── pipeline.py             # 파일 단위 ETL 흐름
    └── readers/
        └── dev_part_master.py  # 엑셀 시트 파서
```

## DB 실행

Docker로 PostgreSQL을 실행합니다.

```powershell
docker compose up -d
```

컨테이너 상태 확인:

```powershell
docker ps
```

현재 포트는 `15432:5432`입니다. Windows 로컬 PostgreSQL이 `5432`를 쓰고 있어서 Docker 포트를 `15432`로 분리했습니다.

접속 정보:

| 항목 | 값 |
|---|---|
| Host | localhost |
| Port | 15432 |
| Database | etl_dev |
| User | etl_user |
| Password | etl_pass |
| Container | etl_postgres |

컨테이너 안에서 psql 접속:

```powershell
docker exec -it etl_postgres psql -U etl_user -d etl_dev
```

호스트에 psql이 설치되어 있다면:

```powershell
psql -h localhost -p 15432 -U etl_user -d etl_dev
```

## Python 의존성

```powershell
uv sync
```

실행 시 `uv`에서 아래 경고가 보일 수 있습니다.

```text
tool.uv.dev-dependencies field is deprecated
```

현재 ETL 실행에는 영향이 없습니다.

## 환경 설정

현재 `.env` 예시:

```text
PGHOST=localhost
PGPORT=15432
PGDATABASE=etl_dev
PGUSER=etl_user
PGPASSWORD=etl_pass
UPLOAD_DIR=./input_files
```

`run.py`는 `.env`를 직접 읽습니다.

## 입력 파일 넣기

엑셀 파일을 아래 폴더에 넣습니다.

```text
input_files/
```

지원 확장자:

```text
.xlsx
.xlsm
```

`~$`로 시작하는 엑셀 임시 파일은 자동 제외합니다.

## ETL 실행

```powershell
uv run python run.py
```

실행하면 다음 순서로 처리됩니다.

```text
1. PostgreSQL 연결
2. schema_postgres.sql 적용
3. 기존 적재 데이터 초기화
4. input_files의 엑셀 파일 자동 탐색
5. 각 파일의 시트 분석
6. 지원 가능한 시트에서 부품 row 추출
7. source_files, ingestion_log, dev_part_master에 저장
8. 파일별/전체 적재 결과 출력
```

주의: 현재 `run.py`는 개발 편의를 위해 매번 `reset_data()`를 호출합니다. 즉 실행할 때마다 기존 `source_files`, `ingestion_log`, `dev_part_master` 데이터가 비워지고 다시 적재됩니다.

운영 모드에서는 이 동작을 옵션화하거나 제거해야 합니다.

## 파서 방식

현재 파서는 3가지 양식을 지원합니다.

| form_id | 설명 |
|---|---|
| dev_part_master_v1 | 기존 이중 헤더 양식. row 8/9 기반 |
| dev_part_master_v2 | 기존 단일 헤더 양식. row 9 기반 |
| dev_part_master_dynamic | 상위 20행에서 헤더를 자동 탐색하는 동적 양식 |

동적 파서는 아래처럼 파일마다 다르게 적힌 컬럼명을 표준 필드로 맞춥니다.

| 표준 의미 | 인식하는 표현 예시 |
|---|---|
| 부품번호 | `Base P/No`, `New P/No`, `P/no.`, `Part No` |
| 부품명 | `Class Desc.`, `Class Desc.(Part Name)`, `Desc.`, `Part` |
| 레벨 | `BOM Level`, `Level`, `Lvl` |
| 변경점 | `Changing Point`, `변경점`, `변경 내역` |
| 변경사유 | `Changing Reason`, `변경사유`, `변경 사유` |

아래 성격의 시트는 현재 `dev_part_master` 적재 대상에서 제외합니다.

```text
History
Summary
DQMS
HSMS
SSOID
FCM
PartDMS설명서
PRA
시험기획
부품인정시험항목
부품등급심의
비교단가
BOM Compare
```

이 시트들은 나중에 별도 테이블로 분리하는 것이 좋습니다.

## 주요 테이블

### source_files

원본 파일 단위 메타데이터를 저장합니다.

```text
file_name
file_hash
file_size
region
ingested_at
```

### ingestion_log

시트별 처리 결과를 저장합니다.

```text
sheet_name
form_id
rows_total
rows_inserted
status
error_message
```

### form_registry

지원하는 엑셀 양식을 등록합니다.

현재:

```text
dev_part_master_v1
dev_part_master_v2
dev_part_master_dynamic
```

### dev_part_master

최종 개발부품/변경부품 row가 저장되는 핵심 테이블입니다.

주요 컬럼:

```text
region
base_model
new_model
event
bom_level_raw
bom_depth
part_type
part_no_base
part_no_new
part_name
qty_base
qty_new
change_point_raw
change_reason_raw
supplier
classification
extra_fields
embedding_text
embedding_dense
```

`embedding_text`는 RAG 검색용 본문이고, `embedding_dense`는 추후 임베딩 벡터를 저장할 컬럼입니다.

## 검증 쿼리

전체 건수:

```sql
SELECT count(*) FROM source_files;
SELECT count(*) FROM dev_part_master;
SELECT count(*) FROM ingestion_log;
```

파일별 적재 row 수:

```sql
SELECT sf.file_name, count(dpm.doc_id) AS rows
FROM source_files sf
LEFT JOIN dev_part_master dpm ON dpm.file_id = sf.file_id
GROUP BY sf.file_name
ORDER BY rows DESC;
```

0 row 파일 확인:

```sql
SELECT sf.file_name, count(dpm.doc_id) AS rows
FROM source_files sf
LEFT JOIN dev_part_master dpm ON dpm.file_id = sf.file_id
GROUP BY sf.file_name
HAVING count(dpm.doc_id) = 0;
```

지역별 row 수:

```sql
SELECT coalesce(region, '(미분류)') AS region, count(*)
FROM dev_part_master
GROUP BY region
ORDER BY count(*) DESC;
```

파서 양식별 row 수:

```sql
SELECT form_id, count(*)
FROM dev_part_master
GROUP BY form_id
ORDER BY count(*) DESC;
```

부품번호 검색:

```sql
SELECT region, sheet_name, source_row, part_no_new, part_name, change_reason_raw
FROM dev_part_master
WHERE part_no_new = 'AAA30802494';
```

변경사유 키워드 검색:

```sql
SELECT part_no_new, part_name, change_reason_raw
FROM dev_part_master
WHERE change_reason_raw ILIKE '%삭제%'
LIMIT 20;
```

샘플 확인:

```sql
SELECT region, sheet_name, part_no_new, part_name, change_reason_raw
FROM dev_part_master
LIMIT 20;
```

## 현재 파일별 적재 결과

| 파일명 | row 수 |
|---|---:|
| 개발부품Master Single IH Final.xlsx | 913 |
| 개발변경부품리스트 전개모델 230110.xlsx | 241 |
| 개발변경부품Master Best 221226.xlsx | 238 |
| 개발변경부품Master Better 221226.xlsx | 238 |
| 개발변경부품Master Good 221226.xlsx | 238 |
| BO24 호주향 개발부품 마스터 240221.xlsm | 122 |
| UAE 24인치 오븐 부품 개발 List 230731.xlsx | 119 |
| BO24 유럽향 VI Steam Heater 개발 부품 마스터.xlsm | 118 |
| BO24 오븐 이집트향 부품 개발 완료 Master 240306.xlsx | 117 |
| 이라크 24인치 오븐 부품개발 완료 List 230731.xlsx | 111 |
| BO24 북유럽 CE 개발부품 마스터 250113.xlsm | 106 |
| BO24 북미 부품 개발 완료 Activity 240409.xlsx | 81 |
| BO24 포르투갈, 그리스 개발부품 마스터 240516.xlsm | 36 |
| 250213 사우디 개발부품Master List.xlsm | 34 |
| BO24 오븐 사우디향 부품 개발 완료 Master 240214.xlsx | 34 |
| ★통합 개발부품Master Integrated Dev Part Master v1.2 동유럽향.xlsx | 24 |
| BO24 포르투갈 그리스 부품 개발 완료 Activity 240627.xlsm | 22 |
| BO24 호주 B700 nonpyro 개발부품 마스터 241120.xlsx | 21 |
| ★통합 개발부품Master Integrated Dev Part Master v1.2 싱가포르향 (1) (1).xlsx | 21 |
| 250424 모리셔스향 파급 개발 개발부품마스터.xlsx | 18 |
| 개발부품마스터 BO24 CIS Best.xlsx | 3 |
| 개발부품마스터 BO24 CIS Better.xlsx | 3 |
| 개발부품마스터 BO24 동유럽 Better 250424.xlsx | 2 |

## Agentic RAG 연결 방향

현재 DB는 Agentic RAG의 검색 대상으로 사용할 수 있습니다.

추천 흐름:

```text
1. dev_part_master.embedding_text를 임베딩 모델로 벡터화
2. 결과를 embedding_dense에 저장
3. 사용자 질문도 같은 임베딩 모델로 벡터화
4. pgvector로 의미 검색
5. SQL 조건 검색을 함께 수행
6. 두 결과를 합쳐서 LLM/Agent가 답변
7. 답변에는 파일명, 시트명, 원본 row 번호를 출처로 표시
```

즉 단순 벡터 검색만 쓰지 않고, 아래처럼 하이브리드 검색을 권장합니다.

```text
벡터 검색:
- "삭제된 부품"
- "컨트롤러 관련 변경"
- "nonpyro 때문에 빠진 부품"

SQL 검색:
- region = '호주'
- part_no_new = '...'
- classification = 'NEW'
- mold_dev = 'O'
```

1차로 만들 Agent 도구 후보:

```text
search_parts_semantic(query, filters)
search_parts_sql(filters)
hybrid_search_parts(query, filters)
get_part_detail(doc_id)
trace_source(doc_id)
summarize_by_file(file_id)
```

## 임베딩 (BGE-M3 하이브리드)

`embedding_dense` (1024-d) + `embedding_sparse` (250002-d lexical weights) 를 한 모델에서 함께 생성합니다.

근거: BGE-M3 (Chen et al., 2024) — dense / sparse(SPLADE-like) / multi-vector(ColBERT) 다중 표현을 self-knowledge distillation으로 학습. 부품번호·모델코드처럼 exact match가 중요한 토큰을 sparse가 잡아 dense의 약점을 보완.

### 설치 (GPU 권장)

```powershell
uv sync --extra embed
# torch는 CUDA 버전에 맞춰 별도 설치
uv pip install torch --index-url https://download.pytorch.org/whl/cu121
```

### 백필 실행

```powershell
# embedding_dense IS NULL인 행만 채움
uv run python -m parsers.backfill_embeddings

# 강제 재계산 / 배치 크기 조정
uv run python -m parsers.backfill_embeddings --all --batch 32
```

### 하이브리드 검색

dense kNN + sparse inner product → Reciprocal Rank Fusion (RRF, Cormack 2009) 으로 결합:

```powershell
uv run python -m parsers.retrieve "컨트롤러 LED 색상 변경" --changes-only
uv run python -m parsers.retrieve "AAA30802494" -k 5
uv run python -m parsers.retrieve "삭제된 부품" --region 호주
```

## 앞으로 개선할 일

우선순위:

```text
1. (완료) embedding_dense 채우는 스크립트 — BGE-M3 hybrid
2. (완료) 하이브리드 검색 함수 — dense + sparse RRF
3. Agentic RAG tool 레이어 작성
4. 도메인 적응: 합성 query 생성 → BGE-M3 contrastive fine-tune (Promptagator/GPL)
5. source_sheets / raw_sheet_rows staging 테이블 추가 검토
6. 시험, 등급심의, DQMS, 비교단가 등 별도 업무 테이블 설계
7. openpyxl이 못 여는 xlsx 파일 복구 로직 추가
```

## 한 줄 요약

ETL_PG는 여러 형태의 개발부품 엑셀 파일을 PostgreSQL에 표준화 저장하는 프로젝트입니다. 현재 23개 파일에서 2,860개 row를 적재했고, 동적 헤더 탐색 파서를 통해 기존에 0 row였던 파일들도 대부분 저장할 수 있게 개선되었습니다.
