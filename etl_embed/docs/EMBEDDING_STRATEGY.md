# 임베딩 전략 — BGE-M3 하이브리드 적용 명세

작성일: 2026-05-26
대상: 비전공자 포함 프로젝트 관계자 전체

---

## 1. 한 줄 요약

`dev_part_master` 테이블에 비어 있던 **임베딩 벡터(embedding_dense)** 를 채우는 작업을 했는데, 단순히 흔한 모델을 쓰는 대신 최근(2024년) 논문에서 제안된 **BGE-M3 하이브리드** 방식을 도입했습니다. 이를 통해 의미 검색(예: "삭제된 부품")과 정확 검색(예: 부품번호 `AEC74157606`)을 모두 잘 잡을 수 있게 했습니다.

---

## 2. 이 작업이 왜 필요했나

### 2.1 우리가 만들고 있는 것

이 프로젝트는 한국어/영어가 섞인 가전 부품 변경 이력 엑셀 파일들을 PostgreSQL DB에 모아 저장해 두고, 나중에 **AI가 자연어로 검색**할 수 있게 만드는 RAG(Retrieval-Augmented Generation) 시스템의 기반입니다.

예시 질문 같은 것들을 받을 수 있어야 합니다.

- "호주향 모델에서 컨트롤러 색상이 바뀐 부품들 알려줘"
- "삭제된 부품 중에 금형 개발이 있었던 것"
- "AAA30802494가 어떤 모델에 쓰였지?"

### 2.2 왜 단순한 SQL로는 부족한가

SQL 검색은 단어가 정확히 일치해야 합니다. 사용자가 "색깔 변경"이라고 검색해도, DB에 "컬러 변경", "Color change", "도장 색상 수정"이라고 적혀 있으면 못 찾습니다. 이걸 해결하려면 **임베딩(embedding)** 이라는 기술이 필요합니다.

### 2.3 임베딩이란

문장을 **숫자 벡터**(예: 1024개의 숫자)로 바꾸는 기술입니다. 의미가 비슷한 문장은 벡터끼리도 가까이 위치합니다.

```
"색깔 변경"     → [0.12, -0.45, 0.78, ...]  (1024개 숫자)
"컬러 수정"     → [0.11, -0.43, 0.79, ...]  (위와 매우 비슷)
"부품번호 AAA"  → [0.92, 0.31, -0.05, ...]  (전혀 다른 위치)
```

검색할 때 사용자 질문도 같은 방식으로 벡터로 바꿔서, **가장 가까운 벡터를 가진 DB 행**을 가져옵니다. 이게 우리가 `embedding_dense` 컬럼에 채워야 했던 것입니다.

### 2.4 이전 상태

- `embedding_text` 컬럼: 임베딩의 **입력이 될 자기서술형 문장**은 이미 준비되어 있었음
  - 예: `"지역: 호주 | 모델: WSED7613B → WSED7613C | 부품번호: AAA30802494 | 부품명: Control PCB | 변경점: LED 추가 | 변경사유: 사용자 요구"`
- `embedding_dense` 컬럼: **비어 있었음 (NULL)** ← 이번 작업의 대상

---

## 3. 왜 "흔한 방법"이 부족했는가

가장 쉬운 방법은 Ollama 같은 도구로 일반 임베딩 모델(예: nomic-embed)을 돌려 벡터를 채우는 것입니다. 하지만 우리 데이터에는 **두 가지 성격이 섞여 있습니다.**

| 성격 | 예시 | 어떤 검색이 필요한가 |
|------|------|----------------------|
| 의미적 텍스트 | "컨트롤러 LED 색상 변경", "삭제된 부품" | **의미 검색** (뜻이 비슷하면 매칭) |
| 정확한 코드 | `AEC74157606`, `WSED7613B` | **정확 매칭** (한 글자라도 다르면 다른 부품) |

일반 임베딩 모델은 첫 번째에는 강하지만, **부품번호처럼 의미가 없는 코드 문자열에는 약합니다.** 부품번호 검색이 잘 안 된다면 RAG 시스템의 신뢰도가 크게 떨어집니다.

---

## 4. 선택한 전략 — BGE-M3 하이브리드

### 4.1 검토한 후보들

최근 2년(2024–2025) 논문/모델 흐름에서 4가지 후보를 비교했습니다.

| 후보 | 핵심 아이디어 | 장점 | 단점 |
|------|---------------|------|------|
| **BGE-M3** (Chen et al., 2024) | 한 모델이 의미 벡터 + 키워드 가중치 동시 생성 | 한국어 강함, 우리 스키마(1024차원)와 정확히 맞음, 부품번호 매칭에 강함 | 저장 컬럼 1개 추가 필요 |
| Qwen3-Embedding (2025) | 최신 MTEB 1위급, instruction 방식 | 가장 단순, 스키마 변경 0 | 부품번호 약점 그대로 |
| BGE-M3 + 도메인 fine-tune | 우리 데이터로 추가 학습 | 품질 최대 | 1–2일 추가 작업, 합성 데이터 필요 |
| Late Chunking (Jina, 2024) | 긴 문서를 통째로 보고 청크별 벡터 추출 | 긴 문서 RAG에 강함 | 우리 데이터는 행당 짧아서 효과 없음 |

### 4.2 선택: **BGE-M3 하이브리드**

이유:
- **이미 설계된 `vector(1024)` 스키마와 정확히 일치** → 기존 코드 변경 최소화
- **부품번호·모델코드의 정확 매칭 약점을 자체적으로 보완** (sparse 벡터를 동시에 만들어 줌)
- **한국어/영어 혼합 데이터에 강함** (XLM-RoBERTa 기반)
- ROI(투자 대비 효과)가 가장 좋음. 도메인 fine-tune은 일단 v2로 미룸

### 4.3 BGE-M3가 만들어 주는 두 가지 벡터

BGE-M3는 문장 하나를 입력하면 **벡터 2종을 동시에** 출력합니다.

**1) Dense 벡터 (의미 벡터, 1024개 숫자)**
- 문장의 "뜻"을 압축한 숫자 묶음
- "색깔 변경"과 "컬러 수정"이 비슷한 위치에 놓임

**2) Sparse 벡터 (키워드 가중치, 25만개 슬롯 중 일부만 채움)**
- 문장에 등장한 단어/토큰별 중요도
- "AEC74157606"이라는 부품번호 토큰은 한 슬롯에 큰 값이 박힘
- 다른 행에도 같은 부품번호가 있으면 정확히 매칭됨

이 두 벡터를 동시에 저장해 놓으면, 검색할 때 **둘 다 활용**해서 의미와 정확 매칭을 함께 잡을 수 있습니다.

### 4.4 두 결과를 합치는 방법 — RRF

검색 시 dense로 한 번, sparse로 한 번 — 총 두 번 검색합니다. 두 결과를 어떻게 합칠까요?

**RRF (Reciprocal Rank Fusion, Cormack et al., 2009)** 이라는 단순하면서도 매우 효과적인 방식을 씁니다.

```
각 후보 행의 최종 점수 = (1 / (60 + dense 검색 순위)) + (1 / (60 + sparse 검색 순위))
```

- 두 검색에서 모두 상위면 점수 높음
- 한쪽만 상위면 중간
- 점수 스케일이 달라도(코사인은 0~1, 내적은 0~수십) **순위만 사용**하므로 정규화가 필요 없음

---

## 5. 실제 변경된 파일

### 5.1 DB 스키마
**파일:** [schema_postgres.sql](../schema_postgres.sql)

- `embedding_sparse sparsevec(250002)` 컬럼 추가
  - pgvector 0.7+ 의 sparsevec 타입 사용
  - 25만 슬롯 중 실제 채워진 것만 저장(공간 효율적)
- 멱등 마이그레이션 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 도 추가 (이미 만들어진 테이블에도 자동 적용)
- sparse 벡터용 HNSW 인덱스 추가 (빠른 검색용)

### 5.2 파이썬 의존성
**파일:** [pyproject.toml](../pyproject.toml)

- `embed` optional 그룹 추가: `FlagEmbedding`, `numpy`
- torch는 GPU 환경마다 달라서 별도 설치

### 5.3 임베딩 인코더
**새 파일:** [parsers/embedder.py](../parsers/embedder.py)

- BGE-M3 모델을 감싸는 단순한 래퍼 클래스
- 첫 호출 시 모델 로드 (lazy load)
- 텍스트 리스트 → (dense 벡터, sparse 가중치 딕셔너리) 변환
- PostgreSQL의 `vector` / `sparsevec` 리터럴 문자열로 직렬화하는 헬퍼

### 5.4 백필 스크립트
**새 파일:** [parsers/backfill_embeddings.py](../parsers/backfill_embeddings.py)

DB에서 임베딩이 비어 있는 행을 가져와서 BGE-M3로 인코딩한 뒤 다시 저장하는 스크립트입니다.

- `embedding_text`가 있고 `embedding_dense`가 NULL인 행만 선택 (재실행 안전)
- DB에서 청크 단위(기본 500행)로 가져옴 (메모리 효율)
- GPU에서 배치 인코딩 (기본 16, GPU 좋으면 32–64 권장)
- 진행률·예상 완료 시간 실시간 출력

### 5.5 하이브리드 검색
**새 파일:** [parsers/retrieve.py](../parsers/retrieve.py)

사용자 질문 → BGE-M3로 dense+sparse 벡터 → SQL 한 번에 두 검색을 RRF로 합쳐 가져옴.

- `--changes-only` 옵션: 변경 이력 행만 검색 (RAG 주 타겟)
- `--region` 옵션: 지역 사전 필터

### 5.6 README
**파일:** [README.md](../README.md) — 설치/실행 방법 섹션 추가

---

## 6. 사용 방법

### 6.1 설치

```powershell
# 기본 의존성 + 임베딩용 추가 패키지
uv sync --extra embed

# torch는 GPU 종류에 맞춰서 (예: CUDA 12.1)
uv pip install torch --index-url https://download.pytorch.org/whl/cu121
```

### 6.2 스키마 적용

```powershell
uv run python run.py
```

기존 ETL 실행과 동일. 내부적으로 `ALTER TABLE`이 자동 실행됩니다.

### 6.3 임베딩 백필

```powershell
# 비어 있는 행만 채움 (기본)
uv run python -m parsers.backfill_embeddings

# 전체 재계산 / 배치 크기 조정
uv run python -m parsers.backfill_embeddings --all --batch 32
```

진행 상황 예시:

```
[embed] pending rows: 2860
[embed] warming up BGE-M3 ...
[embed] model ready (12.3s)
[embed]   500/2860 ( 17.5%) encode= 8.42s db=0.31s rate=  57.3/s ETA= 0.7min
[embed]  1000/2860 ( 35.0%) ...
```

### 6.4 검색 실행

```powershell
# 의미 검색
uv run python -m parsers.retrieve "컨트롤러 LED 색상 변경" --changes-only

# 부품번호 정확 검색
uv run python -m parsers.retrieve "AAA30802494" -k 5

# 지역 필터
uv run python -m parsers.retrieve "삭제된 부품" --region 호주
```

---

## 7. 동작 확인 체크리스트

- [ ] Docker 컨테이너의 pgvector 버전이 0.7 이상인지 확인 (sparsevec 지원)
  ```powershell
  docker exec etl_postgres psql -U etl_user -d etl_dev -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
  ```
- [ ] `uv sync --extra embed` 후 FlagEmbedding과 torch가 설치되었는지
- [ ] GPU 사용 가능한지 (CPU도 동작하지만 매우 느림)
  ```python
  import torch; print(torch.cuda.is_available())
  ```
- [ ] 첫 백필 실행 시 모델 다운로드(약 2.2GB) 진행되는지
- [ ] 백필 완료 후 NULL 행이 없는지
  ```sql
  SELECT COUNT(*) FILTER (WHERE embedding_dense IS NULL) AS dense_null,
         COUNT(*) FILTER (WHERE embedding_sparse IS NULL) AS sparse_null,
         COUNT(*) AS total
  FROM dev_part_master;
  ```
- [ ] 검색 결과가 직관과 일치하는지 (변경사유 키워드 → 관련 행이 상위에)

---

## 8. 다음 단계 후보

### 우선순위 A — 검증 및 평가

**A1. 평가셋 구축 (반나절)**
- 실제 운영에서 나올 법한 질문 30–50개 작성 (도메인 전문가가 정답 행도 표시)
- 예: "사우디향에서 컨트롤러 PCB가 신규 등록된 사례", "AAA30802494가 어느 모델에 들어갔는지"
- 이게 있어야 후속 개선(B, C, D)의 효과를 측정할 수 있음

**A2. RRF 파라미터 / TopK 튜닝**
- 현재 `rrf_k=60`, `topk_each=50` 은 일반 기본값
- 평가셋 위에서 dense-only / sparse-only / hybrid 를 비교해 가중치 또는 k값 조정

### 우선순위 B — 검색 품질 추가 개선

**B1. Cross-encoder Re-ranking (1일)**
- 하이브리드 검색으로 상위 50개 가져온 뒤, 더 비싼 cross-encoder(예: `BAAI/bge-reranker-v2-m3`)로 재정렬
- top-10의 품질을 크게 끌어올릴 수 있음

**B2. 도메인 적응 fine-tune (2–3일)**
- 우리 `embedding_text` 행에서 LLM(GPT-4o 또는 Claude)으로 **합성 질문**을 만들어 (질문, 정답 본문) 페어 수만 개 생성
- BGE-M3를 이 데이터로 contrastive 학습 → 도메인 어휘에 특화
- 참고 논문: Promptagator (Dai et al., 2022), GPL (Wang et al., 2022), "Improving Text Embeddings with LLMs" (Wang et al., 2024)

### 우선순위 C — Agent 레이어

**C1. Agent Tool 인터페이스 정의**
- `search_parts_semantic(query, filters)` — 하이브리드 검색
- `search_parts_sql(filters)` — 정형 조건 검색
- `get_part_detail(doc_id)` — 단건 상세
- `trace_source(doc_id)` — 원본 파일/시트/행 추적

**C2. Agent 시스템 프롬프트 작성**
- 언제 의미 검색을 쓰고 언제 SQL을 쓸지 판단 규칙
- 답변에 출처(파일명, 시트명, 원본 행 번호) 표시 의무화

**C3. Agent SDK 선택 및 연결**
- Claude Agent SDK, LangGraph, LlamaIndex 중 선택

### 우선순위 D — 데이터 확장

**D1. 다른 양식 테이블 분리** (시험, 등급심의, DQMS, 비교단가 등)

**D2. 원본 시트 staging 테이블** (`raw_sheet_rows`)
- 파싱 실패한 행도 일단 저장해 두면 나중에 LLM으로 구조화 추출 가능

**D3. openpyxl 실패 파일 복구**
- 현재 1개 파일이 내부 XML 문제로 열리지 않음 → libreoffice headless 변환 또는 xlsx 수리 라이브러리 사용

---

## 9. 핵심 용어 정리

| 용어 | 뜻 |
|------|----|
| **임베딩 (embedding)** | 문장을 숫자 벡터로 바꾼 것. 의미가 가까우면 벡터도 가깝다. |
| **Dense 벡터** | 모든 자리에 값이 채워진 벡터. 의미를 압축해서 담음. (1024개 숫자) |
| **Sparse 벡터** | 대부분의 자리가 0인 벡터. 키워드별 가중치를 담음. (25만 슬롯 중 일부만) |
| **pgvector** | PostgreSQL 확장. 벡터 컬럼·검색을 지원. |
| **HNSW** | 빠른 근사 최근접 이웃 검색 인덱스. |
| **RAG** | 검색해서 가져온 내용을 LLM에 넣어 답변하게 하는 방식. |
| **RRF** | 두 개의 검색 결과를 순위 기반으로 합치는 방법. |
| **Cross-encoder Re-ranking** | 1차 검색 결과를 더 정밀한 모델로 다시 정렬. |
| **Fine-tune** | 사전 학습된 모델을 우리 데이터로 추가 학습. |

---

## 10. 참고 자료

- BGE-M3 논문: Chen et al., 2024 — "M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation"
- BGE-M3 모델 카드: https://huggingface.co/BAAI/bge-m3
- Reciprocal Rank Fusion: Cormack, Clarke, Buettcher, 2009 — "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods"
- pgvector sparsevec: https://github.com/pgvector/pgvector#sparse-vectors
- LLM 기반 임베딩 합성 학습: Wang et al., 2024 — "Improving Text Embeddings with Large Language Models"
