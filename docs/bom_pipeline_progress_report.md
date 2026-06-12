# BOM 파이프라인 개발 진행 보고서

> 브랜치: `feat/bom_pipeline2`  
> 작성일: 2026-06-12  
> 대상 독자: 개발 현황 공유 / 보고용

---

## 1. 개요

PPTX 개발심의회 문서 + Base BOM Excel → 자동으로 변경된 신규 BOM Excel을 생성하는 AI 파이프라인.  
LLM(GPT-4o) + SQLite 히스토리 DB를 조합하여 PPTX에 명시된 변경점을 BOM에 반영하고, 과거 이력 기반 누락 부품도 제안한다.

---

## 2. 파이프라인 구조

```
PPTX + Base BOM Excel
        │
        ▼
 [1] parse_pptx       슬라이드 타입 자동 감지 → 변경점 추출
        │
        ▼
 [2] bom_match        변경점 ↔ BOM 매핑 (LLM)
        │
        ▼
 [3] history_search   과거 이력 DB에서 유사 케이스 검색 → 연동부품 제안 (LLM)
        │
        ▼
 [4] (human_review)   담당자 확정 — 현재 미연결
        │
        ▼
 [5] write_bom        확정 변경점 → 신규 BOM Excel 생성 (색상 마킹 포함)
```

---

## 3. 노드별 구현 현황

### 3-1. parse_pptx (`bom_pipeline/nodes/parse_pptx.py` · 393줄)

| 항목 | 내용 |
|------|------|
| 타입 A (상세표) | Base P/No / New P/No / Part Name / Lev. 컬럼 있는 표 → 품번 직접 추출 |
| 타입 B (요약표) | 모듈명 + 변경 텍스트만 → LLM이 부품명·변경 내역 파싱 |
| 슬라이드 자동 구분 | 헤더 컬럼 패턴으로 A/B 판별, 혼합 PPTX 처리 가능 |
| Few-shot 예시 | Oven 계열 위주 (추후 타 제품군 확장 필요) |

**퀵존레인지 기준:** 28 슬라이드(타입A=11, 타입B=17), 변경점 165개 추출

---

### 3-2. bom_match (`bom_pipeline/nodes/bom_match.py` · 456줄)

| 항목 | 내용 |
|------|------|
| 역할 | 추출된 변경점을 Base BOM의 실제 품번/트리에 매핑 |
| change_scope | `full` (모듈 전체 교체) / `partial` (특정 하위 부품만 변경) LLM 판단 |
| preserve_parts | LLM이 소재류(Paint, Powder, Coil, Steel 등)를 보존 대상으로 지정 |
| deleted_parts | PPTX에 명시된 삭제 부품을 LLM이 `.1` 레벨 목록에서 추출 |
| 소재 동의어 확장 | `powder ↔ enamel ↔ paint`, `coil ↔ steel ↔ alcot` 등 그룹으로 자동 확장 |

**퀵존레인지 기준:** 164/165 매핑 성공 (Cooktop 모듈은 Base BOM에 없어 fallback)  
소요 시간: 약 20~25분 (TPM 30,000 제한, 429 에러 시 자동 재시도)

---

### 3-3. history_search (`bom_pipeline/nodes/history_search.py` · 344줄)

| 항목 | 내용 |
|------|------|
| 경로1 (SQL 직접) | base_part_no 있는 경우 → DB에서 동일 품번 변경이력 직접 조회 |
| 경로2 (하이브리드) | FTS5(BM25) + 임베딩 코사인 유사도 RRF 합산 → top-30 후보 확보 |
| LLM 역할 | top-5 케이스 선정 + 현재 변경점 목록에 누락된 연동부품 판단 |
| 히스토리 DB | `dev_parts.db` SQLite / dev_part_master 12건 / dev_part_detail 1,068행 |

**퀵존레인지 기준:**  
- 경로1: 전부 0건 (신규 파생 모델로 품번 이력 없음)  
- 경로2: 전 항목 hybrid search 30건 확보  
- linked_parts 후보 총 371개 (165개 항목 중 114개에서 제안, 69%)  
- 소요 시간: 약 26분

---

### 3-4. human_review (`bom_pipeline/nodes/human_review.py` · 221줄)

- 구현은 완료되었으나 LangGraph StateGraph(`graph.py`) 미연결
- 현재는 fixtures JSON을 수동으로 write_bom에 전달하는 방식으로 우회

---

### 3-5. write_bom (`bom_pipeline/nodes/write_bom.py` · 344줄)

| 항목 | 내용 |
|------|------|
| 삭제 | deleted_parts + change_scope=full 모듈의 하위 트리 전체 제거 (보존 소재 제외) |
| 변경 | partial 변경점의 Part No / Description 인라인 수정 |
| 추가 | full replace 신규 행 삽입 + linked_parts 연동부품 행 삽입 |
| 색상 마킹 | 변경=노란, 추가=초록, 삭제=빨강+취소선 |
| 컬럼 구조 | base BOM 원본 컬럼 그대로 유지 |

**퀵존레인지 기준:** base 3,537행 → 신규 BOM 3,884행  
(변경 24 / 추가 347 / 삭제 1,271)

---

## 4. 검증 결과

### 4-1. Compact Oven (WSED7613S → KWS7D4731S)

기준 BOM 대비 마스터 output의 `.1` 레벨 변경 부품 24개 중 **10개 감지 (감지율 42%)**

| 감지 결과 | 개수 | 비율 |
|-----------|------|------|
| 감지 성공 | 10 | 42% |
| 미감지 (치수 파생) | 6 | 25% |
| 미감지 (포장/서비스) | 7 | 29% |
| 미감지 (기타) | 1 | 4% |

### 4-2. 퀵존레인지 (LTIS7338XE → LQIN7137XE)

마스터 output 없어 수치 감지율 측정 불가. 파이프라인 end-to-end 동작 확인 완료.

| 단계 | 결과 |
|------|------|
| parse_pptx | 165개 변경점 추출 |
| bom_match | 164/165 매핑 성공, change_scope full=18 / partial=12 |
| history_search | linked_parts 371개 제안 (69% 항목) |
| write_bom | 신규 BOM xlsx 생성 완료 (`output/quickzone_new_bom.xlsx`) |

---

## 5. 알려진 문제점

### 5-1. [치수 파생 변경] 구조적 한계 — 현재 미해결

PPTX에 "Out Case 치수 변경"만 명시되고 파생 부품(Panel,Side / Plate,Base / Insulator Assembly)은 미언급.  
→ bom_match 감지 불가. history_search 연동부품으로 제안되어야 하지만 DB에 해당 인과 패턴이 없어 불가.

**해결 조건:** 히스토리 DB에 "치수 변경 → 파생 부품 교체" 패턴 케이스 추가 필요.

---

### 5-2. [포장/서비스 부품] 코드 + 데이터 복합 문제 — 현재 미해결

Box, Label,Carton, Manual,Service, Packing,Gasket 등 7개 미감지.

**원인 1 — 코드:** `_SKIP_KEYWORDS`에 `"label"`, `"carton"`, `"manual"`, `"gasket"`, `"bag"` 등이 포함되어 LLM에게 케이스 텍스트가 전달될 때 이 부품들이 제거됨.

**원인 2 — 데이터:** DB 12건에 "치수 변경 → 포장 교체" 인과 패턴 없음. 포장 변경 케이스가 있으나 이유가 전부 "모델명 변경", "라벨 변경".

**원인 3 — BOM 구조:** 포장류와 치수 변경 대상이 모두 `.1` 레벨 sibling. 계층 구조로 자동 파생 불가.

**개선 방향:**  
- `_SKIP_KEYWORDS`를 체결류(Nut/Screw/Washer/Bolt/Rivet)만으로 축소  
- "전체 치수 변경 이벤트 시 포장류 자동 포함" 도메인 규칙 하드코딩 검토

---

### 5-3. [preserve_parts 오보존] — 현재 미해결

`Sheet,Steel(GI)` (RAA33956539)가 Out Case full replace 시 보존 대상으로 잘못 지정됨.

**원인:** `_expand_preserve_by_desc`가 `steel` 키워드로 BOM 전체에서 확장 적용.  
실제로 이 품번은 삭제 대상 `Cover,Rear` 하위 원자재임.

**개선 방향:** expand 로직에 "삭제 대상 subtree 내부 품번은 제외" 필터 추가 필요.

---

### 5-4. [LangGraph graph.py 미연결] — 미완

`bom_pipeline/graph.py` 파일이 비어 있어 파이프라인이 LangGraph StateGraph로 연결되지 않음.  
현재는 각 노드를 직접 호출하거나 별도 스크립트(`run_write_bom.py`)로 우회.

---

### 5-5. [Compact Oven history_search linked_parts = 0] — 데이터 문제

Compact Oven 9개 change_point 전부 linked_parts = 0.  
5-2의 `_SKIP_KEYWORDS` 차단 + DB 패턴 부재가 복합 원인.  
퀵존레인지에서는 69% 항목에서 linked_parts 제안됨 — 타입A 변경점이 많아 유사 패턴 매칭이 더 잘 되는 구조 차이.

---

### 5-6. [파이프라인 over-fitting 위험도] — 낮음 (현재)

현재 few-shot 예시와 preserve_parts 소재 예시가 Oven/Range 계열 위주.  
시연 대상 파일이 모두 동일 계열(퀵존레인지, Studio Double Slide In IH 등)이라 단기적으론 문제없음.  
타 제품군(세탁기, 냉장고 등) 확장 시 few-shot 업데이트 필요.

---

## 6. 주요 파일 목록

| 파일 | 역할 |
|------|------|
| `bom_pipeline/nodes/parse_pptx.py` | 슬라이드 파싱 노드 |
| `bom_pipeline/nodes/bom_match.py` | BOM 매핑 노드 |
| `bom_pipeline/nodes/history_search.py` | 히스토리 검색 노드 |
| `bom_pipeline/nodes/write_bom.py` | BOM 생성 노드 |
| `bom_pipeline/nodes/human_review.py` | 담당자 확인 노드 (미연결) |
| `bom_pipeline/graph.py` | LangGraph 파이프라인 연결 (미작성) |
| `bom_pipeline/db.py` | SQLite DB 쿼리 (FTS5, 임베딩 검색) |
| `bom_pipeline/state.py` | 파이프라인 공유 상태 타입 정의 |
| `dev_parts.db` | 히스토리 DB (12 케이스 / 1,06 8 부품 행) |
| `bom_pipeline/fixtures/quickzone_bommatch.json` | 퀵존 bom_match 결과 픽스처 (13MB) |
| `bom_pipeline/fixtures/quickzone_history.json` | 퀵존 history_search 결과 픽스처 (15MB) |
| `output/quickzone_new_bom.xlsx` | 퀵존 최종 신규 BOM 출력 |
| `docs/pipeline_known_issues.md` | 알려진 문제점 상세 (Compact Oven 기준) |
| `run_write_bom.py` | 임시 실행 스크립트 (graph.py 대체) |

---

## 7. 남은 작업 (우선순위 순)

| 우선순위 | 항목 | 난이도 |
|----------|------|--------|

| 1 | `graph.py` LangGraph 연결 — 단일 진입점으로 통합 | 중 |
| 2 | `_SKIP_KEYWORDS` 체결류만으로 축소 (포장 차단 제거) | 하 |
| 3 | preserve_parts: 삭제 대상 subtree 내부 품번 제외 | 중 |
| 4 | 치수 파생 규칙 도메인 하드코딩 (치수 변경 시 포장 자동 포함) | 중 |
| 5 | 히스토리 DB 케이스 확충 (현재 12건 → 최소 50건 이상) | 고 |
| 6 | 퀵존레인지 마스터 output 입수 후 감지율 재측정 | 중 |
| 7 | few-shot 예시 타 제품군 확장 | 중 |