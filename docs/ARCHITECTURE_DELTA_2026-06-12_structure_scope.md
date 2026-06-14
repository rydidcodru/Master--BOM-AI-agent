# 아키텍처 변경 이력 — 변경점 어휘 v3 + 구조 스코프 부스트 + ②.5 매핑 + Cascade 전사 (2026-06-12)

> 작업 지시서(`claude_code_prompt_structure_scope.md`)의 마무리 체크리스트 §3은
> `docs/ARCHITECTURE_OVERVIEW.md` 갱신을 요구하나 **이 레포에는 해당 문서가 없다**
> (지시서가 참조한 문서는 다른 워크스페이스 소속으로 추정). 그 체크리스트가 요구한
> 갱신 항목을 이 델타 문서로 기록한다. 추후 ARCHITECTURE_OVERVIEW가 생기면 본문에 병합.

## §1 흐름도 갱신 — ①.5 노드 (bom_edge read-only 점선)

```
① 표/PPT 파싱 ─→ ② 정형화(intent_from_change [+DERIVE_CP 슬롯 유도])
                       │
                       ├─→ ①.5 구조 스코프 S 구축 (SEARCH_INCLUDE_STRUCT=1일 때만)
                       │      └╌╌ bom_edge / dev_part_master  ←╌ read-only 점선
                       │           (검색용 사전 조회 — 판정용 전개 ④가 아님)
                       ▼
                  ③ 검색 search_events → RRF(레벨 B) ←─ structure 랭킹 리스트(가중 STRUCT_WEIGHT)
                       │            └─ ②.5 후보-BOM 매핑(MappedEvent, CandidateSet.mapping)
                       ▼
                  ④ HITL 확정 (변경 없음) → ⑤ 전개+판정(analyze_confirmed
                       [+ adopted_mappings+scope 시 CASCADE_TPL 주입]) → ⑥ 출력 (변경 없음)
```

## §3.5 마이그레이션 — 008_structure_scope.sql

additive·멱등만: `change_line.part_name_canon/module_pno/module_name`,
`change_event.module_tags TEXT[]` + trgm/GIN 인덱스. 백필은 `db change-events --recanon`
(canonicalize_part_name — token_canon 오타/변이 + 모델코드·치수 토큰 제거; 멱등).
**모듈 태깅(module_* 채우기) ETL 역추적은 컬럼만 준비, 후속 Phase로 명시 보류.**

## §6.2 레벨 B 융합 — structure 랭킹 리스트

`_rrf_fuse(ranked_lists, weights=None)`로 확장(기본 호출은 기존과 완전 동일 — 테스트 핀).
`SEARCH_INCLUDE_STRUCT=1`이고 S가 존재할 때만, 레벨 A 후보 합집합의 change_line을
`event_structure_score`(0.6×커버리지+0.4×최고점)로 정렬한 **structure 리스트 1개**를
가중 `STRUCT_WEIGHT`로 투입한다. S=∅/None이면 리스트 자체를 투입하지 않는다(빈 리스트
투입 금지 — RRF 분모 왜곡). dedup_by_reason 병합 시 struct_score는 MAX 보존.

## §15 원칙 각주

- 원칙 #1(검색 의미 인덱스 = 변경내역+변경사유): **구조 채널은 additive — 사유 인덱스
  (reason_embedding dense/lexical/sparse)는 청정 유지.** 구조 신호는 별도 랭킹 리스트로만
  융합되고 게이트 off면 바이트 단위 기존 동일.
- 원칙 #4(확정이 전개보다 먼저): **검색용 read-only 조회(①.5 build_scope) ≠ 판정용
  전개(④ expand_confirmed).** ①.5/②.5/cascade의 BOM 접근은 전부 검색·매핑 보조용
  사전 조회이며, 판정용 전개는 HITL 확정 이후 경로 그대로다.

## §16 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-06-12 | Phase 0: `config/changepoint_vocab.yaml` (수확 1512행, 상위 30 라벨 57.3% 커버, attested 검증) |
| 2026-06-12 | Phase 1: L1 변경점 유도 v3 (`intent/derive.py` R0~R6 + LLM 2차 grounding 가드, `DERIVE_CP`) |
| 2026-06-12 | Phase 2: `matching/scoring.py`(trgm 근사 패리티 ±0.05) + ①.5 `structure_scope.py` + 008 + parts 채널 canon |
| 2026-06-12 | Phase 3: ②.5 `mapping/mapper.py` (M1~M5, `<발번대기>` 강제, CandidateSet.mapping additive) |
| 2026-06-12 | Phase 4: `impact/cascade.py` 전사(CASCADE_TPL 50) + `rules.evaluate` extra_findings 주입, 우선순위 불변식 핀 |
| 2026-06-12 | **v2 P5**: cascade 전사 **제거** → `companion_info` 과거 동반변경 체크리스트 정보행(`CASCADE_INFO`). `=1` 골든 갱신(전사 finding 사라짐), `=0` 골든 불변 |
| 2026-06-12 | **v2 P6**: ②.5 매핑 lazy화(검색→**채택 후** `build_adopted_mappings`), DERIVE_CP_LLM 분리 게이트(기본 off) |
| 2026-06-12 | **v2 P7**: part_aliases(한↔영) + retrieval_meta/low_confidence + RESCUE_SEARCH + 수동 보강 + dedup 라인 합집합 + ScopeMeta 관측성·trgm 캐싱 |
| 2026-06-12 | **v2 P8**: `009_confirm_feedback` + `db feedback-report`(운영=평가). part_name_embedding은 **010 후보로 이월** |

## 이번 범위에서 명시적으로 하지 않은 것 (TODO)

- UI 변경 전부 (struct 점수 컬럼, 자동요약 라벨, 매핑 확정 화면, base BOM 업로드 위치 이동)
- ETL 모듈 태깅 역추적 (`module_tags`/`module_pno`/`module_name` 채우기 — 컬럼만 준비)
- ambiguous LLM 동점해소 (mapper에 TODO 주석)
- `part_name_embedding` dense 컬럼 (마이그레이션 009 후보)
- recall@k 골든셋/평가 (별도 작업)
