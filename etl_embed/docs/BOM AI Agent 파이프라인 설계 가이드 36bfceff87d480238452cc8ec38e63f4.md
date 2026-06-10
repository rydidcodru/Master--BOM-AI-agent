# BOM AI Agent 파이프라인 설계 가이드

작성일: 2026-05-26 원본: `agent pipeline 설계.pdf`

---

## **0. 한 줄 요약**

> **"기존 모델의 부품표(Base BOM)와 신규 모델의 변경점 문서(심의회 PPT)를 입력하면, AI Agent가 과거 비슷한 개발 이력을 찾아내고 그 패턴을 적용해 신규 모델의 BOM 초안을 자동으로 만들어주는 시스템."**
> 

사람이 일일이 과거 모델 BOM을 뒤져보며 "이전에 카메라 추가했을 때 어떤 부품들이 같이 바뀌었지?"를 추적하던 작업을, Agent 6개가 협업해서 자동으로 해주는 구조입니다.

---

## **1. 무엇을 만드는 프로젝트인가요?**

### **입력 (사람이 주는 것)**

1. **Base BOM Excel** — "이번 신규 모델은 어떤 모델을 기반으로 만들 거다"의 그 **기반 모델의 부품 목록 전체**. 예: WDEK9419S 모델의 BOM (수백~수천 줄)
2. **심의회 PPT** — "신규 모델은 이런 점들이 바뀐다"는 **변경점·변경사유 문서**. 예: "도어에 카메라 모듈 추가", "BLDC 모터로 변경" 등

### **출력 (Agent가 만들어주는 것)**

- **신규 모델의 BOM 초안 (Excel)**
- 어떤 부품이 추가됐고, 변경됐고, 왜 그렇게 됐는지까지 적힌 표

### **왜 이게 어려운 작업인가?**

변경점은 "카메라 추가"라고 한 줄만 적혀 있지만, 실제로 BOM에 반영하려면:

- 카메라 모듈 (ADD)
- 카메라 고정용 커버 하니스 (ADD)
- 도어 어셈블리 (상위 품번 MODIFY)
- 카메라 장착 위치의 트레이 플라스틱 (MODIFY)

처럼 **연관된 부품 여러 개**가 같이 바뀝니다. 이걸 사람이 일일이 찾으려면 과거 개발 이력 수십 건을 뒤져야 합니다. → **AI Agent가 과거 이력 DB에서 비슷한 케이스를 찾아 자동으로 적용**해주는 게 핵심.

---

## **2. 전체 흐름 한눈에 보기**

```
┌──────────────────────────┐    ┌──────────────────────────┐
│   Base BOM (.xlsx)       │    │   심의회 PPT (.pptx)     │
│   기반 모델 전체 부품    │    │   변경점 + 변경사유      │
└────────────┬─────────────┘    └────────────┬─────────────┘
             │                               │
             └───────────────┬───────────────┘
                             ▼
                    ┌────────────────┐
                    │ change_parser  │  ← PPT/Excel을 JSON으로 정규화
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │   supervisor   │  ← 흐름 제어, 다음 단계 결정
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │    retrieve    │  ← PostgreSQL에서 비슷한 과거 모델 검색
                    └────────┬───────┘  (← dev_part_master 사용!)
                             ▼
                    ┌────────────────┐
                    │   supervisor   │  ← 후보 모델 정리
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │  select_base   │  ← 가장 유사한 1개 모델 선정
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │   apply_diff   │  ← 선정 모델의 변경 패턴을 적용
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │    validate    │  ← BOM 초안 형식·규칙 검증
                    └────────┬───────┘
                             ▼
                    ┌────────────────┐
                    │  human_review  │  ← 사람이 UI에서 확인
                    └────────┬───────┘
                       confirm / edit
                             ▼
                    ┌────────────────┐
                    │ Excel 출력 → END│
                    └────────────────┘
```

---

## **3. 노드별 역할 (쉽게 설명)**

### **3.1 `change_parser` — 입력을 AI가 다룰 수 있는 형태로 정규화**

**하는 일**: 사람이 쓴 PPT/Excel의 비정형 텍스트를 → AI가 처리할 수 있는 **구조화된 JSON**으로 바꿉니다.

**예시 변환**:

```
PPT 원문: "도어에 카메라 모듈 추가 (기능 추가)"
        ↓
JSON:
{
  "cp_id": "CP-001",
  "canonical_part": "Camera Module",   ← 정규화된 부품명
  "pno_hints": ["MEV4272640"],         ← 추정 부품번호
  "action": "ADD",                     ← 동작 (ADD/MODIFY/DELETE)
  "location": "Door Assy",             ← 어디에
  "reason_code": "기능추가"            ← 사유 코드
}
```

**중요한 고려사항**: 부품명이 사람 말투로 들어옵니다 ("도어 카메라", "카메라 모듈", "Camera"...). 이걸 표준 부품명으로 매핑하려면 **부품명 사전(dictionary)**이 필요합니다. 현재 legacy 시스템에선 이게 하드코딩되어 있다고 함.

---

### **3.2 `supervisor` — 흐름 제어자 (감독관)**

**하는 일**: 매 단계마다 다음 어디로 갈지 결정. 입력 검증, 라우팅 담당.

- 1차: `change_parser` 다음에 → `retrieve`로 보냄
- 2차: `retrieve` 결과를 보고 → `select_base`로 보냄

추후엔 "검색 결과가 부족하면 다시 검색하라"는 추가 분기도 가능.

---

### **3.3 `retrieve` — 과거 이력 검색 Agent (ReAct 패턴)**

**하는 일**: PostgreSQL의 `dev_part_master` 테이블을 뒤져서 **변경점이 비슷한 과거 개발 건**을 찾습니다.

**검색 방식**:

1. 변경점 CP 1개씩 순회 (CP-001 → CP-002 → CP-003)
2. CP마다 매칭되는 `file_id`(과거 개발 건) 목록 수집
3. **여러 CP가 동시에 매칭된 file_id에 점수 부여** → 점수 높은 것이 우선 후보

**점수 집계 예시**:

```
CP-001(Camera) → file_id [42, 87, 103]
CP-002(Door Harness) → file_id [42, 55]
CP-003(Tray Plastic) → file_id [42, 87]

집계:
  file_id 42 → 3점 (CP-001,002,003 모두 매칭) ← 최우선
  file_id 87 → 2점
  file_id 55 → 1점
```

**한계 (설계자 본인이 인정한 부분)**: 현재는 **단순 키워드 매칭**입니다. "냉장고 도어 진동 개선" 같은 의미적 유사성은 못 잡습니다. → 추후 임베딩 벡터 검색 추가 필요.

---

### **3.4 `select_base` — 후보 중 최종 1개 선정**

**하는 일**: `retrieve`가 찾아준 후보 목록 중 **가장 유사한 1개 모델**을 LLM이 판정해서 고릅니다.

**판정 기준 (현재 안)**:

1. 매칭된 CP 수가 많을수록 우선
2. 동점이면 → 동일 플랫폼, 동일 개발등급, 유사 지역 순
3. **선정 근거(reasoning)도 함께 반환** → 사람이 "왜 이 모델이 선정됐는지" 확인 가능

---

### **3.5 `apply_diff` — 선정 모델의 변경 패턴을 신규 BOM에 적용**

**하는 일**: "WDEK9419S 모델에서 카메라 추가할 때 이 부품들이 같이 바뀌었네" → 그 패턴을 **현재 Base BOM에 그대로 적용**해서 신규 BOM 초안 행을 만듭니다.

**출력 예시**:

```json
[
  {
    "bom_level": "..2", "part_type": "part",
    "base_pno": "-", "new_pno": "MEV4272640",
    "desc": "Camera Module",
    "qty_base": 0, "qty_new": 1,
    "change_point": "도어에 카메라 모듈 추가",
    "change_reason": "기능추가",
    "supplier": "LG이노텍",
    "classification": "New"
  },
  ...
]
```

---

### **3.6 `validate` + `human_review` — 검증과 사람의 확인**

- **validate**: BOM 초안의 **형식 규칙 검증**. 부품번호 형식 맞나, bom_level 누락 없나, classification 적절한가 등.
- **human_review**: LangGraph의 `interrupt()`로 그래프 실행을 멈춤. Streamlit UI에 초안과 검토 포인트를 띄워서 사람이 확인.

사용자 응답에 따라:

- `confirm` → Excel 출력 후 종료
- `edit` → `apply_diff`로 돌아가서 재생성

---

## **4. 지금 폴더(`etl_pg/`)와의 호환성·연계성**

### **4.1 결론부터: 이 Agent 파이프라인의 "데이터 소스"가 지금 `etl_pg` 프로젝트입니다.**

```
┌─────────────────────────────────────────────────┐
│  현재 단계: etl_pg/ (이미 완성)                 │
│  → 24개 엑셀 파일 → PostgreSQL 적재             │
│  → dev_part_master 테이블에 2,860 row 저장      │
└────────────────────┬────────────────────────────┘
                     │
                     │ (DB가 검색 대상)
                     ▼
┌─────────────────────────────────────────────────┐
│  다음 단계: BOM Agent 파이프라인 (이 PDF 설계)  │
│  → retrieve 노드가 dev_part_master를 검색       │
│  → 과거 이력에서 비슷한 모델 찾기               │
└─────────────────────────────────────────────────┘
```

### **4.2 노드별로 어떤 기존 자산을 쓰는가?**

| Agent 노드 | 사용하는 기존 자산 | 어떻게 연결되나 |
| --- | --- | --- |
| `change_parser` | (없음 — 신규 구축) | PPT/Excel 파싱은 `parsers/readers/`의 패턴을 참고만 가능 |
| `supervisor` | (없음 — LangGraph로 신규 구축) | — |
| **`retrieve`** | **`dev_part_master` 테이블** | 핵심 검색 대상. `part_no_new`, `part_name`, `change_reason_raw`, `change_point_raw` 컬럼을 사용 |
| `select_base` | `dev_part_master`의 메타 컬럼 (`region`, `base_model`, `new_model`) | 후보 모델의 플랫폼/등급/지역 정보 활용 |
| `apply_diff` | `dev_part_master.file_id`로 묶인 변경 패턴 | 같은 `file_id` 내의 행들이 "한 개발 건의 변경 패턴" |
| `validate` | `schema_postgres.sql`의 컬럼 정의 | classification, bom_level 등 표준 값 검증 |

### **4.3 `dev_part_master`의 어떤 컬럼이 핵심으로 쓰이나?**

`schema_postgres.sql`에 이미 정의되어 있는 컬럼들:

| 컬럼 | Agent 파이프라인에서의 역할 |
| --- | --- |
| `file_id` | **개발 건 단위 묶음 키**. retrieve가 점수 집계할 때 이 키로 group by |
| `part_no_new`, `part_no_base` | retrieve의 `search_by_pno` 검색 키 |
| `part_name` | retrieve의 `search_by_part_name` 검색 키 |
| `change_point_raw` | 변경점 텍스트 매칭 |
| `change_reason_raw` | 변경사유 매칭 (`search_by_change_reason`) |
| `classification` | apply_diff가 New/Change/Common 결정할 때 참고 |
| `bom_level_raw`, `bom_depth` | apply_diff가 BOM 트리 위치 잡을 때 |
| `region`, `base_model`, `new_model` | select_base의 동일 플랫폼 판정 |
| **`row_kind`** | 자동 분류 트리거가 `change_history`/`bom_entry`/`marker`로 구분 → **retrieve는 `change_history`만 우선 검색하면 효율적** |
| `embedding_dense vector(1024)` | **현재는 비어있지만**, 의미 검색이 추가될 때 pgvector로 활용 |

### **4.4 지금 부족한 것 (다음에 채워야 할 것)**

| 부족한 부분 | 어디에 채워야 하는가 |
| --- | --- |
| **부품명 정규화 사전** | `change_parser`가 "카메라 모듈" → "Camera Module" 변환할 때 필요. 현재 `dev_part_master.part_name`의 값들을 분석해서 별도 dict 테이블로 구축 |
| **임베딩 벡터** | `dev_part_master.embedding_dense`가 비어있음. retrieve의 의미 검색을 위해 채워야 함 (`embedding_text` 컬럼에 이미 본문은 있음) |
| **검색 도구(tool)들** | `search_by_pno`, `search_by_part_name`, `search_by_change_reason`, `get_related_parts` — 모두 SQL 함수로 구현해야 함 |
| **Base BOM 파서** | 신규 입력으로 들어올 Base BOM Excel을 읽는 파서. `parsers/readers/dev_part_master.py`의 동적 헤더 탐색 로직 재활용 가능 |
| **PPT 파서** | python-pptx로 변경점 텍스트 추출. 신규 모듈 필요 |

### **4.5 호환되는 점 (이미 잘 깔려 있는 점)**

1. **DB 스키마가 Agent 검색을 미리 고려해서 설계됨**
    - `idx_dpm_part_no_new`, `idx_dpm_part_name_trgm`, `idx_dpm_change_reason_trgm` 등 **검색 인덱스가 이미 다 잡혀 있음**
    - `idx_dpm_embedding_hnsw` HNSW 벡터 인덱스도 미리 만들어둠
    - `row_kind` 트리거가 변경 이력만 골라낼 수 있게 자동 분류
2. **`embedding_text` 컬럼이 이미 자기서술형으로 채워져 있음** `parsers/db.py:_build_embedding_text()`가 "지역: 호주 | 모델: X→Y | 부품번호: ... | 변경사유: ..." 형태로 조립해둠 → 임베딩만 돌리면 바로 벡터 검색 가능
3. **`file_id`가 개발 건 단위 키** → retrieve의 점수 집계와 `get_related_parts`가 그대로 동작
4. **23개 파일 × 2,860 row가 이미 적재됨** → 즉시 retrieve 테스트 가능한 양

---

## **5. 단계별 구현 우선순위 (제안)**

이 가이드가 새 작업 우선순위를 정해주진 않지만, PDF 설계와 현재 코드 상태를 보면 자연스러운 순서는:

```
[Phase 1] 검색 도구 구축 (기존 DB 활용)
  1. search_by_pno / search_by_part_name / search_by_change_reason SQL 함수
  2. get_related_parts(file_id) → 같은 개발 건의 부품 패턴 반환
  3. embedding_dense 벡터 채우기 (embedding_text → 임베딩 모델)

[Phase 2] 입력 파서 구축
  4. PPT 파서 (python-pptx 기반 change_parser)
  5. Base BOM 엑셀 파서 (기존 dev_part_master.py 재활용)
  6. 부품명 정규화 사전 구축

[Phase 3] LangGraph 노드 구축
  7. change_parser 노드
  8. retrieve 노드 (ReAct)
  9. supervisor / select_base / apply_diff / validate 노드
  10. human_review interrupt + Streamlit UI

[Phase 4] 통합 테스트
  11. 실제 Base BOM + 심의회 PPT로 end-to-end 테스트
  12. 사용자 피드백 반영 루프 검증
```

---

## **6. 핵심 데이터 흐름 요약 (한 페이지로)**

```
[입력]
  Base BOM.xlsx ──────────────┐
  심의회 PPT.pptx ────────────┤
                              ▼
                       change_parser
                              ▼
                    change_points JSON
                    [{cp_id, canonical_part,
                      pno_hints, action,
                      location, reason_code}, ...]
                              ▼
                          retrieve
                              ▼
              ┌──── PostgreSQL ────┐
              │  dev_part_master   │
              │  (etl_pg가 적재)    │
              └─────────┬──────────┘
                        ▼
              candidate_models [{file_id, score, matched_cps}]
              related_parts [{file_id별 함께 바뀐 부품들}]
                              ▼
                       select_base
                              ▼
                 base_model_id + affected_parts
                              ▼
                       apply_diff
                              ▼
                    bom_draft [신규 BOM 행 리스트]
                              ▼
                        validate
                              ▼
                      human_review (UI)
                              ▼
                   ┌───── confirm ─────┐
                   ▼                   ▼
            Excel 출력             재생성 루프
              [END]
```

---

## **7. 한 줄로 다시 정리**

**`etl_pg`는 "과거 개발 이력 DB"를 만들었고, 이 Agent 파이프라인은 그 DB를 활용해서 "신규 모델의 BOM 초안을 자동 생성"하는 시스템입니다.** DB 스키마와 인덱스, embedding_text 본문이 이미 Agent 검색을 염두에 두고 설계되어 있어 호환성은 좋습니다. 다음 단계는 (1) 검색 SQL 함수, (2) PPT/BOM 파서, (3) LangGraph 노드 구축입니다.