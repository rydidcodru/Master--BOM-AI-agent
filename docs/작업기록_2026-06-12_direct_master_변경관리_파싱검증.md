# 작업기록 — direct_master 변경관리(ledger) + 파싱 검증

작성일: 2026-06-12
범위: `direct_master/` (결정론적 직접 병합 파이프라인)
한 줄 요약: 한 방 병합을 **중간 대장 → 검토·확정 → 최종 조립** 단계형으로 바꾸고,
중간 master를 "변경점+문맥"만으로 좁히고, design point **CSV 덤프**를 추가. PPTX 파싱은
정상 확인. **중복 부품번호 첫-매치 문제(24%)**를 진단(미해결, 다음 작업).

---

## 1. 단계형 변경 관리로 전환 (중간 대장 → 최종 조립)

"BOM은 마지막에 한 번에 굳힌다"는 방향에 맞춰 한 방 병합을 둘로 나눔.

```
변경점 → [중간 master + 변경 대장]  →  사용자: TBD 번호부여 / 확정·반려  →  [최종 조립]
          build_ledger()                                                   finalize_master()
```

- **신규 `ledger.py`**
  - `build_ledger()` — 중간 master + 변경 대장 생성.
    - `auto` = 결정론적 확정(실제품번 교체·삭제)
    - `needs_input` = TBD/상위전파 → 사용자가 번호 정해야 함
  - `finalize_master()` — 확정 대장을 반영해 최종 master 조립.
  - `affected_master()` — 중간 master를 "변경점+문맥"만으로 추림(§2).
  - `attach_history_suggestions()` — needs_input 행에 과거 참고 후보(공급자 없으면 빈 리스트).
- **`merge.py`에 `resolutions` / `assembly_resolutions` 주입 추가**
  - 같은 결정론 엔진이 중간/최종 둘 다 구동. 차이는 사용자 확정값 주입뿐.
  - `status=='rejected'` → 변경 건너뜀 · `kind=='tbd'`+번호 → 실제품번 교체로 굳힘(+상위전파 재계산).

검증(실데이터 253 변경점 / base 1470):
```
대장 143 (자동확정 43 / 번호미정 100 = TBD 92 + 상위전파 8) · 삭제 21 · unmatched 3
TBD 3건 번호부여+확정 → change 22→24, open_tbd↓, 부여번호 master 반영 ✓ · 반려 1건 제외 ✓
```

---

## 2. 중간 master = 전체 BOM이 아니라 "변경점 + 문맥"

기존엔 중간 다운로드가 **최종 조립 BOM(전체)**이 나와 혼란. `affected_master()`로 좁힘.

- 시드 = `applied_action=='change'`(실제 교체된 부품)뿐
- **상위** = 부모→루트 경로(전파된 Assembly 포함)
- **하위** = **직속 자식 1단계만** (서브트리 전체 아님)

함정: 루트 Assembly가 TBD(상위전파)라 그 descendants를 펴면 BOM 전체(1394)가 끌려옴.
→ 시드를 `change`로 한정 + 하위 1단계로 잘라 해결.

```
전체 1394 → 중간 488행 (35%) = 변경 99 (change 96 + propagated 3) + 문맥 약 389
참고: 상위만=99행(7%) / 전체 하위 포함=1077행(77%, 폭발)
```

---

## 3. 표시 컬럼 직관 순서로 재정렬

`pipeline.py` `MASTER_COLUMNS`:
```
Lev(bom_level) · base_part_no · New P/No(new_part_no) · part_name · change point(changing_point) · action(applied_action)
→ 나머지(bom_depth/part_type/qty/classification/supplier/line_order) 뒤로
```
중간 master 표(app 3단계) + 중간/최종 xlsx 모두 이 순서.

---

## 4. design point CSV 덤프 (PPTX↔파싱 대조용)

요청: "pptx 파싱부분 + csv로 만드는 로직".

- **`design_point.py`**: `design_points_to_csv()` / `extract_design_points_csv()`
  - UTF-8 BOM(Excel 한글), 컬럼이 PPTX 표와 동일(`slide_number·design_point·no·lev·base_part_no·new_part_no·part_name·uom·qty·change_point·source`)
- **API**: `POST /extract/csv` (편집된 변경점 행 → CSV)
- **app**: 1단계에 "추출 변경점 CSV 다운로드" 버튼
- **CLI**: `--csv-only`(병합 없이 CSV만) / `--design-csv <경로>`
- 산출물: `data/output/design_points.csv` (253행)

---

## 5. 파싱 검증 결과 — 파싱은 정상

화면상 "안맞음"의 원인을 데이터로 추적:

- 이미지1 = **슬라이드 11**(Oven Assembly 가지, `ABU74573408`)
- 이미지2(중간 master) = base BOM native 순서로 `.1 ABU74573307` 가지를 먼저 표시
- base BOM에 **"Cavity Assembly,Full" 2개**(`ABU74573307`@.1, `ABU74573408`@..2) 실재
  → 같은 이름·다른 번호가 **정상**. 파싱 버그 아님.
- 슬라이드 8/11/15 추출 모두 정확, base_bom과 MATCH 확인.

슬라이드별 추출 행: `{8:11, 9:29, 11:44, 13:21, 14:27, 15:44, 17:19, 18:22, 19:8, 20:16, 21:12}` (총 253)

---

## 6. ✅ 해결 — 중복 부품번호 → 계층 문맥 매칭

### 배경 (왜 중복인가)
같은 부품번호가 BOM 여러 위치에 등장(295종/923행)하는 건 오류가 아니라 **공용 하위 부품**.
예: `4810W1N019A Bracket,Mount`가 Cavity 조립품과 Oven 조립품에 모두 들어감.
변경점 253개 중 62개가 이런 중복 위치를 가리킴.

### 문제
`find_index_by_part_no()`가 **첫 번째 매치만** 반환 → 슬라이드 11(Oven 모듈)의
브라켓 변경이 엉뚱하게 Cavity 가지(line19)에 적용. 정답은 Oven 가지(line213).

### 결정 (사용자)
공용 부품 변경은 **"슬라이드 가지 안에만(모듈별)"** 적용. 각 슬라이드는 자기 모듈을
다루고, 공용 부품은 각 모듈 슬라이드가 자기 가지에서 따로 바꾼다.

### 구현 (`matching.py` 신규)
- 슬라이드(design_point 그룹)별로 `lev` 계층을 따라 **부모 스택**을 유지.
- 자식 부품은 **부모의 서브트리 안에서만**(`find_in_subtree`) 번호를 찾음.
  서브트리 실패 시 전역 첫-매치로 폴백.
- 매칭 결과는 인덱스가 아니라 **line_order(안정 ID)** — merge가 행을 삭제해도 안전.
- `merge.py`에 `hierarchical=True`(기본) 연결. `bom_levels`에
  `find_index_by_line_order` / `find_in_subtree` 추가.

### 결과 (실데이터)
```
가지 안 매칭 185 · 전역 폴백 50 · 매칭실패 1 · base없음 17
>> 문맥으로 교정 26건 (이전엔 엉뚱한 가지에 적용되던 것)
예: slide11-10 4810W1N019A Bracket,Mount
    이전(틀림): Electric > Cavity Assembly,Full > welding > welding
    교정(맞음): Electric > [Oven Assembly] > Cavity Assembly,Full > welding > welding
부수효과: propagated 8→4 (올바른 가지로 모여 불필요한 상위전파 감소)
```
UI(app 3단계)에 "문맥으로 교정 N건" + 교정 내역(첫-매치→그룹가지) expander 표시.

### 후속 버그 수정 — change_id 충돌로 unmatched 폭발
- 증상: 라이브에서 unmatched 품목이 갑자기 많아짐.
- 원인: 매칭 결과를 `change_id`(=`slide{n}-{no}`)로 저장했는데, `no`가 빈 행들이
  같은 키(`slide20-` 등)를 공유 → dict에 마지막 값만 남고, **그 마지막이 매칭 실패면
  같은 키의 행 전부 unmatched**.
- 수정:
  - `matching.py` 매칭 결과를 **행 인덱스**(충돌 불가)로 키잉.
  - `merge.py`는 `enumerate` 인덱스로 조회. change_id에도 `#index`를 붙여 고유화
    (대장/finalize의 resolution 키까지 안정).
- 검증: `no`를 전부 비워 충돌 최대로 만들어도 unmatched=3 유지(이전이면 폭발). ledger_id 전부 고유.

---

## 7. 변경/추가 파일

| 파일 | 변경 |
| --- | --- |
| `direct_master/ledger.py` | **신규** — build_ledger / finalize_master / affected_master / attach_history |
| `direct_master/matching.py` | **신규** — 계층 문맥 매칭(슬라이드 가지 안에서 매칭) |
| `direct_master/merge.py` | resolutions / assembly_resolutions 주입, 계층 매칭 연결(hierarchical) |
| `direct_master/bom_levels.py` | find_index_by_line_order / find_in_subtree 추가 |
| `direct_master/pipeline.py` | MASTER_COLUMNS 재정렬, intermediate_workbook_bytes, LEDGER_COLUMNS |
| `direct_master/design_point.py` | design_points_to_csv / extract_design_points_csv |
| `direct_master/api.py` | /ledger, /ledger/export, /ledger/suggest, /finalize(/export), /extract/csv |
| `direct_master/app.py` | 3단계(중간 master+대장)/4단계(최종 조립), 대장 편집기, CSV 버튼 |
| `direct_master/cli.py` | --csv-only / --design-csv |
| `direct_master/__init__.py` | 신규 export |
| `direct_master/README.md` | 단계형 흐름 + 중간 master 정의 |
| `docs/direct_master_파이프라인_및_테스트가이드.md` | 흐름도 + 테스트 체크리스트 |

---

## 8. 다음 할 일

1. ~~중복-매칭 → 계층 문맥 매칭~~ ✅ 완료(§6).
2. 전역 폴백 50건 점검 — 그룹 가지 밖에서 잡힌 건들이 맞는지 표본 검토.
   (그룹 루트 자체가 중복번호면 루트 매칭도 틀릴 수 있음 — 현재는 전역 첫-매치.)
3. 중간 master 문맥 범위 옵션화(상위만 / 직속하위 / 전체하위) 검토.
4. 전체 웹 플로우 라이브 점검(`uv run python -m direct_master.serve`, 8010/8502).
