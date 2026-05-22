# 개발부품 Master 온톨로지 스키마 (Neo4j 5.x)

> 입력: `schema_analysis.md`의 5개 양식 패밀리 분석 결과
> 목표: ① **유사 변경사례 검색**(신규 변경요청 ↔ 과거 ChangeItem 의미 유사도)
>      ② **BOM 서브트리 영향분석**(특정 Assembly 아래 부품들의 과거 변경 이력)
> 환경: Neo4j 5.x, 노드 vector index + fulltext index 하이브리드, 그레이드는 별도 Lineup 노드, provenance = 파일+시트+행번

---

## 0. 설계 원칙 (Why this shape)

1. **ChangeItem이 허브** — 과거 Master의 한 행(부품 변경 이벤트)을 reified 노드로 만든다. 모든 메타(이유/일정/플래그/공급사)가 여기에 붙고, **임베딩 벡터를 이 노드에 부여**하면 "유사 변경사례 검색"이 단일 vector index 조회로 끝난다.
2. **Part는 canonical hub** — Part No는 프로젝트가 바뀌어도 동일성을 유지하는 유일 키. ChangeItem은 항상 `(FROM_PART, TO_PART)` 쌍으로 Part를 가리킨다(신규는 FROM 없음, 삭제는 TO 없음).
3. **BOM은 properties-on-edge** — `(:Part)-[:HAS_CHILD]->(:Part)` 한 가지 관계로 표현하고 model_code/qty/designator 등은 edge property. 같은 부모-자식 쌍이 여러 모델에서 나오면 multi-edge로 허용(Neo4j는 동일 라벨/방향의 multi-edge 지원).
4. **카테고리(Enum)는 노드로 승격** — Lineup/Grade/PartType/Event/Region은 별도 노드. 이유: 한국어/영어/오타 별칭이 많아 alias 정규화 지점이 필요하고, "같은 등급의 다른 부품" 같은 traversal이 잦다. 마스터 테이블처럼 작동.
5. **provenance는 (:Document)-[:HAS_SHEET]->(:Sheet)-[:HAS_ROW]->(:ChangeItem)** 체인. ChangeItem 자체에도 빠른 조회용 `source_file/source_sheet/source_row`를 비정규화 보관(임베딩 검색 후 원본 셀 인용에 필요).
6. **양식 호환성** — 5개 양식 패밀리(BO24/통합 v1.2/Single IH/그레이드 매트릭스/가로형) 모두 동일 ChangeItem 스키마로 들어오게. 매트릭스형의 "Best-1 컬럼에 1" → ChangeItem-[:APPLIES_TO]->Lineup(Best-1) 관계로 펼친다.
7. **null-tolerant** — 과거 데이터 결측이 매우 잦으므로 모든 비-키 속성은 nullable. 부재 자체가 정보일 때만 `*_missing_reason` 컬럼 별도 보존.

---

## 1. 노드 카탈로그

### 1.1 도메인 허브 노드

#### `:Part` — 부품 마스터 (canonical)
| 속성 | 타입 | 비고 |
|---|---|---|
| `part_no` | string | **PK / UNIQUE** |
| `name` | string | Class Desc.(Part Name) 정규화 |
| `description` | string | BOM의 Description |
| `tech_spec` | string | BOM의 Technical Spec |
| `default_uom` | string | EA, KG 등 |
| `default_uit` | string | F/S/T/G/P/X |
| `default_supply_type` | string | Phantom/Assembly Pull/Supplier |
| `is_assembly` | boolean | name이 `*Assembly*` 또는 BOM에서 자식이 있는가 |
| `embedding_part` | float[384/768/1536] | `name + description + tech_spec` 임베딩 |
| `created_at`, `updated_at` | datetime | |

> 동일 Part No가 여러 BOM/Master에서 등장 → 항상 같은 `:Part` 노드를 가리키게 MERGE.

#### `:ChangeItem` — 변경 이벤트 (Master 행 1개 = 1개)
| 속성 | 타입 | 비고 |
|---|---|---|
| `change_id` | string (uuid) | **PK / UNIQUE** |
| `seq_no` | int | 시트 내 No. |
| `change_action` | string enum | `NEW / CHANGE / COMMON / DELETE` (정규화 후) |
| `change_action_raw` | string | 원본 표기(`Chainging` 등 오타 보존) |
| `change_point` | string | 변경점 (한·영 그대로) |
| `change_reason` | string | 변경사유 |
| `selection_background` | string | 선정 배경 (등급심의 사유) |
| `quantity_base` | float | |
| `quantity_new` | float | |
| `bom_level_text` | string | 원본 `.1`, `..2`, `…3` |
| `bom_level_depth` | int | 점 갯수로 정규화 |
| `mold_dev` | boolean? | ○/X → bool, `-`는 null |
| `in_house` | boolean? | |
| `approval_test` | boolean? | |
| `ckd_flag` | boolean? | |
| `yield_mgmt` | boolean? | |
| `part_traceability` | boolean? | |
| `s_apqp` | boolean? | |
| `factory_audit` | boolean? | |
| `part_approval` | boolean? | |
| `part_certification` | boolean? | |
| `spec_certification` | boolean? | |
| `ctq_target` | boolean? | |
| `key_part_target` | boolean? | |
| `long_leadtime` | boolean? | |
| `four_new` | boolean? | 4新 |
| `apqp` | boolean? | |
| `high_skill` | boolean? | 고도화 |
| `limit_sample` | boolean? | 한도 견본 |
| `design_fmea` | boolean? | |
| `process_fmea` | boolean? | |
| `drbfm_skip_reason` | string | "Why don't you execute DRBFM?" |
| `non_standard_reason` | string | |
| `is_standard` | boolean? | 표준/비표준 |
| `remark` | string | |
| `dwg_review_date` | date? | |
| `dwg_agree_date` | date? | |
| `dwg_confirm_date` | date? | |
| `dwg_dispatch_date` | date? | 도면결재/배포(GPDM) |
| `mold_proto_date` | date? | 금형 초품 |
| `mass_proto_date` | date? | 양산 초품 |
| `dqms_state` | string | OK/NG/대기 |
| `source_file` | string | 비정규화(검색 후 원본 인용) |
| `source_sheet` | string | |
| `source_row` | int | |
| `embedding_change` | float[N] | `change_point + change_reason + selection_background` 임베딩 |
| `text_for_embedding` | string | 임베딩 입력 원문(재계산용 보존) |

#### `:Project` — 개발 건 1개
| 속성 | 타입 | 비고 |
|---|---|---|
| `project_id` | string | **PK / UNIQUE** |
| `name` | string | 예: "BO24 포르투갈향 Better" |
| `dev_grade` | string | Ca/Cb/D 등 |
| `volume` | string | "800대/年" |
| `reason` | string | 개발 사유 (자유 텍스트) |
| `mass_prod_date_base` | date? | |
| `mass_prod_date_new` | date? | |
| `mass_prod_year_month` | string | "25.01" 같은 표기 보존 |
| `events_raw` | string | "CP DV PV PreMP" |
| `embedding_project` | float[N] | name+reason+region 임베딩 |
| `created_at` | datetime | |

#### `:Model` — 모델 코드
| 속성 | 타입 | 비고 |
|---|---|---|
| `model_code` | string | **PK / UNIQUE** (예: WSED7667M, LSIU6339XE.ARSLLGA@CVZ.EKHQ) |
| `model_root` | string | `.`/`@` 앞 머리(WSED7667M) — 동일 모델군 묶음용 |
| `brand` | string | LG / LG SIGNATURE |
| `buyer` | string | LGEUR/LGEUE/LGEUN/LGEUS/LGF |
| `set_pno` | string? | |

> Model 1개는 보통 BOM 1개를 가진다. BOM 트리는 Part에 붙은 `[:HAS_CHILD {model_code}]` 엣지의 집합으로 구성.

### 1.2 카테고리/마스터 노드 (Enum 승격)

#### `:Lineup` — 라인업/그레이드 시트 키 (Best-1, Better-2, Good-1 BK …)
| 속성 | 타입 |
|---|---|
| `lineup_code` | string **UNIQUE** (정규화: `BEST_1`, `GOOD_2_BK` 등) |
| `lineup_label` | string (원본 표기) |
| `tier` | string (`BEST`, `BETTER`, `GOOD`) |
| `variant` | int (1 or 2) |
| `color_finish` | string? (`BK`, `STS`, `Glass`, `null`) |

#### `:Grade` — 부품 개발 등급
| 속성 | 타입 |
|---|---|
| `grade_code` | string **UNIQUE** (`S`,`A`,`B`,`C`,`C1`,`C2`,`Ca`,`Cb`,`D`) |
| `tier` | string (S-tier/A-tier/B-tier/C-tier/D-tier) |
| `requires_apqp` | boolean (가이드 기반 derive) |
| `requires_audit` | boolean |

#### `:PartType` — 부품 타입 (2축)
| 속성 | 타입 |
|---|---|
| `type_code` | string **UNIQUE** (`INJECTION`, `CUTTING`, `SHEET_METAL`, `CIRCUIT`, `STRUCTURE_ASSY`, `PD_PART`, `RAW_MATERIAL`, `PRINTING_MATERIAL`, …) |
| `axis` | string (`PROCESS`/`BOM_ROLE`) |
| `label_kr` | string |
| `label_en` | string |

> ChangeItem이 `[:OF_PROCESS_TYPE]`, `[:OF_BOM_ROLE]` 두 종류로 PartType을 가리키게 한다 (한 부품은 process+role 양쪽 다 있을 수 있음).

#### `:Event` — 개발 이벤트 (CP/PP/DV/PV/PreMP/MP/PQ)
| 속성 | 타입 |
|---|---|
| `event_code` | string **UNIQUE** |
| `phase_order` | int (정렬용: CP=1, PP=2, DV=3, PV=4, PreMP=5, MP=6, PQ=7) |

#### `:Region` — 향(국가/지역) — 파일명/시트명에서 추출
| 속성 | 타입 |
|---|---|
| `region_code` | string **UNIQUE** (`EU_E`, `EU_N`, `MIDDLE_EAST_UAE`, `OCEANIA_AU`, `SEA_SG`, …) |
| `label_kr` | string (`동유럽향`, `호주향`) |
| `buyer_hint` | string? (LGEUR 등) |

#### `:Module` — Module/CMDT (Cavity/Oven/제어/인쇄·포장 …)
| 속성 | 타입 |
|---|---|
| `module_code` | string **UNIQUE** |
| `label_kr` | string |

### 1.3 외부 행위자 노드

#### `:Supplier`
| 속성 | 타입 | 비고 |
|---|---|---|
| `supplier_code` | string? | KR011661 등 (없을 수 있음) |
| `name` | string **UNIQUE on (name, country)** | 한성칼라, 현대정밀, 성진 |
| `country` | string? | KR 등 |
| `role_hints` | string[] | 양산처/금형처/공급처/2차 등 |

#### `:Person` — SSOID 시트 + 설계자/구매 컬럼
| 속성 | 타입 |
|---|---|
| `ssoid` | string **UNIQUE** (예: jeongho7.kim) |
| `name_kr` | string |
| `role` | string (금형/부품개발/설계/SQE/SQA) |

### 1.4 품질/심의 sub-event 노드

#### `:GradeReview` — 부품등급심의회 결정 1건
| 속성 | 타입 |
|---|---|
| `review_id` | string **PK** |
| `meeting_date` | date |
| `place` | string |
| `attendees_raw` | string |
| `grade_decision` | string |
| `decision_background` | string |
| `embedding_review` | float[N] (decision_background) |

#### `:ApprovalTest` — 부품 인정시험 항목 1건
| 속성 | 타입 |
|---|---|
| `test_id` | string **PK** |
| `inspection_items` | string[] |
| `test_items` | string[] |
| `method_condition` | string |
| `sample_count` | int? |
| `executor` | string |
| `final_judgment` | string (OK/NG/대기) |
| `remark` | string |

#### `:TestPlan` — <모듈>_시험기획 1건
| 속성 | 타입 |
|---|---|
| `plan_id` | string **PK** |
| `module` | string |
| `category` | string (구조/외관, 성능, 치수, 친환경) |
| `inspection_spec` | string (예: LG(66)-B-4501-20) |
| `test_method_condition` | string |

### 1.5 Provenance 노드

#### `:Document` — 엑셀 파일
| 속성 | 타입 |
|---|---|
| `file_path` | string **UNIQUE** |
| `file_name` | string |
| `family` | string (`BO24` / `INTEGRATED_V1_2` / `SINGLE_IH` / `GRADE_MATRIX` / `HORIZONTAL_LIST`) |
| `version` | string? (v1.0/v1.1/v1.2 from History 시트) |
| `parsed_at` | datetime |
| `sha256` | string |

#### `:Sheet`
| 속성 | 타입 |
|---|---|
| `sheet_uid` | string **UNIQUE** (`{file_sha256}#{sheet_name}`) |
| `sheet_name` | string |
| `header_row` | int |
| `row_count` | int |

#### `:DocVersion` — 통합 v1.2 등 History 시트 항목
| 속성 | 타입 |
|---|---|
| `version_id` | string **PK** |
| `version` | string (v1.0/v1.1/v1.2) |
| `release_date` | date |
| `note_kr` | string |
| `note_en` | string |

---

## 2. 관계 카탈로그

### 2.1 BOM 트리
```
(:Part)-[:HAS_CHILD {
    model_code,        -- 어느 모델 BOM인지 (multi-edge 허용)
    qty,
    uom,
    uit,
    designator,
    supply_type,
    lvl,               -- BOM Level 정수
    lvl_text,          -- 원본 .1 ..2 …3
    job_explanation,
    cf_no,
    change_in_eco,
    change_out_eco,
    document_id,       -- 출처 BOM 파일
    sort_seq
}]->(:Part)
```
- **multi-edge** — `(A)-[:HAS_CHILD {model_code:M1}]->(B)`, `(A)-[:HAS_CHILD {model_code:M2}]->(B)` 동시 허용.
- 같은 모델 안에서 같은 부모-자식이 designator만 다르게 두 번 등장하면 designator별로 별개 edge.

```
(:Model)-[:HAS_BOM_ROOT]->(:Part)        -- BOM의 Lvl=0 (모델 자기 자신)
```

### 2.2 변경 이벤트 핵심
```
(:Project)-[:INCLUDES_CHANGE]->(:ChangeItem)
(:ChangeItem)-[:FROM_PART]->(:Part)         -- Base P/No (nullable: NEW일 때 없음)
(:ChangeItem)-[:TO_PART]->(:Part)           -- New P/No (nullable: DELETE일 때 없음)
(:ChangeItem)-[:APPLIES_TO_LINEUP]->(:Lineup)   -- 여러 개 가능 (매트릭스 펼침)
(:ChangeItem)-[:OF_PROCESS_TYPE]->(:PartType {axis:'PROCESS'})
(:ChangeItem)-[:OF_BOM_ROLE]->(:PartType {axis:'BOM_ROLE'})
(:ChangeItem)-[:IN_MODULE]->(:Module)
(:ChangeItem)-[:HAS_GRADE]->(:Grade)         -- 부품 개발 등급
(:ChangeItem)-[:SUPPLIED_BY {role:'양산처'|'금형처'|'공급처'}]->(:Supplier)
(:ChangeItem)-[:OWNED_BY {role:'설계'|'구매'|'개발'}]->(:Person)
```

### 2.3 프로젝트 컨텍스트
```
(:Project)-[:BASE_MODEL]->(:Model)
(:Project)-[:NEW_MODEL]->(:Model)
(:Project)-[:TARGETS_REGION]->(:Region)
(:Project)-[:HAS_EVENT]->(:Event)            -- "CP DV PV PreMP" → 4개 엣지
(:Project)-[:USED_FORMAT]->(:DocVersion)     -- 통합 v1.2 등
```

### 2.4 품질/심의 연결
```
(:GradeReview)-[:REVIEWS_PART]->(:Part)
(:GradeReview)-[:DECIDED_FOR]->(:ChangeItem)
(:GradeReview)-[:HELD_IN]->(:Project)
(:GradeReview)-[:ATTENDED_BY {role}]->(:Person)

(:ApprovalTest)-[:TESTS]->(:ChangeItem)
(:ApprovalTest)-[:CONDUCTED_BY]->(:Supplier)   -- 1차/2차 처

(:TestPlan)-[:PLANS]->(:ChangeItem)
(:TestPlan)-[:FOR_MODEL]->(:Model)
```

### 2.5 Provenance 체인
```
(:Document)-[:HAS_SHEET]->(:Sheet)
(:Sheet)-[:HAS_ROW {row_index}]->(:ChangeItem)
(:Sheet)-[:HAS_ROW {row_index}]->(:GradeReview)
(:Sheet)-[:HAS_ROW {row_index}]->(:ApprovalTest)
(:Sheet)-[:HAS_ROW {row_index}]->(:TestPlan)
(:Document)-[:USED_FORMAT]->(:DocVersion)
```

### 2.6 Part 간 추가 관계 (유용한 derive 관계)
```
(:Part)-[:REPLACED_BY {via_change_id, on_date, lineup}]->(:Part)
-- ChangeItem(FROM_PART=A, TO_PART=B, action=CHANGE) 1건당 1개 자동 생성
-- 부품 진화 history를 한 hop으로 보기 위함

(:Part)-[:SIMILAR_TO {score, basis:'embedding'|'cooccurrence'}]->(:Part)
-- 배치 잡으로 사후 생성: 같은 PartType+Grade 묶음 안에서 임베딩 코사인 유사도 상위 K
```

---

## 3. 그래프 모양 (개념도)

```
                ┌─────────────┐
                │  :Document  │
                └──────┬──────┘
                       │ HAS_SHEET
                       ▼
                ┌─────────────┐
                │   :Sheet    │
                └──────┬──────┘
                       │ HAS_ROW {row_index}
                       ▼
   ┌────────────────────────────────────────┐
   │                                        │
   │  (:Project)──INCLUDES_CHANGE──▶ ┌──────┴──────┐
   │     │   TARGETS_REGION              │             │
   │     ├─▶ (:Region)                   │  :ChangeItem │ ◀── 임베딩 hub
   │     │                               │  (event)    │       (유사사례 검색)
   │     ├─▶ (:Model)  BASE / NEW         └──┬───┬───┬──┘
   │     │     │                            │   │   │
   │     │     ▼                            │   │   └─APPLIES_TO_LINEUP─▶ (:Lineup)
   │     │  (:Part)  ◀──HAS_BOM_ROOT       │   │
   │     │     │                            │   ├─OF_PROCESS_TYPE──▶ (:PartType {PROCESS})
   │     │     │ HAS_CHILD {model_code,    │   ├─OF_BOM_ROLE──────▶ (:PartType {BOM_ROLE})
   │     │     │            qty, lvl, …}   │   ├─HAS_GRADE────────▶ (:Grade)
   │     │     ▼                            │   ├─IN_MODULE────────▶ (:Module)
   │     │  (:Part)  ───── ▲                │   ├─SUPPLIED_BY {role}▶ (:Supplier)
   │     │   ⋮                              │   └─OWNED_BY {role}──▶ (:Person)
   │     │                                  │
   │     └─▶ (:Event)                       │
   │                                        ├─FROM_PART──▶ (:Part)
   │                                        └─TO_PART────▶ (:Part)
   │                                              │
   │                                              ▼
   │                                   (:Part)-[REPLACED_BY]-▶ (:Part)
   │                                              ⋮ (변경 체인)
   │
   └────────────────────────────────────────┘
                       ▲
   (:GradeReview)─REVIEWS_PART─┘
   (:ApprovalTest)─TESTS─▶ (:ChangeItem)
   (:TestPlan)────PLANS──▶ (:ChangeItem)
```

---

## 4. 제약 / 인덱스 DDL

> 실제 retrieval Cypher는 별도. 여기서는 **스키마 정의**에 해당하는 constraint·index만 정의.

```cypher
// === UNIQUE constraints (= 자연키 + 자동 인덱스) ===
CREATE CONSTRAINT part_pk          IF NOT EXISTS FOR (n:Part)        REQUIRE n.part_no IS UNIQUE;
CREATE CONSTRAINT change_pk        IF NOT EXISTS FOR (n:ChangeItem)  REQUIRE n.change_id IS UNIQUE;
CREATE CONSTRAINT project_pk       IF NOT EXISTS FOR (n:Project)     REQUIRE n.project_id IS UNIQUE;
CREATE CONSTRAINT model_pk         IF NOT EXISTS FOR (n:Model)       REQUIRE n.model_code IS UNIQUE;
CREATE CONSTRAINT lineup_pk        IF NOT EXISTS FOR (n:Lineup)      REQUIRE n.lineup_code IS UNIQUE;
CREATE CONSTRAINT grade_pk         IF NOT EXISTS FOR (n:Grade)       REQUIRE n.grade_code IS UNIQUE;
CREATE CONSTRAINT parttype_pk      IF NOT EXISTS FOR (n:PartType)    REQUIRE n.type_code IS UNIQUE;
CREATE CONSTRAINT event_pk         IF NOT EXISTS FOR (n:Event)       REQUIRE n.event_code IS UNIQUE;
CREATE CONSTRAINT region_pk        IF NOT EXISTS FOR (n:Region)      REQUIRE n.region_code IS UNIQUE;
CREATE CONSTRAINT module_pk        IF NOT EXISTS FOR (n:Module)      REQUIRE n.module_code IS UNIQUE;
CREATE CONSTRAINT supplier_pk      IF NOT EXISTS FOR (n:Supplier)    REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT person_pk        IF NOT EXISTS FOR (n:Person)      REQUIRE n.ssoid IS UNIQUE;
CREATE CONSTRAINT gradereview_pk   IF NOT EXISTS FOR (n:GradeReview) REQUIRE n.review_id IS UNIQUE;
CREATE CONSTRAINT approvaltest_pk  IF NOT EXISTS FOR (n:ApprovalTest)REQUIRE n.test_id IS UNIQUE;
CREATE CONSTRAINT testplan_pk      IF NOT EXISTS FOR (n:TestPlan)    REQUIRE n.plan_id IS UNIQUE;
CREATE CONSTRAINT document_pk      IF NOT EXISTS FOR (n:Document)    REQUIRE n.file_path IS UNIQUE;
CREATE CONSTRAINT sheet_pk         IF NOT EXISTS FOR (n:Sheet)       REQUIRE n.sheet_uid IS UNIQUE;
CREATE CONSTRAINT docver_pk        IF NOT EXISTS FOR (n:DocVersion)  REQUIRE n.version_id IS UNIQUE;

// === 일반 BTREE/RANGE 인덱스 (필터링용) ===
CREATE INDEX part_name             IF NOT EXISTS FOR (n:Part)        ON (n.name);
CREATE INDEX part_is_assembly      IF NOT EXISTS FOR (n:Part)        ON (n.is_assembly);
CREATE INDEX change_action         IF NOT EXISTS FOR (n:ChangeItem)  ON (n.change_action);
CREATE INDEX change_grade_action   IF NOT EXISTS FOR (n:ChangeItem)  ON (n.change_action, n.bom_level_depth);
CREATE INDEX change_dates          IF NOT EXISTS FOR (n:ChangeItem)  ON (n.mass_proto_date);
CREATE INDEX project_grade         IF NOT EXISTS FOR (n:Project)     ON (n.dev_grade);
CREATE INDEX model_root            IF NOT EXISTS FOR (n:Model)       ON (n.model_root);

// === 관계 인덱스 (BOM traversal 필터링) ===
CREATE INDEX bom_edge_model        IF NOT EXISTS FOR ()-[r:HAS_CHILD]-() ON (r.model_code);
CREATE INDEX bom_edge_lvl          IF NOT EXISTS FOR ()-[r:HAS_CHILD]-() ON (r.lvl);

// === FULLTEXT (한국어 변경사유/이름) ===
CREATE FULLTEXT INDEX ft_change_reason  IF NOT EXISTS
    FOR (n:ChangeItem) ON EACH [n.change_point, n.change_reason, n.selection_background];
CREATE FULLTEXT INDEX ft_part_text      IF NOT EXISTS
    FOR (n:Part) ON EACH [n.name, n.description, n.tech_spec];
CREATE FULLTEXT INDEX ft_review_bg      IF NOT EXISTS
    FOR (n:GradeReview) ON EACH [n.decision_background];
CREATE FULLTEXT INDEX ft_project_reason IF NOT EXISTS
    FOR (n:Project) ON EACH [n.name, n.reason];

// === VECTOR (의미 검색용; 차원은 사용 모델에 맞추어) ===
CREATE VECTOR INDEX vec_change_embedding IF NOT EXISTS
    FOR (n:ChangeItem) ON (n.embedding_change)
    OPTIONS { indexConfig: {
        `vector.dimensions`: 1024,
        `vector.similarity_function`: 'cosine'
    }};
CREATE VECTOR INDEX vec_part_embedding IF NOT EXISTS
    FOR (n:Part) ON (n.embedding_part)
    OPTIONS { indexConfig: {
        `vector.dimensions`: 1024,
        `vector.similarity_function`: 'cosine'
    }};
CREATE VECTOR INDEX vec_project_embedding IF NOT EXISTS
    FOR (n:Project) ON (n.embedding_project)
    OPTIONS { indexConfig: {
        `vector.dimensions`: 1024,
        `vector.similarity_function`: 'cosine'
    }};
CREATE VECTOR INDEX vec_review_embedding IF NOT EXISTS
    FOR (n:GradeReview) ON (n.embedding_review)
    OPTIONS { indexConfig: {
        `vector.dimensions`: 1024,
        `vector.similarity_function`: 'cosine'
    }};
```

> 차원 1024는 자리표시자. 임베딩 모델 확정 후(`text-embedding-3-large`=3072, `bge-m3`=1024, `multilingual-e5-large`=1024) 동일하게 맞춰야 함.

---

## 5. Retrieval 패턴 (어떤 쿼리를 잘 받게 설계했는가)

### 5.1 ① 유사 변경사례 검색 (1순위)
**입력**: 새 변경요청 한 문장 (예: "BLDC 적용으로 Motor 일체화") + 선택적 필터(part_type=회로, grade=Cb, region=동유럽향).
**경로**:
1. 입력 텍스트 임베딩 → `vec_change_embedding` top-K (예: K=20).
2. 각 ChangeItem에서 `[:OF_PROCESS_TYPE]`, `[:HAS_GRADE]`, `[:APPLIES_TO_LINEUP]`,
   `(<-[:INCLUDES_CHANGE]-)(:Project)-[:TARGETS_REGION]->(:Region)`로 메타 매칭 점수 부여.
3. fulltext index(`ft_change_reason`)에서 핵심 키워드(BM25) 점수 hybrid 합산.
4. 최종 결과 ChangeItem 노드 → `[:FROM_PART]`/`[:TO_PART]`로 실제 부품과 BOM 상위·하위로 확장 가능.

→ **이 시나리오를 위해 ChangeItem이 임베딩 보유 + 모든 컨텍스트가 1-hop 거리에 있도록 설계함**.

### 5.2 ② BOM 서브트리 영향분석 (1순위)
**입력**: 어떤 Part No (보통 Assembly) 또는 (Model, Part No).
**경로**:
1. Anchor Part 노드 찾기.
2. `(anchor)-[:HAS_CHILD*1..6 {model_code: $M}]->(descendant:Part)` 가변길이 traversal —
   model_code edge property로 필터링하여 다른 모델 BOM 섞임 방지.
3. 각 descendant마다 `<-[:FROM_PART|TO_PART]-(:ChangeItem)` 로 과거 변경 모음.
4. 변경사유를 추출하여 **부분 트리에서 어떤 모듈/이유가 자주 손대지는지** 집계.
5. 트리에 fan-out이 큰 지점(예: ABU74573228 같은 Cavity Assembly)에서 변경 hotspot 식별.

→ **이 시나리오를 위해 BOM이 `[:HAS_CHILD]` 단일 관계 + model_code edge property로 인덱싱됨**.

### 5.3 부수 패턴들
- **부품 이력 카드**: `(:Part {part_no})` 한 노드 기준으로 `[:FROM_PART|TO_PART]<-(:ChangeItem)`, `[:REPLACED_BY*]` 양방향 확장, `[:HAS_CHILD]` 부모/자식 펼치기, GradeReview·ApprovalTest 모음.
- **공급사 신뢰도**: `(:Supplier)<-[:SUPPLIED_BY]-(:ChangeItem)-[:TESTS_BY*0..]-(:ApprovalTest {final_judgment:'OK'|'NG'})` 비율 집계.
- **양식 진화 추적**: `(:Document {family})` ↔ `(:DocVersion)` 통해 양식이 v1.0→v1.2로 바뀐 사이의 데이터 결측 차이 확인 (스키마 마이그레이션 근거 자료).
- **사람 협업 그래프**: `(:Person)-[:OWNED_BY|:ATTENDED_BY*]-(:ChangeItem|:GradeReview)` 로 누가 어떤 모듈에 자주 관여했는지.

### 5.4 RAG 결과 인용
ChangeItem이 검색되면 `source_file/source_sheet/source_row`로 원본 엑셀 셀을 즉시 인용 가능
(임베딩에 들어간 `text_for_embedding` 원문은 별도 저장되어 있어 재계산도 안전).

---

## 6. ETL 적재 순서 (의존성 그래프)

```
1. 카테고리/마스터 (먼저):
   :Grade, :PartType, :Event, :Lineup, :Region, :Module, :DocVersion
   ← 정적 enum YAML 또는 분석 결과 alias map에서 일괄 적재

2. 외부 행위자:
   :Person   ← history/*/SSOID 시트 + 모든 시트의 담당자 컬럼에서 union
   :Supplier ← 양산처/금형처/공급처 컬럼 + Supplier Code 시트 union

3. 문서 메타:
   :Document → :Sheet  ← 파일 walk + 시트 메타 추출 (sha256 포함)
   :DocVersion         ← '통합 v1.2'의 History 시트 + 'Single IH'의 History 시트

4. 부품 마스터:
   :Part  ← uploads/base_bom.xlsx + ref_boms/*.xlsx + history Master 시트들 union
           - part_no 기준 MERGE
           - 가장 풍부한 description/tech_spec 선택 (longest non-null)
           - 임베딩은 별도 배치 잡

5. BOM 엣지:
   :Part-[:HAS_CHILD {model_code, qty, lvl, …}]->:Part
   ← base_bom + ref_boms 행 단위 적재
   :Model-[:HAS_BOM_ROOT]->:Part (Lvl=0 행)

6. 프로젝트:
   :Project ← 시트 상단 메타(Base/New Model, Buyer, Event, 양산일자)에서 추출
              - region은 파일명/시트명에서 derive
              - 한 파일이 여러 등급 시트를 가지면 시트 묶음별로 Project 1개 또는
                파일 = Project 1개로 통일 (권장: 파일 1개 = Project 1개)

7. 변경 이벤트 (대량):
   :ChangeItem  ← 각 history Master 시트 / base_master 시트의 데이터 행마다 1개
   엣지: INCLUDES_CHANGE, FROM_PART, TO_PART, APPLIES_TO_LINEUP,
        OF_PROCESS_TYPE, OF_BOM_ROLE, IN_MODULE, HAS_GRADE,
        SUPPLIED_BY {role}, OWNED_BY {role}, HAS_ROW
   - 매트릭스형(④)은 Best-1~Good-2 BK 컬럼을 펼쳐 APPLIES_TO_LINEUP 다중 생성.
   - 시트 단위 양식(①②③)은 시트→Lineup 매핑으로 1개 생성.

8. 품질/심의:
   :GradeReview, :ApprovalTest, :TestPlan
   ← 부품등급심의회 시트, 부품인정시험항목 시트, *_시험기획 시트
   - part_no로 ChangeItem 찾아 [:DECIDED_FOR]/[:TESTS]/[:PLANS] 연결
   - 못 찾으면 ChangeItem 대신 Part에 [:REVIEWS_PART]로만 연결

9. 임베딩 배치:
   :Part.embedding_part, :ChangeItem.embedding_change,
   :Project.embedding_project, :GradeReview.embedding_review
   ← 모두 적재된 뒤 별도 배치 잡으로 일괄 생성 (모델 교체 시 재계산 용이)

10. 파생 관계:
    :Part-[:REPLACED_BY]->:Part  ← ChangeItem(action=CHANGE) 1건당 1개 (cypher 일괄 잡)
    :Part-[:SIMILAR_TO {score}]->:Part  ← 임베딩 코사인 top-K 배치 (옵션)
```

---

## 7. 정규화 규칙 (적재 시 일관성 유지)

| 영역 | 규칙 |
|---|---|
| Part No | 좌우 공백 strip, 대문자, 한자 점(`…`)→`...` 변환 후 PK |
| `change_action` | 대소문자/오타 정규화: `New|NEW`→`NEW`, `Change\|Changing\|Chainging`→`CHANGE`, `Common`→`COMMON`, `Delete`→`DELETE`, `← \| - \| X` + base 존재 → COMMON으로 추정 |
| Lineup | 시트명에서 `(BK STS)`, `(BK Glass)` 추출 → `BEST_1_BK_STS` 식 lineup_code 생성. 매트릭스 컬럼명도 동일 매핑. |
| Grade | `c → C`, `cb → Cb` 등 대소문자 통일. `-` 또는 빈 값은 미지정 → :Grade 연결 안 함. |
| Date | `'25.09`, `25.01`, `25.07.23` 혼재 → YYYY-MM-DD 또는 YYYY-MM string로 파싱. 실패 시 `mass_prod_year_month` 원문에 보존. |
| Flag (○/X/●/-) | `○|●|O|o|Y` → true, `X|x|N` → false, 그 외 → null |
| Region | 파일명 substring 매칭: `포르투갈\|그리스` → `EU_S_PT_GR`, `호주` → `OCE_AU` 등 alias map 유지 |
| Person | "박세훈"처럼 한글이름만 있는 경우 → name_kr로 등록, ssoid는 nullable로 두고 SSOID 시트와 매칭 시 채움 |
| 임베딩 입력 | `text_for_embedding = " | ".join([change_point, change_reason, selection_background, part_name])` — 빈 값 제외 |

---

## 8. 트레이드오프 / Open Questions

1. **Lineup vs. ChangeItem 복제**
   매트릭스형은 한 행이 8개 Lineup에 동시에 1로 표시될 수 있다. 현재 설계는 ChangeItem 1개 + 8개 APPLIES_TO_LINEUP 엣지로 줄였다. 만약 Lineup별로 변경점/사유가 **다르게 기재되는 케이스**가 있다면 ChangeItem을 Lineup별로 복제해야 한다 → 데이터 점검 필요.

2. **Part canonicalization 정확성**
   `EAG60744702 → EAG00460901`처럼 신규 P/No로 바뀌면 둘 다 `:Part` 노드로 존재한다. 동일성을 더 엮고 싶다면 `[:REPLACED_BY]` 외에 `[:SAME_FUNCTION_AS]` 같은 의미 관계를 추가할 수 있음 (단, 그래프 폭증 주의).

3. **BOM multi-edge 비용**
   `(:Part)-[:HAS_CHILD]->(:Part)`에 모델별 multi-edge를 두면 인기 부품(공용)에서 엣지 수가 폭증할 수 있다. 대안: BOM별로 `:BomInstance` 중간 노드를 두고 `(Part)<-[:USES]-(:BomInstance)<-[:ROOT_OF]-(:Model)` 패턴. → 트래버설 hop이 늘지만 BOM scope 쿼리는 더 깔끔해짐. **현재는 multi-edge로 시작, BOM 수가 200+ 모델로 늘면 재검토.**

4. **DMS 필수성 메타데이터 위치**
   양식 헤더 아래의 `필수/조건부/옵션` 라벨은 데이터가 아니라 메타이므로 그래프에 넣지 않고 별도 YAML(예: `dms_field_requirements.yaml`)로 두는 것을 권장. ETL 검증에만 사용.

5. **req_id/rec_id(=`final_recommendations.xlsx`)**
   온톨로지에 포함시킬 것인가? 현재는 **포함 안 함** — 출력 산출물이지 history 자체가 아니므로. 만약 사용자 피드백을 다시 history에 학습 데이터로 쓰려면 `:Recommendation`, `:Feedback` 노드를 추가 (참조: `feedback_chat.py`의 `fb_history`).

6. **공정/공장심사 등 enum의 ○/X**
   해당 항목들은 ChangeItem 속성으로 모았지만, **활동 자체를 1급 시민 노드(:QualityActivity)로 모델링**하면 "공정심사 미실시 사유"까지 추적 가능. 데이터 빈도 보고 결정 권장.

7. **Region/Buyer 일관성**
   Buyer(LGEUR/LGEUE/…)는 Region과 별개 차원. 시트마다 Base.Buyer ≠ New.Buyer 케이스(국가 파급)가 잦으므로 `(:Project)-[:BASE_BUYER]->(:Buyer)`, `[:NEW_BUYER]->(:Buyer)`를 두면 파급 패턴 분석에 유리. (현재 설계는 Region만 1차 노드, Buyer는 Model 속성.)

---

## 9. 확장 포인트

- **Activity node**: `s-APQP`, `Factory Audit` 등을 별도 `:QualityActivity` 노드로 승격 → "어떤 활동이 어떤 등급에서 어떤 빈도로 실시되었는가"를 도메인 KPI로 추출 가능.
- **Embedding 갱신 큐**: 임베딩 모델 교체 시 `:ChangeItem {embedding_version:'v1'}` 같은 라벨/속성으로 마이그레이션 추적.
- **사용자 피드백 루프**: `(:User)-[:FED_BACK {label}]->(:Recommendation)-[:DERIVED_FROM]->(:ChangeItem)` 로 retrieval 품질을 학습 신호로 환원.
- **Time slice**: ChangeItem.mass_proto_date를 기반으로 `WHERE n.mass_proto_date >= date('2024-01-01')` 형태로 "최근 2년 사례만"이라는 RAG 필터를 쉽게 추가.

---

*이 온톨로지는 schema_analysis.md의 5개 양식 패밀리를 단일 그래프로 통합하기 위한 1차 설계안. 실제 Cypher 적재/조회 쿼리는 본 문서를 기준으로 별도 작성한다.*
