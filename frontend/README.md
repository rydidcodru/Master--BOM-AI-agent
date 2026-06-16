# Dev Parts BOM Workflow — React 프론트엔드

Streamlit을 대체하는 React(Vite + TypeScript) 프론트엔드. 백엔드 FastAPI는 그대로 사용한다.

> **왜 React로 바꿨나 (사용자 피드백):** 기존 Streamlit은 변경부품/마스터가
> **평면 테이블로 쭉 나열**돼서 (1) 무엇이 변경되는지 알기 어렵고 (2) 미정(TBD)
> 부품이 안 보였다. 이 프론트엔드는 **변경 카드 + 후보 선택 + 미정 강조**로 그 문제를 푼다.

## 실행

백엔드와 프론트엔드를 각각 띄운다.

```powershell
# 1) 백엔드 (FastAPI :8000)
uv run devparts serve

# 2) 프론트엔드 (Vite :5173) — 저장소 루트에서
uv run devparts web
#  또는
cd frontend; pnpm install; pnpm dev
```

접속: http://localhost:5173 (Vite가 `/api/*`를 `http://127.0.0.1:8000`으로 프록시)

백엔드 주소가 다르면 화면 좌측 사이드바의 **FastAPI URL**에 직접 입력하거나
`uv run devparts web --api-port 8001` 로 바꾼다.

## 핵심 UX (변경점 중심)

> **정정된 모델(2026-06-14):** 이 도구의 주인공은 **심의표의 변경점(Design Points)** 이다.
> 그 표가 거의 master 초안(=정답지)이다. 시스템은 신규 번호를 자동 추천하지 않는다.
> 변경점을 명확히 보여주고, 사용자가 **New P/No를 부여(TBD 포함)** 하게 하고,
> "과거엔 이렇게 바뀌었다"를 **참조(옵션)** 로 제시한다. (`docs/파이프라인_설명서_2026-06-14.md`)

- **변경점 카드** (`① 변경점 작성`): 심의표 Design Points를 그대로 가져와 모듈별로 보여준다.
  분류 배지(🔴신규/🟢공용/🟡공용·변경, New P/No 마커로 추정), Lev 들여쓰기, **상세 변경점**,
  `Base P/No → [New P/No 편집]`(TBD 기본·강조), **과거 참조**(접힘, 누르면 과거 변경 사례 +
  "이 번호 가져오기"). 하단에 신규/공용/변경/TBD 집계 + **master CSV 내보내기**.
- **미정(TBD) 강조**: 번호 미정 행은 보라색으로 강조해 "회의에서 부여" 대상임을 표시.
- **변경 카드** (`③ BOM 반영`): 과거 이력 후보를 골라 base BOM에 반영(보조 흐름).
  반영 결과에서 상위전파 `TBD` 행을 보라색 `⚠ 미정(TBD)` 배지로 강조, 변경/추가/삭제/미정 필터.

## 탭 구성

| 탭 | 내용 | 사용 API |
| --- | --- | --- |
| ① 변경점 작성 (master) ★ | 심의표 PPTX → **Design Points 추출**, New P/No 부여(TBD), 과거 참조(검색 흡수), 카드 토글, **「② 보내기」** | `/pptx/extract-bom`, `/changes/recommend`(과거 참조) |
| ② BOM 반영 | ①에서 받은 **master를 base BOM에 직접 반영**(공용 자동 skip, 미정 강조), xlsx | `/bom/base/parse-xlsx`, `/bom/apply-master`, `/bom/apply-master/export` |
| ③ 데이터 확인 | master 목록, embedding 상태 | `/masters`, `/stats` |

흐름: **① 변경점 작성 → (보내기) → ② BOM 반영 → 최종 BOM**. 과거 이력 검색은 ①의
「과거엔 어떻게 바뀌었나? 참조」에 흡수됨(구 검색·추천 탭 제거). 미사용 컴포넌트
(`InputTab.tsx`, `ChangeCard.tsx`)는 남아있으나 nav에서 빠짐.

## 구조

```
frontend/
├─ src/
│  ├─ api.ts          # FastAPI 클라이언트(프록시/절대URL)
│  ├─ types.ts        # 응답/요청 타입
│  ├─ store.tsx       # 탭 간 공유 상태(Streamlit session_state 대체)
│  ├─ util.ts         # 변경 종류 판정(rowKind: 변경/추가/삭제/미정), CSV 파서 등
│  ├─ App.tsx         # 셸 + 사이드바(백엔드 상태) + 탭
│  └─ components/
│     ├─ DesignPointsTab.tsx # ★ 변경점(Design Points) 중심 master 작성 화면
│     ├─ InputTab.tsx        # 검색·추천(보조)
│     ├─ ApplyTab.tsx        # 변경 카드 리스트 + 반영
│     ├─ ChangeCard.tsx      # 변경 1건 카드 + 후보 선택
│     ├─ BomResultView.tsx   # 반영 결과 + 미정 강조 + 필터
│     ├─ DataTab.tsx
│     └─ Badge.tsx
├─ vite.config.ts     # /api 프록시
└─ package.json
```

## 빌드 / 타입체크

```powershell
cd frontend
pnpm build        # tsc -b && vite build  -> dist/
```

> pnpm v10이 빌드 스크립트를 막으면 `.npmrc`(`verify-deps-before-run=false`)와
> `package.json`의 `pnpm.onlyBuiltDependencies`로 처리되어 있다.

## 현재 상태 / 한계

- ✅ 3탭 기능 패리티, 변경 카드 + 후보 선택, 미정(TBD) 강조, xlsx 다운로드, 빌드 통과.
- ⏳ `/bom/subtree-preview`(후보 subtree vs base diff preview)는 카드의 `하위 subtree 포함`
  플래그로 백엔드 반영은 되지만 **전용 diff 미리보기 UI는 아직 미연결**. (다음 단계)
- ⏳ Design Points BOM 덤프(`/pptx/extract-bom`) 화면은 미이식(부가 기능).
- Streamlit 앱(`src/dev_parts_backend/ui/`)은 당분간 병행 유지 가능.
