# pptx_bom_search — 작업 이력

## 개요

LG전자 오븐/주방가전 개발 심의회 PPTX에서 BOM 변경점을 자동 추출하고,
Neo4j에 저장된 과거 이력과 GPT-4o로 유사도를 비교해 후보를 제시하는 LangGraph 파이프라인 + Streamlit 데모 UI.

---

## 수정 내역

### 1. `graph.py` — 버그 수정

**문제**: `normalize` → `fan_out` 연결에서 `add_conditional_edges`의 라우터 함수로 `fan_out_node`를 직접 지정했는데,
`fan_out`은 이미 별도 노드로 등록되어 있어 **함수가 두 번 실행**되는 구조였음.

**수정 전**:
```python
g.add_conditional_edges("normalize", fan_out_node, ["item_search"])
```

**수정 후**:
```python
g.add_edge("normalize",  "fan_out")
g.add_conditional_edges("fan_out", fan_out_node, ["item_search"])
```

---

### 2. `nodes/parse_pptx.py` — 업로드 파일 읽기 지원

**문제**: `parse_pptx_node`가 `state["pptx_paths"]`를 무시하고 `input/` 디렉토리를 하드코딩으로 읽었음.
UI에서 업로드한 PPTX가 실제로 파싱되지 않던 근본 원인.

**수정 전**:
```python
def parse_pptx_node(state):
    input_dir = Path(__file__).parent.parent.parent / "input"
    all_slides = load_all_pptx(input_dir)
```

**수정 후**:
```python
def parse_pptx_node(state):
    pptx_paths = state.get("pptx_paths") or []
    if pptx_paths:
        # 업로드된 파일 경로를 직접 파싱
        all_slides = []
        for p in pptx_paths:
            all_slides.extend(extract_slides(p))
    else:
        # 경로가 없으면 input/ 디렉토리 폴백 (CLI 실행 호환)
        input_dir = Path(__file__).parent.parent.parent / "input"
        all_slides = load_all_pptx(input_dir)
```

`extract_slides` import도 함께 추가:
```python
from loader import load_all_pptx, extract_slides
```

---

### 3. `runner.py` — 단계별 실행 함수 추가

Streamlit UI의 3단계 흐름을 지원하기 위해 파이프라인을 단계별로 분리 실행할 수 있는 함수 추가.

| 함수 | 역할 |
|------|------|
| `save_uploads(uploaded_files)` | Streamlit UploadedFile → 임시 파일 저장, 경로 반환 |
| `step1_parse(pptx_paths)` | PPTX → `change_points` 추출 (GPT-4o) |
| `step2_normalize(change_points)` | 부품명 정규화 (GPT-4o) |
| `step3_search_and_rank(change_points)` | Neo4j 검색 + LLM 유사도 랭킹 → `search_results` 반환 |

---

### 4. `app.py` — Streamlit 3단계 UI 전면 재작성

기존 단일 화면 UI를 **3단계 스텝 구조**로 재작성.

#### STEP 1 — PPTX 업로드 & 파싱
- 복수 PPTX 파일 업로드 (`st.file_uploader`)
- **파싱 시작** 버튼 클릭 → `st.status`로 진행 상황 표시
- GPT-4o가 변경점 자동 추출 후 STEP 2로 전환

#### STEP 2 — 파싱 결과 확인 / 편집
- 추출된 `change_points`를 `st.data_editor`로 표시
- 컬럼 직접 수정, 행 삭제 가능
- **부품명 재정규화** 버튼: 편집된 부품명 기준으로 `canonical_part` / `aliases` 재생성
- **이력 검색 시작** 버튼: Neo4j 검색 + LLM 랭킹 실행 후 STEP 3으로 전환

#### STEP 3 — 유사 이력 후보 선정
- PPTX 파일별 탭 분리
- 각 변경점마다 LLM 추천 후보를 카드 형태로 표시
- 체크박스로 참고할 후보 선택
- **LLM 추천 0건인 경우**: Neo4j 원본 후보풀을 폴백으로 직접 표시 (주황 경고 배너)
- 하단 **선택 확정** 버튼으로 선택 내역 최종 확인

#### 기타 UI 개선
- 사이드바 스텝 네비게이터 (현재 단계 하이라이트)
- **처음부터 다시** 버튼으로 전체 초기화
- 후보 카드: rank 배지, Level 배지, 분류(New/Change/Delete) 배지, 상세 expander

---

### 5. `requirements.txt` — 패키지 추가

```
streamlit>=1.35.0
pandas>=2.0.0
```

---

## 파이프라인 전체 흐름

```
[UI: STEP 1]
  PPTX 업로드 (Streamlit file_uploader)
      ↓ save_uploads() → 임시 파일 저장
  step1_parse(pptx_paths)
      ↓ parse_pptx_node: extract_slides() → GPT-4o → change_points

[UI: STEP 2]
  change_points 테이블 확인 / 편집
      ↓ (선택) step2_normalize(): GPT-4o 부품명 정규화
  이력 검색 시작
      ↓ step3_search_and_rank()

[LangGraph 내부]
  fan_out_node → Send() 병렬 분기
      ↓ (각 change_point마다)
  item_search_node: Neo4j Cypher 검색 (최대 3회 retry)
      ↓
  collect_node: 집계
      ↓
  rank_candidates_node: GPT-4o 유사도 판단 → 상위 5건

[UI: STEP 3]
  search_results 카드 표시
  - LLM 추천 있음: ranked 후보 표시
  - LLM 추천 없음: candidates 원본 풀 폴백 표시
  체크박스로 후보 선정 → 선택 확정
```

---

## 실행 방법

```bash
cd pptx_bom_search

# 의존성 설치
pip install -r requirements.txt

# .env 설정 (OPENAI_API_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
cp .env.example .env

# UI 실행
python3 -m streamlit run app.py --server.port 8501
# → http://localhost:8501
```

---

## 미구현 (STUB) 노드

| 노드 | 파일 | 예정 기능 |
|------|------|----------|
| `human_review` | `nodes/human_review.py` | `langgraph.types.interrupt()`로 교체, 사용자 후보 선택 반영 |
| `merge_selections` | `nodes/merge_selections.py` | `human_selections` → `bom_updates` 변환 |
| `write_bom` | `nodes/write_bom.py` | `openpyxl`로 `base_bom.xlsx`에 변경사항 반영 |
