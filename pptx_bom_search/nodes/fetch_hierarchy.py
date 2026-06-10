from __future__ import annotations

"""
선택된 BomLine(후보)에서 같은 문서(documentId) 안의
상위 Assembly + 자신 + 하위 부품을 계층 순서로 반환.
"""

import os
from neo4j import GraphDatabase

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        uri  = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER",     "neo4j")
        pw   = os.getenv("NEO4J_PASSWORD", "0000")
        _driver = GraphDatabase.driver(uri, auth=(user, pw))
    return _driver


# 같은 documentId 안에서 선택 BomLine의 상위 Assembly 체인 조회
CYPHER_ANCESTORS = """
MATCH path = (root:BomLine)-[:HAS_CHILD*1..10]->(target:BomLine {lineId: $line_id})
WHERE root.documentId = $doc_id
  AND NOT ()-[:HAS_CHILD]->(root)
WITH nodes(path) AS chain
UNWIND chain AS n
WITH DISTINCT n
WHERE n.lineId <> $line_id
RETURN n.lineId         AS lineId,
       n.documentId     AS documentId,
       n.level          AS level,
       n.rawLevel       AS rawLevel,
       n.partNameRaw    AS partName,
       n.partType       AS partType,
       n.basePartNoRaw  AS basePartNoRaw,
       n.newPartNoRaw   AS newPartNoRaw,
       n.changingPoint  AS changingPoint,
       n.changingReason AS changingReason
ORDER BY n.level
"""

# 선택 BomLine 자신 + 하위 재귀 조회 (같은 documentId)
CYPHER_SELF_AND_DESCENDANTS = """
MATCH path = (target:BomLine {lineId: $line_id})-[:HAS_CHILD*0..10]->(child:BomLine)
WHERE child.documentId = $doc_id
WITH DISTINCT child
RETURN child.lineId         AS lineId,
       child.documentId     AS documentId,
       child.level          AS level,
       child.rawLevel       AS rawLevel,
       child.partNameRaw    AS partName,
       child.partType       AS partType,
       child.basePartNoRaw  AS basePartNoRaw,
       child.newPartNoRaw   AS newPartNoRaw,
       child.changingPoint  AS changingPoint,
       child.changingReason AS changingReason
ORDER BY child.level, child.lineId
"""


def _run(cypher: str, **params) -> list[dict]:
    with _get_driver().session() as session:
        return [dict(r) for r in session.run(cypher, **params)]


def _is_valid(row: dict) -> bool:
    """changingPoint 또는 newPartNoRaw가 있는 실제 변경 행만 포함."""
    return bool(row.get("changingPoint") or row.get("newPartNoRaw"))


def fetch_hierarchy(line_id: str, doc_id: str) -> list[dict]:
    """
    선택된 BomLine을 기준으로 계층 행을 반환한다.

    반환 순서: 상위 Assembly(level 오름차순) → 자신 → 하위(level 오름차순)
    각 행: {lineId, documentId, level, rawLevel, partName, partType,
             basePartNoRaw, newPartNoRaw, changingPoint, changingReason}
    """
    if not line_id or not doc_id:
        return []

    try:
        ancestors   = _run(CYPHER_ANCESTORS,           line_id=line_id, doc_id=doc_id)
        self_and_desc = _run(CYPHER_SELF_AND_DESCENDANTS, line_id=line_id, doc_id=doc_id)
    except Exception as e:
        print(f"[WARNING] 계층 조회 실패 ({line_id}): {e}")
        return []

    # 유효한 변경 행만 필터 + 중복 제거 (lineId 기준)
    seen: set[str] = set()
    result: list[dict] = []
    for row in ancestors + self_and_desc:
        lid = row.get("lineId", "")
        if lid in seen:
            continue
        seen.add(lid)
        if _is_valid(row):
            result.append(row)

    # level 오름차순 정렬 (상위→하위)
    result.sort(key=lambda r: (r.get("level") or 0, r.get("lineId", "")))
    return result
