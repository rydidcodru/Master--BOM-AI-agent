// Neo4j BOM CSV load script.
// Put the CSV files in Neo4j's import directory or adjust the base URL.
// Generated load order: canonical_fields.csv, review_cases.csv, source_documents.csv, document_case_edges.csv, source_columns.csv, bom_lines.csv, bom_line_edges.csv, parts.csv, raw_parts.csv, raw_part_resolutions.csv, bom_line_part_resolutions.csv, change_records.csv, change_record_edges.csv, impact_edges.csv, replacement_edges.csv

// 1. CanonicalField
LOAD CSV WITH HEADERS FROM 'file:/canonical_fields.csv' AS row
MERGE (f:CanonicalField {key: row.key})
SET f.displayName = row.displayName,
    f.description = row.description,
    f.dataType = row.dataType;

// 2. ReviewCase
LOAD CSV WITH HEADERS FROM 'file:/review_cases.csv' AS row
MERGE (c:ReviewCase {caseId: row.caseId})
SET c.modelName = row.modelName,
    c.baseModel = row.baseModel,
    c.newModel = row.newModel,
    c.event = row.event,
    c.status = row.status,
    c.summary = row.summary,
    c.searchText = row.searchText,
    c.createdAt = row.createdAt;

// 3. SourceDocument
LOAD CSV WITH HEADERS FROM 'file:/source_documents.csv' AS row
MERGE (d:SourceDocument {documentId: row.documentId})
SET d.fileName = row.fileName,
    d.filePath = row.filePath,
    d.sheetName = row.sheetName,
    d.formatType = row.formatType,
    d.importedAt = row.importedAt,
    d.parserVersion = row.parserVersion,
    d.rawMetadata = row.rawMetadata;

// 4. SourceDocument -> ReviewCase
LOAD CSV WITH HEADERS FROM 'file:/document_case_edges.csv' AS row
MATCH (d:SourceDocument {documentId: row.documentId})
MATCH (c:ReviewCase {caseId: row.caseId})
MERGE (d)-[:SUPPORTS_CASE]->(c);

// 5. SourceColumn and SourceColumn -> CanonicalField
LOAD CSV WITH HEADERS FROM 'file:/source_columns.csv' AS row
MATCH (d:SourceDocument {documentId: row.documentId})
MERGE (sc:SourceColumn {sourceColumnId: row.sourceColumnId})
SET sc.documentId = row.documentId,
    sc.documentFormat = row.documentFormat,
    sc.sheetName = row.sheetName,
    sc.rawColumnName = row.rawColumnName,
    sc.columnIndex = toInteger(row.columnIndex)
MERGE (d)-[:HAS_SOURCE_COLUMN]->(sc)
WITH sc, row
WHERE row.canonicalFieldKey IS NOT NULL AND row.canonicalFieldKey <> ''
MATCH (f:CanonicalField {key: row.canonicalFieldKey})
MERGE (sc)-[r:MAPS_TO]->(f)
SET r.confidence = row.confidence,
    r.method = row.method,
    r.parserVersion = row.parserVersion;

// 6. BomLine
LOAD CSV WITH HEADERS FROM 'file:/bom_lines.csv' AS row
MATCH (d:SourceDocument {documentId: row.documentId})
MATCH (c:ReviewCase {caseId: row.caseId})
MERGE (line:BomLine {lineId: row.lineId})
SET line.documentId = row.documentId,
    line.caseId = row.caseId,
    line.rowNo = row.rowNo,
    line.level = toInteger(row.level),
    line.rawLevel = row.rawLevel,
    line.partType = row.partType,
    line.basePartNoRaw = row.basePartNoRaw,
    line.newPartNoRaw = row.newPartNoRaw,
    line.effectivePartNo = row.effectivePartNo,
    line.partNameRaw = row.partNameRaw,
    line.qtyRaw = row.qtyRaw,
    line.effectiveQty = row.effectiveQty,
    line.supplierRaw = row.supplierRaw,
    line.classification = row.classification,
    line.changingPoint = row.changingPoint,
    line.changingReason = row.changingReason,
    line.rawJson = row.rawJson,
    line.searchText = row.searchText
MERGE (d)-[:CONTAINS_LINE]->(line)
MERGE (c)-[:CONTAINS_LINE]->(line);

// 7. BomLine tree edges
LOAD CSV WITH HEADERS FROM 'file:/bom_line_edges.csv' AS row
MATCH (p:BomLine {lineId: row.parentLineId})
MATCH (ch:BomLine {lineId: row.childLineId})
MERGE (p)-[r:HAS_CHILD]->(ch)
SET r.parentLineId = row.parentLineId,
    r.childLineId = row.childLineId;

// 8. Part
LOAD CSV WITH HEADERS FROM 'file:/parts.csv' AS row
MERGE (p:Part {partId: row.partId})
SET p.canonicalPartNo = row.canonicalPartNo,
    p.canonicalName = row.canonicalName,
    p.partType = row.partType,
    p.status = row.status,
    p.searchText = row.searchText;

// 9. RawPart and BomLine -> RawPart
LOAD CSV WITH HEADERS FROM 'file:/raw_parts.csv' AS row
MATCH (line:BomLine {lineId: row.sourceLineId})
MERGE (rp:RawPart {rawPartId: row.rawPartId})
SET rp.rawPartNo = row.rawPartNo,
    rp.rawPartName = row.rawPartName,
    rp.rawPartType = row.rawPartType,
    rp.sourceRole = row.sourceRole,
    rp.sourceDocumentId = row.sourceDocumentId,
    rp.sourceLineId = row.sourceLineId,
    rp.sourceColumnKey = row.sourceColumnKey
MERGE (line)-[r:REPRESENTS_RAW {role: row.sourceRole}]->(rp);

// 10. RawPart -> Part
LOAD CSV WITH HEADERS FROM 'file:/raw_part_resolutions.csv' AS row
MATCH (rp:RawPart {rawPartId: row.rawPartId})
MATCH (p:Part {partId: row.partId})
MERGE (rp)-[r:RESOLVES_TO]->(p)
SET r.confidence = row.confidence,
    r.method = row.method,
    r.reviewedBy = row.reviewedBy,
    r.reviewedAt = row.reviewedAt;

// 11. BomLine -> Part
LOAD CSV WITH HEADERS FROM 'file:/bom_line_part_resolutions.csv' AS row
MATCH (line:BomLine {lineId: row.lineId})
MATCH (p:Part {partId: row.partId})
MERGE (line)-[r:RESOLVES_TO {role: row.role}]->(p)
SET r.method = row.method;

// 12. ChangeRecord
LOAD CSV WITH HEADERS FROM 'file:/change_records.csv' AS row
MATCH (c:ReviewCase {caseId: row.caseId})
MATCH (line:BomLine {lineId: row.lineId})
MERGE (cr:ChangeRecord {changeId: row.changeId})
SET cr.caseId = row.caseId,
    cr.lineId = row.lineId,
    cr.classification = row.classification,
    cr.basePartNoRaw = row.basePartNoRaw,
    cr.newPartNoRaw = row.newPartNoRaw,
    cr.effectivePartNo = row.effectivePartNo,
    cr.changingPoint = row.changingPoint,
    cr.changingReason = row.changingReason,
    cr.searchText = row.searchText
MERGE (c)-[:HAS_CHANGE_RECORD]->(cr)
MERGE (cr)-[:FROM_LINE]->(line)
MERGE (cr)-[:DIRECTLY_CHANGES]->(line);

// 13. ChangeRecord -> Part relationships
LOAD CSV WITH HEADERS FROM 'file:/change_record_edges.csv' AS row
MATCH (cr:ChangeRecord {changeId: row.changeId})
OPTIONAL MATCH (direct:Part {partId: row.directPartId})
OPTIONAL MATCH (base:Part {partId: row.basePartId})
OPTIONAL MATCH (new:Part {partId: row.newPartId})
FOREACH (_ IN CASE WHEN direct IS NULL THEN [] ELSE [1] END |
  MERGE (cr)-[r:DIRECTLY_CHANGES_PART {role: row.directPartRole}]->(direct)
)
FOREACH (_ IN CASE WHEN base IS NULL THEN [] ELSE [1] END |
  MERGE (cr)-[:BASE_PART]->(base)
)
FOREACH (_ IN CASE WHEN new IS NULL THEN [] ELSE [1] END |
  MERGE (cr)-[:NEW_PART]->(new)
);

// 14. ChangeRecord -> impacted BomLine
LOAD CSV WITH HEADERS FROM 'file:/impact_edges.csv' AS row
MATCH (cr:ChangeRecord {changeId: row.changeId})
MATCH (line:BomLine {lineId: row.impactedLineId})
MERGE (cr)-[r:IMPACTS_LINE {distance: toInteger(row.distance), impactType: row.impactType}]->(line);

// 15. Replacement edges
LOAD CSV WITH HEADERS FROM 'file:/replacement_edges.csv' AS row
MATCH (from:Part {partId: row.fromPartId})
MATCH (to:Part {partId: row.toPartId})
MERGE (from)-[r:REPLACED_BY {caseId: row.caseId, lineId: row.lineId}]->(to)
SET r.documentId = row.documentId,
    r.classification = row.classification,
    r.changingPoint = row.changingPoint,
    r.changingReason = row.changingReason;
