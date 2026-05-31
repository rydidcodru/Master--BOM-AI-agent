# BOM 심의회 분석 파이프라인

LG전자 오븐/주방가전 개발 심의회 PPTX에서 부품 변경점을 추출하고,
Neo4j에 저장된 과거 이력과 GPT-4o로 유사도를 비교해 Master BOM을 자동 작성하는 AI 에이전트.

---

## 전체 흐름

```
[입력]
  심의회 PPTX + base_bom.xlsx (선택)
        │
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 1. PPTX 파싱          parse_pptx_node            │
  │  GPT-4o로 변경점 추출                                    │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[]
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 2. 부품명 정규화       normalize_node             │
  │  canonical_part / aliases 생성                          │
  └──────────────────┬──────────────────────────────────────┘
                     │  change_points[] (canonical 포함)
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 3. 병렬 검색           fan_out → item_search      │
  │  Neo4j Cypher로 유사 이력 후보풀 검색                    │
  │  (부품명 키워드 → 변경내역 키워드 폴백, 최대 3회 retry)  │
  └──────────────────┬──────────────────────────────────────┘
                     │  candidates[] (lineId/documentId 포함)
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 3. LLM 유사도 판단     rank_candidates_node       │
  │  GPT-4o로 의미 유사도 판단 → 상위 5건 ranked[]          │
  └──────────────────┬──────────────────────────────────────┘
                     │  ranked[] (lineId/documentId 포함)
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  [UI] STEP 3. 사용자 후보 선정                           │
  │  카드 UI로 후보 확인, 체크박스로 선택                    │
  │  LLM 추천 0건이면 Neo4j 원본 후보풀 폴백 표시            │
  └──────────────────┬──────────────────────────────────────┘
                     │  selections {sr_idx: [candidate, ...]}
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 4. 계층 조회           fetch_hierarchy            │
  │  선택 후보의 lineId/documentId 기준                      │
  │  같은 문서 내 상위 Assembly + 자신 + 하위 부품 조회      │
  └──────────────────┬──────────────────────────────────────┘
                     │  계층 행[]
                     ▼
  ┌─────────────────────────────────────────────────────────┐
  │  STEP 4. Master BOM 작성     write_master               │
  │  - 상위 Assembly 행: "하위 부품 변경" (과거 이력 그대로) │
  │  - 자신 행: PPTX 변경점 + GPT-4o 변경사유 생성          │
  │  - 하위 부품 행: 과거 이력 그대로                        │
  └──────────────────┬──────────────────────────────────────┘
                     │
                     ▼
  [출력] Master BOM Excel (.xlsx) 다운로드
```

---

## 디렉토리 구조

```
pptx_bom_search/
├── app.py                  # Streamlit 4단계 UI
├── runner.py               # 단계별 실행 함수 (step1~step4)
├── graph.py                # LangGraph 그래프 정의
├── state.py                # BOMSearchState TypedDict
├── loader.py               # PPTX 슬라이드 텍스트 추출
├── main.py                 # CLI 실행 진입점
├── nodes/
│   ├── parse_pptx.py       # STEP 1: PPTX → change_points
│   ├── normalize.py        # STEP 2: 부품명 정규화
│   ├── fan_out.py          # STEP 3: Send()로 병렬 분기
│   ├── item_search.py      # STEP 3: Neo4j 후보풀 검색
│   ├── collect.py          # STEP 3: 병렬 결과 집계
│   ├── rank_candidates.py  # STEP 3: LLM 유사도 랭킹
│   ├── fetch_hierarchy.py  # STEP 4: Neo4j 계층 조회
│   ├── write_master.py     # STEP 4: Master BOM 행 조립 + Excel
│   ├── output.py           # CLI 결과 출력
│   ├── human_review.py     # [STUB] 사용자 선택 인터럽트
│   ├── merge_selections.py # [STUB] 선택 결과 합산
│   └── write_bom.py        # [STUB] base BOM 갱신
├── requirements.txt
├── .env                    # API 키 / DB 접속 정보
└── .env.example
```

---

## 핵심 데이터 구조

### ChangePoint
PPTX에서 추출된 부품 변경점 하나.

| 필드 | 설명 |
|------|------|
| `module` | 소속 모듈 (Cavity / Door / Controller / ...) |
| `part` | 원본 부품명 (Conv. Motor, Panel,Control 등) |
| `change_detail` | 변경 전 → 변경 후 내용 |
| `change_reason` | 변경 사유 |
| `discipline` | 기구 / 제어 / ThinQ / 기타 |
| `type` | Changing / NEW / 삭제 |
| `concern` | 걱정점 |
| `canonical_part` | 정규화된 표준 부품명 (normalize 후 채워짐) |
| `aliases` | 동의어 목록 (normalize 후 채워짐) |
| `source_pptx` | 출처 파일명 |
| `evidence_slide` | 근거 슬라이드 번호 |

### SearchResult
부품 하나에 대한 검색 결과.

| 필드 | 설명 |
|------|------|
| `change_point` | ChangePoint |
| `candidates` | Neo4j 원본 후보풀 (최대 50건) |
| `ranked` | LLM이 선정한 유사 후보 (최대 5건, lineId/documentId 포함) |
| `retry_count` | Neo4j 검색 재시도 횟수 |

### Master BOM 행
| 필드 | 출처 |
|------|------|
| `No` | 자동 순번 |
| `BOM_Level` | Neo4j rawLevel (`.1`, `..2`, `...3`) |
| `Part_Type` | Neo4j partType → 한글 매핑 |
| `Base_PNo` | 과거 이력 basePartNoRaw |
| `New_PNo` | 과거 이력 newPartNoRaw |
| `Class_Desc` | 과거 이력 partName |
| `Changing_Point` | 자신 행: PPTX change_detail / 상위·하위: 과거 이력 |
| `Changing_Reason` | 자신 행: GPT-4o 생성 / 상위: "하위 부품 변경" / 하위: 과거 이력 |
| `Supplier` | 과거 이력 modelName |
| `Classification` | 과거 이력 classification (New / Changing / Delete) |

---

## 노드별 상세

### parse_pptx_node
- **입력**: `state["pptx_paths"]` (없으면 `input/` 디렉토리 폴백)
- **LLM**: GPT-4o, temperature=0
- **프롬프트**: FewShotChatMessagePromptTemplate
  - 예시 1: NPI Extra 양식 (행 단위 표 — 행 하나 = 부품 하나)
  - 예시 2: NXI Compact 양식 (셀 안 번호 목록 — 번호 항목 하나 = 부품 하나)
  - 규칙: 요약 슬라이드 무시, 상세 슬라이드 우선
- **출력**: `change_points[]`

### normalize_node
- **입력**: `change_points[]`
- **LLM**: GPT-4o, temperature=0, 배치 25건
- **동작**: 약어 풀이 (Conv.→Convection), 역순 정규화 (Panel,Control→Control Panel), 한국어 동의어 추가
- **출력**: `canonical_part`, `aliases` 채워진 `change_points[]`

### item_search_node
- **입력**: `Send()`로 전달된 개별 ChangePoint
- **검색 전략**:
  1. `canonical_part` + `part`에서 키워드 추출 → `bl.partNameRaw` 매칭
  2. 결과 부족(< 5건)이면 변경내역 키워드로 `cr.changingPoint` 폴백 병합
  3. retry마다 키워드 수 절반으로 축소 (최대 3회)
- **반환 필드**: `lineId`, `documentId` 포함

### rank_candidates_node
- **입력**: `candidates[]` (최대 50건)
- **LLM**: GPT-4o, temperature=0
- **판단 기준**: 부품 종류 유사성 / 변경 행위 유사성 / 변경 목적 유사성
- **출력**: 상위 5건 `ranked[]` + `lineId`/`documentId` 역매핑 보완

### fetch_hierarchy
- **입력**: 선택된 후보의 `lineId`, `documentId`
- **Cypher 1** (상위): `HAS_CHILD*1..10` 역방향으로 level 1 루트까지 조회
- **Cypher 2** (자신+하위): `HAS_CHILD*0..10` 정방향으로 재귀 조회
- **필터**: 같은 `documentId` 안에서만, `changingPoint` 또는 `newPartNoRaw` 있는 행만
- **정렬**: level 오름차순 (상위 → 자신 → 하위)

### write_master (build_master_rows)
- **자신 행**: PPTX `change_detail` + GPT-4o 변경사유 생성
  - 생성 입력: PPTX change_reason + 선택 후보들의 changingReason (최대 3개)
- **상위 Assembly 행**: 과거 이력 변경점 유지, 변경사유 "하위 부품 변경"
- **하위 부품 행**: 과거 이력 그대로 복사
- **계층 없을 때**: 단독 행 생성 (폴백)

---

## UI 단계별 흐름 (app.py)

| 단계 | 화면 | 주요 기능 |
|------|------|----------|
| STEP 1 | PPTX + base_bom 업로드 | 파일 업로드 → 파싱 시작 → `st.status`로 진행 표시 |
| STEP 2 | 파싱 결과 확인/편집 | `st.data_editor`로 change_points 수정·삭제, 부품명 재정규화 버튼 |
| STEP 3 | 유사 이력 후보 선정 | 파일별 탭, 후보 카드(rank/level/분류 배지), 체크박스 선택, LLM 0건 시 원본 후보풀 폴백 |
| STEP 4 | Master BOM 작성 | 모델명 입력, Master 생성 버튼, 미리보기 테이블, Excel 다운로드 |

---

## Neo4j 스키마

### 노드
| 레이블 | 주요 속성 |
|--------|----------|
| `BomLine` | `lineId`, `documentId`, `caseId`, `level`, `rawLevel`, `partNameRaw`, `partType`, `basePartNoRaw`, `newPartNoRaw`, `changingPoint`, `changingReason` |
| `ChangeRecord` | `changeId`, `caseId`, `lineId`, `classification`, `changingPoint`, `changingReason`, `basePartNoRaw`, `newPartNoRaw` |
| `ReviewCase` | `caseId`, `modelName`, `summary` |
| `SourceDocument` | `documentId` |

### 관계
| 관계 | 의미 |
|------|------|
| `HAS_CHILD` | BomLine → BomLine (부품 계층 구조) |
| `FROM_LINE` | ChangeRecord → BomLine |
| `CONTAINS_LINE` | SourceDocument → BomLine |
| `HAS_CHANGE_RECORD` | BomLine → ChangeRecord |

---

## 실행 방법

```bash
cd pptx_bom_search

# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경 변수 설정
cp .env.example .env
# .env 편집: OPENAI_API_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

# 3. UI 실행
python3 -m streamlit run app.py --server.port 8501
# → http://localhost:8501

# 4. CLI 실행 (input/ 디렉토리 기준)
python3 main.py
```

---

## 미구현 (STUB) — 다음 단계

| 노드 | 파일 | 예정 기능 |
|------|------|----------|
| `human_review` | `nodes/human_review.py` | `langgraph.types.interrupt()`로 교체, 사용자 후보 선택 인터럽트 |
| `merge_selections` | `nodes/merge_selections.py` | `human_selections` → `bom_updates` 변환 |
| `write_bom` | `nodes/write_bom.py` | `base_bom.xlsx`에서 Master 변경이력 기반으로 계층 구조 P/No 교체 |

---

## 주요 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-05-30 | Streamlit 3단계 UI 구현 (app.py 전면 재작성) |
| 2026-05-30 | `graph.py` fan_out 이중 실행 버그 수정 |
| 2026-05-30 | `parse_pptx_node` — state["pptx_paths"] 무시 버그 수정 |
| 2026-05-30 | `runner.py` — step1~step3 단계별 실행 함수 추가 |
| 2026-05-31 | `parse_pptx.py` — FewShotChatMessagePromptTemplate 적용 (Compact Oven 양식 지원) |
| 2026-05-31 | `item_search.py` — lineId/documentId 반환 추가 |
| 2026-05-31 | `nodes/fetch_hierarchy.py` — 신규, Neo4j 계층 조회 |
| 2026-05-31 | `write_master.py` — 계층 구조 포함 Master BOM 행 조립 |
| 2026-05-31 | `rank_candidates.py` — ranked 결과에 lineId/documentId 역매핑 |
| 2026-05-31 | STEP 4 UI 추가 (Master BOM 미리보기 + Excel 다운로드) |
| 2026-05-31 | STEP 3 — LLM 추천 0건 시 Neo4j 원본 후보풀 폴백 표시 |
