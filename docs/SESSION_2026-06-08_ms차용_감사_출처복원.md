# 세션 기록 — 2026-06-08~09 · ms 차용 / 데이터 감사 / 출처 복원 / 표시 개선

> lg_data_pipeline(중첩 레포, branch `agentic-rag-l1-l4`)에서 진행. Master--BOM-AI-agent(ms)와의
> 비교·차용·통합과, 데이터 품질 감사·추출 보강·출처 복원·후보 표시 개선을 한 세션에 수행한 기록.
> 모든 코드 변경은 **가산(additive)·테스트·라이브 적용**. 미커밋 상태(커밋은 별도 요청 시).

---

## 0. TL;DR (핵심 결론)

- **검색 품질**: 깨끗한 비교에서 **ms가 lg를 압도**(ms_agent 0.90 vs lg_only 0.46, Claude 에이전트+심판). 단 ms의 강점은 "더 나은 알고리즘"이 아니라 **같은 소스 파일을 더 잘 추출**한 것 — ms 원본 23파일이 lg `data/raw`와 **23/23 전부 겹침**.
- **lg 병목 = coverage(변경 선례 데이터 양)**, 검색방식·전처리버그·BOM커버리지 아님. (perf 진단: lg rel@20=3.4 vs ms 9.9)
- **오염 발견·제거**: lg change_event의 56%(399/708)가 ms 데이터(`master_import` 스크래치)였음 → 제거 후 lg 고유 309로 복원. 이전 lg-vs-ms 비교는 전부 오염이었음.
- **ms 데이터 정식 도입(tagged)**: `source_project='ms'` 태깅 + **진짜 파일명 출처**로 재import. combined(lg+ms)가 lg 단독보다 높음.
- **측정 정직성**: Claude 단일심판 run 변동 ~±0.10. 큰 격차(ms vs lg_only 0.44)는 유효, 작은 차이는 노이즈.

---

## 1. 인프라 (이 세션에서 기동)

| 서비스 | 포트 | 용도 |
|---|---|---|
| Docker Desktop (엔진) | — | 컨테이너 호스트 (꺼져 있어 기동) |
| `lg_postgres` (pgvector pg16) | 5432 / lg_bom | lg 메인 DB |
| `etl_postgres` (pgvector pg16) | 15432 / etl_dev | ms etl_embed DB (비교 대상) |
| `bom-neo4j` (neo4j 5.26) | 7474/7687 | ms 새 UI(pptx_bom_search)용, 3,417노드 적재 |
| Ollama (네이티브) | 11434 | qwen3:30b(18GB) pull + `gpt-4o`/`gpt-4o-mini` 별칭(=qwen3:30b) |
| lg Streamlit UI | 8501 | `src/ui/main.py` |
| ms Streamlit UI | 8502 | `_ms_latest/pptx_bom_search/app.py` (worktree, 전용 venv `_ms_venv`) |

- lg pgvector 버전 **0.8.2** (sparsevec 지원 확인).
- ms UI는 OpenAI 키 없이 **로컬 qwen3:30b**로 구동(ChatOpenAI `model="gpt-4o"` → Ollama 별칭 + OpenAI 호환 `/v1` 엔드포인트, 코드 수정 0).

---

## 2. 코드 변경 — 마이그레이션 (가산, 라이브 적용)

| # | 파일 | 내용 | 차용 출처 |
|---|---|---|---|
| 003 | `003_raw_json_provenance.sql` | `dev_part_master.raw_json JSONB` — 원본 행 전체 보존 | ms `BomLine.rawJson` |
| 004 | `004_row_idempotency.sql` | `ingestion_log.rows_skipped` + 코드 멱등 스킵 | ms `ON CONFLICT DO NOTHING` |
| 005 | `005_dpm_unique.sql` | `uq_dpm_source UNIQUE(file_id, sheet_name, source_row)` | (행 멱등성 안전망) |
| 006 | `006_source_project.sql` | `dev_part_master/change_event.source_project` + 'lg' 백필 + 인덱스 | (tagged import 출처분리) |
| 007 | `007_reason_sparse.sql` | `change_event.reason_sparse sparsevec(250002)` + HNSW | ms `embedding_sparse` (BGE-M3 lexical) |

---

## 3. 코드 변경 — 모듈/로직

**차용 구현 (additive)**
- `src/db/models.py` — raw_json·source_project·reason_sparse 컬럼, `uq_dpm_source` __table_args__.
- `src/db/load.py` — `build_raw_json`/`raw_value`(원본 행 보존), `load_run` 행 멱등 스킵 + `rows_skipped`.
- `src/agent/repository/impact_graph.py` (신규) — `ImpactedNode`·`walk_impacted_ancestors`(거리/관계 등급, ms `IMPACTS_LINE{distance,impactType}` 등가, 온디맨드 CTE). `pipeline._escalate_parents`에 배선.
- `src/embed/sparse_embedder.py` (신규) — FlagEmbedding BGE-M3 lexical → `sparsevec` 리터럴(ms 인코딩 정합). `load_change_events.update_event_sparse_embeddings`.
- `src/db/retrieve.py` — `search_events`에 sparse 채널(dense+trgm+sparse RRF), `source_project` 필터, **`require_source` 가드**(출처 없는/합성 이벤트 검색 제외).
- `src/db/import_ms.py` (신규→개정) — ms etl_dev tagged import. **진짜 파일명별 적재**(ms source_files 조인)로 출처 복원. ms 비고유 source_row→합성 시퀀스, 원본은 raw_json 보존.
- `src/db/load_change_events.py` — change_event에 `source_project` 전파 + sparse 임베딩 함수.

**전처리 추출 보강 (config)**
- `config/form_signatures.yaml` — `v1_2_template_59`에 `^Best$`/`^Better$`/`^Good$` 시트 + max_col [55,70] → CIS/동유럽 Best/Better(66col) 분류.
- `config/column_dictionary.yaml` — 영어 헤더 별칭 `Changing Point`/`Changing Reason` 추가(검색-핵심 컬럼 드롭 방지).
- `config/normalization.yaml` — `change_type` on_fail `quarantine`→`set_null`('구분'이 부품카테고리(Assy/단품)인 양식에서 멀쩡한 행 통째 탈락하던 corpus 전역 top 사유 완화).

**후보 표시 개선 (UX 피드백)**
- `src/ui/agent_app.py` — 후보 에디터 컬럼 순서 **품번·부품명 선두**, "변경내역"→"**선례 변경점**" 라벨.
- `src/agent/orchestrator/rerank.py` — `_brief`를 `부품: {품번 부품명} | 변경점 | 변경사유`로(재랭킹 LLM도 변경점 인지).

**테스트**: 신규 `test_impact_graph`·`test_raw_json`·`test_import_ms`·`test_sparse_embedder`·`test_extraction_recovery` + `test_db` 멱등성 + 픽스처 고유키 수정. **전체 215 통과, mypy 클린.**

---

## 4. 데이터 작업 (라이브 DB)

1. **오염 제거** — `master_import`(ms etl_dev 2,860행을 `_migrate_master.py`가 lg로 복사, 무태그) 제거. change_event **708→309**(lg 고유). base_master_24 재적재 dup 204 정리(내용 동일 검증). dup 0.
2. **차용 적용** — migration 003–007 라이브. raw_json은 신규 적재만(기존 NULL).
3. **ms tagged import (2차, 진짜 출처)** — ms 2,860행을 **18개 진짜 파일명**으로 적재, `source_project='ms'`. change_event **ms 493건**(파일별 세분 그룹핑), dense+sparse 임베딩. 합성명 `ms_etl_import.xlsx` 잔존 **0**.
4. **추출 보강 재적재** — 전체 dry-run(rows_out 11,840) 후 commit+load. dev_part_master **9,754→11,555 (+1,801)**. 단 **change_event +4뿐**(회복분이 BOM행이지 변경선례 아님).
5. 최종 코퍼스: change_event **lg 319 + ms 493 = 812** (전부 실제 출처, dense+sparse).

---

## 5. 성능 측정 (전부 동일 방법: 10 PPT질의 · Claude 에이전트(확장+재랭킹) · Claude 블라인드 심판 · distinct-precision@5)

| 측정 | lg_only | combined(lg+ms) | ms | 비고 |
|---|---|---|---|---|
| 오염 Claude심판(초기) | — | (lg 0.88) | (ms 0.86) | 56% 오염 — 무효 |
| 깨끗 이중심판 qwen3:30b | 0.34 | — | 0.695 | 독립심판 ms 우세 |
| 깨끗 이중심판 Claude | 0.50 | — | 0.92 | " |
| combined+sparse(Claude) | 0.46 | **0.92** | 0.88(ms_only) | sparse가 ms_only 0.80→0.88 회복 |
| lg vs ms 풀(Claude) | 0.46 | 0.82 | 0.90 | ms 우세 |
| 재적재 후(Claude) | **0.46**(불변) | 0.72 | 0.80 | ms 불변인데 −0.10 → 심판 노이즈 |

**perf-drop 진단(coverage vs ranking)**: lg rel@20=**3.4** vs ms **9.9** → 주범 **coverage**(선례 자체가 적음). ranking 손실은 부차. 단 Q10(MWO Steam)은 lg가 ms 압도(데이터 있는 도메인) → 능력이 아닌 도메인 커버리지 문제.

---

## 6. 비교 분석 (워크플로우 산출)

- **방법·기술·성능**: ms=인메모리/Neo4j 트리+LLM 생성, lg=검색우선 Agentic RAG+HITL+결정론룰. 둘 다 Postgres+pgvector+BGE-M3+RRF 동일 토대.
- **데이터 형식 심판**: 8기준 LG 5·MS 2·무승부 1 → **depends**. lg=정규화 4테이블·Pydantic·무생성/출처 거버넌스, ms=rawJson 원본보존·IMPACTS_LINE 그래프 영향·dense+sparse.
- **전처리 비교**: lg가 전반 우위(quarantine 1급·7지표 게이트·probe guard). ms가 명확히 앞선 1개=행 멱등성(차용 완료).
- **데이터 감사**: 핵심 추출(변경점/사유) 충실·한글정상. 문제 — master 5파일 추출실패(분류 미스), raw_json 기존행 미채움, supplier 매핑 불일치, 변경밀도 5.6%.

---

## 7. 핵심 발견 (정정 포함)

1. **lg 코퍼스 56% ms 오염** → 제거. 이전 비교 무효.
2. **ms 원본 = lg가 가진 같은 파일** (23/23 겹침). "ms가 낫다" = **같은 파일을 더 잘 추출**. lg가 0행 실패한 사우디향·CIS Best/Better를 ms는 성공.
3. **lg 병목 = 추출/coverage**, 검색 알고리즘 아님. 재적재가 검색을 못 올린 건 회복분이 BOM행이라서.
4. **출처 가짜**: `ms_etl_import.xlsx`는 import 시 하드코딩한 합성명 → **진짜 파일명으로 복원** + 합성/무출처 검색 가드.

---

## 8. 남은 작업 / 다음 후보

- **file 8 (사우디향)**: 한 시트에 Good/Better/Best **3블록 적층(~26 변경행, 진짜 선례)** — **멀티블록 어댑터** 필요(유일한 미회복, 검색에 도움될 데이터).
- **추출 갭 조사**: lg가 ms보다 적게 뽑는 시트 변형(Good-1 STS, Master(Best) 등) 보강 → lg 고유 코퍼스 실질 증가(검색 올리는 진짜 레버).
- **커밋**: 이번 세션 일체 미커밋(마이그레이션 003–007, import_ms/sparse/impact_graph, 분류기 보강, 표시 개선 등).
- **정리**: 스크래치(`_*` gitignored)·worktree(`_ms_latest`)·Ollama 별칭(gpt-4o)·ms venv.
- 메모리: `memory/project_corpus_contamination_clean.md` 갱신됨.

---

## 9. 스크래치/산출 파일 (gitignored `_*`)

- 평가: `_api_eval*.py/.md/.log`(claude/dual/local/combined/vs), `_perf_drop.*`, `_reingest_*`.
- import/복구: `_run_import_ms*.py`, `_recover*.py/.log`, `_sparse_backfill.*`.
- 점검: `_agent_search_check*.py/.log`, `_probe_*.py`, `_ms_ui.log`, `_ms_deps_install.log`.
- ms 비교본 백업: `_api_eval*.prev/.contaminated/.before_reingest.md`.
