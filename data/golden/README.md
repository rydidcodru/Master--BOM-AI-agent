# data/golden/ — 사용자 손작업 결과 (Ground Truth)

사용자가 **손으로** 전처리한 결과를 두는 폴더. 이 데이터가 자동 파이프라인의
정답 역할을 한다. 매 dry-run마다 자동 결과와 이 golden을 비교(`diff.py`)해
자동화가 어디서 틀렸는지 찾는다.

## 파일 형식

`data/golden/{원본파일명}.parquet` (또는 `.xlsx`).
원본 raw 파일 하나당 golden 파일 하나.

예: `data/raw/BO24_호주_241120.xlsx` → `data/golden/BO24_호주_241120.parquet`

## 컬럼 명세

96col 정답 schema(`src/ontology/schema.py`의 `ChangeEventRow`)와 **동일한 컬럼**을
사용한다. 최소한 다음 고정 피처 10개는 포함해야 한다:

| 컬럼 | 타입 | 설명 |
|---|---|---|
| base_part_no | str | 변경 전 부품번호 |
| new_part_no | str/null | 변경 후 부품번호 (New 타입은 null 허용) |
| part_name | str | 부품명 |
| bom_level | int | BOM 트리 깊이 |
| part_type | str | 사출/Assy/전장/단품 |
| change_type | str | New / Change / Carry-over |
| change_point | str | 변경점 (자유텍스트) |
| change_reason | str/null | 변경사유 (자유텍스트) |
| qty | float/null | 수량 |
| model_code | str | 모델코드 |

### 메타 컬럼 (선택)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| source_row | int | 원본 엑셀에서의 행 번호 |
| confidence | float | 손작업 확신도 (1.0=확실, 0.5=애매) |

## 운영 방식

1. 초기: 사용자가 raw 파일을 100% 손으로 작업해 golden에 저장
2. 자동화 정확도가 오르면, 사용자는 `needs_review` 행만 검토
3. golden-diff 일치율 95%+ 도달 시 자동화 신뢰 가능

golden 파일이 없는 raw 파일은 diff를 건너뛰고 검증 15지표만으로 판정한다.
