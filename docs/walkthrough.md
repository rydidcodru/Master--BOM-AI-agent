# 실데이터 5개 파일로 보는 전처리 파이프라인 단계별 결과

> 이 문서는 사용자가 업로드한 LG 개발부품 마스터/BOM 엑셀 5개를 전처리 파이프라인에
> 그대로 통과시킨 결과를 단계별 시각화로 설명합니다.
>
> **재현**: `uv sync --extra dev --extra viz && uv run python scripts/build_walkthrough.py`

## 입력 데이터 (data/raw/)

| # | 파일 | 크기 | 시트 | 분류 |
|---|---|---|---|---|
| 1 | `240430_BDO30_SKS_Transitional_MasterList.xlsx` | 1.0 MB | 1시트, 693행 × 20열 | 20col (Transitional) |
| 2 | `BO24_B700_nonpyro_241120.xlsx` | 0.9 MB | 4시트 (96열 멀티헤더 데이터 2 + 메타 2) | 96col |
| 3 | `BO24_Better_250424.xlsx` | 1.6 MB | 2시트 (`Master`, `Better`) | 56col |
| 4 | `LSIU6339XE.ARSLLGACVZ.EKHQ_1.0.xlsx` | 4.1 MB | 1시트 (`ag-grid`, 4081행) | bom_tree |
| 5 | `WDEK9429S.ATTLSNACVZ.EKHQ_1.0.xlsx` | 3.4 MB | 1시트 (`ag-grid`, 3430행) | bom_tree |

---

## Step 0 — 파일 인벤토리

![Step 0 inventory](images/step0_inventory.png)

각 점이 하나의 시트입니다 (가로 = 최대 컬럼 수, 세로 = 최대 행 수, 로그 스케일).

**실데이터 발견 사항:**
- `240430-Transitional` 파일은 **openpyxl이 파싱 못 함** (`#N/A`가 print-titles
  defined name에 있어 `ValueError`). `src/utils/excel.py:read_workbook`이
  **python-calamine으로 자동 폴백**해 모든 단계가 통과합니다.
- openpyxl과 calamine은 선행 빈 행 처리가 달라서, `_strip_leading_empty`로 두
  backend가 동일한 인덱싱을 갖도록 통일했습니다 (`header_row` 설정이 backend와
  무관하게 동작).
- 두 ag-grid 파일은 4000행 안팎 — BOM 트리 전체 덤프이기 때문 (Step 5
  `bom_edges` 적재 영역).

---

## Step 1 — 양식 자동 분류

![Step 1 classification](images/step1_classification.png)

각 셀은 `config/form_signatures.yaml`의 가중치 시그널 합계 (0~1). 빨간 사각형이
임계(0.7) 통과 분류입니다.

**5개 파일 모두 정확 분류, 실패 0건:**

| 파일 | 분류 | 신뢰도 |
|---|---|---|
| 240430-Transitional | `20col` | 1.0 |
| BO24-B700-nonpyro | `96col` | 0.7 (col_count + `aaaa` marker; stage_row는 단일 셀에 합쳐져 미매치) |
| BO24-Better | `56col` | 1.0 |
| LSIU6339XE, WDEK9429S | `bom_tree` | 1.0 |

**실데이터 발견 사항:**
- v2 계획은 4개 양식만 가정했으나 ag-grid 형식은 **5번째 문서 타입**(PDM/PLM
  BOM 트리). `bom_tree` 시그너처 신규 추가 — sheet 이름 `ag-grid` + 헤더 키워드
  `Lvl/Check Out/I.S/ModelECO`.
- 96col stage marker (`CP PP DV PV PQ`)는 실데이터에서 **1개 셀에 공백으로
  합쳐진 문자열**로 들어있어 `stage_row` 시그널 미매치. col_count + `aaaa`
  marker만으로 임계 정확 통과.

---

## Step 2 — 정답 스키마

`src/ontology/schema.py`의 `ChangeEventRow`가 답입니다. 10개 고정 피처
(`base_part_no`, `new_part_no`, `part_name`, `bom_level`, `part_type`,
`change_type`, `change_point`, `change_reason`, `qty`, `model_code`) + 5개 그룹
서브모델 + provenance.

axiom (`config/axioms.yaml`)이 alias 정규화와 패턴 검증을 담당합니다:
- `change_type` alias: `K → Carry-over`, `신규 → New` 등
- `model_code` 패턴: `^[A-Z][A-Z0-9]{3,14}(\.[A-Z0-9.]+)?$` — 실데이터의 영문/
  숫자 혼재 접두부 (예: `WS7D7610B`) 와 정형 suffix (예: `LSIS6338F.ARSLSTC`)
  모두 허용
- 첫 통과 후 실데이터로 검증해 패턴을 완화했습니다 (D-009 기록).

---

## Step 3 — 매핑 + 정규화 (필드 충족률)

![Step 3 field coverage](images/step3_field_coverage.png)

`map.py` + `normalize.py`가 양식별 컬럼을 96col 필드로 옮긴 뒤 비결측 비율을
표시합니다. 초록 = 100% 채워짐, 빨강 = 모두 비어있음.

**핵심 보강 — Meta header enrichment**: 96col / 56col / 20col 마스터의
`model_code`는 R2 메타 헤더(`Base model | ... | WSED7667M.ABMQEUR`)에만 한 번
등장합니다. 데이터 행에는 model_code가 없어요. 새로 추가한
`extract_sheet_meta()`가 그 라벨/값 쌍을 추출해 **모든 데이터 행에
`_meta_model_code` 컬럼으로 broadcast**합니다. 매핑 룰의 source 우선순위 마지막에
`_meta_model_code`를 두어 fallback으로 활용.

**실데이터 결과:**
- `240430-Transitional` (20col): `base_part_no`/`part_name`/`model_code` 모두
  73%+ 채워짐. `change_type`/`change_point`는 실데이터가 비워 0%.
- `BO24-B700-nonpyro` (96col): `model_code`가 메타 enrichment 덕에 73%까지 회복.
  `part_type`은 실데이터가 안 채워서 0%.
- `BO24-Better` (56col): 두 데이터 행 모두 깨끗히 매핑됨 (100%).

---

## Step 4 — 검증 + Quarantine

### 15지표 검증

![Step 4 validation](images/step4_validation.png)

7개 acceptance threshold 중 각 셀에 값과 OK/X. 초록 = 임계 통과.

**실데이터 결과:**
- `column_match`: 모든 파일 1.00 ✓
- `value_format_match`: 0.69~1.00 — `model_code` axiom 완화 후 대폭 개선
- `null_rate_required`: 일부 파일 임계 미달 — 실데이터의 sub-header 행 영향
- `axiom_violation_rate`: 일부 파일 5~25% — sub-header가 데이터로 잘못 들어가는
  부분

→ **NOT ACCEPTABLE** (정상). 게이트가 commit을 막아 실데이터 1차 통과는
"무엇이 어디서 막히는지" 가시화에 집중.

### Quarantine 사유

![Step 3/4 quarantine reasons](images/step3_quarantine.png)

쌓은 막대의 각 색은 어떤 필드/단계에서 실패했는지입니다.

**처리 통과율 (이번 실행 결과):**

| 파일 | 입력 | 깨끗히 통과 | 격리 | 통과율 |
|---|---|---|---|---|
| 240430-Transitional | 684 | **500** | 184 | 73% |
| BO24-B700-nonpyro | 27 | **11** | 16 | 41% |
| BO24-Better | 2 | **2** | 0 | 100% |
| 전체 | 713 | **513** | 200 | **72%** |

**남아 있는 quarantine 사유의 본질:**
- 240430의 184개는 대부분 **빈 행 + 요약/합계 행** — 정상 격리
- B700의 16개는 **R10~R12 sub-header 행** (`DMS기준행`, `Part DMS`, `Q-Map`
  등) + 일부 part_type 누락 데이터
- 사용자 액션: `data/golden/`에 손작업 결과를 같은 파일명으로 두면 매 dry-run
  마다 자동 diff 가능. golden 없을 땐 validation 15지표만으로 판정.

수정 후 재처리:

```bash
uv run python -m src.cli quarantine reprocess --run-id <ID>
```

---

## Step 5 — DB 적재 (보류)

이 walkthrough에선 Postgres를 띄우지 않아 시각화는 없습니다. 동선:

```bash
docker compose up -d postgres
uv run python -m src.cli db init                       # 테이블 + pgvector / pg_trgm
uv run python -m src.cli pipeline commit --run-id <ID> # 게이트 통과 시
uv run python -m src.cli db load --run-id <ID>         # processed → PG
uv run python -m src.cli db verify --run-id <ID>
```

게이트가 NOT ACCEPTABLE이면 `db commit`이 거부됩니다. `bom_tree` 파일 2개는
별도 로더 (`bom_edges` 테이블 대상) 필요 — v2 Step 5 placeholder 영역. 현재는
`status="bom_tree_deferred"` 로 통과시킵니다.

---

## 요약 — 실데이터가 드러낸 8가지 + 조치

| # | 발견 | 조치 |
|---|---|---|
| 1 | openpyxl이 `#N/A` 정의 깨진 워크북 못 읽음 | `src/utils/excel.py` calamine 폴백 |
| 2 | openpyxl/calamine 선행 빈 행 처리 불일치 | `_strip_leading_empty` 통일 |
| 3 | 96col stage marker가 단일 셀로 합쳐짐 | col_count + aaaa marker로 임계 정확 통과 |
| 4 | 96col/20col 모두 R9 헤더 + R10~R12 sub-header + R13 데이터 | `header_row=9` + sub-header는 quarantine 자연 처리 |
| 5 | 56col 3행 멀티헤더 (R7이 통합 헤더) | `header_row=7` |
| 6 | `model_code`가 데이터 행이 아닌 메타 헤더 R2에 있음 | `extract_sheet_meta()` 추가 → `_meta_model_code` broadcast |
| 7 | 헤더에 `\n` 포함 (`BOM\nLevel`) | `_normalize_header`로 공백 collapse |
| 8 | 실데이터 model_code 형식 다양 (`WS7D7610B` 등 영문/숫자 혼재) | axiom 패턴 완화 |
| 9 | `Quanty` 등 오탈자 컬럼 | mapping 룰 alias 추가 |
| 10 | ag-grid는 5번째 양식 (BOM 트리 덤프) | `bom_tree` 신규 + deferred 상태 |

이 walkthrough를 다시 만들 때는
`uv run python scripts/build_walkthrough.py`로 모든 PNG와 리포트가
한 번에 갱신됩니다.
