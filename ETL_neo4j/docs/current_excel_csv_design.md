# 현재 엑셀용 Neo4j 적재 CSV 설계

## 1. 원본 Workbook

현재 원본 파일:

```text
G:\내 드라이브\10.프로젝트 자료\01. LG\data\통합 개발부품Master Extra v1.1.xlsx
```

현재 대상 시트:

```text
Master
```

확인된 원본 구조:

- `pandas.read_excel(..., header=7)` 기준으로 실제 데이터 헤더가 잡힌다.
- `BOM\nLevel`이 비어 있지 않은 행이 BOM 데이터 행이다.
- BOM Level 값은 `.1`, `..2`, `...3` 형태다.
- 현재 파싱 기준 BOM 노드 409개, Root 노드 68개, 최대 Level 6이다.

## 2. CSV 파일 세트

MVP importer는 아래 CSV 파일들을 생성한다.

```text
csv/
  review_cases.csv
  source_documents.csv
  document_case_edges.csv
  canonical_fields.csv
  source_columns.csv
  bom_lines.csv
  bom_line_edges.csv
  raw_parts.csv
  parts.csv
  raw_part_resolutions.csv
  bom_line_part_resolutions.csv
  change_records.csv
  change_record_edges.csv
  impact_edges.csv
  replacement_edges.csv
```

Neo4j `LOAD CSV`를 기준으로 UTF-8을 사용한다.

## 3. ID 전략

재적재해도 같은 노드가 유지되도록 deterministic ID를 사용한다.

권장 규칙:

```text
documentId = "doc:" + sha1(filePath + "|" + sheetName)
caseId     = "case:" + sha1(baseModel + "|" + newModel + "|" + event + "|" + documentId)
lineId     = documentId + ":line:" + rowNo
changeId   = lineId + ":change"
rawPartId  = lineId + ":rawpart:" + role
partId     = "part:" + normalizedPartNo
```

부품번호가 비어 있거나 기호(`←`, `-`, `X`)만 있는 경우에는 유효한 `effectivePartNo`가 있을 때만 Canonical `Part`를 만든다.

## 4. SourceColumn과 CanonicalField 매핑

현재 Workbook 기준 1차 매핑:

| 컬럼 인덱스 | 원본 컬럼명 | 표준 필드 key | 설명 |
|---:|---|---|---|
| 1 | `No.` | `row_no` | 논리 행 번호 |
| 2 | `BOM\nLevel` | `bom_level` | BOM 계층 레벨 |
| 3 | `Part Type` | `part_type` | 원본 부품 유형 |
| 4 | `P/No` | `base_part_no` | Base P/No |
| 5 | `Unnamed: 5` | `new_part_no` | 2단 헤더상 New P/No |
| 6 | `부품명\nClass Desc.(Part Name)` | `part_name` | 부품명 |
| 7 | `Q'ty` | `quantity` | 수량 |
| 9 | `변경점\nChanging Point` | `changing_point` | 변경점 |
| 10 | `변경사유\nChanging Reason` | `changing_reason` | 변경사유 |
| 11 | `양산처\nSupplier` | `supplier_name` | 공급사 |
| 12 | `신규/변경\n부품 대상\nClassification` | `classification` | `Common`, `Change`, `New`, `Delete` |

아직 표준 필드로 매핑하지 않은 컬럼도 `BomLine.rawJson`에는 반드시 보존한다.

## 5. CSV 스키마

### 5.1 review_cases.csv

심의 안건/모델/이벤트 단위로 1행.

```csv
caseId,modelName,baseModel,newModel,event,status,summary,searchText,createdAt
```

현재 Workbook에서는 상단 메타 영역의 `Base Model/Grade`, `New Model/Grade`, `Event`를 가능한 범위에서 파싱한다.

### 5.2 source_documents.csv

실제 원본 파일/시트 단위로 1행.

```csv
documentId,fileName,filePath,sheetName,formatType,importedAt,parserVersion,rawMetadata
```

### 5.3 document_case_edges.csv

원본 문서와 심의 안건을 연결한다.

```csv
documentId,caseId
```

파일 하나가 나중에 여러 `ReviewCase`를 지원할 수 있으므로 별도 edge CSV로 둔다.

### 5.4 canonical_fields.csv

표준 필드 사전.

```csv
key,displayName,description,dataType
```

권장 초기 row:

```csv
row_no,Row No,Source row number,integer
bom_level,BOM Level,Parsed BOM hierarchy level,integer
part_type,Part Type,Source part category,string
base_part_no,Base P/No,Base part number,string
new_part_no,New P/No,New part number or source symbol,string
part_name,Part Name,Part name or class description,string
quantity,Quantity,Quantity or source quantity symbol,string
changing_point,Changing Point,Change point text,string
changing_reason,Changing Reason,Change reason text,string
supplier_name,Supplier,Supplier name,string
classification,Classification,Common Change New Delete,string
```

### 5.5 source_columns.csv

원본 컬럼과 표준 필드 매핑.

```csv
sourceColumnId,documentId,documentFormat,sheetName,rawColumnName,columnIndex,canonicalFieldKey,confidence,method,parserVersion
```

이 CSV는 `SourceColumn` 노드와 `MAPS_TO` 관계를 함께 만들 수 있게 설계한다.

### 5.6 bom_lines.csv

원본 BOM 행 단위.

```csv
lineId,documentId,caseId,rowNo,level,rawLevel,partType,basePartNoRaw,newPartNoRaw,effectivePartNo,partNameRaw,qtyRaw,effectiveQty,supplierRaw,classification,changingPoint,changingReason,rawJson,searchText
```

`effectivePartNo` 해석 규칙:

| 원본 패턴 | effectivePartNo |
|---|---|
| `newPartNoRaw = ←` | `basePartNoRaw` |
| `newPartNoRaw = X` | empty |
| `classification = Change`이고 유효한 New P/No 존재 | `newPartNoRaw` |
| `classification = New`이고 유효한 New P/No 존재 | `newPartNoRaw` |
| 그 외 | 유효하면 `basePartNoRaw` |

원본 기호는 raw 필드에 그대로 보존한다.

### 5.7 bom_line_edges.csv

BOM 부모-자식 트리 edge.

```csv
parentLineId,childLineId,parentRowNo,childRowNo
```

생성 규칙:

- 행 순서대로 읽는다.
- 파싱된 `level` 기준으로 stack을 유지한다.
- 현재 행의 부모는 가장 가까운 이전 행 중 `level = currentLevel - 1`인 행이다.

### 5.8 raw_parts.csv

원본 행에서 추출한 Raw 부품 식별자.

```csv
rawPartId,rawPartNo,rawPartName,rawPartType,sourceRole,sourceDocumentId,sourceLineId,sourceColumnKey
```

권장 role:

```text
base
new
effective
```

비어 있는 기호값은 보존이 필요한 경우가 아니면 `RawPart`를 만들지 않아도 된다.

### 5.9 parts.csv

정규화된 Canonical 부품.

```csv
partId,canonicalPartNo,canonicalName,partType,status,searchText
```

MVP 정규화 규칙:

- 우선 부품번호 기준으로 정규화한다.
- 첫 번째 비어 있지 않은 부품명을 `canonicalName`으로 사용한다.
- 나중에 수작업 보정 가능하도록 둔다.

### 5.10 raw_part_resolutions.csv

`RawPart -> Part` 매핑.

```csv
rawPartId,partId,confidence,method,reviewedBy,reviewedAt
```

초기 `method` 후보:

```text
exact_part_no
effective_part_no
manual
unknown
```

### 5.11 bom_line_part_resolutions.csv

에이전트 쿼리를 쉽게 하기 위한 직접 `BomLine -> Part` 매핑.

```csv
lineId,partId,role,method
```

권장 role:

```text
effective
base
new
```

### 5.12 change_records.csv

`Common`이 아닌 변경 행 단위.

```csv
changeId,caseId,lineId,classification,basePartNoRaw,newPartNoRaw,effectivePartNo,changingPoint,changingReason,searchText
```

아래 조건일 때만 생성한다.

```text
classification in ["Change", "New", "Delete"]
```

### 5.13 change_record_edges.csv

ChangeRecord에서 원본 행과 직접 변경 부품으로 가는 edge.

```csv
changeId,lineId,directPartId,directPartRole,basePartId,newPartId
```

규칙:

- `DIRECTLY_CHANGES`는 `changeId -> lineId`로 연결한다.
- `DIRECTLY_CHANGES_PART`는 해당 변경의 주요 영향 부품으로 연결한다.
- `Change`의 direct part는 보통 New Part다.
- `New`의 direct part는 New Part다.
- `Delete`의 direct part는 Base Part다.

### 5.14 impact_edges.csv

각 변경에서 직접 변경 라인 및 Root까지의 모든 조상 라인으로 가는 영향 edge.

```csv
changeId,impactedLineId,distance,impactType
```

규칙:

| distance | impactType |
|---:|---|
| 0 | `direct` |
| 1 | `parent` |
| 2+ | `ancestor` |

변경 라인에서 부모 edge를 따라 Root까지 올라가며 생성한다.

### 5.15 replacement_edges.csv

명시적인 부품번호 변경 edge.

```csv
fromPartId,toPartId,caseId,documentId,lineId,classification,changingPoint,changingReason
```

아래 조건일 때만 생성한다.

```text
classification = "Change"
and basePartNoRaw is valid
and newPartNoRaw is valid
and basePartNoRaw <> newPartNoRaw
```

## 6. 권장 적재 순서

1. `constraints_indexes.cypher`
2. `canonical_fields.csv`
3. `review_cases.csv`
4. `source_documents.csv`
5. `document_case_edges.csv`
6. `source_columns.csv`
7. `bom_lines.csv`
8. `bom_line_edges.csv`
9. `parts.csv`
10. `raw_parts.csv`
11. `raw_part_resolutions.csv`
12. `bom_line_part_resolutions.csv`
13. `change_records.csv`
14. `change_record_edges.csv`
15. `impact_edges.csv`
16. `replacement_edges.csv`

## 7. Neo4j LOAD CSV 골격

파일 URL은 Neo4j import directory 기준으로 조정한다.

```cypher
LOAD CSV WITH HEADERS FROM 'file:///review_cases.csv' AS row
MERGE (c:ReviewCase {caseId: row.caseId})
SET c += row;

LOAD CSV WITH HEADERS FROM 'file:///source_documents.csv' AS row
MERGE (d:SourceDocument {documentId: row.documentId})
SET d += row;

LOAD CSV WITH HEADERS FROM 'file:///document_case_edges.csv' AS row
MATCH (d:SourceDocument {documentId: row.documentId})
MATCH (c:ReviewCase {caseId: row.caseId})
MERGE (d)-[:SUPPORTS_CASE]->(c);
```

## 8. 검증 쿼리

### 8.1 Level별 BomLine 개수

```cypher
MATCH (line:BomLine)
RETURN line.level AS level, count(*) AS count
ORDER BY level;
```

### 8.2 ChangeRecord가 원본 BomLine에 연결됐는지 확인

```cypher
MATCH (cr:ChangeRecord)
WHERE NOT (cr)-[:FROM_LINE]->(:BomLine)
RETURN cr.changeId AS missingLine
LIMIT 20;
```

### 8.3 직접 변경 라인에 distance 0 영향 edge가 있는지 확인

```cypher
MATCH (cr:ChangeRecord)-[:DIRECTLY_CHANGES]->(line:BomLine)
WHERE NOT (cr)-[:IMPACTS_LINE {distance: 0}]->(line)
RETURN cr.changeId, line.lineId
LIMIT 20;
```

### 8.4 변경 라인과 영향받은 조상 라인 조회

```cypher
MATCH (cr:ChangeRecord)-[r:IMPACTS_LINE]->(line:BomLine)
RETURN
  cr.changeId AS changeId,
  cr.classification AS classification,
  r.distance AS distance,
  r.impactType AS impactType,
  line.rowNo AS rowNo,
  line.level AS level,
  line.partNameRaw AS partName,
  line.effectivePartNo AS partNo
ORDER BY changeId, distance;
```
