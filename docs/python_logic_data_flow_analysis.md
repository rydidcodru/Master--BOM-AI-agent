# Python 로직 의도 및 데이터 흐름 분석

분석 대상 파일:

- `app.py`
- `build_rag.py`
- `doc_packaging.py`
- `rag_client.py`
- `chatbot_flow.py`
- `feedback_chat.py`
- `enrich.py`

## 1. 전체 의도

이 프로젝트는 **기존 개발부품 Master/BOM 이력과 현재 Base BOM, 심의회 PPT의 변경점**을 조합해서, 신규 모델에 필요한 변경부품 리스트와 개발부품 마스터 초안을 자동 생성하는 Streamlit 앱이다.

핵심 목표는 다음 흐름으로 보인다.

1. 과거 개발부품 Master Excel들을 Chroma 벡터 DB에 넣는다.
2. 사용자가 Base BOM Excel과 심의회 PPT를 업로드한다.
3. PPT에서 신규 모델, 개발등급, 출시 국가, 정격, 변경점을 추출한다.
4. 변경점 텍스트를 기준으로 과거 이력 DB와 현재 Base BOM 구조를 검색한다.
5. 검색된 과거 변경 이력을 부품 후보로 파싱한다.
6. Base BOM과 대조해서 이미 있는 부품인지, 신규/참고품번/공용검토인지 판단한다.
7. 사용자가 화면과 피드백 챗봇으로 후보를 검토/수정한다.
8. 확정된 결과를 변경부품리스트 Excel과 개발부품 마스터 Excel로 내보낸다.

즉, 코드는 단순 챗봇이라기보다 **PPT 변경점 추출 + BOM 구조 인식 + 과거 변경 이력 RAG + 부품 후보 후처리 + Excel 산출물 생성**을 한 화면에 묶은 업무 자동화 도구다.

## 2. 큰 아키텍처

```text
과거 개발부품 Master Excel
        |
        v
build_rag.py
        |
        v
doc_packaging.py
        |
        v
Chroma: dev_part_master
        |
        v
rag_client.py

사용자 업로드: Base BOM Excel + 심의회 PPT
        |
        v
app.py
        |
        +--> PPT 파싱: extract_change_review_from_pptx_bytes()
        +--> Base BOM snapshot: make_base_snapshot()
        +--> 현재 BOM 구조화 DB: chroma_structured / bom_structured_v3
        +--> 검색: run_search()
        +--> 후보 생성: generate_proposals_from_docs()
        +--> 검토 UI + feedback_chat.py
        +--> enrich.py
        v
변경부품리스트 Excel / 개발부품 마스터 Excel
```

## 3. 모듈별 역할

### `build_rag.py`

과거 Excel 이력을 Chroma DB에 넣는 오프라인 인덱싱 스크립트다.

주요 흐름:

1. `.env`에서 Azure OpenAI 임베딩 설정을 읽는다.
2. `data/history` 폴더의 `.xlsx`, `.xlsm` 파일을 순회한다.
3. 각 시트마다 헤더 행을 자동 탐지한다.
4. `doc_packaging.make_index_docs_l1_chunks()`로 Excel을 검색 문서 단위로 변환한다.
5. 문서가 너무 길면 토큰 기준으로 분할한다.
6. Azure OpenAI embedding을 만든다.
7. Chroma 컬렉션 `dev_part_master`에 upsert한다.

의도는 과거 개발 변경 이력들을 “부품 단위 row” 그대로 넣지 않고, **L1 Assy 단위 문맥과 domain 단위로 묶어서 검색 품질을 높이는 것**이다.

### `doc_packaging.py`

과거 Master Excel을 RAG 검색용 문서로 포장하는 모듈이다.

핵심 로직:

- Excel 컬럼명을 동의어 기반으로 탐지한다.
- `level`, `base_pno`, `new_pno`, `desc`, `qty`, `change`, `reason`, `part_type` 등을 표준 필드로 읽는다.
- BOM Level 1이 나오면 하나의 chunk 경계로 본다.
- 각 L1 chunk 안의 부품을 전장/기구/인쇄/소모품/미분류 domain으로 나눈다.
- 각 domain별로 Chroma에 넣을 텍스트 문서를 만든다.

문서 텍스트는 대략 다음 형태다.

```text
[SRC] 파일명 | 시트명
[MODEL] 모델명
[GRADE] 등급
[DOMAIN] E
[L1] L1품번 | Desc=L1부품명
- L1:... > L2:... | Base=... New=... | Desc=... | CHG=... | RSN=...
```

이 포맷은 이후 `app.py`의 `generate_proposals_from_docs()`가 다시 파싱하는 사실상의 내부 프로토콜이다.

### `rag_client.py`

런타임에서 과거 이력 Chroma DB를 조회하는 얇은 클라이언트다.

역할:

- Azure OpenAI로 query embedding 생성
- Chroma `dev_part_master` 컬렉션 조회
- 결과를 `{id, dist, meta, text}` 형태로 반환

`app.py`는 이 모듈의 `retrieve_docs()`를 감싸서 필터 지원 여부에 따라 fallback query를 만드는 방식으로 사용한다.

### `chatbot_flow.py`

초기에는 채팅 기반 입력 UI로 설계된 모듈로 보인다.

수집하는 값:

- 개발등급
- 신규 모델명
- 변경점 목록
- 참고 모델
- Base BOM 업로드

현재 `app.py` 하단의 메인 UI는 별도 폼 기반 흐름으로 많이 대체되어 있지만, 이 모듈은 여전히 import되어 있고 FAST MODE 또는 기존 흐름의 잔재로 남아 있다.

### `feedback_chat.py`

추천 결과가 나온 뒤, 사용자가 자연어로 후보를 수정하도록 만든 플로팅 피드백 챗봇이다.

처리 가능한 의도:

- 제외
- 추가
- 공용검토/신규/참고품번 같은 sourcing 변경
- 일괄 확정
- 질의
- 재검색/재검토

데이터는 `st.session_state["proposals"]`를 직접 수정한다. 즉, 별도 DB에 저장하는 구조가 아니라 **현재 Streamlit 세션의 proposal 객체를 mutate하는 방식**이다.

또한 일부 후보에 대해 LLM을 호출해 “확인이 필요한 부품”을 골라 질문 큐를 만들려는 로직이 있다. 이 부분은 `OpenAI()` 기본 클라이언트와 `gpt-4o-mini`를 직접 사용한다.

### `enrich.py`

최종 변경부품리스트/개발부품마스터를 만들기 전에 빈 컬럼을 채우는 후처리 모듈이다.

주요 의도:

- 변경부품 후보 DataFrame의 빈 컬럼을 Base BOM 또는 참고 BOM에서 채운다.
- 품번 exact match를 최우선으로 한다.
- 비고/변경사유에서 `참고: 품번`을 추출해 참고품번 기반으로 보강한다.
- 품번이 없으면 부품명 exact match로 fallback한다.
- `_group_parent_pno`가 있으면 Base BOM의 상위 Assy chain을 삽입한다.

우선순위는 다음과 같다.

1. 현재 품번이 Base BOM에 있으면 Base BOM 정보 사용
2. 비고/변경사유의 참고품번이 Base BOM에 있으면 사용
3. 참고품번이 ref BOM 인덱스에 있으면 사용
4. 부품명으로 Base BOM 매칭

## 4. `app.py`의 핵심 데이터 흐름

`app.py`가 실제 앱의 중심이다. 역할이 매우 넓고, 여러 실험/패치가 누적된 형태다.

### 4.1 초기 설정

앱 시작 시 다음을 수행한다.

- Streamlit page config 설정
- `streamlit_float` 초기화
- `feedback_chat`, `chatbot_flow` reload/import
- FAST MODE 토글 설정
- session state 초기화

FAST MODE가 켜져 있으면 기본값이 자동 주입된다.

```text
dev_grade = B
target_model = KWS9D7687M
change_items = ["도어에 카메라 추가"]
base_bom.xlsx 자동 로드
```

개발 중 빠른 테스트를 위해 만든 우회 경로로 보인다.

### 4.2 Base BOM 업로드

사용자가 Base BOM Excel을 올리면 다음 흐름이 실행된다.

```text
uploaded_bom
  -> read_excel_auto_header()
  -> df_bom 정리
  -> st.session_state["base_df"]
  -> st.session_state["base_df_raw"]
  -> extract_model_code()
  -> st.session_state["bom_model"]
  -> make_base_snapshot()
  -> build_structured_docs_from_base()
  -> chroma_structured / bom_structured_v3 upsert
```

여기서 두 종류의 Base BOM 표현이 생긴다.

- `base_df`, `base_df_raw`: Excel 원본에 가까운 DataFrame
- `base_snapshot`: path, row, 품번/부품명 등을 추린 내부 구조
- `chroma_structured`: 현재 Base BOM을 검색용 문서로 만든 보조 Chroma DB

`base_snapshot`은 proposal 생성 시 구조 비교용으로 쓰려는 의도지만, 실제 핵심 매칭은 `generate_proposals_from_docs()` 안에서 `st.session_state["base_df"]`를 직접 다시 읽는 비중이 크다.

### 4.3 PPT 업로드 및 추출

PPT 업로드 후 `모델 정보 확인` 버튼을 누르면:

```text
pptx bytes
  -> extract_change_review_from_pptx_bytes()
  -> project_meta
  -> change_points_for_bom
  -> module_details
  -> st.session_state["ppt_extraction_result"]
```

PPT 파서는 다음을 한다.

- 슬라이드 텍스트 수집
- 테이블 셀 수집
- 이미지/첨부 존재 여부 수집
- 테이블을 whitelist 기준으로 분류
  - MAIN_CHANGE_TABLE
  - DETAIL_CHANGE_TABLE
  - RISK_TABLE
  - IGNORE
- 체크표/OX/PDR/ODR/NPI/Activity 성격의 테이블은 제외
- 프로젝트 메타와 변경점 후보를 추출

이후 사용자가 추출값을 확인/수정하고 `확인 후 입력값 반영`을 누르면:

```text
product_type
bom_model
target_model
dev_grade
target_country
rating
change_items
ppt_input_confirmed = True
```

가 session state에 저장된다.

### 4.4 분석 시작

분석 시작 버튼을 누르면 다음 순서로 실행된다.

```text
change_items + target_model + dev_grade
  -> build_related_parts_analysis_prompt()
  -> make_base_snapshot()
  -> build_structured_docs_from_base()
  -> run_search()
  -> generate_proposals_from_docs()
  -> merge_proposals_order_independent()
  -> st.session_state["proposals"]
```

`build_related_parts_analysis_prompt()`는 prompt를 만들지만, 현재 검색/제안 생성의 핵심은 LLM 호출이 아니라 rule/RAG 기반 로직이다.

### 4.5 검색: `run_search()`

`run_search()`는 변경점 텍스트를 먼저 intent로 바꾼다.

```text
"도어에 카메라 추가"
  -> target_object = DOOR
  -> action = ADD
  -> main_change_keyword = CAMERA
```

그 다음 두 DB를 검색한다.

1. Legacy Chroma: 과거 변경 이력 `dev_part_master`
2. Structured Chroma: 현재 Base BOM 구조 `bom_structured_v3`

검색은 처음엔 모델/등급 필터를 강하게 적용하고, 결과가 부족하면 단계적으로 완화한다.

```text
모델 + 등급 필터
  -> 부족하면 모델만
  -> 그래도 부족하면 필터 없음
```

이후 유사도 threshold도 점진적으로 낮춘다.

```text
0.30
  -> 부족하면 0.18
  -> 부족하면 0.12
  -> 그래도 부족하면 상위 결과 강제 보장
```

마지막으로 프로젝트 지역/국가 기준 필터를 적용한다. 단, 너무 많이 줄어들면 recall 보호를 위해 필터를 완화한다.

전체적으로 이 함수의 의도는 **정확도를 먼저 챙기되, 결과가 너무 없으면 자동으로 recall을 보강하는 검색 파이프라인**이다.

### 4.6 후보 생성: `generate_proposals_from_docs()`

이 함수가 부품 추천의 중심이다.

주요 단계:

1. 변경점에서 feature/target keyword 추출
2. RAG 문서 텍스트의 `[L1]`, `Base=`, `New=`, `Desc=`, `CHG=`, `RSN=` 라인을 파싱
3. action 판정
   - New만 있으면 ADD
   - Base와 New가 다르면 MODIFY
   - Base만 있으면 DELETE
   - 같지만 변경점 관련성이 있으면 MODIFY
4. 원자재/소모품 필터링
5. RSN 기반 tier 분류
   - CORE: 핵심 feature와 직접 관련
   - CORE_GROUP: 같은 RSN 묶음에서 CORE와 함께 등장
   - CASCADE: target object 영향권
   - EXCLUDE: 무관
6. 같은 문서/L1 안에서 누락된 EXCLUDE 일부 복구
7. 중복 제거
8. Base BOM과 매칭
   - `db_new_pno` 우선
   - `db_base_pno` fallback
   - Base에 있으면 `in_base=True`
   - Base에 없으면 신규/참고품번/공용검토 판단
9. 레벨/수량/유형 보강
10. Base BOM skeleton 순서대로 재정렬
11. L1 Assy 단위 proposal 생성

생성되는 proposal 구조는 대략 다음과 같다.

```json
{
  "proposal_id": "P-001",
  "status": "PENDING",
  "change_summary": "...",
  "target": {
    "main_object": "DOOR",
    "action": "ADD"
  },
  "lvl1": {
    "desc": "상위 Assy명",
    "part_no": "상위 Assy 품번",
    "in_base": true,
    "skip": false
  },
  "changed_parts": [],
  "indirect_parts": [],
  "existing_parts": [],
  "confidence": 0.75,
  "source_docs": [],
  "ref_models": []
}
```

`changed_parts`는 CORE 계열, `indirect_parts`는 CASCADE 계열로 볼 수 있다.

### 4.7 제안 검토 UI

`st.session_state["proposals"]`가 있으면 화면에 L1 Assy별 expander가 생긴다.

각 하위 부품은 `st.data_editor`에서 다음 값들을 보여준다.

- 변경유형
- 부품명
- 품번
- 레벨
- 수량
- 유형
- 변경사유
- 분류
- 비고
- 출처

변경유형 옵션:

- 추가
- 변경(시방)
- 삭제
- 제외
- 확인필요

`확인필요`가 남아 있으면 Excel 다운로드가 막힌다. 즉, 사용자가 ambiguous한 후보를 확정해야 산출물로 넘어가도록 설계되어 있다.

### 4.8 피드백 챗봇

플로팅 챗봇은 현재 proposal을 직접 수정한다.

예상 사용 방식:

```text
Shelf 제외해줘
Camera Module 공용으로 바꿔줘
Door 하위에 Gasket 추가
나머지 전부 추가로 확정
Door assy의 LED 다시 찾아줘
```

`pending_recheck`가 설정되면 `app.py`의 `process_pending_recheck_request()`가 다음 rerun에서 재검색을 수행하고, 기존 후보를 더 나은 대안으로 교체하려고 한다.

### 4.9 Excel 출력

확인필요가 없으면 다음 순서로 출력 데이터가 만들어진다.

```text
proposal_review_all
  -> all_rows
  -> _make_change_parts_list()
  -> enrich.enrich_change_parts()
  -> enrich.expand_with_assy_tree()
  -> ChangePartsList Excel
  -> _make_dev_parts_master()
  -> DevPartsMaster Excel
```

`_make_change_parts_list()`는 화면 검토 결과를 Base BOM 컬럼 양식에 맞춘다.

`enrich_change_parts()`는 빈칸을 Base BOM/ref BOM 기준으로 채운다.

`expand_with_assy_tree()`는 `_group_parent_pno` 기준으로 상위 Assy chain을 삽입한다.

`_make_dev_parts_master()`는 변경부품리스트를 개발부품 마스터 형식으로 재구성한다.

## 5. 주요 저장소와 상태값

### 파일/폴더

- `data/history`: 과거 개발부품 Master Excel 원천
- `data/uploads/base_bom.xlsx`: FAST MODE 기본 Base BOM
- `data/ref_boms`: 참고 BOM Excel
- `chroma_db`: 과거 이력 Chroma DB
- `chroma_structured`: 현재 Base BOM 구조화 Chroma DB
- `data/outputs`: 산출물 예시 저장 위치

### Chroma 컬렉션

- `dev_part_master`
  - 과거 개발 변경 이력
  - `build_rag.py`가 생성
  - `rag_client.py`가 조회

- `bom_structured_v3`
  - 사용자가 업로드한 현재 Base BOM을 구조화한 검색 DB
  - `app.py`가 런타임에 생성/upsert

### 핵심 `st.session_state`

- `base_df`: 필터/정리된 Base BOM
- `base_df_raw`: 원본에 가까운 Base BOM
- `base_snapshot`: Base BOM path snapshot
- `bom_model`: 기준 모델
- `target_model`: 신규 모델
- `dev_grade`: 개발 등급
- `event`: CP/DV/PV/PreMP
- `product_type`: 제품군
- `target_country`: 출시 국가
- `rating`: 정격
- `change_items`: 변경점 목록
- `ppt_extraction_result`: PPT 추출 원본 결과
- `ppt_input_confirmed`: PPT 추출값 반영 여부
- `proposals`: 추천 후보 카드
- `proposal_review_all`: 화면 편집 결과
- `pending_recheck`: 챗봇 재검토 요청
- `feedback_log`: 피드백 이력

## 6. 로직이 이렇게 구성된 이유 추정

### 6.1 LLM보다 규칙/RAG 중심

코드는 LLM에게 전체 결정을 맡기기보다, 과거 Master 이력을 벡터 검색하고 정규식/규칙으로 파싱한다. 업무상 부품번호, BOM Level, 변경사유, 품번 관계가 중요하기 때문에, 자유 생성보다 **근거 문서에서 후보를 뽑고 후처리로 안정화하는 방향**을 택한 것으로 보인다.

### 6.2 과거 이력과 현재 BOM을 분리

과거 변경 이력은 `dev_part_master`, 현재 Base BOM은 `bom_structured_v3`로 나뉜다. 과거 DB는 “무슨 변경 때 어떤 부품이 움직였는가”를 찾는 용도이고, 현재 BOM DB는 “지금 모델 구조에서 어디에 적용해야 하는가”를 보조하는 용도다.

### 6.3 정밀도와 재현성을 위해 많은 중복 제거/병합

`merge_proposals_order_independent()`, `_merge_part_lists()`, `post_retrieval_dedup()`, `_merge_same_part_rows()` 등이 반복적으로 등장한다. 이는 변경점 순서나 검색 결과 순서에 따라 결과가 흔들리는 문제를 줄이려는 의도다.

### 6.4 사용자의 최종 확정을 필수로 둠

`확인필요` 상태가 남으면 Excel 추출을 막는다. 자동 추천이 틀릴 수 있음을 인정하고, 최종 산출물은 사람이 확정하도록 만든 구조다.

### 6.5 업무 양식 보존

Excel 출력 쪽은 Base BOM의 실제 컬럼명을 최대한 유지하고, 개발부품 마스터의 병합 셀/색상/섹션 헤더까지 만든다. 결과적으로 이 앱의 진짜 목적은 “예쁜 추천 화면”보다 **현업 양식에 바로 붙는 파일 생성**에 있다.

## 7. 데이터 흐름 상세 요약

### 사전 인덱싱 흐름

```text
data/history/*.xlsx
  -> build_rag.py
  -> read_sheet_safely()
  -> doc_packaging.make_index_docs_l1_chunks()
  -> expand_docs_by_tokens()
  -> Azure OpenAI embeddings
  -> Chroma dev_part_master
```

### 런타임 입력 흐름

```text
Base BOM upload
  -> read_excel_auto_header()
  -> base_df/base_df_raw
  -> bom_model 추출
  -> base_snapshot
  -> structured docs
  -> Chroma bom_structured_v3

PPT upload
  -> extract_change_review_from_pptx_bytes()
  -> project_meta/change_points/module_details
  -> 사용자 확인/수정
  -> change_items/target_model/dev_grade/region 관련 값
```

### 추천 생성 흐름

```text
change_items
  -> parse_change_intent()
  -> run_search()
      -> legacy Chroma 검색
      -> structured Chroma 검색
      -> 유사도/지역 필터
  -> generate_proposals_from_docs()
      -> 문서 라인 파싱
      -> action/tier 판정
      -> Base BOM 매칭
      -> sourcing 분류
      -> L1별 proposal 생성
  -> merge_proposals_order_independent()
  -> st.session_state["proposals"]
```

### 검토/출력 흐름

```text
proposals
  -> _build_proposal_df()
  -> st.data_editor()
  -> proposal_review_all
  -> 확인필요 0건인지 검사
  -> _make_change_parts_list()
  -> enrich_change_parts()
  -> expand_with_assy_tree()
  -> ChangePartsList Excel
  -> _make_dev_parts_master()
  -> DevPartsMaster Excel
```

## 8. 눈에 띄는 위험 지점

### 8.1 `app.py`가 너무 많은 책임을 가진다

`app.py` 하나에 UI, PPT 파싱, Chroma 관리, 검색, 후보 생성, 검토 UI, Excel 생성이 모두 들어 있다. 현재 기능을 이해하거나 고치려면 한 파일의 여러 thousand lines를 따라가야 한다.

분리 후보:

- `ppt_parser.py`
- `base_bom.py`
- `search_pipeline.py`
- `proposal_builder.py`
- `export_change_list.py`
- `export_dev_master.py`

### 8.2 내부 문서 포맷이 문자열 파싱에 강하게 의존한다

`doc_packaging.py`가 만든 텍스트 라인을 `app.py`가 정규식으로 다시 파싱한다.

예:

```text
- ... | Base=... New=... | Desc=... | CHG=... | RSN=...
```

이 포맷이 조금만 바뀌어도 proposal 생성이 깨질 수 있다. 가능하면 Chroma metadata에 구조화 JSON을 넣고, 텍스트는 검색용으로만 쓰는 편이 더 안정적이다.

### 8.3 인코딩/깨진 문자열 흔적

일부 파일 앞부분과 주석/상수에 깨진 문자열이 보인다. 실제 실행 환경에서는 UTF-8로 상당 부분 복구되지만, 소스 안에 깨진 한글 키워드가 남아 있으면 keyword matching 품질에 영향을 줄 수 있다.

특히 다음 영역은 점검이 필요하다.

- `chatbot_flow.py`의 사용자 문구/skip keyword
- `feedback_chat.py`의 intent keyword
- `doc_packaging.py`의 한국어 column synonym/domain keyword
- `enrich.py`의 column synonym

### 8.4 session state 직접 mutation이 많다

`feedback_chat.py`, `app.py`가 모두 `st.session_state["proposals"]`를 직접 수정한다. 빠르게 만들기는 좋지만, 어떤 액션이 어떤 값을 바꿨는지 추적하기 어렵다.

권장 방향:

- proposal 변경 함수들을 한 모듈에 모으기
- 변경 전/후 diff를 기록하기
- feedback action을 event log로 저장한 뒤 reducer처럼 적용하기

### 8.5 과거 로직과 새 로직이 공존한다

`chatbot_flow.py` 기반 채팅 입력 흐름과 `app.py` 하단 폼/PPT 기반 흐름이 함께 남아 있다. FAST MODE, reload, 중복 import, 유사 함수들이 많아 전체 실행 경로가 흐릿하다.

### 8.6 검색 recall 보호 로직이 많아 오탐 가능성도 커진다

검색 결과가 부족하면 필터와 threshold를 계속 완화한다. 결과가 없는 것보다는 낫지만, 무관 문서가 섞일 위험이 있다.

현재는 이후 RSN/tier 필터와 사용자 검토로 막는 구조다.

## 9. 리팩토링 우선순위 제안

1. `app.py`에서 `generate_proposals_from_docs()`를 별도 모듈로 분리한다.
2. `doc_packaging.py` 출력 포맷을 구조화 JSON 중심으로 바꾼다.
3. `feedback_chat.py`의 proposal mutation을 명시적 action 함수로 모은다.
4. 깨진 한글 keyword/synonym을 UTF-8 기준으로 정리한다.
5. `run_search()`의 relaxed search 단계별 결과를 UI 디버그로 더 명확히 보여준다.
6. Excel export 로직을 `exporters/` 계층으로 분리한다.

## 10. 한 줄 결론

이 코드는 “PPT 변경점과 Base BOM을 입력받아, 과거 개발부품 이력 RAG에서 유사 변경 사례를 찾고, Base BOM과 대조해 변경부품 후보를 만든 뒤, 사용자 검토를 거쳐 현업 Excel 양식으로 내보내는” 자동화 파이프라인이다. 다만 빠르게 기능을 덧붙인 흔적이 많아 `app.py`에 책임이 집중되어 있고, 문자열 포맷/세션 상태/깨진 키워드 의존도가 높은 것이 유지보수상의 핵심 리스크다.
