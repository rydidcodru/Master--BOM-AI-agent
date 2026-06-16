# direct_master — 파이프라인 흐름 & 테스트 가이드

작성일: 2026-06-12
대상: `direct_master/` (결정론적 직접 병합 + 변경 관리 HITL)
요약: PPTX Design Points + Base BOM → **중간 master(변경 대장)** → 사람이 검토·확정 → **최종 BOM**.
과거검색/임베딩/LLM은 기본 0회(표 없는 슬라이드만 선택적으로 LLM 보강).

---

## 1. 왜 이렇게 만들었나 (설계 배경)

- Design Point 표에는 이미 `Base P/No → New P/No`가 적혀 있다 → **과거 이력 추천이 거의 불필요**.
  base_part_no로 매칭해 규칙대로 바로 처리하면 된다(실측: base의 99.6%가 Base BOM에 존재).
- 단, BOM은 **마지막에 한 번에 굳히는** 산출물이다. 그래서 "한 방 병합"이 아니라
  **중간 대장에서 사람이 TBD 번호를 정하고 검토·확정한 뒤** 최종을 조립한다.

### New P/No 규칙

| New P/No | 처리 | 대장 status |
| --- | --- | --- |
| `←` / 동일 | 유지(변경 없음) | 대장에 안 올라옴 |
| `삭제` / Delete | 노드 + 하위 트리 삭제 | `auto` |
| 실제 품번 | base→new 교체 + 상위 전파 | `auto` |
| `TBD` / 미정 | Change(new=TBD) + 상위 전파 | **`needs_input`** |
| (상위전파) Assembly 재발행 | TBD 표시 | **`needs_input`** |
| base 없음 | 신규 추가 후보(additions) | — |
| base 매칭 실패 | unmatched(다른 모델 등) | — |

---

## 2. 파이프라인 흐름

```
┌─ 1. PPTX ─────────────────────────────────────────────────────────────┐
│  extract_change_points(use_llm)                                        │
│   ├ Design Points 표 있는 슬라이드 → 결정론적 파싱(무료·정확)          │
│   └ 표 없는 슬라이드 → (선택) LLM 보강, source='llm'                    │
└───────────────┬───────────────────────────────────────────────────────┘
                │  변경점(change_points)
        [HITL ①] 사용자가 표에서 base_part_no / New P/No 검증·수정
                │
┌─ 2. Base BOM ─┴───────────────────────────────────────────────────────┐
│  parse_base_bom_xlsx(base_bom.xlsx) → BOM rows (트리/레벨/대체품 분리)  │
└───────────────┬───────────────────────────────────────────────────────┘
                │  base_rows
┌─ 3. 중간 master + 변경 대장 ──────────────────────────────────────────┐
│  build_ledger(base_rows, change_points)                                │
│   ├ intermediate_master : **변경점 + 상위(루트까지) + 직속 하위 1단계**  │
│   │                       만 추린 뷰. 전체 BOM이 아님(약 1394→488행).    │
│   └ ledger              : 바뀐 것만 모은 대장(행마다 status)            │
│        auto         = 이미 확정(실제품번 교체·삭제)                     │
│        needs_input  = TBD / 상위전파 → 번호 미정                        │
└───────────────┬───────────────────────────────────────────────────────┘
        [HITL ②] needs_input 행에 resolved_part_no(새 품번) 채우고
                │  status를 confirmed로 (빼려면 rejected). 과거이력 참고(선택).
┌─ 4. 최종 BOM 조립 ────────────────────────────────────────────────────┐
│  finalize_master(base_rows, change_points, ledger)                     │
│   ├ rejected 제외                                                       │
│   ├ 번호부여된 TBD → 실제 품번 교체로 굳힘(+ 상위전파 재계산)           │
│   └ master_bom.xlsx (master / change_ledger / change_list / ...)       │
└────────────────────────────────────────────────────────────────────────┘
```

### 핵심: 단일 엔진 + resolutions 주입

중간/최종 모두 `merge_design_points()` 하나가 돌린다. 차이는 **사용자 확정값 주입**뿐.

```python
merge_design_points(
    base_rows, change_points,
    resolutions={change_id: {"status": "confirmed|rejected", "resolved_part_no": "..."}},
    assembly_resolutions={base_part_no: "재발행 품번"},
)
```

- `status == 'rejected'` → 그 변경 건너뜀
- `kind == 'tbd'` + `resolved_part_no` 있음 → 실제 품번 교체로 굳고 상위전파까지 재계산

---

## 3. 모듈 지도

| 파일 | 역할 |
| --- | --- |
| `design_point.py` | PPTX Design Points 표 결정론 파싱 |
| `extract.py` | 변경점 추출 = 표 우선 + 표 없으면 LLM 보강(선택) |
| `base_bom.py` | Base BOM xlsx 파싱(*S* 대체품 분리) |
| `bom_levels.py` | 레벨/깊이, 트리삭제·상위전파 헬퍼 |
| `merge.py` | `merge_design_points()` — 결정론 병합 + resolutions 주입 |
| `ledger.py` | `build_ledger()` 중간 master+대장 / `finalize_master()` 최종 조립 |
| `pipeline.py` | 오케스트레이션 + xlsx 출력(중간/최종) |
| `xlsx_out.py` | 시트 → xlsx 바이트 |
| `history_hook.py` | TBD 과거참고 훅(자리만, 미연결) |
| `api.py` | FastAPI 백엔드(8010) |
| `app.py` | Streamlit 프론트 HITL(8502) |
| `serve.py` / `cli.py` | 동시 실행 / 파일 일괄 변환 |

### API 엔드포인트(포트 8010)

| 메서드 | 경로 | 입력 → 출력 |
| --- | --- | --- |
| POST | `/extract` | PPTX → 변경점(표+LLM) |
| POST | `/parse-base-bom` | xlsx → BOM rows + summary |
| POST | `/ledger` | base+변경점 → 중간 master + 대장 |
| POST | `/ledger/export` | → `intermediate_master.xlsx` |
| POST | `/ledger/suggest` | 대장 → 과거참고 후보 부착 |
| POST | `/finalize` | base+변경점+확정대장 → 최종 master |
| POST | `/finalize/export` | → `master_bom.xlsx` |
| POST | `/merge`,`/merge/export` | (호환) 한 방 병합 |

---

## 4. 테스트 가이드 (따라 하며 `[ ]` 체크)

샘플 데이터:
- PPTX: `data/input/260309 퀵존레인지 개발등급확정심의회 V5(회의록포함).pptx`
- Base BOM: `data/input/base_bom.xlsx`

> 원래 RAG 앱(8000/8501)과 **포트가 달라** 동시에 띄워도 충돌하지 않는다.

### 4-0. 서버 실행

```powershell
uv run python -m direct_master.serve
```

- [ ] 터미널에 FastAPI(8010) / Streamlit(8502)가 뜬다.
- [ ] `http://127.0.0.1:8502` 접속 → "Direct Master BOM" 화면.
- [ ] 사이드바에 "연결: ok" 초록 메시지.

### 4-1. PPTX 변경점 추출 (1단계)

- [ ] PPTX 업로드 → "변경점 추출" 클릭.
- [ ] "표 253행 + LLM 0행 추출" 비슷한 요약이 뜬다.
- [ ] 아래 표에 `base_part_no / new_part_no / part_name / change_point` 컬럼이 보인다.
- [ ] `New P/No`에 `TBD`, `←`, `삭제`가 원문 그대로 보인다.

**[HITL ①]** 표에서 직접 셀을 고쳐 본다(예: 잘못된 base_part_no 수정).
- [ ] 수정한 값이 표에 반영된다.

### 4-2. Base BOM 업로드 (2단계)

- [ ] `base_bom.xlsx` 업로드 → "Base BOM 불러오기".
- [ ] "Base BOM 1470행 불러옴" 비슷한 메시지.
- [ ] 루트 부품명 / 행 수 / 최대 depth / 대체품 수 캡션이 보인다.

### 4-3. 중간 master + 변경 대장 (3단계, HITL ②)

- [ ] "중간 master · 변경 대장 생성" 클릭.
- [ ] 상단 지표: **대장 행 143 / 자동확정 43 / 번호 미정 100 / 삭제 21 / 상위전파 8 / unmatched 3** 근사.
- [ ] **중간 master 표**가 보인다 — 컬럼 순서 `Lev(bom_level) / base_part_no / new_part_no / part_name / changing_point / applied_action ...`,
      행 수는 **약 488행(전체 1394행 중)** = 변경점 + 상·하위 문맥만(전체 BOM 아님).
- [ ] 그 아래 **변경 대장**에 `status`, `resolved_part_no` 컬럼이 보이고, `needs_input` 행이 노란 경고로 안내된다.

**번호 부여 테스트:**
- [ ] `status=needs_input`인 leaf 행 하나의 `resolved_part_no`에 임의 품번(예: `TEST-001`) 입력.
- [ ] 같은 행 `status`를 `confirmed`로 변경.
- [ ] 다른 `auto` 행 하나를 `rejected`로 변경(이 변경은 최종에서 빠질 예정).
- [ ] "중간 master(intermediate_master.xlsx) 다운로드" → 파일이 받아진다.
  - [ ] 엑셀 열어 `intermediate_master` 시트가 **전체 BOM이 아니라 변경점+문맥(약 488행)**인지 확인.
  - [ ] 컬럼 첫 6개가 `Lev / base_part_no / new_part_no / part_name / changing_point / applied_action` 순.
  - [ ] `change_ledger` 시트도 있고, TBD가 아직 열려 있다(최종 아님).

### 4-4. 최종 BOM 조립 (4단계)

- [ ] "최종 BOM 조립" 클릭.
- [ ] 지표에 `change`가 1 늘고(=TEST-001 굳음), `rejected` 1, `열린 TBD`가 줄어든 게 보인다.
- [ ] 미리보기 master에서 `new_part_no == TEST-001` 행이 보인다(Ctrl+F).
- [ ] "최종 master_bom.xlsx 다운로드" → 받아진다.
  - [ ] 시트: `master` / `change_ledger` / `change_list` / `unmatched` / `additions`.
  - [ ] `rejected`로 돌린 변경이 master에 반영 **안** 된 것을 확인.

### 4-5. (선택) CLI 한 방 변환

```powershell
uv run python -m direct_master.cli `
  --pptx "data/input/260309 퀵존레인지 개발등급확정심의회 V5(회의록포함).pptx" `
  --base-bom "data/input/base_bom.xlsx" `
  --out "data/output/master_bom.xlsx"
```
- [ ] `data/output/master_bom.xlsx`가 생성된다(검토 단계 없이 결정론적 한 방).

---

## 5. 기대 수치 (실데이터 기준 회귀 기준점)

```text
변경점(표) 253 · Base BOM 1470행
build_ledger : 대장 143 (자동확정 43 / 번호미정 100 = TBD 92 + 상위전파 8) · 삭제 21 · unmatched 3
               중간 master(intermediate) 약 488행 = 변경점+상·하위 문맥 (전체 1394행 아님)
finalize     : master 1394행(전체 조립) · 반려/번호부여한 만큼 change↑·open_tbd↓
한 방 merge  : master 1394 · keep 98 / change 22 / tbd 92 / delete 21 / add 17 / unmatched 3 / propagated 8
```

수치가 위와 크게 다르면 입력 데이터(헤더/시트)나 파싱을 먼저 의심한다.

---

## 6. 자주 막히는 곳

- **사이드바 "API 연결 실패"** → 백엔드(8010)가 안 떴거나 포트 점유. `serve.py`로 같이 띄웠는지 확인.
- **대장이 비어 있음** → 1·2단계(변경점·Base BOM)를 먼저 끝내야 3단계 버튼이 활성화된다.
- **최종에 TBD가 남음** → `needs_input` 행에 번호를 안 채웠거나 `confirmed`로 안 바꿈. 3단계 노란 경고 개수 확인.
- **LLM 보강이 안 됨** → 표 없는 슬라이드 대상 + OpenAI 키/모듈 필요. 없으면 조용히 건너뛴다(표만으로도 동작).
