# LG_Data_pipeline

LG 가전 신규 모델 개발 시, 기존 양산 모델(Base) 대비 부품 변경을 추적하는
개발부품 마스터/BOM 문서를 표준화·DB화·검색 가능하게 만드는 데이터 파이프라인.

## 개요 (D-012 — 팀원 ETL_PG 스키마 통합)

이 코드베이스는 팀에서 운영 중인 ETL_PG와 같은 **dev_part_master 스키마**
(4 테이블: source_files / ingestion_log / form_registry / dev_part_master)에
적재한다. ETL_PG와 다음을 추가로 제공:

- **동적 헤더 anchor 탐색** (D-010): 실 95~97col 파일의 헤더가 row 8+9에 있고
  데이터가 row 13부터 시작하는 케이스 자동 처리.
- **NFC/NFKC 차등 정규화**: 식별자(part_no/model_code/buyer)에 NFKC, 자유
  텍스트(change_point/change_reason)에 NFC. macOS 자모분리 복원 포함.
- **calamine 폴백**: openpyxl이 거부하는 invalid XML 파일(BDO30 SKS 케이스)도
  python-calamine으로 자동 회복.
- **결정론적 narrative_text 자동 생성**: dpm 한 행당 한 줄 자연어 → bge-m3
  임베딩 대상. LLM 호출 0회.

아키텍처 결정 배경은 [`DECISIONS.md`](./DECISIONS.md) D-012 참조.

## MVP 기술 스택

| 계층 | 기술 |
|---|---|
| Storage | PostgreSQL 16 + pgvector + pg_trgm (단독) |
| LLM | Ollama (Qwen 2.5) + BGE-M3 임베딩 (1024 dim) |
| Schema/Rules | YAML config + Pydantic + axioms |
| CLI | typer (`python -m src.cli`) |
| 데이터 처리 | pandas + pyarrow + openpyxl + python-calamine + rapidfuzz |

## 데이터 모델 (D-012)

```
source_files       (file_id PK, file_hash UNIQUE, region)
  ↓ CASCADE
ingestion_log      (log_id PK, file_id FK, sheet_name, form_id, status)
form_registry      (form_id PK, description)   -- seed: 9 forms
  ↓
dev_part_master    (doc_id PK, file_id FK, form_id FK,
                    region, base_model, new_model, event,
                    bom_level_raw, bom_depth, part_type,
                    part_no_base, part_no_new, part_name,
                    qty_base, qty_new,
                    change_point_raw, change_reason_raw,
                    supplier, classification,
                    extra_fields JSONB,
                    embedding_text TEXT, embedding_dense vector(1024))
```

## 요구 사항

- Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker Compose

## 설치

```bash
uv sync --extra dev
uv sync --extra embed          # BGE-M3 sentence-transformers fallback (~2GB)
cp .env.example .env
```

## 로컬 구동

```bash
docker compose up -d                              # PostgreSQL + Ollama
docker exec lg_ollama ollama pull qwen2.5:32b
docker exec lg_ollama ollama pull bge-m3
```

## 디렉토리 구조

```
config/
  form_signatures.yaml          # 시트 단위 양식 분류 시그니처
  axioms.yaml                   # 부품번호 / 모델코드 / alias 사전
  column_dictionary.yaml        # 헤더 path → 표준 필드 (성장형)
  narrativize_templates.yaml    # 결정론적 narrative 템플릿
  normalization.yaml            # 필드별 normalize step 시퀀스
data/
  raw/        golden/           # 원본 / 사용자 손작업 (ground truth)
  interim/    processed/        # 인벤토리 / dry_run·committed·rolled_back
  quarantine/ reports/          # 격리 / markdown 리포트
src/
  preprocess/ classify, adapters/, normalize, narrativize, validate, pipeline
  db/         models, engine, load, rollback, schema_dev_part_master.sql, _mapping
  embed/      embedder (Ollama → ST fallback)
  ontology/   axioms (정규식 / alias)
  utils/      logging, paths, excel (openpyxl + calamine)
  cli.py
tests/
```

## 사용 가능한 명령 (D-012)

| 명령 | 단계 | 설명 |
|---|---|---|
| `uv run python -m src.cli inventory` | Step 0 | `data/raw/` 스캔 → file_inventory.parquet |
| `uv run python -m src.cli classify <path> [--all]` | Step 1 | 시트 단위 양식 분류 |
| `uv run python -m src.cli narrativize --part-no ...` | (debug) | 단일 행 narrative 미리보기 |
| `uv run python -m src.cli pipeline run [PATH] [--commit]` | Step 1-5 | 6-step 전체 run + 검증 + 리포트 |
| `uv run python -m src.cli pipeline commit --run-id ID` | Step 5 | dry_run → committed (게이트 통과 시) |
| `uv run python -m src.cli pipeline rollback --run-id ID` | Step 5 | filesystem 단위 committed → rolled_back |
| `uv run python -m src.cli quarantine list --run-id ID` | Step 5 | 격리 행 조회 |
| `uv run python -m src.cli db init` | Step 6 | dev_part_master 스키마 + form_registry seed |
| `uv run python -m src.cli db load --run-id ID [--embed]` | Step 6 | source_files + ingestion_log + dev_part_master 적재 |
| `uv run python -m src.cli db rollback --file-id ID` | Step 6 | file_id 단위 DB 적재 원복 (CASCADE) |
| `uv run python -m src.cli db status` | Step 6 | ingestion_log status 집계 |
| `uv run python -m src.cli db verify [--file-id ID]` | Step 6 | 테이블별 row 카운트 |
| `uv run python -m src.cli db reset --confirm` | (dev) | 전체 데이터 삭제 (개발용) |

## 파이프라인 단계 (6 step, D-012)

| Step | 산출물 | 비고 |
|---|---|---|
| 1 | 시트 단위 form_id 분류 | calamine 폴백 |
| 2 | ExtractedRow 스트림 (dpm fields + extra_fields) | D-010 동적 헤더 anchor |
| 3 | 정규화 (NFC/NFKC 차등) | normalize_dpm_row |
| 4 | narrative_text 결정론적 생성 | LLM 0회 |
| 5 | 7 검증 지표 + quarantine | 통과 시 commit |
| 6 | PostgreSQL 적재 | source_files dedup + dev_part_master insert + (선택) embedding_dense |

이전 v2.0의 Step 4 ER (resolve.py) + 별도 embed step은 D-012로 제거.

## 테스트

```bash
uv run pytest
```

37 단위 테스트 (모두 합성 fixture 기반, D-002). PostgreSQL이 없어도 SQLite로
dev_part_master 적재 / rollback 사이클 검증 가능 — engine.make_engine이
SQLite 연결 시 PRAGMA foreign_keys=ON + form_registry seed 자동 수행.

## D-012 ↔ ETL_PG 호환 노트

| 항목 | 이 코드베이스 | 팀원 ETL_PG |
|---|---|---|
| 스키마 | `src/db/schema_dev_part_master.sql` (적용) | (정본) |
| form_registry seed | 9 form_id (이 README 표) | 동일 |
| embedding_text | 결정론적 templates로 생성 | (명세 동기화 필요 — D-012 재검토 조건) |
| 적재 흐름 | dry_run → committed → load_run | (직접 적재 / 정의에 따름) |
| Rollback | file_id 단위 (CASCADE) | 동일 |
