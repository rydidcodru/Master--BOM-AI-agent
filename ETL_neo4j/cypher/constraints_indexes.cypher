// BOM 지식 그래프 MVP용 Neo4j 제약조건과 인덱스.
// 문법 기준: Neo4j 5.x.

// -----------------------------
// 고유성 제약조건
// -----------------------------

CREATE CONSTRAINT review_case_id_unique IF NOT EXISTS
FOR (n:ReviewCase)
REQUIRE n.caseId IS UNIQUE;

CREATE CONSTRAINT source_document_id_unique IF NOT EXISTS
FOR (n:SourceDocument)
REQUIRE n.documentId IS UNIQUE;

CREATE CONSTRAINT bom_line_id_unique IF NOT EXISTS
FOR (n:BomLine)
REQUIRE n.lineId IS UNIQUE;

CREATE CONSTRAINT change_record_id_unique IF NOT EXISTS
FOR (n:ChangeRecord)
REQUIRE n.changeId IS UNIQUE;

CREATE CONSTRAINT raw_part_id_unique IF NOT EXISTS
FOR (n:RawPart)
REQUIRE n.rawPartId IS UNIQUE;

CREATE CONSTRAINT part_id_unique IF NOT EXISTS
FOR (n:Part)
REQUIRE n.partId IS UNIQUE;

CREATE CONSTRAINT source_column_id_unique IF NOT EXISTS
FOR (n:SourceColumn)
REQUIRE n.sourceColumnId IS UNIQUE;

CREATE CONSTRAINT canonical_field_key_unique IF NOT EXISTS
FOR (n:CanonicalField)
REQUIRE n.key IS UNIQUE;

// -----------------------------
// 필수 속성 제약조건
// 속성 존재 제약은 Neo4j Enterprise에서 지원된다.
// Community Edition에서는 아래 블록을 주석 처리하고 고유성 제약조건만 유지한다.
// (Enterprise로 전환하면 /* */ 를 제거해서 활성화)
// -----------------------------

/*
CREATE CONSTRAINT review_case_id_exists IF NOT EXISTS
FOR (n:ReviewCase)
REQUIRE n.caseId IS NOT NULL;

CREATE CONSTRAINT source_document_id_exists IF NOT EXISTS
FOR (n:SourceDocument)
REQUIRE n.documentId IS NOT NULL;

CREATE CONSTRAINT bom_line_id_exists IF NOT EXISTS
FOR (n:BomLine)
REQUIRE n.lineId IS NOT NULL;

CREATE CONSTRAINT change_record_id_exists IF NOT EXISTS
FOR (n:ChangeRecord)
REQUIRE n.changeId IS NOT NULL;

CREATE CONSTRAINT raw_part_id_exists IF NOT EXISTS
FOR (n:RawPart)
REQUIRE n.rawPartId IS NOT NULL;

CREATE CONSTRAINT part_id_exists IF NOT EXISTS
FOR (n:Part)
REQUIRE n.partId IS NOT NULL;

CREATE CONSTRAINT source_column_id_exists IF NOT EXISTS
FOR (n:SourceColumn)
REQUIRE n.sourceColumnId IS NOT NULL;

CREATE CONSTRAINT canonical_field_key_exists IF NOT EXISTS
FOR (n:CanonicalField)
REQUIRE n.key IS NOT NULL;
*/

// -----------------------------
// 조회용 인덱스
// -----------------------------

CREATE INDEX review_case_model_idx IF NOT EXISTS
FOR (n:ReviewCase)
ON (n.modelName);

CREATE INDEX review_case_event_idx IF NOT EXISTS
FOR (n:ReviewCase)
ON (n.event);

CREATE INDEX source_document_file_idx IF NOT EXISTS
FOR (n:SourceDocument)
ON (n.fileName);

CREATE INDEX bom_line_case_row_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.caseId, n.rowNo);

CREATE INDEX bom_line_document_row_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.documentId, n.rowNo);

CREATE INDEX bom_line_level_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.level);

CREATE INDEX bom_line_classification_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.classification);

CREATE INDEX bom_line_effective_part_no_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.effectivePartNo);

CREATE INDEX bom_line_base_part_no_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.basePartNoRaw);

CREATE INDEX bom_line_new_part_no_idx IF NOT EXISTS
FOR (n:BomLine)
ON (n.newPartNoRaw);

CREATE INDEX change_record_case_idx IF NOT EXISTS
FOR (n:ChangeRecord)
ON (n.caseId);

CREATE INDEX change_record_classification_idx IF NOT EXISTS
FOR (n:ChangeRecord)
ON (n.classification);

CREATE INDEX change_record_effective_part_no_idx IF NOT EXISTS
FOR (n:ChangeRecord)
ON (n.effectivePartNo);

CREATE INDEX raw_part_no_idx IF NOT EXISTS
FOR (n:RawPart)
ON (n.rawPartNo);

CREATE INDEX raw_part_source_line_idx IF NOT EXISTS
FOR (n:RawPart)
ON (n.sourceLineId);

CREATE INDEX part_no_idx IF NOT EXISTS
FOR (n:Part)
ON (n.canonicalPartNo);

CREATE INDEX part_name_idx IF NOT EXISTS
FOR (n:Part)
ON (n.canonicalName);

CREATE INDEX source_column_raw_name_idx IF NOT EXISTS
FOR (n:SourceColumn)
ON (n.rawColumnName);

// -----------------------------
// 검색/RAG용 full-text 인덱스
// -----------------------------

CREATE FULLTEXT INDEX review_case_search_idx IF NOT EXISTS
FOR (n:ReviewCase)
ON EACH [n.searchText, n.summary, n.modelName, n.baseModel, n.newModel];

CREATE FULLTEXT INDEX bom_line_search_idx IF NOT EXISTS
FOR (n:BomLine)
ON EACH [n.searchText, n.partNameRaw, n.basePartNoRaw, n.newPartNoRaw, n.supplierRaw, n.changingPoint, n.changingReason];

CREATE FULLTEXT INDEX change_record_search_idx IF NOT EXISTS
FOR (n:ChangeRecord)
ON EACH [n.searchText, n.basePartNoRaw, n.newPartNoRaw, n.changingPoint, n.changingReason];

CREATE FULLTEXT INDEX part_search_idx IF NOT EXISTS
FOR (n:Part)
ON EACH [n.searchText, n.canonicalPartNo, n.canonicalName, n.partType];
