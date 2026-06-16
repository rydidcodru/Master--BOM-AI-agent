# 개발부품 BOM 도구 (dev_parts_bom_tool)

심의표(PPTX)의 **변경점(Design Points)** 을 가져와 **개발부품마스터(master)** 를 작성하고,
Base BOM에 반영해 **최종 BOM**을 산출하는 도구입니다.

- **백엔드**: FastAPI + SQLite (Docker/PostgreSQL 불필요)
- **프론트엔드**: **React (Vite + TypeScript)** — 빌드물(`frontend/dist`)을 FastAPI가 같은 서버에서 정적 서빙
- 정체: "RAG 생성"이 아니라 **변경점 → master 작성 + 결정론적 BOM 편집** 도구. 과거 이력 검색은 "예전엔 이렇게 바뀜" **참조(옵션)**.

> 프론트엔드는 **React**(`frontend/`) 단일입니다. (과거 Streamlit UI는 제거됨)

---

## 폴더 구조

```text
sqlite_rag_backend/
├─ data/
│  ├─ input/                       # master/detail CSV, example.xlsx(마스터 양식) 등
│  └─ dev_parts.db                 # SQLite DB(과거 이력)
├─ frontend/                       # React(Vite+TS) 프론트엔드
│  ├─ src/components/              # DesignPointsTab, ApplyTab, BomResultView …
│  └─ dist/                        # 빌드 결과(서버가 정적 서빙)
├─ src/dev_parts_backend/
│  ├─ api/main.py                  # FastAPI 엔드포인트 + 정적 서빙 + CORS
│  ├─ bom_table.py                 # Design Points 표 + "변경점 요약" 추출
│  ├─ base_bom.py                  # Base BOM xlsx 파싱(openpyxl 무의존)
│  ├─ workflow.py                  # apply_master_changes(변경점→BOM 반영) + 과거 검색
│  ├─ master_template.py           # 개발부품마스터 산출(example.xlsx 템플릿 채우기)
│  ├─ templates/master_template.xlsx
│  ├─ xlsx.py                      # 의존성 없는 xlsx writer
│  ├─ rag/                         # dense 검색(과거 참조)
│  ├─ loader.py / db.py / cli.py   # CSV 적재 / 스키마 / CLI
└─ pyproject.toml
```

---

## 실행 방법 1 — 개발자 (uv)

저장소 루트에서 `uv`로 실행합니다. 백엔드와 프론트엔드를 각각 띄웁니다.

```powershell
uv run devparts serve       # 백엔드 :8000  (frontend/dist 있으면 UI도 같이 서빙)
uv run devparts web         # 프론트엔드 dev :5173  (UI 수정·HMR, /api → :8000 프록시)
```

- **UI를 고치며 개발**: 두 개 다 실행 → http://localhost:5173 접속(Vite가 `/api`를 :8000으로 프록시).
- **백엔드만으로 UI까지 보기**: `serve`만 실행 → http://localhost:8000.
  단, 빌드물(`frontend/dist`)이 있어야 합니다. 없거나 UI를 바꿨으면 먼저 빌드:

  ```powershell
  cd frontend
  node node_modules/vite/bin/vite.js build      # frontend/dist 생성/갱신
  ```

DB는 `data/dev_parts.db`를 사용합니다.

---

## 비전공자 실행 방법 1 — pip (배포 패키지)

`uv`·Node 없이 **Python만** 있으면 됩니다. 배포 패키지(`release/dev_parts_bom_tool/`, 또는 전달받은
zip의 압축 해제본) 안에서 실행합니다.

1. **Python 3.11+ 설치** (python.org, 설치 시 **"Add python.exe to PATH"** 체크). 이미 있으면 생략.
2. 패키지 폴더의 **`시작.bat` 더블클릭**.
   - 최초 1회만 구성요소 자동 설치(1~2분) → 브라우저 자동 열림.
   - 안 열리면 http://127.0.0.1:8000 접속.
3. 종료: 검은 창 닫기.

`시작.bat`이 안 되면 그 폴더에서 직접:

```powershell
python -m pip install -r requirements.txt
python run_server.py        # http://127.0.0.1:8000  (UI + API 함께)
```

자세한 안내는 패키지 안의 **`실행가이드.md`** 참고.

---

## 화면 흐름 (React, 3탭)

```
① 변경점 작성(master)  →  ② 개발부품마스터 산출  →  ③ 데이터 확인
```

- **① 변경점 작성**: 심의표 PPTX + Base BOM **함께 업로드** → **추출 검수**(원본 표 수정/행 추가·삭제/순서) →
  **변경점 작성(카드)**: New P/No 부여(TBD/공용/변경), 모듈·부모자식 토글, **드래그앤드롭**(아래=하위/위=형제),
  Level 수정·상위 부품 지정, 슬라이드 **변경점 요약** 표시, 과거 참조. (상태는 탭 이동에도 유지)
- **② 개발부품마스터 산출**: master+BaseBOM 반영 → **전체 BOM 트리**(접기/펼치기, 색구분), 하위→상위 전파(TBD).
  다운로드: **개발부품마스터 xlsx/csv**(example.xlsx 양식, 변경점 상·하위 트리 + 공용 Common 포함), **적용된 BOM xlsx**.
- **③ 데이터 확인**: 과거 모델 목록·통계.

자세한 개념/모듈은 `docs/파이프라인_설명서_2026-06-14.md`, `docs/React_전환_보고서_2026-06-14.md`,
`docs/담당자_피드백_정리_2026-06-15.md` 참고.

---

## 주요 API

| 메서드 · 경로 | 설명 |
| --- | --- |
| `POST /pptx/extract-bom` | Design Points 표 + 모듈별 변경점 요약 추출 |
| `POST /bom/base/parse-xlsx` | Base BOM 엑셀(.xlsx) → BOM rows |
| `POST /bom/apply-master` | 변경점(master)을 Base BOM에 반영 → new_bom/change_list/unmatched |
| `POST /bom/apply-master/export` | 적용된 BOM xlsx |
| `POST /bom/master/export?fmt=xlsx\|csv` | 개발부품마스터(템플릿 양식) 산출 |
| `GET /masters`, `/stats`, `/health` | 데이터/상태 |
| `POST /changes/recommend` | (옵션) 과거 이력 검색 — "과거 참조" |

API 문서: `http://localhost:8000/docs`

---

## 데이터 적재(개발) — CSV → SQLite

과거 이력 DB(`dev_part_detail`)를 새로 구성할 때만 필요합니다.

`master.csv` 필수 컬럼:

```text
input_master_key,source_file,source_sheet,base_model,base_grade,new_model,new_grade,event,region,project_name
```

`detail.csv` 기본 컬럼:

```text
input_master_key,line_order,row_no,bom_level,part_type,base_part_no,new_part_no,part_name,base_qty,new_qty,changing_point,changing_reason,supplier,classification,mold_dev_modify,inhouse_prod,part_approval_test,raw_json,changing_text,changing_embedding
```

`bom_level`이 `.1`, `..2`, `...3` 형태면 `bom_depth`는 자동 계산됩니다.

임베딩 CSV 생성 후 적재:

```powershell
uv run devparts embed-csv --input "data/input/detail.csv" --output "data/input/detail_multi_embedded.csv"
uv run devparts init-db
uv run devparts load-csv --detail "data/input/detail_multi_embedded.csv" --reset
uv run devparts stats
```

---

## 과거 참조 검색 점수 (Dense + BM25)

"과거엔 어떻게 바뀌었나? 참조"는 과거 이력 DB를 dense(임베딩)+BM25로 검색합니다.
`OPENAI_API_KEY`가 없거나 임베딩이 실패하면 BM25(어휘)만으로 fallback(서버는 실패하지 않음).

```text
identity_recall = 0.45*dense_part_name + 0.30*module_part_bm25 + 0.15*identity_text + 0.10*dense_combined
final_score     = 0.50*identity_recall + 0.15*dense_changing_point + 0.15*dense_reason + 0.20*change_bm25
```

`base_part_no`가 입력에 있으면 exact match 가산점으로 최우선 후보가 됩니다.

- `src/dev_parts_backend/rag/search.py`: multi-vector cosine
- `src/dev_parts_backend/workflow.py`: Dense + BM25 hybrid 점수
