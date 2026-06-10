from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from loader import load_all_pptx, extract_slides
from state import BOMSearchState

# ── 시스템 프롬프트 ────────────────────────────────────────────
SYSTEM_PROMPT = """당신은 제조업 개발심의회 PPTX에서 부품 변경점을 추출하는 전문가입니다.

슬라이드 텍스트를 보고 표 구조를 스스로 파악한 뒤, 부품 변경점을 JSON 배열로만 반환하세요.
설명 텍스트 없이 JSON만 출력하세요.

반환 형식:
[
  {{
    "module": "Cavity",
    "part": "Conv. Motor",
    "change_detail": "AC motor → BLDC motor",
    "change_reason": "균일 가열 성능 및 요리 시간 단축",
    "discipline": "기구",
    "type": "Changing",
    "concern": "",
    "source_pptx": "",
    "evidence_slide": 8
  }}
]

필드 규칙:
- module: 부품이 속한 모듈 (Cavity / Door / Controller / Insulator / Panel / OutCase / SW / ThinQ 등)
- part: 원본 부품명 그대로
- change_detail: "변경 전 → 변경 후" 형식. 없으면 변경점 텍스트 그대로
- change_reason: 변경사유 텍스트. 없으면 change_detail에서 목적절 유추. 불가능하면 ""
- discipline: 기구 / 제어 / ThinQ / 기타
- type: Changing / NEW / 삭제 중 하나
- concern: 걱정점 텍스트. 없으면 ""
- source_pptx: "" (비워둘 것)
- evidence_slide: 슬라이드 번호 (정수)

표 구조 파악 규칙:
- 표 한 행에 부품명이 하나면 → 행 하나 = change_point 하나
- 표 한 셀 안에 "1. xxx 2. xxx" 형태로 여러 부품이 나열된 경우 → 번호 항목 하나 = change_point 하나로 분리
- 요약 슬라이드(모듈별 주요변경점 나열)와 상세 슬라이드가 함께 있으면 → 상세 슬라이드 우선, 요약은 무시
- 변경사유 컬럼이 없으면 변경내역·Remark·맥락에서 유추

제외 규칙:
- part가 비어있는 행
- 헤더 행 (No., Part, 구분 등만 있는 행)
- 동일 부품 중복 시 하나로 합산
- SW/ThinQ 변경점도 포함 (discipline 필드로 구분)"""

# ── Few-shot 예시 ──────────────────────────────────────────────
# 예시 1: 행 단위 상세 표 (NPI Extra 양식)
EXAMPLE_INPUT_1 = """소스 파일: NPI_Extra_sample.pptx

=== 슬라이드 8: 유첨1. 개발 변경점 상세 작성 내용 ===
유첨1. 개발 변경점 상세 작성 내용

| 구분 | No | Part | 변경 내역 (변경 전 → 변경 후) | 변경 사유 | 걱정점 |
| 기구 (4M 변경포함) | 1 | Conv. Motor | AC motor → BLDC motor | 균일 가열 성능 및 요리 시간 단축 | 요리 성능 및 온도 정밀도 |
|  | 2 | Conv. Fan | 용접 타입 Fan 적용 | BLDC 모터 적용으로 인한 Fan 형상변경 |  |
|  | 3 | Conv. Fan Cover | Fan cover hole 위치 및 size 변경 | BLDC 모터 적용으로 인한 Fan Cover 형상변경 |  |
|  | 4 | Cover, Camera | Camera 모듈 장착으로 인한 부품 추가 | Door 카메라 적용 | Camera module 온도 Harness cover와 Cavity 간섭 |
|  | 7 | Panel,Control | 4.3" → 6.8" LCD 구조 적용 | 4.3" → 6.8" LCD 변경 | Controller 개폐 성능 확인 |
| 제어 | 1 | LCD S/W | OS 적용 및 UI 추가/변경 | 신규기능(고내 카메라, 사이드 라이팅)에 따른 UI 추가 |  |
| ThinQ App | 1 | 카메라 모니터링 | 영상인식 기능 추가 | 카메라 기능 추가로 영상인식 기능 추가 |  |"""

EXAMPLE_OUTPUT_1 = json.dumps([
    {"module": "Cavity", "part": "Conv. Motor",    "change_detail": "AC motor → BLDC motor",                          "change_reason": "균일 가열 성능 및 요리 시간 단축",                    "discipline": "기구",   "type": "Changing", "concern": "요리 성능 및 온도 정밀도",  "source_pptx": "", "evidence_slide": 8},
    {"module": "Cavity", "part": "Conv. Fan",      "change_detail": "용접 타입 Fan 적용",                             "change_reason": "BLDC 모터 적용으로 인한 Fan 형상변경",                "discipline": "기구",   "type": "Changing", "concern": "",                          "source_pptx": "", "evidence_slide": 8},
    {"module": "Cavity", "part": "Conv. Fan Cover","change_detail": "Fan cover hole 위치 및 size 변경",               "change_reason": "BLDC 모터 적용으로 인한 Fan Cover 형상변경",          "discipline": "기구",   "type": "Changing", "concern": "",                          "source_pptx": "", "evidence_slide": 8},
    {"module": "Door",   "part": "Cover, Camera",  "change_detail": "Camera 모듈 장착으로 인한 부품 추가",            "change_reason": "Door 카메라 적용",                                    "discipline": "기구",   "type": "NEW",      "concern": "Camera module 온도 Harness cover와 Cavity 간섭", "source_pptx": "", "evidence_slide": 8},
    {"module": "Controller", "part": "Panel,Control","change_detail": '4.3" → 6.8" LCD 구조 적용',                   "change_reason": '4.3" → 6.8" LCD 변경',                               "discipline": "기구",   "type": "Changing", "concern": "Controller 개폐 성능 확인",  "source_pptx": "", "evidence_slide": 8},
    {"module": "SW",     "part": "LCD S/W",         "change_detail": "OS 적용 및 UI 추가/변경",                       "change_reason": "신규기능(고내 카메라, 사이드 라이팅)에 따른 UI 추가", "discipline": "제어",   "type": "Changing", "concern": "",                          "source_pptx": "", "evidence_slide": 8},
    {"module": "ThinQ",  "part": "카메라 모니터링", "change_detail": "영상인식 기능 추가",                             "change_reason": "카메라 기능 추가로 영상인식 기능 추가",               "discipline": "ThinQ",  "type": "NEW",      "concern": "",                          "source_pptx": "", "evidence_slide": 8},
], ensure_ascii=False, indent=2)

# 예시 2: 셀 안에 번호 목록 (NXI Compact 상세 양식)
EXAMPLE_INPUT_2 = """소스 파일: NXI_Compact_sample.pptx

=== 슬라이드 7: 유첨3. 개발 변경점 상세 작성 내용 ===
| No. | Module명 | 주요 변경점 |
| ① | Cavity | 1. 제품 치수 변경에 따른 H 치수 137mm 감소 2. Cavity 조립 방식 변경 3. MWO, Steam 기능 착탈 구조 반영 |
| ② | Door | 1. 제품 치수 변경에 따른 Size 변경 2. Hinge Bar 형상 수정 |
| ③ | Controller | 1. Conv Heater 삭제에 따른 인쇄 변경 |
| ⑤ | Panel Assembly | 1. 제품 치수 변경에 따른 Size 변경 2. Conv Heater 삭제 |

=== 슬라이드 11: 유첨3. 개발 변경점_Door Assembly ===
유첨3. 개발 변경점_Door Assembly

**높이 137.0mm 감소

1. Glass : 높이 137.0mm 감소 됨에 따라 Size가 변경된 Glass 적용
(Assembly 구조는 Base와 동일)

2. Frame,Door : 높이 137.0mm 감소 됨에 따라 Size가 변경된 Frame,Door 적용
(Assembly 구조는 Base와 동일)

3. Hinge Assembly :
- Door 무게 변경에 따른 Spring 강도 변경
- B700 (B900) 구조에 따른 Hinge Bar 형상 변경

| Module | Base_BO24 Good | B700_Oven Only |
| Door Assembly |  | Door assembly 품번추가 : TBD |"""

EXAMPLE_OUTPUT_2 = json.dumps([
    {"module": "Cavity",  "part": "Cavity Assembly",  "change_detail": "H 치수 137mm 감소",                                       "change_reason": "제품 치수 변경",                           "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
    {"module": "Cavity",  "part": "Cavity Assembly",  "change_detail": "Cavity 조립 방식 변경",                                   "change_reason": "신규 Platform 적용에 따른 조립 구조 변경", "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
    {"module": "Cavity",  "part": "Cavity Assembly",  "change_detail": "MWO, Steam 기능 착탈 구조 반영",                          "change_reason": "MWO/Steam 기능 추가",                      "discipline": "기구", "type": "NEW",      "concern": "", "source_pptx": "", "evidence_slide": 7},
    {"module": "Door",    "part": "Glass",            "change_detail": "높이 137.0mm 감소에 따른 Size 변경 Glass 적용",           "change_reason": "제품 치수 변경",                           "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 11},
    {"module": "Door",    "part": "Frame,Door",       "change_detail": "높이 137.0mm 감소에 따른 Size 변경 Frame,Door 적용",      "change_reason": "제품 치수 변경",                           "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 11},
    {"module": "Door",    "part": "Hinge Assembly",   "change_detail": "Door 무게 변경에 따른 Spring 강도 변경 및 Hinge Bar 형상 변경", "change_reason": "Door 무게 변경 및 B700 구조 적용",   "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 11},
    {"module": "Controller","part": "Panel,Control",  "change_detail": "Conv Heater 삭제에 따른 인쇄 변경",                       "change_reason": "Conv Heater 삭제",                         "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
    {"module": "Panel",   "part": "Panel Assembly",   "change_detail": "제품 치수 변경에 따른 Size 변경 및 Conv Heater 삭제",     "change_reason": "제품 치수 변경, Conv Heater 삭제",         "discipline": "기구", "type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
], ensure_ascii=False, indent=2)

FEW_SHOT_EXAMPLES = [
    {"input": EXAMPLE_INPUT_1, "output": EXAMPLE_OUTPUT_1},
    {"input": EXAMPLE_INPUT_2, "output": EXAMPLE_OUTPUT_2},
]

HUMAN_TEMPLATE = """소스 파일: {source_pptx}

{pptx_text}"""

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)

        # Few-shot 예시 포맷
        example_prompt = ChatPromptTemplate.from_messages([
            ("human",  "{input}"),
            ("ai",     "{output}"),
        ])

        few_shot_prompt = FewShotChatMessagePromptTemplate(
            example_prompt=example_prompt,
            examples=FEW_SHOT_EXAMPLES,
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            few_shot_prompt,
            ("human", HUMAN_TEMPLATE),
        ])

        _chain = prompt | llm | JsonOutputParser()
    return _chain


def _slides_to_text(slides: list[dict]) -> str:
    parts = []
    for s in slides:
        parts.append(f"=== 슬라이드 {s['slide']}: {s['title']} ===\n{s['content']}")
    return "\n\n".join(parts)


def _safe_parse(raw) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("items", "changes", "results", "data"):
            if isinstance(raw.get(key), list):
                return raw[key]
        return [raw]
    if isinstance(raw, str):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return []


def parse_pptx_node(state: BOMSearchState) -> dict:
    pptx_paths = state.get("pptx_paths") or []

    # pptx_paths가 없으면 input/ 디렉토리 폴백
    if pptx_paths:
        all_slides = []
        for p in pptx_paths:
            try:
                all_slides.extend(extract_slides(p))
            except Exception as e:
                print(f"[WARNING] {Path(p).name} 로드 실패: {e}")
    else:
        input_dir = Path(__file__).parent.parent.parent / "input"
        all_slides = load_all_pptx(input_dir)

    if not all_slides:
        print("[WARNING] 추출된 슬라이드 없음")
        return {"change_points": []}

    # 파일별로 묶어서 LLM 호출
    by_file: dict[str, list[dict]] = {}
    for s in all_slides:
        by_file.setdefault(s["source_pptx"], []).append(s)

    chain = _get_chain()
    all_change_points = []

    for source_pptx, slides in by_file.items():
        print(f"[parse_pptx] {source_pptx}: {len(slides)}개 슬라이드 파싱 중...")
        pptx_text = _slides_to_text(slides)
        try:
            raw = chain.invoke({"source_pptx": source_pptx, "pptx_text": pptx_text})
            items = _safe_parse(raw)
        except Exception as e:
            print(f"[WARNING] LLM 파싱 실패 ({source_pptx}): {e}")
            items = []

        for item in items:
            if not item.get("part"):
                continue
            item["source_pptx"] = source_pptx
            item.setdefault("canonical_part", "")
            item.setdefault("aliases", [])
            all_change_points.append(item)

        print(f"  → {len(items)}개 부품 추출")

    print(f"[parse_pptx] 총 {len(all_change_points)}개 change_point 추출 완료")
    return {"change_points": all_change_points}
