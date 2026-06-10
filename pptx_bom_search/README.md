# BOM 심의회 자동화 데모

개발 심의회 PPTX를 업로드하면 과거 변경 이력을 자동으로 찾아주고,  
사용자가 최종 후보를 선정하면 **Master BOM Excel**을 자동으로 작성해주는 AI 도구입니다.

---

## 실행 전 준비사항

아래 3가지가 준비되어 있어야 합니다.

| 항목 | 확인 방법 |
|------|----------|
| Python 3.10 이상 | 터미널에서 `python3 --version` |
| OpenAI API 키 | [platform.openai.com](https://platform.openai.com) 에서 발급 |
| Neo4j 데이터베이스 | 로컬에서 실행 중이어야 함 (담당자에게 문의) |

---

## 설치 및 실행 방법

### 1단계 — 이 폴더로 이동

터미널(Mac: Terminal, Windows: 명령 프롬프트)을 열고 아래를 입력합니다.

```bash
cd pptx_bom_search
```

---

### 2단계 — API 키 설정

`.env.example` 파일을 복사해서 `.env` 파일을 만듭니다.

**Mac / Linux:**
```bash
cp .env.example .env
```

**Windows:**
```bash
copy .env.example .env
```

그런 다음 `.env` 파일을 메모장(또는 텍스트 편집기)으로 열어서 아래 항목을 채웁니다.

```
OPENAI_API_KEY=여기에_OpenAI_API_키_입력
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=여기에_Neo4j_비밀번호_입력
```

> ⚠️ `.env` 파일은 절대 외부에 공유하거나 git에 올리지 마세요. API 키가 포함되어 있습니다.

---

### 3단계 — 패키지 설치

아래 명령어를 한 번만 실행합니다.

```bash
pip install -r requirements.txt
```

---

### 4단계 — 실행

```bash
python3 -m streamlit run app.py
```

실행 후 브라우저가 자동으로 열립니다.  
열리지 않으면 브라우저에서 직접 **http://localhost:8501** 을 입력하세요.

---

## 사용 방법

실행하면 4단계 화면이 순서대로 진행됩니다.

### STEP 1 — 파일 업로드 & 파싱
- 심의회 PPTX 파일을 업로드합니다 (여러 개 동시 가능)
- base_bom.xlsx 파일도 함께 업로드합니다 (선택)
- **파싱 시작** 버튼을 누르면 AI가 변경점을 자동 추출합니다

### STEP 2 — 파싱 결과 확인
- AI가 추출한 변경점 목록을 확인합니다
- 잘못 추출된 항목은 수정하거나 삭제할 수 있습니다
- 확인 후 **이력 검색 시작** 버튼을 누릅니다

### STEP 3 — 후보 선정
- 각 부품별로 AI가 찾은 과거 유사 이력 후보가 표시됩니다
- 체크박스로 참고할 후보를 선택합니다
- AI가 후보를 찾지 못한 경우, 검색된 원본 이력 목록이 대신 표시됩니다
- 선택 완료 후 **선택 확정** 버튼을 누릅니다

### STEP 4 — Master BOM 작성
- Base Model / New Model 이름을 입력합니다
- **Master BOM 생성** 버튼을 누르면 AI가 변경사유를 자동 작성합니다
- 미리보기 확인 후 **Excel 다운로드** 버튼으로 저장합니다

---

## 서버 재시작 방법

터미널에서 `Ctrl + C` 로 서버를 끄고, 다시 아래 명령어를 입력합니다.

```bash
python3 -m streamlit run app.py
```

---

## 문제 해결

| 증상 | 해결 방법 |
|------|----------|
| `ModuleNotFoundError` 오류 | `pip install -r requirements.txt` 다시 실행 |
| 브라우저가 안 열림 | 주소창에 `http://localhost:8501` 직접 입력 |
| `OPENAI_API_KEY` 오류 | `.env` 파일에 API 키가 올바르게 입력되었는지 확인 |
| Neo4j 연결 오류 | Neo4j 데이터베이스가 실행 중인지 확인 (담당자 문의) |
| 파싱 결과가 이상함 | PPTX 파일이 심의회 표준 양식인지 확인 |
