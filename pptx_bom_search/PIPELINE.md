# BOM 심의회 분석 파이프라인

LG전자 오븐/주방가전 개발 심의회 PPTX + Base BOM에서 변경점을 추출하고,
과거 변경이력을 참조해 변경부품리스트.xlsx를 자동 생성하는 AI 에이전트.

---

## 전체 흐름

```
[입력]
  심의회 PPTX (1개 이상) + Base BOM xlsx (선택)
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 1. PPTX 파싱           parse_pptx_node            │
  │  GPT-4o로 슬라이드 텍스트에서 변경점 추출               │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[]
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 2. 파싱 결과 확인/편집  [UI]                       │
  │  사용자가 change_points 수정·삭제 후 다음 단계 진행      │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[] (사용자 확정)
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 3. Base BOM 매칭       bom_match_node             │
  │  Level별 분할 LLM 매칭 (L1→L2→L3→L4)                   │
  │  Tentative/Confirm 전략으로 상위→하위 정밀화             │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[] (base_part_no, matched_subtree 포함)
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 4. 과거 이력 조회      history_search_node         │
  │  base_part_no로 Neo4j CSV에서 유사 케이스 검색           │
  │  LLM: Top 5 케이스 선정 + 연동 부품 후보 판단            │
  │  사용자가 연동 부품 체크박스로 선택                      │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[] (history_candidates 포함) + selected_linked[]
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 5. Excel 생성          export_excel               │
  │  변경부품리스트.xlsx 생성 (참고용_Extra 포맷 기준)        │
  └──────────────────┬──────────────────────────────────────┘
                     │
                     ▼
  [출력] 변경부품리스트.xlsx 다운로드
```

---

## 디렉토리 구조

```
pptx_bom_search/
├── app.py                  # Streamlit 5단계 UI
├── runner.py               # 단계별 실행 함수 (step1~step5)
├── state.py                # BOMSearchState, ChangePoint, HistoryCandidate, LinkedPart TypedDict
├── nodes/
│   ├── parse_pptx.py       # STEP 1: PPTX → change_points
│   ├── bom_match.py        # STEP 3: Base BOM LLM 매칭 (level-by-level)
│   ├── history_search.py   # STEP 4: 과거 이력 조회 + 연동 부품 추출
│   └── export_excel.py     # STEP 5: 변경부품리스트.xlsx 생성
├── requirements.txt
└── .env
```

---

## 핵심 데이터 구조

### ChangePoint

PPTX에서 추출된 부품 변경점 하나. STEP이 진행되면서 필드가 채워짐.

| 필드 | 채워지는 시점 | 설명 |
|------|-------------|------|
| `module` | STEP 1 | 소속 모듈 (Cavity / Door / Controller 등) |
| `part` | STEP 1 | PPTX에 기재된 부품명 |
| `change_detail` | STEP 1 | 변경 내역 |
| `change_reason` | STEP 1 | 변경 사유 |
| `discipline` | STEP 1 | 기구 / 제어 / ThinQ |
| `type` | STEP 1 | Changing / NEW / 삭제 |
| `concern` | STEP 1 | 걱정점 |
| `source_pptx` | STEP 1 | 출처 파일명 |
| `evidence_slide` | STEP 1 | 근거 슬라이드 번호 |
| `base_part_no` | STEP 3 | Base BOM 매칭 품번 |
| `bom_level` | STEP 3 | BOM 계층 (.1 / ..2 / ...3) |
| `part_type` | STEP 3 | 부품 유형 (사출 / 판금 / 전장 등) |
| `qty` | STEP 3 | 수량 |
| `supplier` | STEP 3 | 양산처 |
| `supply_type` | STEP 3 | Assembly Pull / Phantom / Supplier |
| `bom_matched` | STEP 3 | Base BOM 매칭 성공 여부 |
| `match_confidence` | STEP 3 | high / medium / low |
| `match_reason` | STEP 3 | LLM 매칭 근거 |
| `matched_subtree` | STEP 3 | 매칭 부품 + 하위 트리 전체 (BomRow 목록) |
| `history_candidates` | STEP 4 | 유사 과거 이력 Top 5 (HistoryCandidate 목록) |

### HistoryCandidate

과거 유사 이력 케이스 1건.

| 필드 | 설명 |
|------|------|
| `case_id` | Neo4j caseId |
| `model_name` | 신모델명 |
| `base_model` | 기준모델명 |
| `rank` | 1~5 (1이 가장 유사) |
| `select_reason` | LLM 선정 이유 |
| `linked_parts` | 연동 부품 후보 목록 (LinkedPart) |

### LinkedPart

과거 이력에서 함께 변경된 연동 부품 후보.

| 필드 | 설명 |
|------|------|
| `part_no` | 품번 |
| `part_name` | 부품명 |
| `change_type` | 추가 / 변경 / 삭제 |
| `relevance_reason` | LLM 관련성 근거 |

---

## 노드별 상세

### parse_pptx_node (`nodes/parse_pptx.py`)

- **입력**: `pptx_paths[]`
- **LLM**: GPT-4o, temperature=0
- **동작**: 슬라이드 텍스트 전체를 컨텍스트로 넘겨 변경점 JSON 배열 추출
- **출력**: `change_points[]`

### bom_match_node (`nodes/bom_match.py`)

- **입력**: `change_points[]`, `base_bom_path`
- **LLM**: GPT-4o, temperature=0
- **핵심 전략 — Level-by-Level 매칭**:
  - Base BOM 전체(857행 ≈ 30,608 tokens)를 한 번에 넘기면 TPM 한도(30,000) 초과
  - L1(58행) → L2(108행) → L3(223행) → L4(35행) 순서로 레벨별 분할 처리
- **Tentative/Confirm 전략**:
  - L1 매칭 결과는 `tentative`로 보류
  - L2~L4에서 L1 매칭 결과의 하위 부품이 발견되면 더 정밀한 것으로 교체(confirm)
  - 더 깊은 레벨에서 매칭 안 되면 L1 tentative를 그대로 확정
  - 이유: L1만 보면 "Door Assembly" 같은 상위 어셈블리에 잘못 매칭되고, 하위로 내려가면 "Frame,Decor"처럼 실제 부품에 정밀 매칭됨
- **출력**: `base_part_no`, `matched_subtree`, `bom_matched` 등 채워진 `change_points[]`

### history_search_node (`nodes/history_search.py`)

- **입력**: `change_points[]` (bom_matched=True인 것만 처리)
- **데이터 소스**: `ETL_neo4j/outputs/neo4j_csv/` CSV 파일들 (review_cases, change_records, bom_lines)
- **동작**:
  1. `base_part_no`로 change_records에서 관련 케이스 검색
  2. 케이스별 변경 부품 목록 구성 + 규칙 기반 1차 필터 (체결류/라벨류 제외, subtree 중복 제외)
  3. LLM 1회 호출: Top 5 케이스 선정 + 각 케이스의 연동 부품 후보 판단
- **규칙 기반 제외 키워드**: nut, screw, washer, bolt, rivet, label, barcode, rating, carton, warranty, card, manual, bag, tape, clip, pin, ring, seal, gasket
- **출력**: `history_candidates[]` 채워진 `change_points[]`

### export_excel (`nodes/export_excel.py`)

- **입력**: `change_points[]`, `selected_linked[]`, 메타정보(base_model, new_model, event)
- **포맷 기준**: `legacy/data/참고용_Extra_결과.xlsx`
  - Row 1: 공통/Common
  - Row 2~4: Base Model, New Model, Event
  - Row 8~9: 컬럼 헤더 + 서브헤더
  - Row 10~: 데이터
- **중복 제거**: base_pno 또는 part_name 기준으로 연동 부품 중복 제거
- **분류별 행 색상**: Change(흰색), New(연초록), Delete(연주황)
- **출력**: xlsx bytes (st.download_button에 직접 사용)

---

## UI 단계별 흐름 (app.py)

| 단계 | 화면 | 주요 기능 |
|------|------|----------|
| STEP 1 | PPTX + Base BOM 업로드 | 파일 업로드 → GPT-4o 파싱 → st.status 진행 표시 |
| STEP 2 | 파싱 결과 확인/편집 | st.data_editor로 change_points 수정·삭제. Base BOM 없어도 다음 단계 진행 가능 |
| STEP 3 | Base BOM 매칭 결과 | 매칭/신규/실패 카드 UI. Base P/No 직접 수정 가능. 하위 부품 트리 expander |
| STEP 4 | 과거 이력 후보 조회 | 변경점별 Top 5 이력 카드 (별점, 선정 이유, 연동 부품). 하단에 연동 부품 선택 체크박스 테이블 |
| STEP 5 | 변경부품리스트 생성 | 메타정보 입력 (Base/New Model, Event). 미리보기 테이블. xlsx 다운로드 버튼 |

---

## ETL 데이터 현황 및 문제점

### 현재 운영 CSV 현황 (2026-05-28 기준)

| 항목 | 수치 |
|------|------|
| 총 케이스 | 65건 |
| 변경 레코드 | 1,163건 |
| 레코드 있는 케이스 | 21건 |
| ETL 입력 파일 | 26개 (ETL_neo4j/uploads/) |

### 문제 1: 모델명 파싱 실패 (65건 중 ~45건)

**원인**: 파일 헤더의 `Base Model/Grade` 셀 옆에 실제 모델명 대신 양식 레이블 텍스트("모델명(등급)")가 들어있음.

```
파일 헤더 구조:  [ Base Model/Grade ] [ 모델명(등급) ]  ← 값이 레이블
실제여야 할 구조: [ Base Model/Grade ] [ WSED7613S / B ]
```

**영향**:
- STEP 4에서 "같은 모델 케이스 중복 제외" 규칙이 무력화됨 (모두 동일 모델명으로 인식)
- 이력 검색 결과의 신뢰도 저하

**해결 방향**: ETL `parse_metadata()`에서 레이블 텍스트 감지 후 스킵, 실제 모델명 패턴(영문+숫자) 매칭으로 개선 필요

### 문제 2: Classification 컬럼 파싱 실패 → changes=0

**원인**: 파일별로 Classification 컬럼명이 다양함 ("신규/변경 부품 대상", "분류", "구분" 등). ETL `detect_columns()`가 인식 못하는 경우 change_records에 데이터가 쌓이지 않음.

**영향**:
- 65건 케이스 중 실제로 변경 레코드가 있는 케이스는 21건뿐
- 나머지 44건은 BOM 행 데이터는 있지만 변경 이력이 없음 → STEP 4 검색 대상에서 제외됨

**해결 방향**: `detect_columns()`에 한국어 컬럼명 패턴 추가, Classification 자동 추론 로직 보완

### 문제 3: 실질적 고유 케이스 수 과장

**원인**: Best/Better/Good 등급별로 별도 파일·시트로 관리되는 구조. 동일 모델의 동일 변경점이 등급 수만큼 중복 집계됨.

```
개발변경부품Master Best 221226.xlsx  → 205건
개발변경부품Master Better 221226.xlsx → 205건  ← 실질적으로 동일 내용
개발변경부품Master Good 221226.xlsx  → 205건
```

**영향**: 표면상 1,163건이지만 실질적으로 다른 케이스는 3~4건 수준

**해결 방향**: case_id 생성 시 base_model + new_model + event 기준으로 중복 탐지 및 병합

### 문제 4: PPTX 부품명 ↔ Base BOM 부품명 불일치

**원인**: PPTX는 설계자가 자유롭게 작성하는 문서, BOM은 DMS 등록 정식명칭을 사용. 같은 부품을 다르게 표기.

**사례**:
- "Conv. Fan Cover" ↔ "Cover,Heater" (명칭 완전 다름)
- "Water Tank" ↔ BOM에 없음 또는 다른 표기
- "Out Case Assembly" ↔ BOM에 없음
- "Panel,Control" → BOM: "Decor,Control Panel" (순서/표기 다름)

**영향**: STEP 3 매칭 실패 → base_part_no 없음 → STEP 4 조회 불가 → STEP 5 리스트 불완전

**해결 방향**: PPTX 별칭 ↔ BOM 정식명 매핑 테이블 구축. 자주 나오는 패턴은 규칙으로 고정, 나머지는 LLM 매칭.

---

## 개선 우선순위

| 순위 | 항목 | 예상 효과 | 작업 난이도 |
|------|------|----------|-----------|
| 1 | ETL `parse_metadata()` 개선 (모델명 파싱 실패 해결) | 이력 케이스 품질 향상, 중복 제외 로직 정상화 | 낮음 |
| 2 | ETL `detect_columns()` 개선 (Classification 컬럼 인식) | changes=0 케이스 해소, 유효 레코드 수 증가 | 중간 |
| 3 | PPTX 별칭 ↔ BOM 정식명 매핑 테이블 구축 | STEP 3 매칭 실패 감소 (현재 미매칭 부품들 해결) | 중간 (초기 구축은 수작업) |
| 4 | New P/No 처리 (Changing 타입) | 변경부품리스트 완성도 향상 | 높음 |

### 1순위 상세: ETL parse_metadata 개선

```python
# 현재 문제: 레이블 텍스트를 값으로 읽음
# "모델명(등급)", "개발모델(Best)", "※Base 단가 캡쳐본" 등

# 개선 방향:
# 1. 레이블 패턴 블랙리스트 추가
METADATA_LABEL_BLACKLIST = {"모델명(등급)", "개발모델(best)", "개발모델(good)", "※base 단가 캡쳐본"}

# 2. 실제 모델번호 패턴 화이트리스트 (영문+숫자 조합)
import re
MODEL_NO_PATTERN = re.compile(r'^[A-Z]{2,6}\d{4,}', re.IGNORECASE)
```

### 3순위 상세: 매핑 테이블 구조안

```json
// alias_map.json
{
  "Conv. Fan Cover": "Cover,Heater",
  "Out Case": "Case,Outer",
  "Water Tank": "Tank,Water Assembly",
  "Probe": "Probe,Meat",
  "Choke": "Coil,Choke"
}
```

bom_match_node에서 LLM 호출 전 전처리로 치환. 매핑 테이블은 사용자가 STEP 3 UI에서 직접 추가 가능하도록 확장 가능.

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-05-30 | Streamlit 초기 UI 구현 |
| 2026-05-31 | parse_pptx, bom_match 노드 구현 |
| 2026-06-01 | Level-by-level BOM 매칭 전략 도입 (TPM 한도 대응) |
| 2026-06-01 | Tentative/Confirm 전략 도입 (상위 어셈블리 오매칭 방지) |
| 2026-06-07 | history_search_node 구현 (STEP 4) |
| 2026-06-07 | STEP 4 UI: 이력 카드 + 연동 부품 체크박스 선택 |
| 2026-06-08 | export_excel 구현 (STEP 5) |
| 2026-06-08 | 변경부품리스트.xlsx 생성 및 다운로드 (참고용_Extra 포맷 기준) |
