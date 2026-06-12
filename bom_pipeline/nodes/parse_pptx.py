"""
PPTX 파싱 노드.

슬라이드 타입을 자동 감지하여 두 경로로 처리:

  [타입 A] 상세 표 슬라이드 (퀵존레인지 등)
    - Base P/No / New P/No / Part Name / Lev. 컬럼이 있는 표
    - base_part_no, new_part_no, bom_level 직접 추출
    - 이후 history_search에서 base_part_no로 DB 바로 조회 가능

  [타입 B] 요약 표 슬라이드 (Compact Oven 등)
    - Module명 + 주요 변경점 텍스트만 있는 표
    - module, part(추정), change_detail, change_reason 추출
    - base_part_no = "" → history_search에서 FTS+임베딩으로 보완
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from loader import load_pptx
from state import BOMPipelineState

# ── 타입 A 프롬프트 (Base P/No 있는 상세 표) ─────────────────────────────

TYPE_A_SYSTEM = """당신은 제조업 개발심의회 PPTX에서 부품 변경점을 추출하는 전문가입니다.

이 슬라이드는 Base P/No, New P/No, Part Name, Lev. 컬럼이 있는 상세 변경점 표입니다.
표의 각 행에서 부품 변경점을 추출하여 JSON 배열로만 반환하세요. 설명 없이 JSON만 출력하세요.

반환 형식:
[
  {{
    "module": "Controller",
    "part": "Controller Assembly,Mechanical",
    "bom_level": ".1",
    "base_part_no": "ACM76191721",
    "new_part_no": "TBD",
    "change_detail": "하위 부품 변경",
    "change_reason": "",
    "discipline": "기구",
    "change_type": "Changing",
    "concern": "",
    "source_pptx": "",
    "evidence_slide": 9
  }}
]

필드 규칙:
- module: 슬라이드 제목이나 맥락에서 파악한 상위 모듈명
- part: Part Name 컬럼 값 그대로
- bom_level: Lev. 컬럼 값 (.1 / ..2 / ...3 / ....4 등). 없으면 ""
- base_part_no: Base P/No 컬럼 값. "-" 또는 빈 값이면 ""
- new_part_no: New P/No 컬럼 값 그대로 (TBD / 삭제 / ← / 품번 등)
- change_detail: 상세 변경점 컬럼 값. 없으면 new_part_no에서 유추
- change_reason: 변경 사유. 컬럼 없으면 ""
- discipline: 기구 / 제어 / ThinQ / 기타 (맥락 판단)
- change_type 규칙:
    base_part_no="" 이고 new_part_no가 품번/TBD → "NEW"
    new_part_no="삭제" 또는 "Delete" → "삭제"
    그 외 → "Changing"
- concern: ""
- source_pptx: "" (비워둘 것)
- evidence_slide: 슬라이드 번호 (정수)

제외 규칙:
- Part Name이 비어있거나 헤더 행 (No. / Lev. / Part Name 등만 있는 행)
- new_part_no가 "←" 인 행 (변경 없음)
- 하위 부품 변경으로만 표시되고 실제 변경이 없는 어셈블리 행은 포함 (하위 변경 파악용)
"""

TYPE_A_HUMAN = """소스 파일: {source_pptx}

{slide_text}"""

# ── 타입 B 프롬프트 (Module명 + 주요 변경점 요약 표) ─────────────────────

TYPE_B_SYSTEM = """당신은 제조업 개발심의회 PPTX에서 부품 변경점을 추출하는 전문가입니다.

이 슬라이드는 Module명과 주요 변경점 텍스트만 있는 요약 표입니다.
각 변경점을 개별 항목으로 분리하여 JSON 배열로만 반환하세요. 설명 없이 JSON만 출력하세요.

반환 형식:
[
  {{
    "module": "Cavity",
    "part": "Cavity Assembly",
    "bom_level": "",
    "base_part_no": "",
    "new_part_no": "",
    "change_detail": "H 치수 137mm 감소",
    "change_reason": "제품 치수 변경",
    "discipline": "기구",
    "change_type": "Changing",
    "concern": "",
    "source_pptx": "",
    "evidence_slide": 7
  }}
]

필드 규칙:
- module: Module명 컬럼 값
- part: 변경점 텍스트에서 추정한 부품명. 특정 부품명 없으면 module + " Assembly"
- bom_level: "" (정보 없음)
- base_part_no: "" (정보 없음)
- new_part_no: "" (정보 없음)
- change_detail: 변경점 텍스트 1항목. "변경 전 → 변경 후" 형식으로 작성 가능하면 변환
- change_reason: 변경점 텍스트에서 목적절 유추. 불가능하면 ""
- discipline: 기구 / 제어 / ThinQ / 기타
- change_type: NEW / Changing / 삭제 중 텍스트에서 판단
- concern: ""
- source_pptx: "" (비워둘 것)
- evidence_slide: 슬라이드 번호 (정수)

분리 규칙:
- "1. xxx 2. xxx" 형태 → 번호 항목 하나 = change_point 하나
- 같은 모듈에 여러 변경점 → 각각 별도 항목
- "변경점 없음" / "동일" 표현 → 제외
"""

TYPE_B_HUMAN = """소스 파일: {source_pptx}

{slide_text}"""

# ── Few-shot 예시 ─────────────────────────────────────────────────────────

TYPE_A_EXAMPLES = [
    {
        "input": """소스 파일: 퀵존레인지.pptx

=== 슬라이드 9: 유첨 1. 개발 변경점 상세 작성 내용 - Controller ===
No. | Lev. | Base P/No | New P/No | Part Name | UOM | Qty | 상세 변경점
1 | .1 | ACM76191721 | TBD | Controller Assembly,Mechanical | EA | 1 | 하위 부품 변경
2 | ..2 | ACM76199004 | TBD | Controller Assembly,Sub | EA | 1 | LG Sig. Type 적용(6.8\" LCD)
3 | ....4 | MFM64499201 | EAJ30079201 | Membrane → LCD Touch Panel | EA | 1 | 6.8\" LCD 적용
4 | ...3 | EAV65008503 | Delete | LED Display Module | EA | 1 | Back Lighting 삭제""",
        "output": json.dumps([
            {"module": "Controller", "part": "Controller Assembly,Mechanical", "bom_level": ".1",
             "base_part_no": "ACM76191721", "new_part_no": "TBD",
             "change_detail": "하위 부품 변경", "change_reason": "",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 9},
            {"module": "Controller", "part": "Controller Assembly,Sub", "bom_level": "..2",
             "base_part_no": "ACM76199004", "new_part_no": "TBD",
             "change_detail": "LG Sig. Type 적용(6.8\" LCD)", "change_reason": "",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 9},
            {"module": "Controller", "part": "Membrane → LCD Touch Panel", "bom_level": "....4",
             "base_part_no": "MFM64499201", "new_part_no": "EAJ30079201",
             "change_detail": "Membrane → LCD Touch Panel (6.8\" LCD 적용)", "change_reason": "6.8\" LCD 적용",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 9},
            {"module": "Controller", "part": "LED Display Module", "bom_level": "...3",
             "base_part_no": "EAV65008503", "new_part_no": "Delete",
             "change_detail": "Back Lighting 삭제", "change_reason": "Back Lighting 삭제",
             "discipline": "기구", "change_type": "삭제", "concern": "", "source_pptx": "", "evidence_slide": 9},
        ], ensure_ascii=False),
    }
]

TYPE_B_EXAMPLES = [
    {
        "input": """소스 파일: CompactOven.pptx

=== 슬라이드 7: 유첨3. 개발 변경점 상세 작성 내용 ===
No. | Module명 | 주요 변경점
① | Cavity | 1. 제품 치수 변경에 따른 H 치수 137mm 감소 2. Cavity 조립 방식 변경 3. MWO, Steam 기능 착탈 구조 반영
② | Door | 1. 제품 치수 변경에 따른 Size 변경 2. Hinge Bar 형상 수정
③ | Controller | 1. Conv Heater 삭제에 따른 인쇄 변경
⑤ | Panel Assembly | 1. 제품 치수 변경에 따른 Size 변경 2. Conv Heater 삭제""",
        "output": json.dumps([
            {"module": "Cavity", "part": "Cavity Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "H 치수 137mm 감소", "change_reason": "제품 치수 변경",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Cavity", "part": "Cavity Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "Cavity 조립 방식 변경", "change_reason": "신규 Platform 적용",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Cavity", "part": "Cavity Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "MWO, Steam 기능 착탈 구조 반영", "change_reason": "MWO/Steam 기능 추가",
             "discipline": "기구", "change_type": "NEW", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Door", "part": "Door Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "제품 치수 변경에 따른 Size 변경", "change_reason": "제품 치수 변경",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Door", "part": "Hinge Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "Hinge Bar 형상 수정", "change_reason": "Door 치수 변경",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Controller", "part": "Panel,Control", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "Conv Heater 삭제에 따른 인쇄 변경", "change_reason": "Conv Heater 삭제",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Panel Assembly", "part": "Panel Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "제품 치수 변경에 따른 Size 변경", "change_reason": "제품 치수 변경",
             "discipline": "기구", "change_type": "Changing", "concern": "", "source_pptx": "", "evidence_slide": 7},
            {"module": "Panel Assembly", "part": "Panel Assembly", "bom_level": "",
             "base_part_no": "", "new_part_no": "",
             "change_detail": "Conv Heater 삭제", "change_reason": "Conv Heater 삭제",
             "discipline": "기구", "change_type": "삭제", "concern": "", "source_pptx": "", "evidence_slide": 7},
        ], ensure_ascii=False),
    }
]


# ── LLM 체인 ──────────────────────────────────────────────────────────────

_chain_a = None
_chain_b = None


def _make_chain(system: str, human: str, examples: list[dict]):
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    example_prompt = ChatPromptTemplate.from_messages([
        ("human", "{input}"),
        ("ai", "{output}"),
    ])
    few_shot = FewShotChatMessagePromptTemplate(
        example_prompt=example_prompt,
        examples=examples,
    )
    prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        few_shot,
        ("human", human),
    ])
    return prompt | llm | JsonOutputParser()


def _get_chain_a():
    global _chain_a
    if _chain_a is None:
        _chain_a = _make_chain(TYPE_A_SYSTEM, TYPE_A_HUMAN, TYPE_A_EXAMPLES)
    return _chain_a


def _get_chain_b():
    global _chain_b
    if _chain_b is None:
        _chain_b = _make_chain(TYPE_B_SYSTEM, TYPE_B_HUMAN, TYPE_B_EXAMPLES)
    return _chain_b


# ── 유틸 ──────────────────────────────────────────────────────────────────

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


def _slides_to_text(slides: list[dict]) -> str:
    parts = []
    for s in slides:
        parts.append(f"=== 슬라이드 {s['slide']}: {s['title']} ===\n{s['content']}")
    return "\n\n".join(parts)


def _is_no_change(item: dict) -> bool:
    """변경 없음 항목 필터."""
    new_pno = (item.get("new_part_no") or "").strip()
    detail = (item.get("change_detail") or "").strip()
    # "←" 는 변경 없음 표시
    if new_pno == "←":
        return True
    # 변경점도 비어있고 부품명도 없으면 제외
    if not item.get("part") and not detail:
        return True
    return False


def _dedup(items: list[dict]) -> list[dict]:
    """(base_part_no, part, change_detail) 기준 중복 제거."""
    seen: set[tuple] = set()
    result = []
    for item in items:
        key = (
            item.get("base_part_no", ""),
            item.get("part", ""),
            item.get("change_detail", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


# ── 노드 ──────────────────────────────────────────────────────────────────

def parse_pptx_node(state: BOMPipelineState) -> dict:
    pptx_path = state.get("pptx_path", "")
    if not pptx_path or not Path(pptx_path).exists():
        print("[parse_pptx] pptx_path 없음")
        return {"change_points": []}

    print(f"[parse_pptx] 로드 중: {Path(pptx_path).name}")
    slides = load_pptx(pptx_path)

    if not slides:
        print("[parse_pptx] 관련 슬라이드 없음")
        return {"change_points": []}

    # 타입별 분리
    slides_a = [s for s in slides if s["has_pno"]]
    slides_b = [s for s in slides if not s["has_pno"]]
    print(f"[parse_pptx] 총 {len(slides)}개 슬라이드 | 타입A(상세표)={len(slides_a)} 타입B(요약표)={len(slides_b)}")

    source_name = Path(pptx_path).name
    all_items: list[dict] = []

    # 타입 A 처리
    if slides_a:
        # 슬라이드별로 각각 호출 (표 구조가 슬라이드마다 다를 수 있음)
        for slide in slides_a:
            slide_text = f"=== 슬라이드 {slide['slide']}: {slide['title']} ===\n{slide['content']}"
            print(f"  [타입A] 슬라이드 {slide['slide']}: {slide['title'][:50]}")
            try:
                raw = _get_chain_a().invoke({
                    "source_pptx": source_name,
                    "slide_text": slide_text,
                })
                items = _safe_parse(raw)
            except Exception as e:
                print(f"  [WARNING] 타입A 파싱 실패 슬라이드 {slide['slide']}: {e}")
                items = []

            for item in items:
                item["source_pptx"] = source_name
            all_items.extend(items)
            print(f"    → {len(items)}개 추출")

    # 타입 B 처리 — 슬라이드 묶어서 1회 호출
    if slides_b:
        slide_text = _slides_to_text(slides_b)
        print(f"  [타입B] {len(slides_b)}개 슬라이드 묶어서 파싱")
        try:
            raw = _get_chain_b().invoke({
                "source_pptx": source_name,
                "slide_text": slide_text,
            })
            items = _safe_parse(raw)
        except Exception as e:
            print(f"  [WARNING] 타입B 파싱 실패: {e}")
            items = []

        for item in items:
            item["source_pptx"] = source_name
        all_items.extend(items)
        print(f"    → {len(items)}개 추출")

    # 후처리: 변경 없음 제거 + 중복 제거 + 기본값 채우기
    all_items = [item for item in all_items if not _is_no_change(item)]
    all_items = _dedup(all_items)

    for item in all_items:
        item.setdefault("module", "")
        item.setdefault("part", "")
        item.setdefault("bom_level", "")
        item.setdefault("base_part_no", "")
        item.setdefault("new_part_no", "")
        item.setdefault("change_detail", "")
        item.setdefault("change_reason", "")
        item.setdefault("discipline", "기구")
        item.setdefault("change_type", "Changing")
        item.setdefault("concern", "")
        item.setdefault("evidence_slide", 0)
        item.setdefault("history_candidates", [])

    type_a_cnt = sum(1 for i in all_items if i.get("base_part_no"))
    type_b_cnt = len(all_items) - type_a_cnt
    print(f"[parse_pptx] 완료: 총 {len(all_items)}개 "
          f"(P/No 있음={type_a_cnt} / P/No 없음={type_b_cnt})")

    return {"change_points": all_items}