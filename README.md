# MASTER-BOM-AI-AGENT FRAMEWORK 

## Description
**기존 개발부품 Master/BOM 이력과 현재 Base BOM, 심의회 PPT의 변경점**을 조합해서 
신규 모델에 필요한 변경부품 리스트와 개발부품 마스터 초안을 자동 생성하는 프레임워크


## FLOW
- as-is
    개발 시작→ 변경점 정리 → 과거 유사 모델 탐색 →개발 부품 Master 초안 작성
    →(부품 시험 여부 판단 → 부품 시험기획) →BOM 초반 작성 → 누락 부품 여부등 검토 
    → BOM 심의 → 개발 부품 Mater,BOM 최종 검토 → NFDM BOM 작성
- To-be
    개발 시작 → 변경점 입력 → 유사 사례 자동 검색(RAG) →
    → 개발 부품 Master 초안 작성
    →BOM 초안 생성
    →각 담당자 검토 및 확정 → NPDM BOM 작성
## 기술 스택

| Layer | Stack |
| --- | --- |
| Frontend | streamlit |
| Backend | --- |
| Database | --- |
| GPU | --- |
| Infra | --- |
| API Spec | --- |

## TO-DO



## Roles
- **서준일** - 전체 통합 및 데이터베이스 설계
- **한승용** - 데이터 분석 및 전처리
- **문영훈** - RAG 파이프라인 구축
- **서현우** - RAG 파이프라인 구축

---

## Project Guidelines
### Conventional Commits
`type(scope): subject`
- type: 변경 작업 종류
- scope: 영향 범위 (생략 가능)
- subject: 변경 내용 요약 설명
- 예시: `docs: update readme` → "문서(docs) 수정", "readme파일 업데이트"
- 가능하면 구분가능한 수정단위 하나씩 커밋
### Conventional Branch
`type/short-description`
- type: 작업 종류
- short-description: 간략 설명
- 가능하면 하나의 브랜치에서 하나의 기능/모듈 단위 작업
