# BOM 변경 영향 분석 시스템 — 전체 흐름 정리 (현재 구현 기준)

> **이 문서의 용도**: 시스템이 *실제로 지금 어떻게 동작하는지*를 처음 보는 사람(또는 AI 어시스턴트)에게
> 그대로 붙여넣어 설명하기 위한 자기완결 문서입니다. "내가 상상한 흐름"과 "실제 구현된 흐름"을
> 맞춰보는 게 목적이라, 마지막 §10에 **흔한 오해 vs 실제** 체크리스트를 두었습니다.
> 기준 시점: 2026-06-12 (Phase 0~4 + v2 패치 P5~P8 반영).

---

## 1. 한 줄 요약

**심의회 PPT(또는 수동 입력)의 변경항목을 받아 → 변경내역+변경사유로 "과거 변경 이력"을 검색해
비슷한 선례 부품 세트를 제시하고 → 사람이 확정한 뒤에만 → 확정 부품의 BOM 하위 트리를 결정론으로
펼쳐 영향 부품·변경점을 출력**한다.

핵심 철학 3가지:
- **검색의 정답 인덱스는 "변경사유+변경내역"이다** (부품명/품번은 보조 채널로 추가).
- **사람 확정이 트리 전개보다 먼저다** (AI가 끝에 한 번에 다 하고 승인받는 방식 금지).
- **새 품번을 절대 만들지 않는다** (있는 건 검색으로 회수, 없으면 `<발번대기>`).

---

## 2. 두 개의 데이터 구조 (이걸 먼저 이해해야 흐름이 보임)

시스템은 성격이 다른 **두 종류의 데이터**를 분리해서 다룬다.

### 구조 B — 과거 "변경 이벤트" (검색 대상)
"예전에 이런 사유로 이 부품들을 이렇게 바꿨다"는 **이력 묶음**.

| 테이블 | 의미 |
|---|---|
| `change_event` | 한 변경사유 = 한 이벤트. **`reason_embedding`(= 변경내역+변경사유 임베딩)이 검색 인덱스의 정답.** 부품명은 이 임베딩에 안 들어감. |
| `change_line` | 그 이벤트로 **함께 바뀐 부품들**. part_name / base_pno / new_pno / changepoint(변경점) / classification(New/Change/Delete) 등. |

→ 검색은 이걸 뒤진다. "지금 이 변경과 비슷한 과거 변경이 있었나?"

### 구조 A — 현재 "BOM 트리" (하위 전개 대상)
"이 제품의 부품 구성은 이렇게 생겼다"는 **정적 트리(DAG)**.

| 테이블 | 의미 |
|---|---|
| `bom_edge` | (file_id, parent_pno, child_pno, bom_level) 엣지. 한 BOM 파일 = 한 트리 범위(file_id로 스코핑). |
| `dev_part_master` | 부품 마스터(품번↔부품명↔part_type 등). |

→ 확정된 부품을 시작점으로 이 트리를 펼친다(`walk_subtree`, 재귀 CTE, **LLM 0회**).

**즉, 검색(구조 B)과 트리 전개(구조 A)는 완전히 다른 데이터다.** 검색은 BOM 트리를 직접
뒤지지 않는다 — 과거 변경이력을 뒤진다.

---

## 3. 전체 흐름도

```
 [입력] 심의회 PPT(.pptx)  ─또는─  수동 텍스트(변경내역/사유)
        │
        ▼
 ① 표 파싱 (결정론, LLM 0)
        │  PPT → 변경항목[] (구분·부품명·변경내역·변경사유·모듈명)
        ▼
 ② 정형화 (intent_from_change, 결정론)                       ← LLM은 검색 보강(선택)
        │  · 검색 쿼리 = 변경내역+변경사유 (정답 인덱스용)
        │  · 부품명/품번 있으면 → "부품 보강 쿼리" 추가 (parts 채널용)
        │  · [DERIVE_CP] 사유에서 (대상·속성·행위) 슬롯 유도 → 정렬 쿼리 보강
        │
        ├──▶ ①.5 구조 스코프 S 구축  [SEARCH_INCLUDE_STRUCT, 기본 OFF]
        │      모듈명 + base_model → BOM 파일 해석 → 모듈 하위 트리 S
        │      ╌╌ bom_edge / dev_part_master  ←╌ **검색 보조용 read-only** (판정 전개 아님)
        ▼
 ③ 검색 (search_events → RRF 융합)
        │  4(+1) 채널을 RRF로 합침: dense(사유) + lexical + sparse + parts (+ structure)
        │  → 후보 = 과거 변경이벤트 묶음 N개 (각각 부품 라인 세트)
        │  · [P7.3] 같은 사유 dedup 시 부품 라인은 합집합 보존(중요 부품 누락 방지)
        │  · [P7.2] dense 최고<SEARCH_LOW_CONF면 low_confidence 배지 / RESCUE_SEARCH 구제검색
        │           / 사람이 키워드로 수동 보강(manual_added)
        ▼
 ④ 사람 확정 (HITL 게이트) ★여기서 멈춘다★
        │  채택/거절 + 변경점 지정 + 신규 P/No 입력 → "확정 닻"
        │  (확정 전에는 트리 전개·문서 생성 안 함 — 절대원칙)
        │
        ├──▶ ②.5 후보-BOM 매핑  [채택분만·채택 후 lazy, S 있을 때, 결정론] (P6)
        │      채택 이벤트 라인을 현재 BOM 하위 트리 S에 사상 → matched/ambiguous/add_proposal/dropped
        ├──▶ [P8] confirm_feedback 1행 기록 (운영=평가, 기록 전용·비차단)
        ▼
 ⑤ 하위 전개 + 영향 판정 (결정론, LLM 0)
        │  walk_subtree(확정 닻, 하위, 깊이≤4) → 부품별 룰 평가(impact_rules.yaml)
        │  · 호환성 깨지면만 상위(부모) 채반
        │  · [P5] 채택 이벤트의 '나머지 동반변경'은 체크리스트 "참고: 과거 동반 변경"
        │         정보행으로만 표시 (전사 finding 아님 — BOM 행으로 안 나감, CASCADE_INFO)
        ▼
 ⑥ 출력 조립
        │  16컬럼 트리 형식 / New BOM(xlsx) — 모든 행에 출처 [SRC], 신규=<발번대기>
        ▼
 [출력] 개발마스터 행 + New BOM + 체크리스트
```

---

## 4. 단계별 상세 (입력 / 처리 / 결정론·LLM / 게이트)

### ① 표 파싱 — `src/agent/ppt/extractor.py`
- **입력**: 심의회 PPT bytes. **처리**: 슬라이드/표 역할 분류 → 변경항목 추출(구분·부품명·변경내역·변경사유·모듈). 취소선 텍스트 제외, 모듈요약표에서 인과절로 사유 분리.
- **LLM**: 0회 (순수 python-pptx + 키워드 화이트리스트).

### ② 정형화 — `src/agent/intent/structurizer.py : intent_from_change`
- **검색 쿼리 구성**:
  - 기본: `변경내역 / 변경사유` 텍스트 (← 이게 정답 인덱스 `reason_embedding`과 맞는 청정 쿼리).
  - 부품명/품번이 있으면: **부품 보강 쿼리**(변경텍스트 + 부품명 + 품번) 추가 → parts 채널이 매칭.
  - **[DERIVE_CP, 기본 ON]**: 변경내역이 없고 사유만 있을 때, 사유에서 `(대상, 속성, 행위)` 슬롯을 유도(`derive.py`, 결정론 R0~R6 + 실패 시 LLM 2차)해 **정렬 쿼리 2개**(합성형·라벨형)를 추가. 게이트 off면 기존과 바이트 단위 동일.
- **base_model**: 검색어로 **안 쓴다**(항목마다 같아서 변별력 0). 표기 + ①.5 BOM 파일 해석 키로만 사용.
- **LLM**: 기본 0회(결정론). LLM_PROVIDER opt-in 시 쿼리 재작성·멀티쿼리 확장으로 보강(검색 recall↑), 환각 식별자는 sanitize.

### ①.5 구조 스코프 — `src/agent/orchestrator/structure_scope.py` **[SEARCH_INCLUDE_STRUCT, 기본 OFF]**
- **입력**: 모듈명 리스트 + base_model. **처리**: base_model → bom_edge 보유 BOM 파일 해석 → 얕은 레벨 노드에서 모듈명과 퍼지 매칭으로 닻 찾음 → `walk_subtree`로 하위 트리 S 구축 → `ScopeIndex`.
- **중요**: 이 BOM 조회는 **검색 보조용 read-only**다. 절대원칙의 "판정용 전개(⑤)"가 **아니다**.
- 실패(파일 미존재/모듈 미매칭/공집합)면 조용히 None → 기존 동작과 동일.
- **LLM**: 0회.

### ③ 검색 — `src/db/retrieve.py : search_events` + `orchestrator/orchestrate.py : propose_candidates`
- **단위**: 결과 1건 = 한 과거 변경이벤트(= 함께 바뀐 부품 세트). 부품 라인은 `lookup_lines_by_event`로 회수.
- **채널 RRF 융합** (§6 표 참고).
- **LLM**: 검색 자체는 0회. (opt-in 시 ②의 멀티쿼리/재랭킹만 LLM.)

### ②.5 후보-BOM 매핑 — `src/agent/mapping/mapper.py` **[S가 있을 때만, 결정론]**
- **입력**: 후보 이벤트의 과거 라인 세트 + `ScopeIndex`(①.5 인스턴스 재사용). **처리**: 라인별로 현재 BOM 트리 S에 사상.
- **판정(M1~M5, 첫 매치)**: pno 정확일치(matched) / 이름 canon 높음(matched) / 애매(ambiguous, 후보 ≤3) / 트리 밖이지만 추가계(add_proposal=`<발번대기>`) / 그 외(dropped).
- **출력**: `CandidateSet.mapping`에 부착(additive). **UI 표시·확정 플로우는 이번 범위 밖 — 데이터만 흐름.**
- **LLM**: 0회. (ambiguous 동점해소 LLM은 미구현 TODO.)

### ④ 사람 확정 (HITL) — `src/agent/confirm/` + UI ★흐름이 여기서 멈춤★
- 사용자가 후보 행을 **채택/거절**, 변경점 지정, 신규 P/No 입력(없으면 `<발번대기>`) → **확정 닻**.
- **확정 전에는 트리 전개·판정·문서 생성을 하지 않는다** (절대원칙 #4).

### ⑤ 하위 전개 + 영향 판정 — `src/agent/pipeline.py : analyze_confirmed` (결정론)
- `expand_confirmed`: 확정 닻을 시작점으로 `walk_subtree`(하위, 깊이≤4, 사이클 가드).
- `impact/rules.py`: 부품별 룰 평가(`config/impact_rules.yaml`). 충돌은 `(priority, action_rank)`로 해소.
- **상향 채반**: 호환성(form-fit-function)이 깨질 때만 부모를 NEW로 채반.
- **[cascade, S 있을 때]** `impact/cascade.py`: 과거 함께 바뀐 자손 라인을 현재 트리에 레벨 정렬로 전사 → `CASCADE_TPL`(CHECK, priority 50) finding 주입. 트리 밖은 정보 레코드(BOM 행으로 안 나감).
- **LLM**: 0회 (룰·전개·판정 전부 결정론). L4 자연어 설명에만 LLM(선택).

### ⑥ 출력 — `src/agent/docgen/`, `src/agent/basebom/`
- 16컬럼 트리 형식 / New BOM(서식보존 xlsx). **모든 추천 행에 출처 `[SRC]`**, 신규 품번은 `<발번대기>`, 출처 없으면 미출력.

---

## 5. 데이터 모델 요약

```
change_event (검색 인덱스)            change_line (이벤트별 부품)         bom_edge (현재 BOM 트리)
├ reason_embedding ← 검색 정답        ├ part_name / part_name_canon       ├ file_id (트리 범위)
├ reason_sparse (sparse 채널)        ├ base_pno / new_pno                ├ parent_pno / child_pno
├ change_log / change_reason          ├ changepoint / classification      └ bom_level / qty
├ base_model / new_model              ├ part_type / source_ref
└ source_ref / source_project         └ (module_pno/name: 컬럼만, ETL 보류)   dev_part_master
                                                                            └ 품번↔부품명↔part_type
```
- `change_event ─(event_id)─< change_line` : 한 사유로 함께 바뀐 부품들.
- `bom_edge` 재귀 CTE = `walk_subtree` 의 대상 (구조 A).

---

## 6. 검색 채널 (RRF로 합쳐짐) — `search_events`

| 채널 | 무엇을 매칭 | 비고 |
|---|---|---|
| **dense** (semantic) | `reason_embedding`(변경내역+사유 임베딩) vs 쿼리 임베딩 | **정답 인덱스.** bge-m3 1024d. 임베딩 필요. |
| **lexical** | `change_log`+`change_reason` 텍스트 vs 쿼리 (pg_trgm) | 임베딩 없이 동작. |
| **sparse** | `reason_sparse`(BGE-M3 lexical) | 코드/모델 exact-match 보강. `FlagEmbedding` 설치 시. |
| **parts** | `change_line.part_name/base_pno/new_pno` + `base_model/new_model` vs 쿼리 (word_similarity) | **[SEARCH_INCLUDE_PARTS, 기본 ON]** 2026-06-10 추가. part_name_canon도 함께. |
| **structure** (①.5) | 후보 이벤트 라인이 현재 BOM 하위 트리 S에 얼마나 정합 | **[SEARCH_INCLUDE_STRUCT, 기본 OFF]** 별도 랭킹 리스트로 RRF 투입(가중 STRUCT_WEIGHT). |

→ 각 채널이 뽑은 순위를 `Σ weight/(60+rank)`로 합산(RRF). **dense 인덱스는 항상 청정 유지**(부품/구조는 별도 채널로만 더함).

---

## 7. 게이트(.env) — 현재 기본값

| env | 기본 | 의미 |
|---|---|---|
| `SEARCH_INCLUDE_PARTS` | **on** | 부품명/품번 parts 채널. |
| `DERIVE_CP` | **on** | 사유→슬롯 유도 정렬 쿼리 보강(결정론 1차). |
| `SEARCH_INCLUDE_STRUCT` | **off** | ①.5 구조 부스트 + ②.5 매핑 + 동반변경 정보행. **평가 후 켜는 걸 권장.** |
| `STRUCT_WEIGHT` | 1.0 | structure 랭킹 가중. |
| `MATCH_TAU_HI/LO`, `MATCH_EPS`, `MATCH_THETA_COHERENCE`, `MATCH_TYPE_SCORE` | 0.75/0.45/0.08/0.50/0.20 | 매칭 임계(①.5/②.5 공유). |
| `CASCADE_INFO` | **on** | P5 과거 동반변경 정보행 출력(STRUCT 게이트 안에서만 유효 — 매핑 있을 때만). |
| `DERIVE_CP_LLM` | **off** | P6 변경점 유도 LLM 2차 분리 게이트. 기본 결정론 1차만. |
| `RESCUE_SEARCH` | **off** | P7 저신뢰 시 1회 구제 재검색. |
| `SEARCH_LOW_CONF` | 0.30 | P7 "후보 약함"(low_confidence) 판정용 dense 최고 유사도 하한. |
| `FEEDBACK_LOG` | **on** | P8 확정 피드백 기록(confirm_feedback). |
| `LLM_PROVIDER` | (현재 `anthropic`) | `ollama`(로컬 기본) 또는 `anthropic`(opt-in, Claude). |
| `ENABLE_EMBEDDING` | (현재 1) | dense/parts 검색에 임베딩 사용. Ollama bge-m3 없으면 ST 폴백(첫 검색 ~1분). |

**게이트 off면 그 기능은 기존 동작과 바이트 단위 동일** — 모든 신규 기능은 additive + 우아한 폴백.

---

## 8. 결정론 vs LLM (이 경계가 설계의 핵심)

| 부분 | 방식 |
|---|---|
| 표 파싱 ① | **결정론** |
| 정형화 ② / 검색 재작성 | 기본 결정론, LLM은 **검색 보강만**(opt-in) |
| 검색 ③ / 매핑 ②.5 / 구조 스코프 ①.5 | **결정론** |
| 트리 전개 ⑤ / 룰 판정 / cascade 전사 | **결정론 (LLM import 0 — 단언 테스트로 강제)** |
| L4 자연어 설명 | LLM(선택) |

→ **"무엇을 바꿀지 판정"하는 곳엔 LLM이 없다.** LLM은 검색을 넓히고 설명을 쓰는 데만.

---

## 9. 지금 구현된 것 vs 아직 아닌 것

### ✅ 구현·검증됨
- ① PPT 파싱, ② 정형화(+DERIVE_CP 슬롯 유도), ③ 4채널 검색+RRF, ④ HITL 확정 게이트, ⑤ walk_subtree 전개+룰 판정+상향 채반, ⑥ New BOM/문서 출력.
- ①.5 구조 스코프, ②.5 후보-BOM 매핑(채택 후 lazy), 과거 동반변경 정보행 — **결정론, 게이트 뒤(SEARCH_INCLUDE_STRUCT)**.
- v2/P7 검색 견고성: 한↔영 별칭(part_aliases), low_confidence 배지 + RESCUE_SEARCH + 수동 보강, dedup 라인 합집합 보존, ①.5 관측성(ScopeMeta).
- v2/P8 confirm_feedback(009) + `db feedback-report` — 확정이 평가 데이터.
- 마이그레이션 008·009 적용, recanon 백필 완료. **테스트 332 통과.**
- **검증**: 실데이터 정답 1건 "하위 트리 매칭" 통과(pno 정확/이름 canon/모호→CHECK/트리밖→drop), 통제 recall 테스트(정답 6건) 6/6 1등 회수.

### ⏳ 아직 안 한 것 (의도적 보류 = TODO)
- **모듈 태깅 ETL**(`change_line.module_pno/module_name`, `change_event.module_tags` 채우기): 컬럼만 준비.
- **ambiguous LLM 동점해소**(매핑이 애매할 때 닫힌 택1): 미구현.
- **part_name 전용 dense 임베딩**(마이그레이션 **010** 후보로 이월): 미구현.
- **수작업 recall@k 골든셋·정식 ablation 벤치**: 만들지 않는다 — `db feedback-report` + `eval replay`(TODO)로 대체.
- **MMR 등 후보 다양성 재랭킹**: P7.3 합집합 보존으로 1차 대응, 피드백 데이터에서 필요성 보이면 후속.

---

## 10. "내가 상상한 것 vs 실제 구현" — 자주 어긋나는 지점 체크리스트

> AI와 대화하거나 팀과 맞춰볼 때 이 항목들을 먼저 확인하면 오해가 빨리 풀립니다.

1. **검색은 BOM 트리를 직접 뒤지지 않는다.** → 검색 대상은 **과거 변경이벤트(구조 B)**다. BOM 트리(구조 A)는 *확정 이후* 전개와 *구조 스코프(보조)*에만 쓴다.
2. **검색의 정답은 "사유+내역"이다.** → 부품명/품번은 2026-06-10부터 *추가 채널(parts)*로 들어왔지만, dense 인덱스 자체는 사유만으로 청정하게 유지된다.
3. **구조 스코프(①.5)와 매핑(②.5)은 기본 꺼져 있다.** → `SEARCH_INCLUDE_STRUCT=1`이어야 동작/표시된다. 기본 흐름은 4채널 검색까지다.
4. **②.5 매핑 결과는 아직 "참고용"이다.** → 매핑이 자동으로 New BOM/확정을 바꾸지 않는다. 사람이 ④에서 확정해야 ⑤가 돈다.
5. **AI가 영향 판정을 하지 않는다.** → 전개·룰·cascade는 전부 결정론. LLM은 검색 보강·설명 전용.
6. **새 품번을 만들지 않는다.** → 없으면 `<발번대기>`. 추가계 매핑(add_proposal)도 품번은 항상 `<발번대기>`.
7. **사람 확정이 먼저다.** → "AI가 끝까지 다 만들고 마지막에 승인" 방식이 아니다. 후보 제시 → **멈춤** → 확정 → 전개.
8. **base_model은 검색어가 아니다.** → 항목마다 동일해 변별력이 없어서 쿼리에 안 넣는다. BOM 파일 해석·표기용.
9. **과거 동반 변경은 "정보행"이지 자동 전개가 아니다 (v2/P5).** → 예전엔 레벨창 전사(cascade)로 finding을 만들었지만 제거했다. 지금은 채택 이벤트에서 사람이 채택하지 않은 나머지 라인을 체크리스트 "참고: 과거 동반 변경"에 **참고 정보**로만 적는다 — BOM/개발마스터 행으로는 절대 안 나간다.
10. **②.5 매핑은 후보 탐색 시점이 아니라 "채택 후"에 계산된다 (v2/P6).** → 후보 화면엔 구조 정합 점수(struct_score)만 보이고, 라인 단위 매핑은 사람이 채택한 1~2건에 대해서만 lazy로 돈다.
11. **"검색이 안 될 수 있다"를 시스템이 인정한다 (v2/P7).** → dense 최고 유사도가 낮으면 "검색 신뢰 낮음" 배지를 띄우고, 사람이 키워드로 직접 보강 검색(manual_added)할 수 있다. 한↔영 용어(도어↔Door)는 별칭表로 메운다.
12. **확정이 곧 평가 데이터다 (v2/P8).** → 매 확정이 `confirm_feedback`에 기록되고 `db feedback-report`로 채택률·순위·수동보강 비율(미회수율 하한)을 본다. 별도 골든셋을 만들지 않는다.

---

## 11. 관련 코드 지도 (빠른 참조)

| 단계 | 파일 |
|---|---|
| ① PPT 파싱 | `src/agent/ppt/extractor.py` |
| ② 정형화 / 슬롯 유도 | `src/agent/intent/structurizer.py`, `intent/derive.py`, `ontology/changepoint_vocab.py` |
| ①.5 구조 스코프 | `src/agent/orchestrator/structure_scope.py`, `matching/scoring.py` |
| ③ 검색 | `src/db/retrieve.py`, `orchestrator/orchestrate.py` |
| ②.5 매핑 (채택 후 lazy) | `src/agent/mapping/mapper.py`, `mapping/models.py`, `pipeline.build_adopted_mappings` |
| 부품명 별칭(한↔영) | `config/part_aliases.yaml`, `src/ontology/part_aliases.py`, `preprocess/normalize.canonicalize_part_name` |
| ④ 확정 + 피드백 | `src/agent/confirm/`, `src/agent/feedback.py`(P8), `src/ui/agent_app.py` |
| ⑤ 전개·판정·동반변경정보행 | `src/agent/pipeline.py`, `impact/rules.py`, `impact/cascade.py`(companion_info), `repository/bom.py` |
| 피드백 리포트 | `src/cli.py` `db feedback-report` (confirm_feedback 읽기 유일 경로) |
| ⑥ 출력 | `src/agent/docgen/`, `src/agent/basebom/` |
| UI | `src/ui/main.py`(라우터), `agent_app.py`(분석), `inspect_app.py`(검수), `env_panel.py`(상태) |
| 어휘/룰 설정 | `config/changepoint_vocab.yaml`, `config/impact_rules.yaml`, `config/normalization.yaml` |

---

*문서 끝. 흐름·동작과 실제 코드가 어긋나 보이면 이 문서(§10)와 위 파일을 함께 확인하세요.*
