from __future__ import annotations

"""
확정된 부품명으로 Neo4j 과거 변경 이력을 조회하는 모듈.

각 부품마다:
  - 과거 변경점 / 변경사유 / Base P/No / New P/No / 모델명
  - 같은 케이스(caseId)에서 함께 바뀐 관련 부품 목록
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


# 부품명으로 과거 변경 이력 조회 + 같은 caseId 관련 부품 함께 반환
CYPHER_HISTORY = """
MATCH (cr:ChangeRecord)-[:FROM_LINE]->(bl:BomLine)
WHERE ANY(kw IN $keywords WHERE toLower(bl.partNameRaw) CONTAINS toLower(kw))
  AND cr.changingPoint IS NOT NULL AND cr.changingPoint <> ''
OPTIONAL MATCH (rc:ReviewCase {caseId: cr.caseId})
WITH cr, bl, rc
ORDER BY cr.changeId DESC
LIMIT 5
OPTIONAL MATCH (bl2:BomLine {caseId: cr.caseId})
WHERE bl2.changingPoint IS NOT NULL
  AND bl2.partNameRaw <> bl.partNameRaw
WITH cr, bl, rc,
     collect(DISTINCT bl2.partNameRaw)[..10] AS related_parts
RETURN
  cr.changeId       AS changeId,
  bl.partNameRaw    AS partName,
  bl.lineId         AS lineId,
  bl.documentId     AS documentId,
  bl.level          AS level,
  bl.rawLevel       AS rawLevel,
  bl.partType       AS partType,
  cr.classification AS classification,
  cr.changingPoint  AS changingPoint,
  cr.changingReason AS changingReason,
  cr.basePartNoRaw  AS basePartNoRaw,
  cr.newPartNoRaw   AS newPartNoRaw,
  rc.modelName      AS modelName,
  related_parts     AS relatedParts
ORDER BY cr.changeId DESC
"""


def _extract_keywords(description: str) -> list[str]:
    """부품명에서 매칭 키워드 추출."""
    import re
    stop = {"and", "the", "for", "with", "type", "assy", "part", "sub"}
    tokens = re.split(r"[^A-Za-z0-9가-힣]+", description)
    result = []
    seen: set[str] = set()
    for t in sorted(tokens, key=len, reverse=True):
        t = t.strip()
        if len(t) >= 3 and t.lower() not in stop and t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)
    return result[:5]


def search_history_by_part(description: str) -> list[dict]:
    """
    부품명(description)으로 Neo4j 과거 변경 이력을 조회한다.

    반환: [
      {
        "changeId", "partName", "lineId", "documentId",
        "level", "rawLevel", "partType", "classification",
        "changingPoint", "changingReason",
        "basePartNoRaw", "newPartNoRaw", "modelName",
        "relatedParts": [부품명, ...]
      }, ...
    ]
    """
    if not description:
        return []

    keywords = _extract_keywords(description)
    if not keywords:
        return []

    try:
        with _get_driver().session() as s:
            rows = [dict(r) for r in s.run(CYPHER_HISTORY, keywords=keywords)]
        return rows
    except Exception as e:
        print(f"[WARNING] 이력 조회 실패 ({description}): {e}")
        return []