# Graph RAG (LlamaIndex KG + Neo4j) 셋업 가이드

이 문서는 [docs/python_logic_data_flow_analysis.md](python_logic_data_flow_analysis.md)에서 지적된 검색 약점(8.2 텍스트 포맷 의존, 8.6 recall 보호로 인한 오탐)을 해소하기 위해 도입한 Graph RAG 모듈의 설치/실행 절차다.

## 1. 사전 준비

### 1.1 Docker (Neo4j 컨테이너)

```powershell
# 최초 1회: Neo4j 컨테이너 기동 (백그라운드)
docker compose -f docker/docker-compose.neo4j.yml up -d

# 상태 확인
docker compose -f docker/docker-compose.neo4j.yml ps

# Neo4j Browser 접속
# http://localhost:7474  (user: neo4j / pw: lgbom-poc-2026)
```

볼륨은 `.neo4j_data`, `.neo4j_logs`, `.neo4j_import`, `.neo4j_plugins`에 영구 저장된다. 컨테이너를 내려도 데이터는 유지된다.

### 1.2 의존성 설치

```powershell
uv sync
# 또는
pip install -e .
```

추가된 패키지 (`pyproject.toml`):
- `llama-index-core==0.12.34`
- `llama-index-graph-stores-neo4j==0.4.6`
- `llama-index-embeddings-azure-openai==0.3.2`
- `llama-index-llms-azure-openai==0.3.0`
- `neo4j==5.27.0`

### 1.3 환경변수

[.env.example](../.env.example) 참고. 핵심:

```
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_DEPLOYMENT_TEXT_EMBEDDING_3_SMALL=text-embedding-3-small
AZURE_DEPLOYMENT_GPT_4O_MINI=gpt-4o-mini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=lgbom-poc-2026
NEO4J_DB=neo4j
GRAPH_RAG_ENABLED=1
```

`GRAPH_RAG_ENABLED=0`으로 두면 기존 Chroma 기반 `run_search`로 폴백한다 (롤백 안전망).

### 1.4 (선택) Ollama 로컬 LLM 사용

기본은 Azure OpenAI gpt-4o-mini. 로컬 Ollama로 LLM만 갈아끼우려면(Embedding은 계속 Azure):

```powershell
# Ollama 설치 후 모델 받기
ollama pull llama3.1:8b

# Ollama 서버 기동 (보통 자동 시작됨)
ollama serve   # 이미 떠 있다면 생략

# .env 수정
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_LLM_MODEL=llama3.1:8b
OLLAMA_REQUEST_TIMEOUT=120
```

이 토글은 3곳 모두에 동시 적용된다 (별도 코드 변경 불필요):
- [graph_rag/llm_extract.py](../graph_rag/llm_extract.py) — RSN 추출
- [graph_rag/config.py](../graph_rag/config.py) — LlamaIndex Settings.llm
- [feedback_chat.py](../feedback_chat.py) — 검토 큐 LLM

Embedding(`text-embedding-3-small`, 1536d)은 변경되지 않으므로 Neo4j vector index와 기존 그래프 빌드는 그대로 유지된다.

원복: `LLM_PROVIDER=azure` (또는 키 자체를 지움).

**제약**: Ollama OpenAI 호환 API는 `response_format={"type":"json_object"}`를 v0.1.30+에서 지원. 출력이 깨지면 모델을 `qwen2.5:7b` 등으로 바꾸거나 prompt를 더 엄격하게 조정.

### 1.5 (선택) Ollama 로컬 임베딩 사용

기본은 Azure `text-embedding-3-small` (1536d). 로컬 Ollama 임베딩으로 갈아끼우려면:

```powershell
# 임베딩 모델 받기 (한국어 BOM에는 bge-m3 권장)
ollama pull bge-m3

# .env 추가/수정
EMBED_PROVIDER=ollama
OLLAMA_EMBED_MODEL=bge-m3
OLLAMA_EMBED_DIM=1024
```

**⚠ 차원 변경은 vector index 호환 깨짐. 반드시 재빌드 필요:**

```powershell
python scripts/build_graph_rag.py --reset --reset-vector
```

`--reset-vector` 옵션이 하는 일:
1. Neo4j의 `part_vec` / `assy_vec` / `rsn_vec` 인덱스 drop
2. Part / Assembly / ChangeReason 노드의 `.embedding` 속성 제거 (이전 차원 잔재 제거)
3. `ensure_schema()`가 새 차원으로 vector index 재생성
4. ingest 단계가 새 임베딩 모델로 재계산

차원 표:
| 모델 | 차원 | 비고 |
|---|---|---|
| `text-embedding-3-small` (Azure 기본) | 1536 | 영어/다국어 균형 |
| `bge-m3` | 1024 | 다국어, 한국어 강함 (권장) |
| `mxbai-embed-large` | 1024 | 영어 특화 |
| `nomic-embed-text` | 768 | 가볍고 빠름 |

원복: `EMBED_PROVIDER=azure` 후 `python scripts/build_graph_rag.py --reset --reset-vector` 재실행.

**legacy 영향 없음**: [build_rag.py](../build_rag.py) / [rag_client.py](../rag_client.py)의 Chroma 인덱싱은 항상 Azure 임베딩을 사용하므로 `GRAPH_RAG_ENABLED=0` 폴백 경로는 그대로 작동.

## 2. 그래프 빌드

```powershell
# 전체 빌드 (Base BOM + 과거 이력 + RSN LLM 추출)
python scripts/build_graph_rag.py --reset

# 빠른 검증용: 과거 이력 5개 파일만, LLM 추출 생략
python scripts/build_graph_rag.py --reset --limit 5 --no-llm

# 검증
python scripts/verify_graph_rag.py
```

빌드 단계:
1. Neo4j 연결 확인 (60초 헬스체크)
2. 스키마 ensure (constraint + vector index)
3. Base BOM Excel → Part/Assembly/PARENT_CHILD (LLM 0회)
4. data/history/*.xlsx → ChangeEvent/AFFECTS/Model/Region (LLM 0회)
5. RSN 자유 텍스트 → ChangeReason/Feature (gpt-4o-mini + sha256 캐시)
6. 카운트 요약

RSN 추출은 캐시되므로(`data/cache/llm_extract.sqlite`) 재실행 시 비용이 거의 없다.

## 3. Streamlit 실행

```powershell
streamlit run app.py
```

`app.py:run_search()`가 자동으로 `graph_rag.search()`에 위임한다. 호출부(`generate_proposals_from_docs`), proposals 스키마, Excel 출력은 무변경.

FAST MODE 시나리오:
1. 사이드바 ⚡ FAST MODE ON
2. 변경점: "도어에 카메라 추가"
3. 분석 시작 클릭
4. proposals 카드에 CAMERA 관련 부품이 CORE로, 도어 캐스케이드가 indirect로 잡히는지 확인

## 4. 비교 / 디버그

```powershell
# Graph RAG 결과만 (CLI)
python scripts/compare_search.py --change "도어에 카메라 추가" --model WSED7687M --grade B
```

Neo4j Browser에서 직접 쿼리:
```cypher
// 노드/관계 카운트
MATCH (n) RETURN labels(n) AS lbl, count(*) ORDER BY count(*) DESC;
MATCH ()-[r]->() RETURN type(r), count(*) ORDER BY count(*) DESC;

// CAMERA Feature를 가진 ChangeEvent 경로
MATCH path = (a:Assembly)<-[:PART_OF]-(p:Part)-[:CHANGED_BY]->(e:ChangeEvent)
              -[:TRIGGERED_BY]->(:ChangeReason)-[:REFERENCES]->(f:Feature {name:'CAMERA'})
WHERE toUpper(a.l1_key) CONTAINS 'DOOR'
RETURN path LIMIT 10;
```

## 5. 트러블슈팅

| 증상 | 원인/조치 |
|---|---|
| `Neo4j 연결 실패` | `docker compose ps`로 컨테이너 상태 확인. 헬스체크 30초 대기. 포트 7687 충돌 시 다른 컨테이너 종료 |
| `AOAI env 누락` | `.env` 파일 존재 + 5개 AZURE_* 키 확인 |
| `db.index.vector.queryNodes` 미지원 | Neo4j 5.11+ 필요. 5.26 권장 |
| ingestion 도중 OOM | `NEO4J_server_memory_heap_max__size` 상향 (docker-compose 환경변수) |
| RSN LLM 추출이 느림 | `--no-llm`으로 일단 구조만 빌드 후 별도 실행, 또는 `--limit`로 점진 빌드 |
| 한글 검색 누락 | `toUpper(...) CONTAINS toUpper(...)` 패턴 사용 (이미 retriever에 반영). 그래도 부족하면 `intent.OBJECT_ALIASES`에 별칭 추가 |
| 결과가 너무 많음/적음 | `graph_rag/retriever.py`의 `HOP1_MIN_RESULTS`, `TRAVERSAL_LIMIT`, `PRIMARY_DOC_CAP` 튜닝 |

## 6. 롤백

문제 시 즉시 폴백:
```powershell
$env:GRAPH_RAG_ENABLED="0"; streamlit run app.py
```

기존 `chroma_db/`, `chroma_structured/` 디스크는 보존되어 있다.

## 7. 다음 sprint 후보

이 sprint에서 제외된 항목:
- LangGraph 기반 Agent loop
- `generate_proposals_from_docs` 재작성 (현재는 텍스트 포맷으로 호환)
- 사이드바 디버그 UI에 graph metric 시각화 (`search_debug` 키에는 이미 노출됨)
- PPT 슬라이드 LLM 보강 (`graph_rag/extractors/ppt_extractor.py` 자리만 잡힘)
