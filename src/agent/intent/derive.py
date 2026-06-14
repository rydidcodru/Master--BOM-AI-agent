"""L1 변경점 유도 v3 — 변경사유에서 (target, attribute, action) 슬롯 구조화.

**검색 쿼리 보강 전용**(②). 결과는 ``ChangeIntent.derived_change_slots``에 실리고
``intent_from_change``가 합성형/라벨형 정렬 쿼리를 추가한다. HITL 이후 출력 경로
(docgen/basebom)에는 절대 흘리지 않는다 — derived 변경점이 문서/New BOM에 "사실"로
출력되는 경로 금지(작업 지시 절대 제약).

1차 결정론(R0~R6, ``config/changepoint_vocab.yaml`` 구동) → 실패분만 LLM 2차
(opt-in — 호출측이 ``llm``을 주입했을 때만). LLM 출력은 코드 측 이중 가드
(action Literal 멤버십 + target/attribute 전 토큰 grounding)를 통과한 슬롯만 채택.

결정론 경로 LLM import 0회 — ``LlmClient``는 TYPE_CHECKING 전용 import.
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.agent.intent.connectives import (
    AMBIGUOUS_MARKERS,
    CAUSE_MARKERS,
    PURPOSE_MARKERS,
)
from src.agent.intent.models import ChangeSlot
from src.ontology.changepoint_vocab import (
    attribute_keywords,
    cue_to_action,
    generic_labels_for,
    load_vocab,
    tail_modality,
)
from src.utils.logging import get_logger

if TYPE_CHECKING:  # 결정론 경로 LLM import 0회 — 타입 전용
    from src.agent.llm.client import LlmClient

log = get_logger(__name__)

_MAX_SLOTS = 3

# 비모호 마커 — "(으)로"는 모호 마커(AMBIGUOUS_MARKERS)라 최하위 우선순위.
_NON_AMBIGUOUS_MARKERS: list[str] = [
    m for m in (*PURPOSE_MARKERS, *CAUSE_MARKERS) if m not in AMBIGUOUS_MARKERS
]
# 연결/원인 어절 판별용 (전체-사유 fallback에서 명사구에서 제외할 토큰).
_JUNK_MARKERS: list[str] = [m for m in (*PURPOSE_MARKERS, *CAUSE_MARKERS) if len(m) >= 2]

_PAREN_TAIL_RE = re.compile(r"\s*\([^()]*\)\s*$")
_JOSA_CHARS = "을를이가은는의와과도에로"


def env_flag(name: str, default: bool = False) -> bool:
    """불리언 env 게이트. 미설정 → default, '0/false/no/off/빈값' → False."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# 결정론 1차 (R0~R6) — vocab 사전 구동, LLM 0회
# ---------------------------------------------------------------------------


def _r0_normalize(text: str) -> str:
    """R0 — NFC·공백 압축·말미 구두점/괄호주석 제거."""
    t = unicodedata.normalize("NFC", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    t = _PAREN_TAIL_RE.sub("", t)
    return t.strip(" .,;:!?·-")


def _variants_by_len() -> list[str]:
    """행위 enum + 변이형(cue) — 길이 내림차순(긴 변이형 먼저 매칭)."""
    return sorted(cue_to_action(), key=len, reverse=True)


def _action_at_token_end(token: str) -> tuple[str, str] | None:
    """토큰 말미의 행위어 → (행위어 앞 prefix, canonical action). 없으면 None.

    intra-token 결합("사이즈축소" → 사이즈+축소, "품번변경" → 품번+변경)도 처리.
    """
    for variant in _variants_by_len():
        if token == variant:
            return "", cue_to_action()[variant]
        if token.endswith(variant) and len(token) > len(variant):
            return token[: -len(variant)], cue_to_action()[variant]
    return None


def _ends_with_action(text: str) -> bool:
    toks = text.split()
    return bool(toks) and _action_at_token_end(toks[-1]) is not None


def _strip_tails(t: str) -> str:
    """R3 — 말미 양태(필요/요망/검토…) 반복 절단, 명사형 종결.

    오절단 방지: 공백 경계가 아니면(예: "변경함") 남는 절이 행위어로 끝날 때만 깎는다
    ("포함"/"재검토"는 보존).
    """
    while True:
        for tail in tail_modality():
            if t == tail:
                return ""
            if not t.endswith(tail):
                continue
            rest = t[: -len(tail)].rstrip(" .,·-")
            boundary = t[-len(tail) - 1] == " "
            if boundary or _ends_with_action(rest):
                t = rest
                break
        else:
            return t


def _rightmost_clause(text: str) -> str | None:
    """R2 — 비모호 마커 최우측 발생 기준 우측 절. 없으면 None."""
    best: tuple[int, int] | None = None  # (end, marker_len)
    for m in _NON_AMBIGUOUS_MARKERS:
        i = text.rfind(m)
        if i < 1:  # 절 선두 마커는 원인절이 없음 — 비채택
            continue
        end = i + len(m)
        if best is None or end > best[0] or (end == best[0] and len(m) > best[1]):
            best = (end, len(m))
    if best is None:
        return None
    return text[best[0] :].strip(" ,·-")


def _ambiguous_clause(text: str) -> str | None:
    """"(으)로" 모호 마커 — 토큰 말미(다음이 공백)일 때만, 최우측 발생."""
    best: re.Match[str] | None = None
    for mk in AMBIGUOUS_MARKERS:
        for m in re.finditer(re.escape(mk) + r"(?= )", text):
            if m.start() < 1:
                continue
            if (
                best is None
                or m.end() > best.end()
                or (m.end() == best.end() and len(m.group()) > len(best.group()))
            ):
                best = m
    if best is None:
        return None
    return text[best.end() :].strip(" ,·-")


def _clean_noun(token: str) -> str:
    """말미 조사 1자 제거(보수적 — 3자 이상 토큰만, 남는 길이 2 이상)."""
    t = token.strip(" .,·-")
    if len(t) >= 3 and t[-1] in _JOSA_CHARS and len(t[:-1]) >= 2:
        return t[:-1]
    return t


def _is_junk_token(token: str) -> bool:
    """연결/원인 어절(마커·cue 결합 토큰, 예: "불요하여") — 명사구에서 제외."""
    return any(token.endswith(m) for m in _JUNK_MARKERS)


def _split_target_attribute(
    nouns: list[str], part_name: str | None
) -> tuple[str, str | None]:
    """R5 — 행위어 앞 명사구 → (target, attribute).

    말단의 연속 attribute-ish 토큰(vocab generic_map 키워드 + 종류/타입/방식)을
    attribute로 분리. 명사구 전체가 attribute-ish(또는 공집합)이면 ``part_name``으로
    target을 합성(R5 합성), part_name도 없으면 단독 명사는 target으로 본다.
    """
    attrs = {a.casefold() for a in attribute_keywords()}
    k = len(nouns)
    while k > 0 and nouns[k - 1].casefold() in attrs:
        k -= 1
    head, attr_tokens = nouns[:k], nouns[k:]
    if head:
        return " ".join(head), (" ".join(attr_tokens) or None)
    if attr_tokens:
        if part_name:
            return part_name, " ".join(attr_tokens)  # R5 합성
        if len(attr_tokens) == 1:
            return attr_tokens[0], None  # "재질 변경" → target=재질
        return " ".join(attr_tokens[:-1]), attr_tokens[-1]
    if part_name:
        return part_name, None  # 행위어가 절 선두 — part_name 합성
    return "", None


def _slot_from_clause(clause: str, part_name: str | None) -> ChangeSlot | None:
    """절 1개 → 슬롯 (R4 행위 검증 + R5 대상 검증 + R6 길이 가드). 실패 시 None."""
    tokens = clause.split()
    if not tokens:
        return None
    # R4 — 마지막 행위-bearing 토큰. 없으면 실패.
    act_idx: int | None = None
    act = ""
    prefix = ""
    for i in range(len(tokens) - 1, -1, -1):
        hit = _action_at_token_end(tokens[i])
        if hit is not None:
            act_idx, (prefix, act) = i, hit
            break
    if act_idx is None:
        return None
    # R5 — 행위어 앞 명사구(+ intra-token prefix). 연결 어절·조사 정리.
    raw_nouns = tokens[:act_idx] + ([prefix] if prefix else [])
    nouns = [
        _clean_noun(n) for n in raw_nouns if n and not _is_junk_token(n)
    ]
    nouns = [n for n in nouns if n]
    target, attribute = _split_target_attribute(nouns, part_name)
    if not target:
        return None
    # R6 — 합성문 3~40자, 토큰 2~8개.
    composed = " ".join(filter(None, [target, attribute, act]))
    n_tok = len(composed.split())
    if not (3 <= len(composed) <= 40 and 2 <= n_tok <= 8):
        return None
    return ChangeSlot(target=target, attribute=attribute, action=act)


def _split_compound(text: str) -> list[str]:
    """복합 사유를 절로 분해 — vocab split_tokens 구동.

    단어형("및"/"그리고")은 공백 경계, ","/"+"는 문자 그대로, "/"는 한글-한글 사이만
    분리("S/W", "P/No" 보존).
    """
    tokens = [str(t) for t in load_vocab().get("split_tokens", [])]
    t = text
    word_toks = [w for w in tokens if re.fullmatch(r"[가-힣]+", w)]
    if word_toks:
        t = re.sub(r" (?:" + "|".join(map(re.escape, word_toks)) + r") ", "\x00", t)
    for ch in tokens:
        if ch in (",", "+"):
            t = t.replace(ch, "\x00")
    if "/" in tokens:
        t = re.sub(r"(?<=[가-힣])\s*/\s*(?=[가-힣])", "\x00", t)
    parts = [p.strip() for p in t.split("\x00") if p.strip()]
    return parts or [text]


def _slot_from_one(text: str, part_name: str | None) -> ChangeSlot | None:
    """절 1개에 R1~R6 적용. 마커 절(R2) → 모호 마커 절 → 무마커 직결형(R1) 순."""
    text = _strip_tails(text)
    if not text:
        return None
    clause = _rightmost_clause(text)
    if clause:
        s = _slot_from_clause(_strip_tails(clause), part_name)
        if s is not None:
            return s
    amb = _ambiguous_clause(text)
    if amb:  # 모호 마커는 행위절 검증(R4~R6) 통과 시에만 채택
        s = _slot_from_clause(_strip_tails(amb), part_name)
        if s is not None:
            return s
    # R1 무마커 직결형 / 마커 절 실패 fallback — 전체에서 행위 탐색(연결 어절 제외).
    return _slot_from_clause(text, part_name)


def _derive_deterministic(reason: str, part_name: str | None) -> list[ChangeSlot]:
    text = _r0_normalize(reason)
    slots: list[ChangeSlot] = []
    for clause_text in _split_compound(text):
        s = _slot_from_one(clause_text, part_name)
        if s is not None and s not in slots:
            slots.append(s)
        if len(slots) >= _MAX_SLOTS:
            break
    return slots


# ---------------------------------------------------------------------------
# LLM 2차 (opt-in, 결정론 실패분만) — 코드 측 이중 가드
# ---------------------------------------------------------------------------

_LLM_PROMPT_TEMPLATE = """당신은 BOM 변경점 추출기입니다. 변경사유에서 "무엇을(target),
어떤 속성을(attribute), 어떻게(action)" 바꾸는지 구조화합니다.

규칙:
1. target·attribute는 변경사유 또는 부품명에 이미 있는 단어만
   그대로 사용합니다(철자 교정·번역·새 단어 생성 금지). attribute는 없으면 null.
2. action은 다음 중에서만 고릅니다:
   변경 | 추가 | 삭제 | 적용 | 축소 | 확대 | 통합 | 분리 | 보강 | 교체
3. 행위 판정 단서: {labels_hint}
4. 무엇을 바꾸는지(target)가 사유·부품명 어디에도 없으면 빈 배열.
   (예: "원가 절감", "법규 대응" → [])
5. 복합 변경은 최대 3개까지 배열로.
6. JSON만 출력: {{"changes":[{{"target":"...","attribute":"...","action":"..."}}]}}

변경사유: {change_reason}
부품명(있으면): {part_name}"""


def _labels_hint() -> str:
    """vocab actions/action_cues에서 행위 판정 단서 조립 (행위당 변이형 ≤5)."""
    cues: dict[str, list[str]] = load_vocab().get("action_cues") or {}
    parts = [
        f"{'·'.join(str(c) for c in v[:5])} → {a}" for a, v in cues.items() if v
    ]
    return " / ".join(parts)


def _ground_text(reason: str, part_name: str | None) -> str:
    t = unicodedata.normalize("NFC", f"{reason} {part_name or ''}")
    return re.sub(r"\s+", " ", t).casefold()


def _grounded(value: str, ground: str) -> bool:
    """전 토큰 grounding — 조사 제거·len≥2 토큰이 ground의 부분문자열인지.

    가짜 품번류 영숫자 토큰(사유/부품명에 없는)도 여기서 차단된다.
    """
    for tok in str(value).split():
        tok = _clean_noun(tok)
        if len(tok) < 2:
            continue
        if tok.casefold() not in ground:
            return False
    return True


def _derive_llm(
    reason: str, part_name: str | None, llm: "LlmClient"
) -> list[ChangeSlot]:
    prompt = _LLM_PROMPT_TEMPLATE.format(
        labels_hint=_labels_hint(),
        change_reason=reason,
        part_name=part_name or "없음",
    )
    try:
        raw: dict[str, Any] = llm.complete_json(prompt)
    except Exception as exc:  # noqa: BLE001 — LLM 실패는 결정론 결과(없음)로 폴백
        log.warning("derive.llm_call_failed", error=str(exc)[:160])
        return []
    ground = _ground_text(reason, part_name)
    slots: list[ChangeSlot] = []
    for item in (raw or {}).get("changes", [])[:_MAX_SLOTS]:
        if not isinstance(item, dict):
            continue
        try:
            slot = ChangeSlot.model_validate(item)  # action Literal 멤버십 가드
        except ValidationError:
            continue  # 깨진 슬롯 폐기
        if not _grounded(slot.target, ground):
            continue
        if slot.attribute and not _grounded(slot.attribute, ground):
            continue
        if slot not in slots:
            slots.append(slot)
    return slots


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def derive_change_slots(
    reason: str,
    part_name: str | None = None,
    llm: "LlmClient | None" = None,
) -> tuple[list[ChangeSlot], str] | None:
    """변경사유 → (슬롯들, source). source ∈ {"det","llm"}. 검색 쿼리 보강 전용.

    결정론 1차(R0~R6)가 슬롯을 내면 ``("det")``, 실패분만 ``llm``(주입 시)으로 2차
    시도해 ``("llm")``. 둘 다 실패면 None — 호출측은 기존 reason-only 쿼리 유지.
    """
    reason = (reason or "").strip()
    if not reason:
        return None
    slots = _derive_deterministic(reason, part_name or None)
    if slots:
        return slots, "det"
    if llm is None:
        return None
    slots = _derive_llm(reason, part_name or None, llm)
    if slots:
        return slots, "llm"
    return None


def generic_labels(slots: list[ChangeSlot]) -> list[str]:
    """슬롯들 → vocab generic_map 코퍼스 일반 라벨 (라벨형 정렬 쿼리용)."""
    return generic_labels_for(
        [
            (" ".join(filter(None, [s.target, s.attribute])), s.action)
            for s in slots
        ]
    )


__all__ = ["ChangeSlot", "derive_change_slots", "env_flag", "generic_labels"]
