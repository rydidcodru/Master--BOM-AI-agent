# ETL_PG 프로젝트 명세서

작성일: 2026-05-25

## 1. 이 프로젝트는 무엇인가요?

ETL_PG는 여러 엑셀 파일에 흩어져 있는 개발부품, 변경부품, 신규부품 정보를 PostgreSQL 데이터베이스에 모아 저장하는 프로젝트입니다.

쉽게 말하면, 사람이 엑셀을 하나씩 열어서 부품번호, 부품명, 변경 사유, 모델명 등을 확인하던 일을 프로그램이 대신 읽고 정리해서 DB에 넣는 구조입니다.

프로젝트 이름의 ETL은 아래 뜻입니다.

| 단계 | 의미 | 이 프로젝트에서 하는 일 |
|---|---|---|
| Extract | 추출 | `input_files` 폴더의 엑셀 파일을 읽음 |
| Transform | 변환 | 시트 구조를 분석하고 필요한 컬럼명을 표준 이름으로 바꿈 |
| Load | 적재 | PostgreSQL 테이블에 저장 |

## 2. 전체 흐름

현재 데이터 흐름은 아래와 같습니다.

```text
input_files 폴더
  -> run.py 실행
  -> 엑셀 파일 자동 탐색
  -> 각 파일의 시트 분석
  -> 지원 가능한 시트에서 부품 row 추출
  -> PostgreSQL 저장
  -> 적재 결과 요약 출력
```

실행 명령은 다음과 같습니다.

```powershell
uv run python run.py
```

PostgreSQL은 Docker 컨테이너로 실행합니다.

```powershell
docker compose up -d
```

현재 DB 접속 정보는 다음과 같습니다.

| 항목 | 값 |
|---|---|
| Host | localhost |
| Port | 15432 |
| Database | etl_dev |
| User | etl_user |
| Password | etl_pass |
| Container | etl_postgres |

## 3. 입력 데이터 위치

엑셀 파일은 아래 폴더에 둡니다.

```text
input_files/
```

현재 코드는 이 폴더 안의 `.xlsx`, `.xlsm` 파일을 자동으로 찾습니다. 예전처럼 파일명을 코드에 하나씩 하드코딩하지 않습니다.

임시 엑셀 파일인 `~$`로 시작하는 파일은 제외합니다.

## 4. 주요 코드 역할

| 파일 | 역할 |
|---|---|
| `run.py` | 전체 실행 스크립트. 스키마 적용, 데이터 초기화, 파일 반복 처리, 결과 요약을 담당 |
| `parsers/pipeline.py` | 파일 1개 단위의 처리 흐름 담당. 파일 해시 계산, 지역 추론, source_files 등록, 파서 호출 |
| `parsers/readers/dev_part_master.py` | 엑셀 시트를 실제로 읽고 부품 row로 변환하는 핵심 파서 |
| `parsers/db.py` | PostgreSQL 연결, INSERT, 로그 저장 담당 |
| `schema_postgres.sql` | DB 테이블, 인덱스, 양식 등록 정보 정의 |

## 5. DB 테이블 구조

### source_files

엑셀 파일 1개당 1개 row가 저장됩니다.

저장되는 주요 정보는 다음과 같습니다.

| 컬럼 | 설명 |
|---|---|
| file_id | 파일 고유 ID |
| file_name | 파일명 |
| file_hash | 파일 내용 기준 해시값. 중복 적재 방지에 사용 |
| file_size | 파일 크기 |
| region | 파일명에서 추론한 지역 |
| ingested_at | 적재 시각 |

### ingestion_log

시트별 처리 결과가 저장됩니다.

예를 들어 한 엑셀 파일 안에 `Master`, `변경부품 list`, `History` 시트가 있으면 각 시트의 처리 결과가 로그로 남습니다.

| 컬럼 | 설명 |
|---|---|
| file_id | 어떤 파일의 로그인지 |
| sheet_name | 시트명 |
| form_id | 어떤 파서 양식으로 처리했는지 |
| rows_total | 파서가 읽은 row 수 |
| rows_inserted | 실제 DB에 들어간 row 수 |
| status | completed, skipped 등 처리 상태 |
| error_message | 실패 또는 스킵 사유 |

### form_registry

지원하는 엑셀 양식의 정의가 저장됩니다.

현재 등록된 양식은 다음과 같습니다.

| form_id | 설명 |
|---|---|
| dev_part_master_v1 | 기존 이중 헤더 양식. row 8/9 기반 |
| dev_part_master_v2 | 기존 단일 헤더 양식. row 9 기반 |
| dev_part_master_dynamic | 새로 추가한 동적 헤더 탐색 양식 |

### dev_part_master

최종 부품 데이터가 저장되는 핵심 테이블입니다.

주요 컬럼은 다음과 같습니다.

| 컬럼 | 설명 |
|---|---|
| file_id | 원본 파일 ID |
| form_id | 어떤 양식으로 파싱됐는지 |
| sheet_name | 원본 시트명 |
| source_row | 원본 엑셀 row 번호 |
| region | 지역 |
| base_model | 기존 모델 |
| new_model | 신규 모델 |
| event | 개발 이벤트 |
| bom_level_raw | 원본 BOM Level 또는 Level 값 |
| bom_depth | BOM 깊이 숫자 |
| part_type | 부품 유형 |
| part_no_base | 기존 부품번호 |
| part_no_new | 신규 부품번호 또는 대표 부품번호 |
| part_name | 부품명 |
| qty_base | 기존 수량 |
| qty_new | 신규 수량 |
| change_point_raw | 변경점 또는 변경 내역 |
| change_reason_raw | 변경 사유 |
| supplier | 양산처 또는 공급사 |
| classification | New, Changing, Common 등 구분 |
| extra_fields | 정형 컬럼에 없는 추가 정보 |
| embedding_text | 검색/임베딩용 요약 문장 |

## 6. 파서가 데이터를 읽는 방식

처음에는 파서가 특정 위치만 읽었습니다.

```text
v1: row 8, row 9에 헤더가 있어야 함
v2: row 9에 헤더가 있어야 함
```

하지만 실제 엑셀 파일들은 헤더 위치와 컬럼명이 제각각이었습니다.

예를 들어 같은 의미의 컬럼도 파일마다 다르게 적혀 있었습니다.

| 의미 | 엑셀에서 발견된 표현 |
|---|---|
| 부품번호 | `Base P/No`, `New P/No`, `P/no.`, `Part No` |
| 부품명 | `Class Desc.`, `Class Desc.(Part Name)`, `Desc.`, `Part` |
| 레벨 | `BOM Level`, `Level`, `Lvl` |
| 변경 내역 | `변경점`, `Changing Point`, `변경 내역` |
| 변경 사유 | `변경사유`, `Changing Reason`, `변경 사유` |

그래서 현재는 다음 방식으로 개선했습니다.

```text
1. 기존 v1/v2 양식이면 기존 방식으로 빠르게 처리
2. 기존 양식이 아니면 상위 20행을 훑음
3. 헤더처럼 보이는 row를 찾음
4. 컬럼명을 표준 이름으로 매핑
5. 데이터 row를 읽어서 dev_part_master에 저장
```

이 동적 파서의 이름은 `dev_part_master_dynamic`입니다.

## 7. 저장 대상에서 제외하는 시트

모든 시트를 무조건 `dev_part_master`에 넣지는 않습니다.

아래 성격의 시트는 부품 마스터라기보다 보조 자료, 집계, 시험, 설명서에 가까워서 제외합니다.

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

이런 데이터까지 저장하려면 나중에 별도 테이블을 만드는 것이 좋습니다.

예를 들어:

```text
part_qualification_tests
first_article_inspection
part_grade_reviews
raw_sheet_rows
```

## 8. 현재 적재 결과

현재 기준으로 DB에 들어간 결과는 다음과 같습니다.

| 항목 | 값 |
|---|---:|
| source_files | 23 files |
| dev_part_master | 2,860 rows |
| ingestion_log | 120 logs |

`input_files`에는 24개 엑셀 파일이 있지만, 그중 1개는 openpyxl이 파일 내부 XML 문제로 열지 못했습니다. 따라서 실제 DB에 등록된 파일은 23개입니다.

## 9. 파일별 적재 row 수

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

이전에는 0 row였던 파일들도 현재는 대부분 데이터가 들어갑니다.

예를 들어:

| 파일명 | 이전 | 현재 |
|---|---:|---:|
| 개발변경부품Master Good 221226.xlsx | 0 | 238 |
| 개발변경부품Master Best 221226.xlsx | 0 | 238 |
| 개발변경부품Master Better 221226.xlsx | 0 | 238 |
| 개발변경부품리스트 전개모델 230110.xlsx | 0 | 241 |
| BO24 오븐 사우디향 부품 개발 완료 Master 240214.xlsx | 0 | 34 |
| BO24 북미 부품 개발 완료 Activity 240409.xlsx | 0 | 81 |

## 10. 지역별 적재 row 수

| 지역 | row 수 |
|---|---:|
| 미분류 | 1,868 |
| 호주 | 143 |
| UAE | 119 |
| 유럽 | 118 |
| 이집트 | 117 |
| 이라크 | 111 |
| 북유럽 | 106 |
| 북미 | 81 |
| 사우디 | 68 |
| 포르투갈/그리스 | 58 |
| 동유럽 | 26 |
| 싱가포르 | 21 |
| 모리셔스 | 18 |
| CIS | 6 |

지역은 현재 파일명에 포함된 단어로 추론합니다. 파일명에 지역명이 명확히 없으면 `미분류`로 남을 수 있습니다.

## 11. 파서 양식별 적재 row 수

| form_id | row 수 | 설명 |
|---|---:|---|
| dev_part_master_dynamic | 1,210 | 새로 추가한 동적 헤더 탐색 파서 |
| dev_part_master_v1 | 958 | 기존 이중 헤더 양식 |
| dev_part_master_v2 | 692 | 기존 단일 헤더 양식 |

동적 파서가 1,210 rows를 처리했습니다. 즉 기존 고정 row 방식만으로는 놓쳤던 데이터가 꽤 많았습니다.

## 12. 아직 처리하지 못한 파일

아래 파일은 엑셀 파일 내부 XML 문제 때문에 openpyxl이 열지 못했습니다.

```text
240430 BDO30 SKS Transitional 개발부품Master List Ver.20200814 Part DMS v2.xlsx
```

오류 요약:

```text
#N/A is not a valid print titles definition
```

이 문제는 파서의 컬럼 매핑 문제가 아니라, 엑셀 파일 안의 인쇄 제목 설정 또는 workbook XML이 openpyxl에서 읽기 어려운 상태라 발생합니다.

해결 방법 후보:

```text
1. Excel에서 파일을 열고 다른 이름으로 저장
2. 인쇄 제목/페이지 설정을 지운 뒤 저장
3. LibreOffice로 열어서 xlsx로 다시 저장
4. openpyxl 로딩 전 XML을 정리하는 별도 복구 단계 추가
```

## 13. 현재 구조의 장점

현재 구조의 장점은 다음과 같습니다.

```text
1. input_files에 파일을 넣으면 자동으로 탐색한다.
2. 파일명 하드코딩이 제거됐다.
3. 같은 의미의 컬럼명이 달라도 alias로 흡수한다.
4. 기존 v1/v2 양식과 새 동적 양식을 함께 지원한다.
5. 파일별, 시트별 처리 로그가 DB에 남는다.
6. 원본 파일 hash를 저장해서 중복 적재를 막을 수 있다.
```

## 14. 현재 한계

아직 한계도 있습니다.

```text
1. 모든 엑셀 시트를 저장하는 구조는 아니다.
2. 시험, 등급심의, DQMS, 비교단가 같은 시트는 제외한다.
3. 지역 추론은 파일명 기반이라 완벽하지 않다.
4. 파일 내부 XML이 깨진 경우는 아직 자동 복구하지 못한다.
5. dev_part_master는 부품/변경부품 중심 테이블이므로 모든 업무 데이터를 억지로 넣으면 안 된다.
```

## 15. 앞으로 확장하면 좋은 방향

추후에 더 많은 종류의 엑셀이 들어온다면 아래 구조를 추가하는 것이 좋습니다.

### source_sheets

파일 안의 각 시트를 따로 기록하는 테이블입니다.

```text
file_id
sheet_name
detected_form_id
header_row_index
data_start_row
status
rows_seen
rows_parsed
error_message
```

### raw_sheet_rows

원본 row를 JSON으로 보관하는 staging 테이블입니다.

이 테이블이 있으면 지금 구조화하지 못한 시트도 나중에 다시 분석할 수 있습니다.

```text
sheet_id
source_row
raw_values
normalized_values
mapped_values
status
error_message
```

### 업무별 별도 테이블

`dev_part_master`에 넣기 애매한 데이터는 별도 테이블로 분리하는 것이 좋습니다.

예:

```text
first_article_inspection
part_qualification_tests
part_grade_reviews
test_plans
cost_comparison
```

## 16. 자주 쓰는 확인 명령어

DB 접속:

```powershell
docker exec -it etl_postgres psql -U etl_user -d etl_dev
```

전체 건수 확인:

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

샘플 데이터 확인:

```sql
SELECT region, sheet_name, part_no_new, part_name, change_reason_raw
FROM dev_part_master
LIMIT 20;
```

## 17. 한 줄 요약

ETL_PG는 다양한 개발부품 엑셀 파일을 읽어서 PostgreSQL에 표준화 저장하는 프로젝트입니다. 현재는 23개 파일에서 2,860개 부품/변경부품 row를 저장했고, 기존 고정 파서로 놓치던 파일들도 동적 헤더 탐색 파서를 통해 처리할 수 있게 개선되었습니다.
