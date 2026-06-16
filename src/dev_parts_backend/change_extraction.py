import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .llm import chat_json, llm_model_name, openai_llm_enabled
from .module_mapping import (
    best_prefix_matches,
    enrich_change_with_mapping,
    looks_like_part_no,
    mapping_catalog_lines,
)
from .pptx_text import extract_slide_texts


OUTPUT_COLUMNS = [
    "change_id",
    "slide_number",
    "description",
    "module_name",
    "part_name",
    "base_part_no",
    "change_point",
    "change_reason",
    "change_type",
    "target_value",
    "change_intent_keywords",
    "evidence",
    "primary_prefix",
    "mapped_prefixes",
]
SAFE_FILENAME_RE = re.compile(r"[^0-9A-Za-z가-힣_.-]+")
TARGET_VALUE_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mm|cm|m|kg|g|inch|in|ea|개|도|%)\b", re.IGNORECASE)


def infer_intent_fields(source_text: str) -> dict[str, Any]:
    lowered = source_text.lower()
    keywords: list[str] = []
    change_type = ""
    if any(term in lowered for term in ("치수", "사이즈", "폭", "너비", "높이", "길이", "dimension", "width", "height", "size", "mm")):
        change_type = "dimension"
        keywords.extend(["치수", "dimension", "width", "height", "size"])
    if any(term in lowered for term in ("착탈", "탈착", "착용", "분리", "removable", "detachable", "detach", "attach")):
        change_type = change_type or "structure"
        keywords.extend(["착탈", "탈착", "분리", "removable", "detachable"])
    if any(term in lowered for term in ("스팀", "steam", "stema")):
        keywords.extend(["스팀", "steam", "stema"])
    if any(term in lowered for term in ("축소", "줄", "감소", "reduce", "reduced")):
        keywords.extend(["축소", "감소", "reduce"])
    if any(term in lowered for term in ("확대", "증가", "늘", "increase", "increased")):
        keywords.extend(["확대", "증가", "increase"])
    target_match = TARGET_VALUE_RE.search(source_text)
    return {
        "change_type": change_type,
        "target_value": target_match.group(0).replace(" ", "") if target_match else "",
        "change_intent_keywords": sorted(set(keywords)),
    }


def slide_context(slides: list[dict[str, Any]], *, max_chars: int = 18000) -> str:
    chunks = []
    used = 0
    for slide in slides:
        text = str(slide.get("text") or "").strip()
        if not text:
            continue
        block = f"[Slide {slide.get('slide_number')}]\n{text}\n"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 500:
                chunks.append(block[:remaining])
            break
        chunks.append(block)
        used += len(block)
    return "\n".join(chunks)


def rule_mapping_drafts(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    drafts = []
    for slide in slides:
        text = str(slide.get("text") or "").strip()
        if not text:
            continue
        matches = best_prefix_matches({"description": text}, threshold=0.65, limit=1)
        if not matches:
            continue
        description = matches[0]["description"]
        draft = enrich_change_with_mapping(
            {
                "change_id": f"slide-{slide.get('slide_number')}-draft-1",
                "slide_number": slide.get("slide_number"),
                "description": description,
                "module_name": description,
                "part_name": description,
                "base_part_no": "",
                "change_point": "",
                "change_reason": "",
                **infer_intent_fields(text),
                "evidence": text[:500],
            }
        )
        drafts.append(draft)
    return drafts


def extraction_messages(slides: list[dict[str, Any]]) -> list[dict[str, str]]:
    catalog_text = "\n".join(mapping_catalog_lines())
    slides_text = slide_context(slides)
    system = (
        "You extract structured development-change items from PPTX slide text for a BOM search workflow. "
        "Work like a careful extraction agent: split the slide content into ATOMIC changes (one changed "
        "module + one concrete change per row), keep the changed module/part name exactly as written on the "
        "slide, and never invent part numbers or changes that are not in the text. Return JSON only."
    )
    user = f"""
다음 PPTX 슬라이드 텍스트에서 개발 변경 항목을 추출하세요.

핵심 규칙:
- 실제 개발 변경점 또는 상세 작성 내용이 있는 항목만 추출합니다. 추측으로 없는 변경을 만들지 않습니다.
- [원자적 분리] 한 모듈에 변경이 여러 개면 변경마다 행을 나눕니다. 한 행에는 변경점 하나만 담습니다.
  예: Cavity에 "치수 137mm 축소"와 "Steam 모듈 반영"이 있으면 2개 행으로 나눕니다.
- [모듈명] module_name과 part_name은 슬라이드에 적힌 부품/모듈 이름을 그대로 씁니다(영문 표기 우선).
  사전 description으로 억지로 바꾸지 않습니다. 표준 모듈명 매핑은 시스템이 나중에 자동으로 합니다.
  description에는 가장 가까운 표준 모듈명 후보를 적되, 확실하지 않으면 module_name과 같게 둡니다.
- [동일성 유지] description / module_name / part_name은 같은 모듈을 가리켜야 합니다. 서로 다른 모듈을 섞지 않습니다.
- [base_part_no] 실제 BOM 부품번호(예: ADC56000821, AEV73730303)만 넣습니다.
  모델/등급 코드(예: BO24, LTIS7338XE, LQEN7135XE)나 치수/스펙 값은 절대 base_part_no에 넣지 않습니다. 없으면 빈 문자열.
- change_point는 "무엇이 어떻게 바뀌는지"를 한 문장으로, change_reason은 "왜"를 적습니다.
- change_type은 dimension / structure / material / color / electrical / mechanical / other 중 하나로 고릅니다.
- target_value는 137mm, 6.8inch처럼 변경 목표 수치가 있으면 넣고, 없으면 빈 문자열입니다.
- change_intent_keywords는 변경 의도를 찾기 좋은 키워드를 3~8개 뽑습니다. 한국어와 영어 동의어를 같이 넣어도 됩니다.

출력 JSON 형식:
{{
  "changes": [
    {{
      "change_id": "slide-7-change-1",
      "slide_number": 7,
      "description": "Cavity Assembly",
      "module_name": "Cavity",
      "part_name": "Cavity",
      "base_part_no": "",
      "change_point": "제품 치수 변경에 따라 Cavity 높이(H)를 137mm 축소",
      "change_reason": "제품 크기 축소에 맞춰 내부 공간 재설계",
      "change_type": "dimension",
      "target_value": "137mm",
      "change_intent_keywords": ["치수", "137mm", "축소", "height", "dimension"],
      "evidence": "슬라이드에서 근거가 되는 짧은 원문"
    }}
  ]
}}

참고용 표준 모듈명 후보(억지로 끼워맞추지 말 것):
{catalog_text}

PPTX 슬라이드 텍스트:
{slides_text}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def normalize_llm_change(raw: dict[str, Any], index: int) -> dict[str, Any]:
    slide_number = raw.get("slide_number")
    change_id = str(raw.get("change_id") or f"pptx-change-{index + 1}")
    # LLM이 모델/등급 코드나 스펙 값을 base_part_no로 잘못 넣는 경우를 걸러낸다.
    raw_part_no = str(raw.get("base_part_no") or "").strip()
    base_part_no = raw_part_no if looks_like_part_no(raw_part_no) else ""
    change = {
        "change_id": change_id,
        "slide_number": slide_number,
        "description": str(raw.get("description") or "").strip(),
        "module_name": str(raw.get("module_name") or "").strip(),
        "part_name": str(raw.get("part_name") or "").strip(),
        "base_part_no": base_part_no,
        "change_point": str(raw.get("change_point") or "").strip(),
        "change_reason": str(raw.get("change_reason") or "").strip(),
        "change_type": str(raw.get("change_type") or "").strip(),
        "target_value": str(raw.get("target_value") or "").strip(),
        "change_intent_keywords": raw.get("change_intent_keywords") or [],
        "evidence": str(raw.get("evidence") or "").strip(),
    }
    inferred = infer_intent_fields(" ".join([change["change_point"], change["change_reason"], change["evidence"]]))
    if not change["change_type"]:
        change["change_type"] = inferred["change_type"]
    if not change["target_value"]:
        change["target_value"] = inferred["target_value"]
    if not change["change_intent_keywords"]:
        change["change_intent_keywords"] = inferred["change_intent_keywords"]
    mapped = enrich_change_with_mapping(change)
    mapped["extra"] = {
        "slide_number": slide_number,
        "evidence": change["evidence"],
        "llm_description": change["description"],
    }
    return mapped


def safe_stem(filename: str) -> str:
    stem = Path(filename or "pptx_changes").stem or "pptx_changes"
    safe = SAFE_FILENAME_RE.sub("_", stem).strip("._")
    return safe or "pptx_changes"


def change_csv_row(change: dict[str, Any]) -> dict[str, Any]:
    mapping = change.get("mapping") or {}
    raw_keywords = change.get("change_intent_keywords") or []
    if isinstance(raw_keywords, str):
        keywords = [raw_keywords]
    else:
        keywords = list(raw_keywords)
    return {
        "change_id": change.get("change_id", ""),
        "slide_number": change.get("slide_number", ""),
        "description": change.get("description", ""),
        "module_name": change.get("module_name", ""),
        "part_name": change.get("part_name", ""),
        "base_part_no": change.get("base_part_no", ""),
        "change_point": change.get("change_point", ""),
        "change_reason": change.get("change_reason", ""),
        "change_type": change.get("change_type", ""),
        "target_value": change.get("target_value", ""),
        "change_intent_keywords": ",".join(str(item) for item in keywords if str(item).strip()),
        "evidence": change.get("evidence") or (change.get("extra") or {}).get("evidence", ""),
        "primary_prefix": mapping.get("primary_prefix", ""),
        "mapped_prefixes": ",".join(change.get("mapped_prefixes") or []),
    }


def save_changes_csv(changes: list[dict[str, Any]], *, filename: str = "", output_dir: str | Path = "data/output") -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_path / f"{safe_stem(filename)}_extracted_changes_{timestamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for change in changes:
            writer.writerow(change_csv_row(change))
    return {
        "path": str(csv_path),
        "rows": len(changes),
        "columns": OUTPUT_COLUMNS,
    }


def extract_changes_from_pptx(
    pptx_bytes: bytes,
    *,
    filename: str = "",
    use_llm: bool = True,
    model: str | None = None,
) -> dict[str, Any]:
    slides = extract_slide_texts(pptx_bytes)
    llm_status: dict[str, Any] = {"status": "disabled", "model": llm_model_name(model)}
    changes: list[dict[str, Any]] = []
    raw_llm: dict[str, Any] | None = None

    if use_llm and openai_llm_enabled() and any(slide.get("text") for slide in slides):
        try:
            raw_llm = chat_json(extraction_messages(slides), model=model, temperature=0.0)
            raw_changes = raw_llm.get("changes") if isinstance(raw_llm, dict) else []
            if not isinstance(raw_changes, list):
                raw_changes = []
            changes = [normalize_llm_change(item, index) for index, item in enumerate(raw_changes) if isinstance(item, dict)]
            llm_status = {"status": "extracted", "model": llm_model_name(model), "changes": len(changes)}
        except Exception as exc:
            changes = rule_mapping_drafts(slides)
            llm_status = {
                "status": "failed_rule_mapping_fallback",
                "model": llm_model_name(model),
                "error": str(exc),
                "changes": len(changes),
            }
    elif use_llm:
        changes = rule_mapping_drafts(slides)
        llm_status = {
            "status": "skipped_no_openai_api_key_rule_mapping_only",
            "model": llm_model_name(model),
            "changes": len(changes),
        }
    else:
        changes = rule_mapping_drafts(slides)
        llm_status = {"status": "disabled_rule_mapping_only", "model": llm_model_name(model), "changes": len(changes)}

    output_csv = save_changes_csv(changes, filename=filename) if changes else None

    return {
        "filename": filename,
        "slide_count": len(slides),
        "slides": slides,
        "changes": changes,
        "output_csv": output_csv,
        "llm_status": llm_status,
        "raw_llm": raw_llm,
        "mapping_catalog_count": len(mapping_catalog_lines()),
        "prompt_preview": json.dumps(
            {"slide_chars": len(slide_context(slides)), "use_llm": use_llm},
            ensure_ascii=False,
        ),
    }
