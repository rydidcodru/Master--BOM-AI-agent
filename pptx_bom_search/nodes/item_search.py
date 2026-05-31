from __future__ import annotations

import os

from neo4j import GraphDatabase

MAX_RETRY = 3
MIN_RESULTS = 5  # LLM 판단에 넘길 최소 후보 수

CYPHER = """
MATCH (cr:ChangeRecord)-[:FROM_LINE]->(bl:BomLine)
WHERE cr.changingPoint IS NOT NULL AND cr.changingPoint <> ''
  AND ANY(kw IN $part_kw WHERE toLower(bl.partNameRaw) CONTAINS toLower(kw))
OPTIONAL MATCH (rc:ReviewCase {caseId: cr.caseId})
RETURN DISTINCT
  bl.partNameRaw    AS partName,
  bl.level          AS level,
  bl.rawLevel       AS rawLevel,
  bl.partType       AS partType,
  bl.lineId         AS lineId,
  bl.documentId     AS documentId,
  cr.changeId       AS changeId,
  cr.classification AS classification,
  cr.changingPoint  AS changingPoint,
  cr.changingReason AS changingReason,
  cr.basePartNoRaw  AS basePartNoRaw,
  cr.newPartNoRaw   AS newPartNoRaw,
  rc.modelName      AS modelName,
  rc.summary        AS caseSummary
ORDER BY cr.changeId
LIMIT 50
"""

# part_kw 없이 change 키워드만으로 폴백
CYPHER_FALLBACK = """
MATCH (cr:ChangeRecord)-[:FROM_LINE]->(bl:BomLine)
WHERE cr.changingPoint IS NOT NULL AND cr.changingPoint <> ''
  AND ANY(kw IN $chg_kw WHERE toLower(cr.changingPoint) CONTAINS toLower(kw)
                           OR toLower(cr.changingReason) CONTAINS toLower(kw))
OPTIONAL MATCH (rc:ReviewCase {caseId: cr.caseId})
RETURN DISTINCT
  bl.partNameRaw    AS partName,
  bl.level          AS level,
  bl.rawLevel       AS rawLevel,
  bl.partType       AS partType,
  bl.lineId         AS lineId,
  bl.documentId     AS documentId,
  cr.changeId       AS changeId,
  cr.classification AS classification,
  cr.changingPoint  AS changingPoint,
  cr.changingReason AS changingReason,
  cr.basePartNoRaw  AS basePartNoRaw,
  cr.newPartNoRaw   AS newPartNoRaw,
  rc.modelName      AS modelName,
  rc.summary        AS caseSummary
ORDER BY cr.changeId
LIMIT 50
"""

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "0000")
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def _run_query(cypher: str, **params) -> list[dict]:
    with _get_driver().session() as session:
        return [dict(r) for r in session.run(cypher, **params)]


def _extract_part_keywords(item: dict) -> list[str]:
    """부품명에서 짧은 매칭 토큰 추출 (2글자 이상, 일반 단어 제외)"""
    import re
    stop = {"the", "and", "for", "with", "from", "into", "type", "size",
            "assy", "part", "sub", "base", "new", "old"}
    canonical = item.get("canonical_part", "") or item.get("part", "")
    raw_part = item.get("part", "")
    blob = f"{canonical} {raw_part}"
    tokens = re.split(r"[^A-Za-z0-9가-힣]+", blob)
    result = []
    for t in tokens:
        t = t.strip()
        if len(t) >= 2 and t.lower() not in stop:
            result.append(t)
    # 중복 제거, 길이 순 정렬 (긴 것 우선 — 더 구체적)
    seen = set()
    ordered = []
    for t in sorted(result, key=len, reverse=True):
        if t.lower() not in seen:
            seen.add(t.lower())
            ordered.append(t)
    return ordered[:8]


def _extract_change_keywords(item: dict) -> list[str]:
    """변경내역+변경사유에서 핵심 키워드 추출"""
    import re
    stop = {"및", "으로", "에서", "위해", "인한", "따른", "the", "and", "for",
            "from", "into", "with", "type", "적용", "변경", "신규", "추가"}
    blob = f"{item.get('change_detail', '')} {item.get('change_reason', '')}"
    tokens = re.split(r"[^A-Za-z0-9가-힣]+", blob)
    result = []
    for t in tokens:
        t = t.strip()
        if len(t) >= 2 and t.lower() not in stop:
            result.append(t)
    seen = set()
    ordered = []
    for t in sorted(result, key=len, reverse=True):
        if t.lower() not in seen:
            seen.add(t.lower())
            ordered.append(t)
    return ordered[:8]


def _relax(keywords: list[str], retry: int) -> list[str]:
    """retry마다 키워드를 절반으로 줄임"""
    keep = max(2, len(keywords) - retry * 2)
    return keywords[:keep]


def item_search_node(state: dict) -> dict:
    """
    fan_out이 Send()로 보내는 개별 항목 처리 노드.
    state = {"item": ChangePoint, "retry_count": int}
    """
    item = state["item"]
    part_kw = _extract_part_keywords(item)
    chg_kw = _extract_change_keywords(item)

    candidates: list[dict] = []
    final_retry = 0

    for attempt in range(MAX_RETRY + 1):
        p_kw = _relax(part_kw, attempt)
        try:
            if p_kw:
                candidates = _run_query(CYPHER, part_kw=p_kw)
            # 결과 부족하면 change 키워드 폴백 병합
            if len(candidates) < MIN_RESULTS and chg_kw:
                c_kw = _relax(chg_kw, attempt)
                extras = _run_query(CYPHER_FALLBACK, chg_kw=c_kw)
                seen = {r["changeId"] for r in candidates}
                candidates += [r for r in extras if r["changeId"] not in seen]
        except Exception as e:
            print(f"  [WARNING] Neo4j 오류 ({item.get('part')}): {e}")
            candidates = []

        final_retry = attempt
        if len(candidates) >= MIN_RESULTS:
            break
        if attempt < MAX_RETRY:
            print(f"  [{item.get('part')}] 후보 {len(candidates)}건 → 쿼리 완화 (시도 {attempt + 1})")

    print(f"  [{item.get('part')}] Neo4j 후보풀 {len(candidates)}건 (시도 {final_retry + 1}회)")

    return {
        "search_results": [{
            "change_point": item,
            "candidates": candidates,      # LLM 판단 전 원본 후보풀
            "ranked": [],                  # rank_candidates 노드에서 채워짐
            "retry_count": final_retry,
        }]
    }
