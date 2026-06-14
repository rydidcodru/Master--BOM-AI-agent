"""L1 structurizer — 자유텍스트 → ChangeIntent.

흐름: 정규식 선추출(품번/모델/region — config/axioms.yaml 패턴 재사용, 결정론) →
로컬 LLM JSON 모드 의미 슬롯(게이트, self-consistency 옵션) → confidence 임계 미만이면
raw fallback. 결과는 dev_part_master.change_intent JSONB에 캐시 가능.

LLM 비활성(ENABLE_LLM!=1)이거나 실패해도 정규식 경로로 항상 ChangeIntent를 만든다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.agent.intent.derive import derive_change_slots, env_flag, generic_labels
from src.agent.intent.models import ChangeIntent, ChangeSlot, IntentSource, LlmSlots
from src.agent.llm.client import LlmClient, default_llm, llm_enabled
from src.db.models import DevPartMaster
from src.ontology.axioms import (
    normalize_model_code,
    normalize_part_no,
    region_from_buyer,
    validate_model_code,
    validate_part_no,
    validate_region,
)
from src.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_THRESHOLD = 0.35
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.@/()\-]*")
_STRIP = "./-()"

_SYSTEM = (
    "너는 LG 가전 부품 BOM 변경 설명에서 구조화된 변경 의도를 추출한다. "
    "반드시 JSON 객체만 반환한다. 품번을 새로 지어내지 않는다."
)


def _tokenize(text: str) -> list[str]:
    return [t.strip(_STRIP) for t in _TOKEN_RE.findall(text)]


def _extract_entities(text: str) -> tuple[list[str], list[str], str | None]:
    """정규식 선추출: (part_nos, models, region). 결정론, LLM 0회."""
    part_nos: list[str] = []
    models: list[str] = []
    region: str | None = None
    for tok in _tokenize(text):
        if not tok:
            continue
        if region is None:
            r = region_from_buyer(tok) or (tok.upper() if validate_region(tok) else None)
            if r:
                region = r
                continue
        if ("." in tok or "@" in tok) and validate_model_code(tok):
            norm = normalize_model_code(tok)
            if norm not in models:
                models.append(norm)
        elif validate_part_no(tok):
            norm = normalize_part_no(tok)
            if norm not in part_nos:
                part_nos.append(norm)
    return part_nos, models, region


def _deterministic_queries(
    text: str, part_nos: list[str], models: list[str], attribute: str | None
) -> list[str]:
    """LLM 없이 검색용 재작성 쿼리 생성 (3~4개)."""
    candidates: list[str] = []
    if text:
        candidates.append(text)
    if part_nos:
        candidates.append(f"{part_nos[0]} 변경 영향")
    if models and attribute:
        candidates.append(f"{models[0]} {attribute}")
    elif models:
        candidates.append(f"{models[0]} 변경")
    if attribute:
        candidates.append(f"{attribute} 변경 부품")
    out: list[str] = []
    for q in candidates:
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out[:4] or ([text] if text else [])


def _heuristic_confidence(
    part_nos: list[str], models: list[str], region: str | None
) -> float:
    conf = 0.25
    if part_nos:
        conf += 0.30
    if models:
        conf += 0.15
    if region:
        conf += 0.10
    return min(1.0, conf)


def _build_prompt(text: str, part_nos: list[str], models: list[str]) -> str:
    return (
        "다음 변경 설명을 분석해 JSON으로 반환하라.\n"
        f"설명: {text}\n"
        f"이미 추출된 품번: {part_nos}\n"
        f"이미 추출된 모델: {models}\n"
        "JSON 키:\n"
        "- change_attribute: 무엇이 바뀌나 (예: 재질/치수/공급처/UIT/색상). 모르면 null\n"
        "- change_direction: 증가/감소/대체/삭제/추가 중 하나 또는 null\n"
        "- intent_summary: 한 문장 한국어 요약\n"
        "- rewritten_queries: 검색용 재작성 쿼리 3~4개 (한국어 문자열 배열)\n"
        "- confidence: 0~1 실수\n"
    )


def _merge_slots(samples: list[LlmSlots]) -> LlmSlots:
    """self-consistency: 과반 일치 슬롯만 유지, 쿼리는 합집합, confidence는 평균."""
    n = len(samples)

    def majority(vals: list[str | None]) -> str | None:
        counts = Counter(v for v in vals if v)
        if not counts:
            return None
        val, cnt = counts.most_common(1)[0]
        return val if cnt * 2 >= n else None

    summaries = [s.intent_summary for s in samples if s.intent_summary]
    queries: list[str] = []
    for s in samples:
        for q in s.rewritten_queries:
            if q not in queries:
                queries.append(q)
    return LlmSlots(
        change_attribute=majority([s.change_attribute for s in samples]),
        change_direction=majority([s.change_direction for s in samples]),
        intent_summary=Counter(summaries).most_common(1)[0][0] if summaries else "",
        rewritten_queries=queries,
        confidence=sum(s.confidence for s in samples) / n,
    )


def _run_llm(
    llm: LlmClient, text: str, part_nos: list[str], models: list[str], n: int
) -> LlmSlots | None:
    prompt = _build_prompt(text, part_nos, models)
    samples: list[LlmSlots] = []
    runs = max(1, n)
    for _ in range(runs):
        temperature = 0.0 if runs == 1 else 0.4
        try:
            raw = llm.complete_json(prompt, system=_SYSTEM, temperature=temperature)
            samples.append(LlmSlots.model_validate(raw))
        except (ValidationError, ValueError) as exc:
            log.warning("l1.llm_schema_reject", error=str(exc)[:160])
        except Exception as exc:  # noqa: BLE001 — 네트워크 등 → fallback
            log.warning("l1.llm_call_failed", error=str(exc)[:160])
    if not samples:
        return None
    return _merge_slots(samples) if len(samples) > 1 else samples[0]


def structurize(
    raw_text: str,
    *,
    llm: LlmClient | None = None,
    confidence_threshold: float = _DEFAULT_THRESHOLD,
    self_consistency: int = 1,
) -> ChangeIntent:
    """자유텍스트 → ChangeIntent. ``llm=None``이면 ENABLE_LLM=1일 때만 LLM 사용."""
    text = unicodedata.normalize("NFC", raw_text or "").strip()
    part_nos, models, region = _extract_entities(text)

    if llm is None and llm_enabled():
        llm = default_llm()

    slots: LlmSlots | None = None
    if llm is not None and text:
        slots = _run_llm(llm, text, part_nos, models, self_consistency)

    source: IntentSource
    if slots is not None:
        attribute = slots.change_attribute
        direction = slots.change_direction
        summary = slots.intent_summary
        queries = slots.rewritten_queries or _deterministic_queries(
            text, part_nos, models, attribute
        )
        confidence = slots.confidence
        source = "regex+llm"
    else:
        attribute = direction = None
        summary = ""
        queries = _deterministic_queries(text, part_nos, models, None)
        confidence = _heuristic_confidence(part_nos, models, region)
        source = "regex"

    if confidence < confidence_threshold:
        source = "raw_fallback"
        queries = [text] if text else []

    return ChangeIntent(
        raw_text=text,
        part_nos=part_nos,
        models=models,
        region=region,
        change_attribute=attribute,
        change_direction=direction,
        intent_summary=summary,
        rewritten_queries=queries,
        confidence=confidence,
        source=source,
    )


def _maybe_llm() -> LlmClient | None:
    """게이트된 LLM(있으면). 변경점 유도 2차용 — 게이트 off/실패 시 None(결정론만)."""
    if not llm_enabled():
        return None
    try:
        return default_llm()
    except Exception:  # noqa: BLE001 — 게이트 미충족/미설치 → 결정론 폴백
        return None


def _clean_idents(values: list[str] | None) -> list[str]:
    """식별자(품번/모델) 리스트 정리: NFC·strip·플레이스홀더 제거·중복 제거. 순서 보존."""
    out: list[str] = []
    for v in values or []:
        v = unicodedata.normalize("NFC", str(v or "").strip())
        if not v or v in ("정보 없음", "내용 없음", "<발번대기>"):
            continue
        if v not in out:
            out.append(v)
    return out


def intent_from_change(
    *,
    change_detail: str,
    change_reason: str,
    base_model: str | None = None,
    region: str | None = None,
    part_name: str | None = None,
    part_nos: list[str] | None = None,
    models: list[str] | None = None,
    module_names: list[str] | None = None,
) -> ChangeIntent:
    """PPT 변경항목(변경내용 + 변경사유 [+ 부품명·식별자]) → ChangeIntent (L1 우회, 결정론).

    기본 검색 쿼리는 **변경내용/변경사유 텍스트**로 구성한다. ``part_name``/``part_nos``/
    ``models`` 중 **placeholder를 거른 뒤 남는 값이 있으면** **부품 보강 쿼리**(변경텍스트 +
    부품명 + 품번)를 추가로 만들고 그 값을 intent에 싣는다 — ``search_events``의 parts 채널이
    부품명/품번/모델 컬럼과 word_similarity로 매칭한다(2026-06-10 사용자 확정: 검색 키에 부품명+
    식별자 포함, [[feedback-search-by-reason-not-id]] 개정). 인자가 없거나 모두 placeholder/빈
    값이면 **reason-only** 동작을 그대로 유지한다(하위호환). ``base_model``은 검색어가 아니라
    표기/선택적 필터용으로 보존 — 항목마다 동일해 변별력이 없고 결과를 동질화하므로 쿼리에
    자동 주입하지 않는다. 자유텍스트용 :func:`structurize`와 병행.
    """
    detail = unicodedata.normalize("NFC", (change_detail or "").strip())
    reason = unicodedata.normalize("NFC", (change_reason or "").strip())
    for tok in ("정보 없음", "내용 없음"):
        if detail == tok:
            detail = ""
        if reason == tok:
            reason = ""
    name = unicodedata.normalize("NFC", (part_name or "").strip())
    if name in ("정보 없음", "내용 없음"):
        name = ""
    pno_list = _clean_idents(part_nos)
    model_list = _clean_idents(models)

    raw = " / ".join(p for p in (detail, reason) if p)
    # reason-only 쿼리(기존) — dense(의미) 채널을 깨끗이 유지하기 위해 보존한다.
    queries: list[str] = []
    for q in (raw, detail, reason):
        q = q.strip()
        if q and q not in queries:
            queries.append(q)
    # 변경점 유도 v3 (DERIVE_CP 게이트, 기본 on) — 변경내역이 없고 사유만 있을 때
    # 사유에서 (target, attribute, action) 슬롯을 유도해 정렬 쿼리를 최대 2개 추가.
    # 둘 다 합본형(슬롯 단독 쿼리 금지 — 2~4토큰 단독 쿼리는 lexical/sparse 노이즈
    # 고득점 위험). 게이트 off/유도 실패 시 기존 쿼리와 바이트 단위 동일.
    derived_slots: list[ChangeSlot] = []
    derived_src: str | None = None
    if not detail and reason and env_flag("DERIVE_CP", default=True):
        # P6.2: LLM 2차는 분리 게이트(DERIVE_CP_LLM, 기본 off) + 기존 ENABLE_LLM 체계 이중.
        # 기본은 결정론 1차만 — 결정론 실패 대표 사례("원가 절감")는 슬롯이 원래 없어 LLM도
        # 빈 배열이 정답이라, 2차의 한계 회수는 P8 피드백 데이터로 판단한다.
        derive_llm = _maybe_llm() if env_flag("DERIVE_CP_LLM", default=False) else None
        d = derive_change_slots(reason, name or None, llm=derive_llm)
        if d is not None:
            derived_slots, derived_src = d
            composed = " ".join(
                p
                for s in derived_slots
                for p in (s.target, s.attribute, s.action)
                if p
            )
            for q in (
                f"{composed} {reason}",  # ① 합성형 정렬
                *(
                    [f"{' '.join(g)} {reason}"]  # ② 라벨형 정렬 (generic_map 조회)
                    if (g := generic_labels(derived_slots))
                    else []
                ),
            ):
                q = q.strip()
                if q and q not in queries:
                    queries.append(q)
    # 부품명·품번이 있으면 '부품 보강 쿼리'(변경텍스트 + 부품 식별)를 추가. parts 채널이
    # 부품명/품번/모델 컬럼과 매칭하므로 식별 토큰이 쿼리에 들어 있어야 한다.
    ident_tail = " ".join(t for t in ([name, *pno_list, *model_list]) if t).strip()
    if ident_tail:
        parts_q = (f"{raw} {ident_tail}").strip() if raw else ident_tail
        if parts_q and parts_q not in queries:
            queries.append(parts_q)

    # 이름-add 쿼리 (env INTENT_NAME_REASON, 기본 off) — terse 사유 행이 같은 부품 패밀리
    # 이벤트로 연결되도록 부품명을 사유와 합본한 쿼리를 *추가*한다(치환 아님 — 치환형은 검증서
    # terse 행 top-1 퇴행). parts_q 뒤에 붙여 기존 쿼리 보존(eviction 방지). 안전형(9방향 검증
    # 산물): 부품명만 싣고 식별번호/모델코드는 미주입(dense 인덱스 정체성 보호). 프로브 7/12 vs 6/12.
    if name and raw and env_flag("INTENT_NAME_REASON", default=False):
        name_q = f"{name} {raw}".strip()
        if name_q not in queries:
            queries.append(name_q)

    # ①.5 구조 스코프용 모듈명 — 검색어 미주입(표기/닻 매칭용), placeholder 제거.
    mod_list = _clean_idents(module_names)

    has_any = bool(raw or ident_tail)
    return ChangeIntent(
        raw_text=raw,
        part_nos=pno_list,
        models=model_list,
        region=region,
        base_model=(base_model or None),
        module_names=mod_list,
        change_attribute=None,
        change_direction=None,
        # 요약은 '변경'을 가리켜야 한다(부품 식별이 아니라) — 부품 정체성은 part_nos/part_name에.
        intent_summary=detail or reason,
        rewritten_queries=queries[:4] or ([raw or ident_tail] if has_any else []),
        confidence=1.0 if has_any else 0.0,
        source="ppt",
        derived_change_slots=derived_slots,
        derived_cp_source=derived_src,  # type: ignore[arg-type]
    )


def cache_change_intent(session: Session, doc_id: int, intent: ChangeIntent) -> None:
    """ChangeIntent를 dev_part_master.change_intent JSONB에 캐시."""
    row = session.get(DevPartMaster, doc_id)
    if row is None:
        raise ValueError(f"unknown doc_id={doc_id}")
    row.change_intent = intent.model_dump()
    session.commit()
