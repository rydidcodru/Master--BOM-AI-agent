import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_MAPPING_PATH = Path("docs/BOM_Code_Mapping_Dictionary.json")
WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 매핑된 description에서 부품 종류를 구분하지 못하는 일반 단어.
# 예: "Door Assembly" -> 핵심 토큰은 "door"이고 "assembly"는 게이트 정밀화에 무의미하다.
GENERIC_DESCRIPTION_TOKENS = {
    "assembly",
    "ass",
    "assy",
    "full",
    "sub",
    "upper",
    "lower",
    "part",
    "parts",
    "comp",
    "kit",
    "set",
    "module",
    "unit",
}


def description_tokens(value: Any) -> list[str]:
    """description에서 부품 종류를 가르는 의미 토큰만 추출한다.

    일반 단어(assembly 등)는 다른 의미 토큰이 있을 때만 제거한다.
    혼합 prefix 버킷 안에서 part_name과 비교해 다른 부품을 떨어뜨리는 데 쓴다.
    """
    all_tokens = [token.lower() for token in WORD_RE.findall(clean_text(value)) if len(token) >= 2]
    meaningful = [token for token in all_tokens if token not in GENERIC_DESCRIPTION_TOKENS]
    return meaningful or all_tokens


@dataclass(frozen=True)
class PrefixMapping:
    prefix: str
    description: str
    confidence: str
    share: float
    examples: tuple[str, ...]


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_text(value: Any) -> str:
    return " ".join(WORD_RE.findall(clean_text(value).lower()))


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if left_tokens and right_tokens and (left_tokens <= right_tokens or right_tokens <= left_tokens):
        return 1.0
    token_overlap = len(left_tokens & right_tokens) / max(len(right_tokens), 1)
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    if len(left_norm) <= 2 or len(right_norm) <= 2:
        return token_overlap
    return max(token_overlap, sequence_score)


@lru_cache(maxsize=4)
def load_prefix_mappings(path: str | Path = DEFAULT_MAPPING_PATH) -> tuple[PrefixMapping, ...]:
    mapping_path = Path(path)
    if not mapping_path.exists():
        return ()
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    mappings = []
    for item in payload.get("prefix_dictionary", []):
        prefix = clean_text(item.get("prefix")).upper()
        description = clean_text(item.get("dominant_description_major"))
        if not prefix or not description:
            continue
        mappings.append(
            PrefixMapping(
                prefix=prefix,
                description=description,
                confidence=clean_text(item.get("confidence")) or "UNKNOWN",
                share=float(item.get("dominant_description_major_share") or 0.0),
                examples=tuple(clean_text(example) for example in item.get("examples", []) if clean_text(example)),
            )
        )
    return tuple(mappings)


@lru_cache(maxsize=4)
def mapping_catalog(path: str | Path = DEFAULT_MAPPING_PATH) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in load_prefix_mappings(path):
        key = item.description
        existing = grouped.setdefault(
            key,
            {
                "description": item.description,
                "prefixes": [],
                "confidence": item.confidence,
                "max_share": item.share,
                "examples": [],
            },
        )
        existing["prefixes"].append(item.prefix)
        existing["max_share"] = max(float(existing["max_share"]), item.share)
        if item.confidence.upper().startswith("HIGH"):
            existing["confidence"] = item.confidence
        existing["examples"].extend(example for example in item.examples if example not in existing["examples"])

    return tuple(
        sorted(
            grouped.values(),
            key=lambda row: (0 if str(row["confidence"]).upper().startswith("HIGH") else 1, row["description"].lower()),
        )
    )


def mapping_catalog_lines(*, limit: int | None = None) -> list[str]:
    rows = mapping_catalog()
    if limit is not None:
        rows = rows[:limit]
    return [
        f"- {row['description']} | prefixes: {', '.join(row['prefixes'])} | confidence: {row['confidence']}"
        for row in rows
    ]


def best_prefix_matches(change: dict[str, Any], *, threshold: float = 0.78, limit: int = 3) -> list[dict[str, Any]]:
    query = " ".join(
        clean_text(change.get(field))
        for field in ("description", "module_name", "part_name")
        if clean_text(change.get(field))
    )
    if not query:
        return []

    scored: list[tuple[float, PrefixMapping]] = []
    for item in load_prefix_mappings():
        score = text_similarity(query, item.description)
        if score >= threshold:
            if item.confidence.upper().startswith("HIGH"):
                score += 0.08
            elif item.confidence.upper().startswith("MEDIUM"):
                score += 0.04
            score += min(item.share, 1.0) * 0.04
            scored.append((score, item))
    scored.sort(key=lambda entry: entry[0], reverse=True)

    matches = []
    for score, item in scored[:limit]:
        matches.append(
            {
                "prefix": item.prefix,
                "description": item.description,
                "confidence": item.confidence,
                "share": round(item.share, 4),
                "match_score": round(min(score, 1.0), 4),
                "examples": list(item.examples[:5]),
            }
        )
    return matches


def enrich_change_with_mapping(change: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(change)
    matches = best_prefix_matches(enriched)
    if not matches:
        return enriched

    best = matches[0]
    best_description = normalize_text(best["description"])
    gate_prefixes = [
        item["prefix"]
        for item in matches
        if normalize_text(item["description"]) == best_description
    ] or [best["prefix"]]
    mapping = {
        "matched_prefixes": matches,
        "primary_prefix": best["prefix"],
        "primary_description": best["description"],
        "gate_prefixes": gate_prefixes,
        "confidence": best["confidence"],
        "share": best.get("share", 0.0),
        # 혼합 prefix 버킷에서 다른 부품을 떨어뜨리기 위한 핵심 토큰.
        # 예: "Door Assembly" -> ["door"], "Coil" -> ["coil"]
        "primary_description_tokens": description_tokens(best["description"]),
    }
    enriched["mapping"] = mapping
    enriched["mapped_prefixes"] = gate_prefixes

    if not clean_text(enriched.get("module_name")):
        enriched["module_name"] = best["description"]
    if not clean_text(enriched.get("part_name")):
        enriched["part_name"] = best["description"]
    return enriched


def enrich_changes_with_mapping(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_change_with_mapping(change) for change in changes]


@lru_cache(maxsize=4)
def known_prefixes(path: str | Path = DEFAULT_MAPPING_PATH) -> frozenset[str]:
    """사전에 등록된 모든 BOM prefix 집합."""
    return frozenset(item.prefix for item in load_prefix_mappings(path))


def looks_like_part_no(value: Any) -> bool:
    """실제 BOM part_no처럼 보이는지 판정한다.

    LLM이 모델/등급 코드(예: BO24, LTIS7338XE)를 base_part_no로 잘못 넣는 것을
    막기 위해, 사전 prefix로 시작하고 숫자를 포함하는 8자 이상만 허용한다.
    """
    normalized = re.sub(r"[\s\-_/.]+", "", clean_text(value).upper())
    if len(normalized) < 8 or not any(ch.isdigit() for ch in normalized):
        return False
    prefixes = known_prefixes()
    if not prefixes:
        # 사전이 없으면 길이/숫자 휴리스틱만으로 통과.
        return True
    return any(normalized.startswith(prefix) for prefix in prefixes)
