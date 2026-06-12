# BOM 변경 파이프라인 기술 명세서

> 작성일: 2026-06-13  
> 대상 시스템: `bom_pipeline` (FastAPI + React)

---

## 1. 시스템 개요

개발심의회 PPTX에서 BOM 변경점을 자동으로 추출하고, 과거 이력 데이터베이스를 참조하여 신규 BOM Excel 파일을 생성하는 AI 지원 파이프라인이다. 담당자가 AI 추천 결과를 화면에서 직접 검토·확정한 후 xlsx를 내려받는 방식으로 동작한다.

### 전체 흐름

```
PPTX + Base BOM Excel
        │
        ▼
 [1] parse_pptx       슬라이드 자동 감지 → 변경점 리스트 추출 (GPT-4o)
        │
        ▼
 [2] bom_match        변경점 ↔ Base BOM 매핑, 변경 범위 판단 (GPT-4o-mini)
        │
        ▼
 [3] history_search   과거 이력 DB 검색 → 유사 케이스 + 연동부품 제안 (GPT-4o)
        │
        ▼
 [4] human_review     담당자 UI 검토·확정 (Step2~Step3 화면)
        │
        ▼
 [5] write_bom        확정 변경점 → 신규 BOM xlsx 생성 (색상 마킹)
```

### 기술 스택

| 구분 | 기술 |
|------|------|
| 백엔드 | Python 3.11 · FastAPI · uvicorn |
| AI | LangChain · OpenAI GPT-4o / GPT-4o-mini · text-embedding-3-small |
| DB | SQLite (FTS5 + 임베딩 벡터) |
| 프론트엔드 | React 18 · TypeScript · Vite · AG Grid |
| Excel 처리 | openpyxl |

---

## 2. 단계별 상세 동작

---

### Step 1: parse_pptx — PPTX 파싱 및 변경점 추출

**파일**: `bom_pipeline/nodes/parse_pptx.py`  
**역할**: 개발심의회 PPTX에서 BOM 변경점 구조화 데이터를 추출한다.

#### 2-1. 슬라이드 분류

`loader.py`의 `load_pptx()`가 PPTX를 로드한 뒤 각 슬라이드를 두 타입으로 분류한다.

| 타입 | 판별 기준 | 예시 문서 |
|------|----------|----------|
| **타입 A** (상세 표) | `Base P/No`, `New P/No`, `Lev.` 컬럼이 표에 존재 | QuickZone 레인지 |
| **타입 B** (요약 표) | Module명 + 주요 변경점 텍스트만 있는 표 | Compact Oven |

`has_pno` 플래그로 구분하며, 동일 문서에 두 타입이 혼재할 수 있다.

#### 2-2. LLM 추출 (GPT-4o · Few-shot)

**타입 A**: 슬라이드별로 개별 호출.  
  - Base P/No, New P/No, BOM 레벨을 직접 표에서 추출.  
  - `change_type`: `base_part_no=""`이고 new가 품번이면 `"NEW"`, new가 `"삭제"`이면 `"삭제"`, 나머지는 `"Changing"`.

**타입 B**: 전체 슬라이드를 묶어 1회 호출.  
  - Module명 단위 변경점을 개별 항목으로 분리.  
  - base_part_no, new_part_no는 빈 문자열로 추출됨 → 이후 bom_match에서 채움.  
  - 번호 목록(`1. xxx 2. xxx`)은 항목별로 별도 change_point로 분리.

#### 2-3. 후처리

1. `new_part_no == "←"` 또는 부품명·변경점이 모두 비어 있으면 제외 (변경 없음 필터).
2. `(base_part_no, part, change_detail)` 기준 중복 제거.
3. 모든 항목에 기본값 채우기 (`history_candidates: []` 포함).

#### 출력 스키마 (ChangePoint)

```json
{
  "module":          "Cavity",
  "part":            "Cavity Assembly",
  "bom_level":       "",
  "base_part_no":    "",
  "new_part_no":     "",
  "change_detail":   "H 치수 137mm 감소",
  "change_reason":   "제품 치수 변경",
  "discipline":      "기구",
  "change_type":     "Changing",
  "concern":         "",
  "evidence_slide":  7,
  "source_pptx":     "CompactOven.pptx",
  "history_candidates": []
}
```

#### 알려진 한계

- **타입 B 문서**는 P/No가 없으므로 이 단계에서 부품을 특정할 수 없다. bom_match에서 LLM이 Base BOM을 보고 추론한다.
- PPTX 슬라이드가 자유 텍스트 형식이거나 표가 이미지로 삽입된 경우 파싱이 누락될 수 있다.

---

### Step 2: bom_match — 변경점 ↔ Base BOM 매핑

**파일**: `bom_pipeline/nodes/bom_match.py`  
**역할**: 각 change_point를 Base BOM의 실제 Part No에 연결하고, 변경 범위(`change_scope`)를 판단한다.

#### 2-1. Base BOM 로드

`openpyxl`로 xlsx 읽기. 주요 컬럼 인덱스 (0-based):

| 컬럼 | 인덱스 | 내용 |
|------|--------|------|
| B | 1 | Part No |
| C | 2 | Level (0 / .1 / ..2 / ...3) |
| J | 9 | Parent No |
| L | 11 | Description |
| AB | 27 | Maker |

`parent_no → [child BomRow]` children 맵을 구성해 서브트리 탐색에 사용.

#### 2-2. 타입 A: Part No 직접 확인

`base_part_no`가 이미 있으면 Base BOM에서 해당 행을 찾아 `expand_subtree()`로 하위 트리를 전개하고 `matched_subtree`에 저장.  
Base BOM에 없는 경우(신규 부품) → `match_confidence: "low"`, `matched_subtree: []`.

#### 2-3. 타입 B: 3단계 매핑 프로세스

**1단계 — 자동 매핑 (키워드)**  
module명의 단어들을 AND 조건으로 `.1` 레벨 부품의 description과 비교.  
실패 시 첫 단어만으로 재시도.

**2단계 — LLM 매핑 폴백 (GPT-4o-mini)**  
자동 매핑 실패 시 `.1` 레벨 전체 목록을 LLM에 전달.  
LLM이 의미 유사도로 후보를 선택한다.  
예시: `"Out Case Assembly"` → `"Cover Assembly,Rear"`.

**3단계 — LLM 부품 특정 및 변경 범위 판단 (GPT-4o-mini)**  
매핑된 `.1` 부품의 하위 트리 + `.1` 전체 목록을 LLM에 전달.  
LLM이 `change_detail`을 분석해 아래를 결정:

| 결과 필드 | 의미 |
|----------|------|
| `change_scope: "full"` | 모듈 전체 교체 — 하위 트리 전체 삭제 후 이력 기반 교체 |
| `change_scope: "partial"` | 특정 하위 부품만 변경 |
| `preserve_parts` | `full` 교체 시에도 BOM에 남겨야 할 공용 소재/원자재 품번 목록 |
| `deleted_parts` | 변경 내용에 "삭제"가 명시된 경우의 삭제 대상 부품 목록 |

**설계 결정: "삭제" 키워드 강제 partial 처리**  
`change_detail`에 "삭제" 키워드가 포함된 경우 반드시 `change_scope="partial"`로 처리한다. 이를 어기면 해당 모듈의 서브트리 전체(예: Controller Assembly 30행)가 삭제되는 오탐이 발생한 바 있다. LLM 프롬프트에 이 규칙이 명시되어 있다.

**preserve_parts 확장 로직**  
LLM이 지정한 소재 품번과 동일 소재 계열(Powder/Enamel/Paint, Resin/EPS/PP/ABS, Coil/Steel 등)을 BOM 전체에서 검색하여 자동 확장한다. `_expand_preserve_by_desc()` 참조.

#### 출력 (ChangePoint에 추가되는 필드)

```json
{
  "base_part_no":    "ACM76198404",
  "change_scope":    "partial",
  "match_confidence": "medium",
  "match_reason":    "트리 내 Panel,Control 특정",
  "matched_subtree": [...],
  "preserve_parts":  ["RAB35949601", "RAB33416801"],
  "deleted_parts": [
    {"part_no": "5900W1N004B", "description": "Fan,Convection", "lvl": "..2", "reason": "Conv Heater 삭제"}
  ]
}
```

#### 알려진 한계

- LLM이 `full` 범위를 과도하게 적용하는 경향이 있다 (보수적 설정이 필요).
- 자동 매핑 키워드가 Base BOM description과 표기가 다른 경우 LLM 폴백에 의존해야 한다.

---

### Step 3: history_search — 과거 이력 검색

**파일**: `bom_pipeline/nodes/history_search.py`  
**역할**: 각 change_point에 대해 과거 BOM 변경 이력 DB에서 유사 케이스를 검색하고 연동부품을 제안한다.

#### 3-1. DB 구조

SQLite 기반, 두 테이블:
- `bom_change_master`: 변경 케이스 메타 (base_model, new_model, source_file)
- `bom_change_detail`: 케이스별 변경 부품 상세 (part_name, base_part_no, new_part_no, changing_point 등)

`bom_change_detail`에 FTS5 인덱스와 `text-embedding-3-small` 임베딩 벡터가 저장되어 있다.

#### 3-2. 검색 경로 (항상 두 경로 병행)

**경로 1 — SQL 직접 조회**  
`base_part_no`가 있는 경우 `bom_change_detail.base_part_no = ?`로 동일 품번의 과거 변경 이력을 직접 조회.  
결과에 `search_path: "exact"` 태그.

**경로 2 — Hybrid 검색**  
`{part} {change_detail} {change_reason}` 텍스트로 임베딩 생성.  
FTS5 키워드 점수 + 코사인 유사도 점수를 합산하여 top-30 행 반환.  
임베딩 생성 실패 시 FTS5 단독 검색으로 폴백.  
중복 `detail_id` 제거 후 두 경로 결과 합산.

#### 3-3. 케이스 그룹핑

`master_id` 기준으로 조회 결과를 그룹핑 → 케이스 단위로 LLM에 전달.  
체결류(Nut, Screw, Washer, Bolt 등) 및 라벨류(Label,Barcode / Label,Rating)는 그룹 구성에서 제외.

#### 3-4. LLM 케이스 선정 + 연동부품 판단 (GPT-4o)

케이스 목록 전체를 LLM에 전달해 아래를 수행:

1. **top-5 케이스 선정** + 선정 이유 작성  
   `[직접이력]` 케이스(경로1 결과)를 우선 고려하도록 프롬프트에 명시.

2. **연동부품 후보** 도출  
   현재 change_points에 없는 부품 중 함께 변경될 가능성이 있는 부품 제안.  
   치수/모델 변경 케이스에서는 Box, Manual, Packing 등 포장·서비스 부품도 포함.

#### 3-5. `case_parts` — write_bom 변경 적용의 핵심 데이터

각 케이스의 `case_parts`에는 해당 케이스에서 변경된 모든 부품 목록이 들어있다.  
write_bom은 이 데이터를 보고 현재 Base BOM의 어떤 부품을 어떤 품번으로 교체할지 결정한다.

| base_part_no | new_part_no | 처리 |
|---|---|---|
| 있음 | 있음 | BOM 내 해당 행 인라인 변경 |
| 없음 | 있음 | 신규 행 추가 (P/No: `TBD(원래품번)`) |
| 있음 | 없음 | 해당 행 + 서브트리 삭제 |

**설계 결정: 모델 간 부품 오염 방지**  
`case_parts`의 `base_part_no`가 현재 Base BOM에 존재하고, 해당 모듈의 `matched_subtree` 안에 있는 경우에만 변경을 적용한다. 이 필터 없이는 다른 모델의 이력 케이스 부품이 현재 BOM에 잘못 삽입되는 문제가 발생한다.

**설계 결정: TBD 품번 처리**  
순수 신규 추가 부품(base 없음)은 이력의 P/No를 그대로 쓰면 타 모델 품번이 삽입된다. `TBD(원래품번)` 형식으로 표기해 담당자가 직접 확정하도록 한다.

#### 출력 (ChangePoint에 추가되는 필드)

```json
{
  "history_candidates": [
    {
      "master_id":     42,
      "base_model":    "LTIS7338XE",
      "new_model":     "LTIS7440XE",
      "source_file":   "QuickZone_2024.xlsx",
      "rank":          1,
      "select_reason": "[직접이력] 동일 Cavity Assembly 치수 변경 패턴",
      "case_parts": [
        {
          "part_name":      "Cavity Assembly,welding",
          "base_part_no":   "ABU73833314",
          "new_part_no":    "ABU74012201",
          "bom_level":      "..2",
          "changing_point": "H 치수 변경",
          "changing_reason": "제품 치수 변경"
        }
      ],
      "linked_parts": [
        {
          "part_name":       "Box,Packing",
          "base_part_no":    "ACF75693701",
          "new_part_no":     "TBD",
          "change_type":     "변경",
          "relevance_reason": "치수 변경 시 포장재 연동 교체 필요"
        }
      ]
    }
  ]
}
```

#### 알려진 한계

- DB에 해당 부품이나 유사 변경점 이력이 없으면 검색 결과가 0건이다.
- 이력이 풍부하지 않은 신규 플랫폼 부품(신제품 초도 BOM)은 경로1 결과가 항상 0건이다.

---

### Step 4: human_review — 담당자 검토 UI

**파일**: `bom_pipeline/frontend/src/pages/Step2Review.tsx`, `Step3BomEdit.tsx`  
**역할**: AI 추천 결과를 담당자가 행 단위로 확인·확정하거나 제외한다.

#### 4-1. Step2: 변경점 검토 (이력 케이스 선택)

- 각 change_point 카드를 아코디언 형태로 표시.
- LLM이 선정한 이력 케이스를 탭으로 표시; 체크박스로 참고 여부 결정.
- 연동부품 후보를 체크박스로 추가 여부 결정.
- `"BOM 편집으로 →"` 클릭 시 선택 내용을 `SelectionItem` 배열로 변환해 다음 단계로 전달.

#### 4-2. Step3: BOM 편집 (확정 화면)

`/bom/build` API를 호출해 전체 Base BOM을 변경 마킹된 상태로 받아 AG Grid에 표시.

**그리드 구성**
- 행 배경색: 변경=노란, 추가=초록, 삭제=빨강, 확정=연초록
- `Part No` 컬럼: 변경 행에서 `이전 P/No → 이후 P/No` 인라인 표시
- 핀 고정 컬럼: 상태 배지, 확정 아이콘, 레벨, Part No

**확정 워크플로우**
1. 변경·추가·삭제 행 클릭 → 우측 슬라이드 드로어 열림
2. 드로어에서 New P/No 입력 후 "확정" 버튼 클릭 → `confirmed: true`
3. 불필요한 항목은 "이 항목 제외" 클릭 → `excluded: true`
4. 상단 **미확정 카운터**가 0이 되면 xlsx 다운로드 버튼 활성화
5. "이전 변경 ◀ / 다음 변경 ▶" 버튼으로 그리드 스크롤 없이 탐색 가능

**설계 결정: 미확정 게이트**  
변경·추가·삭제 행이 하나라도 `confirmed=false`, `excluded=false`인 상태이면 xlsx 다운로드가 비활성화된다. 미검토 항목이 그대로 반영된 BOM이 생성되는 것을 방지하기 위함이다.

---

### Step 5: write_bom — 신규 BOM xlsx 생성

**파일**: `bom_pipeline/nodes/write_bom.py`  
**역할**: 확정된 변경점(selections)을 Base BOM에 적용하여 새 BOM xlsx를 생성한다.

`analyze_bom_changes()`와 `write_bom()` 두 함수로 구성된다.  
- `analyze_bom_changes()`: xlsx 생성 없이 변경 계획만 계산. `/bom/build` API의 프리뷰용.  
- `write_bom()`: 실제 xlsx 파일 생성. `/bom/export` API에서 사용.

#### 5-1. Base BOM 로드 및 인덱스 구성

- 전체 행을 `all_rows: list[list[Any]]`로 로드.
- `pno_to_idx: dict[str, int]` — Part No → 행 인덱스 빠른 조회용.
- `children: dict[str, list[str]]` — parent_no → child part_no 목록 (서브트리 탐색용).

#### 5-2. preserve_parts 확장

LLM이 지정한 보존 품번을 소재 계열 그룹(`_MATERIAL_GROUPS`)과 description 매칭으로 확장.  
`_expand_preserve_by_desc()` 실행 후 각 보존 품번의 서브트리까지 추가 확장.

#### 5-3. case_parts 기반 변경 계획 수집 (핵심)

선택된 이력 케이스의 `case_parts`를 순회하며 아래 세 맵을 채운다:

| 맵/리스트 | 내용 |
|----------|------|
| `case_change_map[base_pno]` | `(new_pno, note)` — 인라인 Part No 교체 |
| `case_add_list` | `(parent_pno, part_name, TBD(new_pno), note)` — 신규 행 추가 |
| `case_delete_pnos` | 서브트리 포함 삭제 대상 품번 집합 |

적용 필터 (모두 만족해야 반영):
1. `base_p`가 현재 Base BOM에 존재 (`base_p in pno_to_idx`)
2. `matched_subtree`가 있는 경우 그 안에 포함 (`base_p in subtree_pnos`)

#### 5-4. 삭제 대상 최종 수집

삭제는 세 경로로 수집된 후 합산된다:

| 경로 | 소스 |
|------|------|
| A | `deleted_parts` — bom_match LLM이 명시한 삭제 대상 |
| B | `change_scope="full"` — 모듈 서브트리 전체 삭제 |
| C | `case_parts`의 `new_part_no`가 비어있는 항목 |

`preserve_parts`에 포함된 품번은 어느 경로의 삭제 대상에서도 제외된다.  
`case_change_map`에서 변경(교체) 처리될 품번도 삭제 대상에서 제외된다.

#### 5-5. 행 처리 순서

각 Base BOM 행을 순서대로 순회하며 아래 우선순위로 처리:

```
1. pno in delete_pnos   → 삭제 처리 (빨간 마킹, 취소선)
2. pno in case_change_map → 인라인 변경 (Part No 교체, 노란 마킹)
3. pno in pno_to_sel    → partial scope 직접 변경 (new_part_no_confirmed 반영)
4. 해당 없음             → 원본 행 그대로 유지
```

처리 후 `case_add_list`와 `added_linked_parts` 행을 목록 끝에 추가.

#### 5-6. xlsx 생성

- 원본 Base BOM의 컬럼 구조를 그대로 유지.
- 변경/추가/삭제 행에 배경색 적용 (노란/초록/빨강).
- 삭제 행에 취소선 + 회색 폰트 적용.
- 주요 컬럼 너비 자동 조정 후 bytes로 반환.

#### 출력 요약 로그 예시

```
[write_bom] 완료: 전체 892행 (변경 12 / 추가 5 / 삭제 34)
```

---

## 3. API 엔드포인트

**파일**: `bom_pipeline/api/routers/`

### 파이프라인 노드 실행

| 메서드 | 경로 | 입력 | 출력 |
|--------|------|------|------|
| POST | `/api/pipeline/bom_match` | PPTX + BOM 파일 (multipart) 또는 `fixture` 키 | `{change_points, bom_rows}` |
| POST | `/api/pipeline/history_search` | `{change_points, fixture?}` | `{change_points}` (history_candidates 추가됨) |

### BOM 빌드 및 다운로드

| 메서드 | 경로 | 입력 | 출력 |
|--------|------|------|------|
| POST | `/api/bom/build` | `{change_points, selections, bom_rows, fixture?}` | `{rows, summary}` |
| POST | `/api/bom/export` | multipart: `req_json` + 선택적 `base_bom` 파일 | xlsx binary |

### `/bom/build` 응답 구조

```json
{
  "rows": [
    {
      "part_no":               "ABU74012201",
      "lvl":                   "..2",
      "parent_no":             "ABU74574508",
      "description":           "Cavity Assembly,welding",
      "qty":                   "1",
      "uom":                   "EA",
      "change_status":         "변경",
      "change_note":           "[이력#1] H 치수 변경",
      "original_part_no":      "ABU73833314",
      "row_id":                "ABU74012201_12",
      "confirmed":             false,
      "excluded":              false
    }
  ],
  "summary": {"변경": 12, "추가": 5, "삭제": 34, "미확정": 51}
}
```

### Fixture 경로

로컬 개발/시연용으로 미리 등록된 경로:

| fixture 키 | Base BOM 경로 |
|-----------|--------------|
| `"quickzone"` | `시연참고용/LTIS7338XE.ARSLLGA@CVZ.EKHQ 1.0.xlsx` |
| `"compact_oven"` | `input/WSED7613S.ASTQEUR@CVZ.EKHQ 1.0 (1).xlsx` |

---

## 4. 주요 설계 결정 및 배경

### 4-1. change_scope: full vs partial

| 구분 | full | partial |
|------|------|---------|
| 의미 | 모듈 서브트리 전체 교체 | 특정 하위 부품만 변경 |
| 트리거 | 치수 변경, Size 변경, 모델 변경 등 모듈 전반 영향 | 특정 부품 삭제, 단일 부품 사양 변경 |
| 처리 | 서브트리 전체 삭제 → 이력 케이스 기반 재구성 | 해당 행만 인라인 교체/삭제 |
| 위험 | 과도한 삭제 → preserve_parts로 보호 | 누락 위험 낮음 |

**실제 발생한 문제**: `"Conv Heater 삭제에 따른 인쇄 변경"` 변경점에서 LLM이 `scope=full`로 판단 → Controller Assembly 서브트리 30행(PCB, Harness, Motor 포함) 전체 삭제. 원인은 "인쇄 변경"이 아닌 "Conv Heater 삭제" 키워드에서 전체 교체로 오판.  
**대응**: LLM 프롬프트에 "삭제 키워드가 있으면 반드시 partial"을 명시 규칙으로 추가.

### 4-2. 담당자 확정 UI 방식 선택

**A안 (채택)**: 전체 Base BOM을 그리드에 표시, 변경 행 하이라이팅 → 행 단위 확정/제외  
**B안 (기각)**: 변경 내용만 요약 표시 → 자동 xlsx 생성

A안을 채택한 이유: 담당자가 변경 전후 맥락(상위/하위 부품 구조)을 직접 확인하면서 결정해야 실수를 방지할 수 있다. 자동 생성 방식은 AI 오판이 그대로 반영된 BOM이 나갈 위험이 있다.

### 4-3. TBD 품번 처리

이력 케이스의 신규 추가 부품(`base_part_no` 없음)을 그대로 BOM에 삽입하면 타 모델 전용 P/No가 섞인다. `TBD(원래품번)` 형식으로 저장해 담당자가 실제 P/No를 확정 입력하도록 설계.

### 4-4. 미확정 게이트

xlsx 다운로드 전에 모든 변경 행이 `confirmed=true` 또는 `excluded=true` 상태여야 한다. 검토되지 않은 AI 추천이 그대로 최종 BOM에 반영되는 것을 방지한다.

---

## 5. 검증 결과 (Compact Oven 기준)

| 항목 | 값 |
|------|-----|
| 기준 삭제 행 수 | 134행 |
| 정확 삭제 | 25행 |
| 오탐 삭제 | 11행 |
| TBD 추가 | 12행 |
| 삭제 Recall | 18.7% |
| 삭제 Precision | 69.4% |

**Recall이 낮은 원인**:  
Compact Oven PPTX는 개발 초기 문서로 P/No가 전혀 없는 타입 B 문서이며, 실제 변경되는 모듈(Hinge, Insulator, Box, Label, Manual, Panel Side 등) 중 20개가 PPTX에 언급되지 않았다. 즉, AI가 틀린 것이 아니라 입력 문서 자체에 정보가 없는 경우다.

---

## 6. 서버 실행 방법

**백엔드 (FastAPI)**
```bash
cd bom_pipeline
uvicorn api.main:app --reload
# http://localhost:8000
```

**프론트엔드 (Vite React)**
```bash
cd bom_pipeline/frontend
npm run dev
# http://localhost:5173
```