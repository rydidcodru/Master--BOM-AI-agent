# `data/` 폴더 엑셀 파일 분석 — 개발부품 Master 스키마 설계용 정리

> 목적: `data/` 하위에 흩어져 있는 과거/현재 개발부품 Master, BOM, 산출물 엑셀을
> 모두 열어 시트·컬럼·행 구조와 상호 관계를 파악하여 **신규 통합 스키마 설계**에
> 사용할 수 있는 형태로 정리한다.

---

## 1. 폴더 구조 한눈에 보기

```
data/
├── camera_example.xlsx                # (참고) 카메라 사업부 Single 시트 예시
├── 참고용_Extra_결과.xlsx               # Best/Better/Good 그레이드별 변경부품 결과 예시
├── case_meta.sqlite                   # (메타DB, 본 문서 범위 외)
├── history/                           # 24개 과거 개발부품 Master / 개발완료 List
├── outputs/
│   └── final_recommendations.xlsx     # 시스템 최종 추천 결과 (앱 산출물)
├── ref_boms/                          # 모델별 Reference BOM (ag-grid 포맷)
│   ├── LSIU6339XE.ARSLLGA@CVZ.EKHQ 1.0.xlsx
│   └── WDEK9429S.ATTLSNA@CVZ.EKHQ 1.0.xlsx
└── uploads/                           # 현재 작업 입력 (앱이 직접 읽음)
    ├── base_bom.xlsx                  # Base 모델 BOM
    └── base_master.xlsx               # 변경부품 Master(=과거 history와 같은 양식)
```

`app.py`/`enrich.py` 등 코드 기준 흐름:
`uploads/base_bom.xlsx` + `uploads/base_master.xlsx` → 처리 → `outputs/final_recommendations.xlsx`.
`ref_boms/`는 부품 역조회·신규 모델 BOM 참고용, `history/`는 과거 사례 검색(RAG) 소스.

---

## 2. 파일 그룹별 역할

| 그룹 | 위치 | 역할 | 대표 파일 |
|---|---|---|---|
| **Base BOM** | `uploads/`, `ref_boms/` | 모델 단위 부품 트리 (Lvl, Parent Part No 등) — ag-grid에서 export된 BOM | `base_bom.xlsx`, `LSIU6339XE…1.0.xlsx` |
| **Base / Working Master** | `uploads/` | 현재 작업 중인 변경부품 Master(Best/Better/Good 컬럼 보유) | `base_master.xlsx` |
| **History Master** | `history/` | 과거 모델 개발부품 Master — 양식이 시기/사업부별로 다름 (DMS, BO24, 통합, Single IH …) | `★통합 개발부품Master Integrated Dev Part Master v1.2 동유럽향.xlsx` 외 22건 |
| **Output** | `outputs/` | 앱 산출물 (req_id/rec_id 기반 추천) | `final_recommendations.xlsx` |
| **Reference (예시)** | data 직하 | 양식 학습/예시용 | `camera_example.xlsx`, `참고용_Extra_결과.xlsx` |

---

## 3. 양식(스키마) 패밀리 식별

`history/` 24개 파일을 양식별로 묶으면 **5개 패밀리**로 나뉘며, 컬럼 구성/병합셀
헤더가 패밀리 안에서는 거의 동일하다.

### ① BO24 시리즈 (가장 풍부) — "New & Changing Part Development List"
- 파일 예: `BO24 북유럽 CE 개발부품 마스터 250113.xlsm`, `BO24 포르투갈 그리스 …`,
  `BO24 호주향 / 사우디향 / 이집트향 / 북미 …`, `이라크/UAE 24인치 …`.
- 시트 구성(전형): `변경부품 list_Best1` / `_Better1` / `_Good1` / `_Good2` (등급별 시트 분할), `부품등급 심의회의록`, `부품인정시험항목`, `<모듈>_시험기획`, `SSOID`, `신규부품리스트`, `PartDMS설명서`, `FCM`.
- 헤더 위치: 보통 8~9행. 헤더가 2행 병합(공통/세부) 구조.
- 핵심 컬럼군 (BO24 공통):
  - 식별: `No.`, `BOM Level`, `Part Type`(절삭/판금/사출/회로/기구…), `Base P/No`, `New P/No`, `Class Desc.(Part Name)`, `Fig.`, `Quanty`
  - 변경: `변경점` / `변경사유` / `구분(New/Change/Common/Delete)`
  - 책임자: `설계자`, `부품개발`, `금형처`, `양산처`, `금형 개발`, `사내제작 or 직거래`
  - DRBFM: `Design`, `Process`, `Why don't you execute DRBFM?`
  - 도면 일정: `심의도`, `도면합의`, `확정도`, `도면결재/배포(GPDM)`, `양산구매`
  - 부품 사양 플래그: `표준/비표준`, `비표준 사유`, `그리스 도포 여부`, `CTQ 대상`, `핵심부품 대상`, `장납기 여부`, `4신여부`, `APQP`, `고도화`, `수율 관리 대상 부품`, `한도 견본(샘플)`
  - 시험: `인정시험 실시`, `부품품질`, `서류항목`, `시험항목`, `공정진단`, `공장심사`
  - 초품: `초품일정`, `금형 초품`, `금형 이관`, `HSMS`, `양산초품`
- DMS 기준행: 시트 11~12행에 `DMS기준행 C / U / V / H / E / F / J / K / I / AJ / AG …`,
  바로 아래에 `필수/조건부/옵션` 라벨 → **DMS(부품개발등록시스템) 컬럼 코드 매핑**과
  **필수/옵션 여부**가 양식 안에 내장되어 있다.

### ② 통합 v1.2 (★ 표시 파일) — 2025년 신규 통합 양식
- 파일: `★통합 개발부품Master Integrated Dev Part Master v1.2 동유럽향.xlsx`,
  `… 싱가포르향 (1) (1).xlsx`.
- 시트: `History`(버전 이력), `Good-1 BK`, `Good-2 BK`, `Good-1 STS`, `Good-2 STS` …
- History 시트에서 추적되는 버전: v1.0(2025.06.23 신규제정) → v1.1(FMEA 공통부) →
  v1.2(2025.09.09 해외라인업/CKD Item 추가). 작성자 모두 `김동욱(kims.kim)`.
- 헤더 구조(8~9행 2행 병합):
  - 공통(Common): `No.`, `BOM Level`, `Part Type`, `Base P/No`, `New P/No`,
    `부품명(Base/New)`, `Q'ty`, `변경점/변경사유(Changing Point/Reason)`,
    `양산처(Supplier, CKD 유무 포함)`, `신규/변경 부품 대상(Classification)`,
    `금형 개발/수정 Mold Dev/Modify(○/X)`, `사내 제작 In-house Prod.(○/X)`,
    `부품 인정시험 실시 여부 Part Approval Test(○/X)`.
  - Cb 등급 Only: `설계 FMEA 실시(○/X)`, `미실시 사유`.
  - 부품등급심의(Parts Grade Review): `부품 등급(Part Grade)`,
    `수율 관리(Yield Management)`, `부품 추적성(Part Tracking)`, `s-APQP`,
    `공정or공장심사(Factory/Process Audit)`, `Remark`.
  - 리빙/빌트인쿠킹 Only: `부품 승인`, `부품인정`, `시방인정`, `선정 배경`.
- 메타데이터(시트 상단 1~7행): `Base Model/Grade`, `New Model/Grade`, `Event`(CP/DV/PV/PreMP).
- 시트 = 등급(Best/Better/Good × Color), 즉 **(모델 × 마감/색) 한 조합당 한 시트**.

### ③ Single IH 등 — `개발부품Master Single IH Final.xlsx` 패밀리
- 시트: `History`(자체 버전이력), `Master`, `부품등급심의회`, `PRA 평가`(Part Risk Assessment 점수표).
- Master 시트 컬럼은 통합 v1.2와 거의 동일하나 일부 컬럼(`전수검사 구축`, `도면리뷰 부품`, `칸칸 대차 적용`, `SQA 초품/수입검사지도서`, `HSMS FCM`, `신규재질/색상`, `Antibacterial`, `Substance(CAS No.)`)이 추가됨.
- 즉 **통합 v1.2의 상위 superset**으로 볼 수 있음.

### ④ 그레이드 매트릭스형 — `개발변경부품Master Best/Better/Good 221226.xlsx`,
   `개발변경부품리스트 전개모델 230110.xlsx`, `base_master.xlsx`
- 시트: `개발변경부품리스트`, `부품 등급 Guide`.
- 헤더(2행): `No. | Module | CMDT | 도입 | 신규 | Lvl | Picture | 설계 | 구매 | 사내 | 입고 | 양산처 | Supplier Code | P/no. | Desc. | Part Grade | Best-1 | Best-2 | Better-1 | Better-2 | Good-1 | Good-2 | Good-1 BK | Good-2 BK | SVC 대상부품 | 선정 근거 | 상세 내용`.
- **특징**: 한 행 = 부품, 그레이드 컬럼(Best-1 ~ Good-2 BK)에 `수량(1/2)` 또는 공란을 두어
  어느 그레이드에 적용되는지를 표시 → 즉 **(부품 ↔ 그레이드) 적용 매트릭스**.
- `Lvl`은 BOM 들여쓰기 점 표기(`.1`, `..2`, `...3`, `....4`)로 BOM Tree 깊이를 보존.

### ⑤ "신규개발리스트" 가로형 — `UAE 24인치 …`, `이라크 24인치 …`, `BO24 북미 부품 개발 완료 …`
- 시트: `신규 개발리스트`, `신규 개발리스트_<나라>`, `부품등급심의회회의록`, `FCM`.
- 헤더(8~9행 2행): 변경 부품 리스트 / `P/no.(Base/New)` / `Qty(Best/Better/Good)` /
  변경 상세 내용(`변경점`,`변경사유`,`EUR 확대`) / 업체 선정(`설계`,`개발구매`,`공급처`,`양산처`) /
  금형투자(`투자`,`투자금액`,`금형처`,`금형일정`) / FMEA Execution / 도면일정 / 부품 사양 / 시험 / 단가.
- BO24와 거의 같은 정보지만 컬럼 정의가 가로로 평탄화되어 있고 **그레이드 컬럼(Best/Better/Good)**이 직접 들어가 있어 ④와 ①의 혼합형으로 볼 수 있음.

> **요약**: 양식이 어떻든 핵심은 결국 다음 5개 도메인이다.
> (1) 부품 식별 (Base P/No → New P/No, Class Desc, BOM Level, Part Type)
> (2) 변경 정보 (변경점 / 변경사유 / 구분 New·Change·Common·Delete)
> (3) 책임/소싱 (설계자, 개발구매, 양산처, 금형처, 사내제작 여부)
> (4) 품질 게이트 (부품등급, FMEA, DRBFM, s-APQP, CTQ, 4新, 수율, 한도 견본, 부품인정/시방인정/부품승인, 공장·공정심사)
> (5) 일정 (도면 심의/합의/확정/결재, 금형 초품, 양산초품, 양산일자, Event=CP/DV/PV/PreMP/MP)

---

## 4. 파일별 상세

### 4.1 `uploads/base_master.xlsx` — 현재 작업 입력 Master
- 시트:
  - `개발변경부품리스트` — 헤더 2행(r2), 데이터는 r4부터.
  - `부품 등급 Guide` — 비어 있음(가이드 자리).
- 컬럼(④ 패밀리와 동일):
  `No, Module, CMDT, 도입, 신규(New/Common/Change), Lvl(.1 ..2 …), Picture, 설계, 구매, 사내, 입고(●), 양산처, Supplier Code(KR…), P/no., Desc., Part Grade(C 등), Best-1, Best-2, Better-1, Better-2, Good-1, Good-2, Good-1 BK, Good-2 BK, SVC 대상부품, 선정 근거, 상세 내용`.
- 예시: `AGG74419316 / Packing Assembly,Electric Oven / Grade=C / Best~Good 모두 1` 처럼 그레이드 매트릭스 형태.

### 4.2 `uploads/base_bom.xlsx`, `ref_boms/*.xlsx` — Base BOM (ag-grid export)
- 시트명 모두 `ag-grid`, 헤더 1행, 데이터 2행~ (대량 BOM, 1만+ 행 가능).
- 컬럼(36):
  `Part No, Lvl(0/.1/..2 … 표기), I.S, MR, Document(체크아웃 ID), Check Out,
   Change(ModelECO 등), R, Parent Part No(모), Part Name(자), Description,
   Technical Spec, Qty, UOM, UIT(F/S/T/G/P/X 등), Supply Type(Phantom/Assembly Pull/Supplier),
   Designator, SVC Loc, SVC, CKD, From CKD, Designator/Split Qty, Designator/Split Comments,
   BOM Exp. Flag, Job Explanation, SC, Maker, Standard, Substitute For, Copy From,
   Start Date, End Date, Type, Change In, Change Out`.
- 첫 행이 모델 자체(Part No = 모델코드 = Parent), `Lvl=0` → 이후 점 들여쓰기로 트리 구성.
- `ref_boms/`의 두 파일은 모델 단위 참고용(`LSIU6339XE`, `WDEK9429S`).

### 4.3 `outputs/final_recommendations.xlsx`
- 시트: `final_recommendations`, 12컬럼.
- 컬럼: `req_id, rec_id, part_no, part_name, bom_level, change_action,
  sourcing, confidence, rationale, parent_assy, group_id, final_status`.
- 의미:
  - `req_id`: 요청 ID(앱 내부 요청 단위).
  - `rec_id`: 추천 ID(요청당 N개 후보).
  - `change_action`: New/Change/Common/Delete (변경구분).
  - `sourcing`: 양산처/공급망 추천.
  - `confidence`: 0~1 모델 신뢰도, `rationale`: 추천 근거 텍스트.
  - `parent_assy`: 상위 Assembly Part No (BOM 트리 참조).
  - `group_id`: 동일 묶음(예: 같은 변경사유) 그룹핑.
  - `final_status`: CONFIRMED 등 사용자 확정 상태.

### 4.4 `참고용_Extra_결과.xlsx`
- 단일 시트(Sheet1). 상단 메타: `Base Model/Grade=WSED7667M / B`, `New=WS9D7688M / B`,
  `Event=CP DV PV PreMP`.
- 헤더 8~9행: `No. | BOM Level | Part Type | P/No(Base/New) | 부품명(Base/New) | Q'ty | 변경점 | 변경사유 | 양산처 | 신규/변경 부품 대상 | 금형 개발/수정(○/X) | 사내 제작(○/X) | 부품 인정시험 실시(○/X)` — **통합 v1.2 양식의 단일 시트 축소판**.
- 데이터 예시: `EAG60744702 → EAG00460901 / Jack,DC Power / Change / "3점식 Meat Probe로 변경"`.

### 4.5 `camera_example.xlsx`
- 단일 시트, 헤더 9행. BO24 시리즈 헤더 구조의 단순 예제(20컬럼).
- DMS 기준행(컬럼 알파벳 코드 C/U/V/H/E/F/J/K/I/AA)이 헤더 바로 아래에 들어가 있어
  **DMS 컬럼 매핑 학습용 sample**로 활용 가능.

### 4.6 `history/` 24개 파일별 메모

| 파일 | 패밀리 | 핵심 시트 | 비고 |
|---|---|---|---|
| `240430 BDO30 SKS Transitional 개발부품Master List Ver.20200814 Part DMS v2.xlsx` | (heritage) | — | XML 손상으로 openpyxl 오픈 실패(엑셀 정식 오픈은 가능). 양식: 2020년 DMS Part 양식 |
| `250213 사우디 개발부품Master List.xlsm` | ① BO24 | `변경부품 list (BK STS)`, `(BK Glass)`, `부품인정시험항목(BK STS/Glass)`, `신규부품리스트` | (BK STS/BK Glass) = 색·마감 그레이드 시트 분할 |
| `250424 모리셔스향 파급 개발 개발부품마스터.xlsx` | ① BO24 | `WSED7667M(Best)`, `WS7D7632WB(Good)` … | 시트명 = 모델P/N(등급) |
| `BO24 북미 부품 개발 완료 Activity 240409.xlsx` | ⑤ 신규개발리스트 가로형 | 신규 개발리스트 | |
| `BO24 북유럽 CE 개발부품 마스터 250113.xlsm` | ① BO24 | `변경부품 list_Best1/Better1/Best/Better/Good1,BK/Good2,BK`, `부품등급 심의회의록`, `부품인정시험항목`, `Best-1_시험기획` | 등급 × 색 시트 풀세트 |
| `BO24 오븐 사우디향 부품 개발 완료 Master 240214.xlsx` | ① BO24 | 변경부품 list | |
| `BO24 오븐 이집트향 부품 개발 완료 Master 240306.xlsx` | ① BO24 | 변경부품 list | |
| `BO24 유럽향 VI Steam Heater 개발 부품 마스터.xlsm` | ① BO24 | 변경부품 list | |
| `BO24 포르투갈 그리스 부품 개발 완료 Activity 240627.xlsm` | ① BO24 + ⑤ 일부 | `Master`(텍스트 요약), `변경부품 list_Better1`, `부품등급 심의회의록`, `Controller_시험 기획`, `SSOID`(담당자코드표), `신규부품리스트`, `PartDMS설명서` | DMS 컬럼 설명 시트 있음 — **스키마 설계 시 정의서**로 활용 추천 |
| `BO24 포르투갈, 그리스 개발부품 마스터 240516.xlsm` | ① BO24 | 변경부품 list | |
| `BO24 호주 B700 nonpyro 개발부품 마스터 241120.xlsx` | ① BO24 | `변경부품 list_Best1`, `부품등급 심의회의록`, `부품인정시험항목` | |
| `BO24 호주향 개발부품 마스터 240221.xlsm` | ① BO24 | 변경부품 list | |
| `UAE 24인치 오븐 부품 개발 List 230731.xlsx` | ⑤ 가로형 | `신규 개발리스트`, `신규 개발리스트_UAE`, `부품등급심의회회의록`, `FCM` | |
| `이라크 24인치 오븐 부품개발 완료 List 230731.xlsx` | ⑤ 가로형 | 동일 | UAE와 자매 파일 |
| `★통합 개발부품Master Integrated Dev Part Master v1.2 동유럽향.xlsx` | ② 통합 v1.2 | `History`, `Good-1/2 BK`, `Good-1/2 STS` | 2025.09 최신 통합양식 |
| `★통합 개발부품Master Integrated Dev Part Master v1.2 싱가포르향 (1) (1).xlsx` | ② 통합 v1.2 | 동일 | 향(나라)만 다름 |
| `개발변경부품Master Best 221226.xlsx` | ④ 그레이드 매트릭스 | `개발변경부품리스트`, `부품 등급 Guide` | |
| `개발변경부품Master Better 221226.xlsx` | ④ | 동일 | Better 분리본 |
| `개발변경부품Master Good 221226.xlsx` | ④ | 동일 | Good 분리본 |
| `개발변경부품리스트 전개모델 230110.xlsx` | ④ | `제품 Spec. 정리`(Base/전개모델 차이점), `개발변경부품리스트`, `부품 등급 Guide` | **그레이드 전개 규칙 정의서** |
| `개발부품Master Single IH Final.xlsx` | ③ Single IH | `History`, `Master`, `부품등급심의회`, `PRA 평가` | PRA 평가 시트 = 부품 Risk Assessment 항목/점수 기준 — 스키마 enum 후보 |
| `개발부품마스터 BO24 CIS Best/Better.xlsx`, `개발부품마스터 BO24 동유럽 Better 250424.xlsx` | (BO24 + 매트릭스 혼합) | `Master`, `Best`/`Better` | `Master` 시트는 부품 타입별 카운트, `Best/Better` 시트가 본 데이터 |

---

## 5. 파일 간 상관관계 (데이터 흐름)

```
                    ┌─────────────────────────┐
ref_boms/*.xlsx ──► │  BOM 트리 (Lvl, Parent) │ ◄── 모델별 reference
                    └─────────────┬───────────┘
                                  │  (Part No 매칭)
                                  ▼
uploads/base_bom.xlsx  ──►  현재 작업 모델 BOM
                                  │
                                  │  (Base P/No, New P/No, Lvl, Class Desc)
                                  ▼
uploads/base_master.xlsx ──► 변경부품 Master (Best/Better/Good 매트릭스)
                                  │
                                  │  + history/ 의 과거 사례 (RAG)
                                  ▼
                       ┌─────────────────────┐
                       │ 앱 처리 (enrich.py, │
                       │  build_rag.py 등)   │
                       └──────────┬──────────┘
                                  ▼
                  outputs/final_recommendations.xlsx
                  (req_id, rec_id, part_no, change_action, …)
```

### 5.1 키(조인) 관계

| 관계 | from | to | 조인 키 |
|---|---|---|---|
| BOM ↔ Master | `base_bom.ag-grid.Part No` | `base_master.개발변경부품리스트.P/no.` / `New P/No` | **Part No** (= `P/no.`, = `Base P/No` 또는 `New P/No`) |
| BOM ↔ BOM 자기참조 | `base_bom.Parent Part No(모)` | `base_bom.Part No` | Part No (트리) |
| Master ↔ Output | `base_master.P/no.` (혹은 history의 `New P/No`) | `final_recommendations.part_no` | Part No |
| Output ↔ Output 자기참조 | `final_recommendations.parent_assy` | `…part_no` | Part No |
| 등급 시트 ↔ 메타 헤더 | `<sheet>.r2~r4 Base/New Model/Grade, Event` | 시트 본문 부품 | (모델, 등급, Event) 컨텍스트 |
| 등급심의회 ↔ Master | `부품등급심의회.Part No` | `Master.New P/No` | Part No |
| 시험기획 ↔ Master | `<모듈>_시험기획.품번` | `Master.New P/No` | Part No |
| 부품인정시험항목 ↔ Master | `부품인정시험항목.P/No` | `Master.New P/No` | Part No |
| 신규부품리스트 ↔ DMS | 자체 컬럼이 `Key-In/LOV/N/A` 라벨로 DMS 등록 입력 양식임 | DMS 시스템 | (외부 시스템) |

### 5.2 변경 식별(작성 로직) 패턴
모든 패밀리에서 한 행 = 한 부품 변경이고, 변경 종류는 `Base P/No` × `New P/No`
조합으로 결정된다:
- `Base = "-"`, `New = 값`  → **신규(New)**
- `Base = 값`,  `New = "X"` 또는 `-` → **삭제(Delete)**
- `Base = 값`,  `New = 다른 값` → **변경(Change)** (하위 변경, 외관 변경, 언어 변경 등)
- `Base = 값`,  `New = "←"` 또는 `Base = New` → **공용/Common**
이 로직이 `구분`/`Classification`/`신규(New/Common/Change/Delete)` 컬럼에
한국어·영어 혼용으로 직렬화되어 있다 → **enum 정규화 대상**.

### 5.3 그레이드(Best-1, Best-2, Better-1, …, Good-2 BK) 의미
`개발변경부품리스트 전개모델 230110.xlsx`의 `제품 Spec. 정리` 시트에 매핑이 명시됨:

| 기본 | 전개모델 | 개발등급 | 차이점 |
|---|---|---|---|
| Best-1 | Best-2 | C2 | S/W 변경, Meat Probe 삭제 |
| Better-1 | Better-2 | D | Color 변경 Matte BK→STS, Telescopic 3단→2단 |
| Good-1 | Good-2 | D | Telescopic(2단) 삭제 |
| — | Good-1_BK | D | Color 변경 STS → Matte BK |
| — | Good-2_BK | D | Telescopic 삭제 + Color STS → Matte BK |

→ 그레이드 = `(라인업등급) × (Color/마감)`의 곱. 통합 v1.2에서는 시트명으로 분리,
④ 매트릭스형에서는 컬럼으로 분리.

### 5.4 DMS 컬럼 매핑(중요)
BO24/카메라 양식 헤더 바로 아래 `DMS기준행`은 각 컬럼이 사내 **Part DMS(부품개발등록시스템)**의
어느 컬럼에 대응되는지를 알파벳 코드로 박아둔 부분이다. 예:
- `No.`→C, `사내제작/직거래`→U, `Part Type`→H, `Base P/No`→E, `New P/No`→F,
  `Class Desc.`→I 등.
또 그 아래 행에 `필수 / 조건부 / 옵션`이 라벨링되어 있어 **신규 스키마 필드의 NOT NULL 여부**의 1차 근거가 된다. 자세한 정의는 `BO24 포르투갈 그리스 … Activity 240627.xlsm`의 `PartDMS설명서` 시트에 항목별 설명이 있다.

---

## 6. 신규 통합 스키마 설계 가이드라인

### 6.1 정규화된 엔티티 후보

```
Model
├── model_code (PK)            e.g. WSED7667M
├── grade_code                 e.g. B, Cb, D
├── buyer                      e.g. LGEUR, LGEUE, LGEUN, LGEUS, LGF
├── brand                      e.g. LG, LG SIGNATURE
├── set_pno
├── mass_prod_date
└── event                      e.g. "CP DV PV PreMP"

Project (= 한 개발 건)
├── project_id (PK)
├── name                       e.g. "BO24 포르투갈향 Better"
├── base_model_id (FK→Model)
├── new_model_id  (FK→Model)
├── dev_grade                  e.g. Ca, Cb, D
├── target_region              e.g. 동유럽향, 싱가포르향
├── lineup                     e.g. Best-1/Best-2/Better-1/Better-2/Good-1/Good-2/Good-1 BK/Good-2 BK
├── volume                     e.g. "800대/年"
├── reason                     e.g. "24인치 오븐 포르투갈 파급…"
└── schedule (DV/PV/PreMP/MP 일자)

Part (마스터)
├── part_no (PK)               e.g. AGG74419316
├── name                       Class Desc.(Part Name)
├── description / tech_spec
├── default_supplier_id
├── default_maker
├── standard_flag, svc_flag
└── …

BomEdge  (BOM 트리)
├── model_id (FK)
├── parent_part_no
├── child_part_no
├── lvl (int)                  0/1/2/3/… (점 갯수)
├── qty, uom, uit, supply_type, designator, document, change_action
└── PK = (model_id, parent_part_no, child_part_no, designator)

ChangePartItem (= "변경부품 list" 한 행)
├── change_id (PK)
├── project_id (FK)
├── lineup (Best-1/…/Good-2 BK)         ← 그레이드 시트 또는 매트릭스 컬럼 출처
├── seq_no                              ← No.
├── base_part_no (FK→Part, nullable)
├── new_part_no  (FK→Part, nullable)
├── bom_level (text or int)
├── part_type           e.g. Structure Assy, PD Part, Raw Material, Printing Material,
│                              사출, 절삭, 판금, 회로, 전장, 기구, Ass'y
├── quantity_base, quantity_new
├── change_action       enum(New, Change, Common, Delete)
├── change_point        ← 변경점
├── change_reason       ← 변경사유 (한글 10자 이상)
├── supplier_id (FK→Supplier)
├── designer_id (FK→Person)
├── purchaser_id (FK→Person)
├── mold_supplier_id (FK→Supplier)
├── prod_supplier_id (FK→Supplier)
├── ckd_flag
├── mold_dev_flag, in_house_flag, approval_test_flag
├── part_grade           e.g. S, A, B, Ca, Cb, D
├── grade_basis          ← 선정 배경
├── flags (○/X 군):
│     - yield_mgmt, part_traceability, s_apqp,
│       factory_or_process_audit, part_approval, part_certification,
│       spec_certification, ctq_target, key_part_target,
│       long_leadtime, four_new (4新), apqp, high_skill,
│       limit_sample, design_fmea, process_fmea, drbfm_design, drbfm_process,
│       drawing_review, drawing_register
├── env_flags:  food_contact, hsms_fcm, new_material_color,
│               new_material_supplier, antibacterial,
│               substance_cas, biocidial_supplier
├── schedule:
│     dwg_review, dwg_agree, dwg_confirm, dwg_dispatch_gpdm,
│     mold_proto, prod_proto_request, proto_done, mass_buy
├── related_docs:  drbfm_id, fmea_id, gpdm_id, dqms_state
└── remark / selection_background

PartGradeReview (부품등급심의회)
├── review_id (PK)
├── project_id (FK)
├── meeting_date, place, attendees
├── part_no (FK)
├── grade, yield_mgmt, traceability, s_apqp,
│   factory_audit, part_approval, part_cert, spec_cert,
└── background

PartApprovalTest (부품인정시험항목)
├── test_id (PK)
├── change_id (FK→ChangePartItem)
├── part_no
├── supplier_1st, supplier_2nd
├── drawing_exists, material_surface, cavity,
│   four_new, safety_key, reliability_unit, part_approval_system,
│   ctq_drawing, certification_basis(drawing/test_std/drbfm),
│   limit_approval, ctq_drawing_only
├── inspection_items[], test_items[]
├── method_condition, executor, sample_count
├── progress_supplier, progress_lg, progress_3rd
└── final_judgment, remark

TestPlan (<모듈>_시험기획)  — Controller, Cavity 등 모듈별 시험표
├── plan_id (PK)
├── change_id (FK)
├── module, part_type1/2, in_house_flag, intake_flag, shape,
│   part_name, model_best/better/good, eco_no, change_point,
│   intake_date, ckd_flag, lab, owner_name, four_new, grade, key_part,
│   reliability, supplier, part_class, factory_audit, drbfm,
│   part_no, category(검사/성능/치수/친환경),
│   inspection_item, inspection_spec(LG(66)-B-4501-20),
│   test_item, test_method_condition

PartDmsTemplate (신규부품리스트 → DMS 등록)
├── template_id (PK)
├── project_id (FK)
├── part_no, project_code, type(전용/공용/이원화), part_dev_project_code,
│   name_shape, spec, base_part_no, qty, change_point, change_reason,
│   standard_flag, non_standard_reason, long_leadtime, grade,
│   four_new, yield_mgmt, apqp, high_skill, maker, mold_in_house,
│   drbfm_design, drbfm_process, drbfm_skip_reason,
│   limit_sample, first_part_approval, first_part_skip_reason,
│   factory_audit, process_audit
└── input_kind(Key-In/LOV/N/A), requirement(필수/조건부/옵션)

Supplier
├── supplier_code (PK)     e.g. KR011661
└── name                    e.g. 현대정밀

Person (SSOID 시트)
├── ssoid (PK)              e.g. jeongho7.kim
├── name_kr                 e.g. 김정호
└── role                    e.g. 금형, 부품개발, 설계, SQE, SQA

Recommendation (= final_recommendations.xlsx)
├── rec_id (PK)
├── req_id (FK)
├── part_no, part_name, bom_level
├── change_action, sourcing, confidence, rationale
├── parent_assy, group_id, final_status
└── created_at
```

### 6.2 컨트롤 어휘(enum) 후보
- `change_action`: `New | Change | Common | Delete | NEW | Changing | Chainging | Common` → **정규화 필요**(과거 데이터에 오타 `Chainging` 존재).
- `lineup / grade column`: `Best-1, Best-2, Better-1, Better-2, Good-1, Good-2, Good-1 BK, Good-2 BK, BK STS, BK Glass`.
- `dev_grade`: `S, A, B, C, C1, C2, Ca, Cb, D`.
- `part_type`(2축):
  - 공정/공법: `사출, 절삭, 판금, 회로, 전장, 기구, 인쇄/포장`.
  - BOM Role: `Structure Assy, PD Part, Raw Material, Printing Material, Ass'y, Assembly`.
- `event`: `CP, PP, DV, PV, PreMP, MP, PQ` (멀티값 — "CP DV PV PreMP" 형태로 저장됨, **배열/플래그로 분해 필요**).
- `flag` 컬럼들: 대부분 `○/X/●/N/Y/-` 혼용 → `boolean | null + reason`으로 정규화 추천.
- `BOM Level` 표기: `0`, `.1`, `..2`, `...3`, `....4`, `…3` (점 한자 사용 케이스 존재) →
  **integer depth + 원본 문자열 모두 보존** 권장.

### 6.3 데이터 품질 이슈(스키마 설계 시 고려)
- 헤더가 8~9행에 위치하고 그 위에 메타데이터(Base/New Model, Buyer, 양산일자, Event)가 있음
  → ETL 시 시트별 헤더 자동 탐지 필요(현 분석 스크립트도 "비어있지 않은 셀이 가장 많은 첫 15행" 휴리스틱 사용).
- 한 파일 안에서 동일 양식이 시트별(등급별 / 색별 / 라인업별)로 반복됨 → ETL은
  **시트 메타(모델, 등급, Event)를 행에 부착**해서 long-format으로 펼쳐야 함.
- 점 표기 BOM Level은 시각 정렬용 — 부모는 `Parent Part No(모)`로 별도 보존되어 있으므로
  실제 트리는 BOM 파일에서 가져오고 Master 행은 그 부품에 attach.
- `Base P/No = "-"` / `New P/No = "←"` / `"X"` 등 sentinel 값을 null로 변환 필요.
- 같은 부품이 여러 모델/등급/프로젝트에서 등장 → `Part` 마스터 분리 필요(중복 제거 효과 큼).
- 작성자가 다양해 컬럼 공란/오타가 잦음(`Chainging`, "Goood-2 BK" 등) — ETL에 fuzzy alias map 필요.

### 6.4 권장 적재 전략(요약)
1. **Part / Supplier / Person** 마스터 테이블 먼저 채움 (BOM + 모든 history 통합).
2. **Model / Project** 추출 (시트 상단 메타 + 파일명에서 region 추출).
3. **BomEdge**는 `ref_boms/`, `uploads/base_bom.xlsx`에서 단일 소스로.
4. **ChangePartItem**은 모든 history 시트를 long-format으로 normalize.
5. **PartGradeReview / PartApprovalTest / TestPlan**은 같은 파일 안 sibling 시트라
   `change_id` 또는 `(project_id, part_no)`로 연결.
6. **Recommendation**은 별도 산출물 — `req_id`는 앱 세션, `rec_id`는 모델 출력.

---

## 7. 부록 — 컬럼 정규화 매핑표(주요 별칭)

| 정규화 컬럼 | 원본 별칭(시트별) |
|---|---|
| `base_part_no` | `Base P/No`, `Base Part.N`, `기존 P/No.`, `P/no.(Base)` |
| `new_part_no` | `New P/No`, `New Part.N`, `부품 P/No.`, `P/no.(New)` |
| `part_name` | `Class Desc.(Part Name)`, `Desc.`, `부품명`, `품명(형상명)`, `Part Name(자)` |
| `bom_level` | `BOM Level`, `Lvl`, `Level` |
| `part_type` | `Part Type`, `CMDT`, `Module`, `Module/CMDT` |
| `change_action` | `구분`, `Classification`, `신규`, `Change/New/Delete`, `신규/변경 부품 대상` |
| `change_point` | `변경점`, `Changing Point` |
| `change_reason` | `변경사유`, `Changing Reason`, `변경 사유` |
| `quantity` | `Quanty`, `Qty`, `Q'ty`, `수량` |
| `supplier_name` | `양산처`, `Supplier`, `공급처`, `Maker` |
| `supplier_code` | `Supplier Code` |
| `part_grade` | `부품 등급`, `부품 개발 등급`, `개발 등급`, `Part Grade`, `Grade`, `등급` |
| `yield_mgmt` | `수율 관리`, `Yield Management`, `수율관리` |
| `s_apqp` | `s-APQP`, `APQP` |
| `factory_audit` | `공정or공장심사`, `공장심사 or 공정심사`, `공장심사(공정진단)` |
| `part_approval` | `부품 승인`, `Part Approval(단가 등록 포함)` |
| `part_certification` | `부품인정`, `부품 인정시험 실시 여부`, `Part Approval Test` |
| `spec_certification` | `시방인정` |
| `ctq` | `CTQ 대상`, `CTQ 항목(도면 기준)` |
| `four_new` | `4신여부`, `4新` |
| `long_leadtime` | `장납기 여부`, `장납기` |
| `mold_dev` | `금형 개발`, `금형 개발/수정`, `Mold Dev/Modify` |
| `in_house` | `사내 제작`, `사내제작 or 직거래`, `In-house Prod.`, `사내(●)` |
| `event` | `Event(CP/PP/DV/PV/PreMP/MP/PQ)` |
| `selection_background` | `선정 배경`, `등급 선정 배경`, `Remark` |
| `drbfm_design` | `Design`(DRBFM 칸), `DRBFM 설계` |
| `drbfm_process` | `Process`(DRBFM 칸), `DRBFM 공정` |
| `drbfm_skip_reason` | `Why don't you execute DRBFM?` |
| `drawing_review` | `심의도`, `도면리뷰` |
| `drawing_agree` | `도면합의` |
| `drawing_confirm` | `확정도` |
| `drawing_dispatch` | `도면결재/배포(GPDM)` |
| `mold_proto` | `금형 초품`, `금형완료`, `금형일정` |
| `mass_proto` | `양산초품`, `초품의뢰`, `초품 완료` |
| `dqms_state` | `DQMS`, `DQMS 상태`, `DQMS Part Approval Test(OK/NG)` |
| `hsms` | `HSMS`, `HSMS시스템 FCM` |

---

*분석 기준: `data/` 하위 모든 `.xlsx`/`.xlsm` (1개 손상 파일 `240430 BDO30 SKS …` 제외).
분석 도구: openpyxl read-only, 헤더 자동 탐지(첫 15행 중 비어있지 않은 셀이 가장 많은 행).*
