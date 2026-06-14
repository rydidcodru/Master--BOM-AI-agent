"""변경점 어휘 v3 로더 — ``config/changepoint_vocab.yaml``.

행위(action)는 닫힌 enum, 대상/속성은 열린 슬롯(grounding 검증)이라는 어휘 계약을
코드에 노출한다. 사용처:

- L1 변경점 유도(``agent/intent/derive.py``) — actions/action_cues/tail_modality/마커 분리.
- 부품명 canon(``preprocess/normalize.canonicalize_part_name``) — token_canon 공유.
- ②.5 매핑(``agent/mapping/mapper.py``) — "추가계" 행위 판정.

순수 결정론(파일 로드 + dict 조회) — LLM/DB import 0회.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import yaml

from src.utils.paths import CONFIG_DIR

CHANGEPOINT_VOCAB_PATH = CONFIG_DIR / "changepoint_vocab.yaml"


@lru_cache(maxsize=1)
def load_vocab() -> dict[str, Any]:
    """YAML 전체 dict (lru_cache). 키: split_tokens/actions/action_cues/tail_modality/
    label_aliases/token_canon/generic_map/attribute_map."""
    return cast(
        dict[str, Any],
        yaml.safe_load(CHANGEPOINT_VOCAB_PATH.read_text(encoding="utf-8")),
    )


@lru_cache(maxsize=1)
def actions() -> tuple[str, ...]:
    """닫힌 행위 enum (YAML actions 순서 보존)."""
    return tuple(load_vocab().get("actions", []))


@lru_cache(maxsize=1)
def cue_to_action() -> dict[str, str]:
    """변이형(cue) → canonical 행위 역인덱스. canonical 자신도 포함."""
    out: dict[str, str] = {a: a for a in actions()}
    for action, cues in (load_vocab().get("action_cues") or {}).items():
        for cue in cues or []:
            out[str(cue)] = str(action)
    return out


@lru_cache(maxsize=1)
def tail_modality() -> tuple[str, ...]:
    """말미 양태 표현("필요"/"요망" 등) — 길이 내림차순(긴 것 먼저 깎기)."""
    tails = [str(t) for t in load_vocab().get("tail_modality", [])]
    return tuple(sorted(tails, key=len, reverse=True))


@lru_cache(maxsize=1)
def split_tokens() -> tuple[str, ...]:
    return tuple(str(t) for t in load_vocab().get("split_tokens", []))


def generic_map() -> list[dict[str, Any]]:
    return list(load_vocab().get("generic_map") or [])


@lru_cache(maxsize=1)
def attribute_keywords() -> frozenset[str]:
    """generic_map 키워드 중 단일 토큰 — R5의 '말단 명사=attribute' 분리 판정용.

    "법랑 종류"처럼 '<속성> 종류/타입/방식' 꼴도 attribute로 묶기 위해 일반 접미 명사를
    포함한다. (다중 토큰 키워드 "하위 부품" 등은 토큰 매칭 대상이 아니므로 제외.)
    """
    kws: set[str] = {"종류", "타입", "방식"}
    for entry in generic_map():
        for kw in entry.get("keywords") or []:
            kw = str(kw)
            if " " not in kw:
                kws.add(kw)
    return frozenset(kws)


@lru_cache(maxsize=1)
def token_canon() -> dict[str, str]:
    """변이/오타 토큰 → canonical 토큰 (casefold 키). 부품명 canon과 derive가 공유."""
    out: dict[str, str] = {}
    for canon, variants in (load_vocab().get("token_canon") or {}).items():
        for v in variants or []:
            out[str(v).casefold()] = str(canon)
    return out


def attribute_map() -> dict[str, str]:
    """라벨 → change_attribute (J4-a 룰 게이팅용 역방향)."""
    return dict(load_vocab().get("attribute_map") or {})


def generic_labels_for(slot_texts: list[tuple[str, str]]) -> list[str]:
    """슬롯 [(결합텍스트, action)] → 코퍼스 일반 라벨 목록 (순서 보존, 중복 제거).

    generic_map을 순서대로 스캔해 키워드가 결합텍스트(target+attribute)에 나타나고
    when_action 조건(있으면)이 맞는 첫 항목의 label을 채택한다.
    """
    labels: list[str] = []
    for text, action in slot_texts:
        low = text.casefold()
        for entry in generic_map():
            when = entry.get("when_action")
            if when and action not in [str(a) for a in when]:
                continue
            if any(str(kw).casefold() in low for kw in entry.get("keywords") or []):
                label = str(entry.get("label", ""))
                if label and label not in labels:
                    labels.append(label)
                break
    return labels


__all__ = [
    "CHANGEPOINT_VOCAB_PATH",
    "actions",
    "attribute_keywords",
    "attribute_map",
    "cue_to_action",
    "generic_labels_for",
    "generic_map",
    "load_vocab",
    "split_tokens",
    "tail_modality",
    "token_canon",
]
