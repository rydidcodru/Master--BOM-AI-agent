# Neo4j BOM 지식 그래프 스키마

## 1. 목적

이 그래프는 과거 BOM 심의 변경 이력을 저장해서, 에이전트가 과거 변경 사례, 영향받은 상위 Assembly, 원본 근거, 정규화된 부품 식별자를 따라갈 수 있게 만드는 기반 지식 그래프다.

1차 목적은 단순 트리 시각화가 아니라 아래 작업을 지원하는 것이다.

- 과거 심의 안건 조회
- 변경부품 리스트 추출
- 변경된 하위 BOM 라인에서 Root 라인까지의 상위 영향도 분석
- 반정형 엑셀 원본 행의 `rawJson` fallback 조회
- 향후 `ReviewCase`, `BomLine`, `ChangeRecord`, `Part` 중심 RAG 검색

## 2. 핵심 설계 결정

### 2.1 ReviewCase를 업무 단위로 둔다

`ReviewCase`는 심의 안건/모델/이벤트 단위다. 실제 파일은 `SourceDocument`로 별도 저장한다. 파일 하나가 나중에 여러 안건을 지원할 수 있기 때문이다.

```cypher
(:SourceDocument)-[:SUPPORTS_CASE]->(:ReviewCase)
```

### 2.2 원본 행은 반드시 보존한다

파싱된 엑셀 데이터 행은 모두 `BomLine`이 된다. 그리고 원본 행 전체를 JSON 문자열로 `BomLine.rawJson`에 저장한다.

이 결정이 중요한 이유는 원본 엑셀 포맷이 반정형이고 파일마다 다를 수 있기 때문이다. 정규화 필드가 누락되거나 잘못 해석되면, 에이전트가 `rawJson`을 직접 확인할 수 있어야 한다.

### 2.3 부품 식별자는 Raw 레이어를 거쳐 정규화한다

원본 파일에서 읽힌 부품 값과 정규화된 부품을 분리한다.

```cypher
(:BomLine)-[:REPRESENTS_RAW]->(:RawPart)
(:RawPart)-[:RESOLVES_TO]->(:Part)
(:BomLine)-[:RESOLVES_TO]->(:Part)
```

`BomLine -> Part` 직접 관계는 의도적인 중복이다. 에이전트가 짧은 Cypher로 질의할 수 있게 하기 위함이다. 단, 이 관계는 `RawPart -> Part` 매핑과 일관되게 유지해야 한다.

### 2.4 Common이 아닌 행만 ChangeRecord를 만든다

`ChangeRecord`는 아래 조건일 때만 생성한다.

```text
classification in ["Change", "New", "Delete"]
```

`Common` 행은 BOM 구조 보존을 위해 `BomLine`으로는 남기지만, 변경내역은 아니므로 `ChangeRecord`를 만들지 않는다.

### 2.5 하위 변경은 Root까지 모든 조상 라인에 영향을 준다

하위 BOM 라인이 변경되면, 그 라인을 포함하는 모든 부모 라인과 Root 라인이 영향받은 것으로 본다.

이 영향 관계는 적재 시점에 미리 생성한다.

```cypher
(:ChangeRecord)-[:DIRECTLY_CHANGES]->(:BomLine)
(:ChangeRecord)-[:IMPACTS_LINE {distance, impactType}]->(:BomLine)
```

`distance = 0`은 직접 변경된 라인, `distance = 1`은 직접 부모, 그 이상은 조상 라인을 뜻한다. Root까지 포함한다.

## 3. 노드 라벨

### 3.1 ReviewCase

심의 안건/모델/이벤트 단위.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `caseId` | string | yes | 안정적인 고유 ID |
| `modelName` | string | no | 대표 모델 또는 New Model |
| `baseModel` | string | no | Base Model/Grade |
| `newModel` | string | no | New Model/Grade |
| `event` | string | no | 현재 데이터에서는 보통 `DV` |
| `status` | string | no | 예: `approved`, `imported` |
| `summary` | string | no | 사람이 쓰거나 생성한 요약 |
| `searchText` | string | no | 검색/RAG용 텍스트 |
| `createdAt` | datetime/string | no | 적재 시각 |

### 3.2 SourceDocument

실제 원본 파일과 파싱 메타데이터.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `documentId` | string | yes | 안정적인 고유 ID |
| `fileName` | string | yes | 원본 파일명 |
| `filePath` | string | no | 원본 경로 |
| `sheetName` | string | no | 예: `Master` |
| `formatType` | string | no | 예: `lg_master_extra_v1_1` |
| `importedAt` | datetime/string | no | 적재 시각 |
| `parserVersion` | string | no | 파서 버전 |
| `rawMetadata` | string | no | 문서 메타데이터 JSON 문자열 |

### 3.3 BomLine

원본 BOM 행 하나.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `lineId` | string | yes | 문서+행 기반 안정 ID |
| `documentId` | string | yes | SourceDocument 참조값 |
| `caseId` | string | yes | ReviewCase 참조값 |
| `rowNo` | integer | yes | 원본 또는 논리 행 번호 |
| `level` | integer | yes | 파싱된 BOM Level |
| `rawLevel` | string | yes | 원본 값. 예: `.1`, `..2` |
| `partType` | string | no | 원본 Part Type |
| `basePartNoRaw` | string | no | 원본 Base P/No |
| `newPartNoRaw` | string | no | 원본 New P/No |
| `effectivePartNo` | string | no | 에이전트가 기준으로 삼을 유효 부품번호 |
| `partNameRaw` | string | no | 원본 부품명 |
| `qtyRaw` | string | no | 원본 수량. 기호 보존 |
| `effectiveQty` | float/string | no | 해석된 수량 |
| `supplierRaw` | string | no | 원본 Supplier |
| `classification` | string | no | `Common`, `Change`, `New`, `Delete` |
| `changingPoint` | string | no | 원본 변경점 |
| `changingReason` | string | no | 원본 변경사유 |
| `rawJson` | string | yes | 원본 행 전체 JSON 문자열 |
| `searchText` | string | no | 검색/RAG용 행 텍스트 |

### 3.4 ChangeRecord

`Common`이 아닌 변경 행의 의미 단위.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `changeId` | string | yes | 보통 `lineId` 기반 안정 ID |
| `caseId` | string | yes | ReviewCase 참조값 |
| `lineId` | string | yes | 원본 BomLine 참조값 |
| `classification` | string | yes | `Change`, `New`, `Delete` |
| `basePartNoRaw` | string | no | 원본 Base P/No |
| `newPartNoRaw` | string | no | 원본 New P/No |
| `effectivePartNo` | string | no | 주요 영향 부품번호 |
| `changingPoint` | string | no | 변경점 |
| `changingReason` | string | no | 변경사유 |
| `searchText` | string | no | 검색/RAG용 변경 텍스트 |

### 3.5 RawPart

원본 파일에서 읽힌 부품 식별자.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `rawPartId` | string | yes | 안정적인 고유 ID |
| `rawPartNo` | string | no | 원본 부품번호 |
| `rawPartName` | string | no | 원본 부품명 |
| `rawPartType` | string | no | 원본 Part Type |
| `sourceRole` | string | no | `base`, `new`, `effective` |
| `sourceDocumentId` | string | no | 원본 문서 ID |
| `sourceLineId` | string | no | 원본 라인 ID |
| `sourceColumnKey` | string | no | 원본 컬럼 매핑 키 |

### 3.6 Part

정규화된 부품 식별자.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `partId` | string | yes | 안정적인 Canonical ID |
| `canonicalPartNo` | string | no | 정규화 부품번호 |
| `canonicalName` | string | no | 정규화 부품명 |
| `partType` | string | no | 정규화 Part Type |
| `status` | string | no | 예: `active`, `deleted`, `unknown` |
| `searchText` | string | no | 검색/RAG용 부품 텍스트 |

### 3.7 SourceColumn

특정 문서/포맷/시트의 원본 컬럼.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `sourceColumnId` | string | yes | 안정적인 고유 ID |
| `documentId` | string | yes | SourceDocument 참조값 |
| `documentFormat` | string | no | 파서 포맷 |
| `sheetName` | string | no | 시트명 |
| `rawColumnName` | string | yes | 원본 컬럼명 |
| `columnIndex` | integer | yes | 0 기준 컬럼 인덱스 |

### 3.8 CanonicalField

표준 필드 사전.

| 속성 | 타입 | 필수 | 설명 |
|---|---:|---:|---|
| `key` | string | yes | 예: `base_part_no`, `part_name` |
| `displayName` | string | no | 사람이 읽는 필드명 |
| `description` | string | no | 의미 설명 |
| `dataType` | string | no | `string`, `number`, `json` 등 |

## 4. 관계 타입

### 4.1 원본 문서와 안건 관계

```cypher
(:SourceDocument)-[:SUPPORTS_CASE]->(:ReviewCase)
(:SourceDocument)-[:CONTAINS_LINE]->(:BomLine)
(:ReviewCase)-[:CONTAINS_LINE]->(:BomLine)
```

### 4.2 BOM 트리 관계

```cypher
(:BomLine)-[:HAS_CHILD {
  parentLineId,
  childLineId
}]->(:BomLine)
```

### 4.3 부품 정규화 관계

```cypher
(:BomLine)-[:REPRESENTS_RAW {role}]->(:RawPart)
(:RawPart)-[:RESOLVES_TO {
  confidence,
  method,
  reviewedBy,
  reviewedAt
}]->(:Part)
(:BomLine)-[:RESOLVES_TO {role, method}]->(:Part)
```

### 4.4 변경 관계

```cypher
(:ReviewCase)-[:HAS_CHANGE_RECORD]->(:ChangeRecord)
(:ChangeRecord)-[:FROM_LINE]->(:BomLine)
(:ChangeRecord)-[:DIRECTLY_CHANGES]->(:BomLine)
(:ChangeRecord)-[:DIRECTLY_CHANGES_PART {role}]->(:Part)
(:ChangeRecord)-[:IMPACTS_LINE {
  distance,
  impactType
}]->(:BomLine)
```

`IMPACTS_LINE`의 `impactType` 규칙:

| distance | impactType |
|---:|---|
| 0 | `direct` |
| 1 | `parent` |
| 2+ | `ancestor` |

명시적인 부품번호 변경은 선택적으로 아래 관계를 만든다.

```cypher
(:Part)-[:REPLACED_BY {
  caseId,
  documentId,
  lineId,
  classification,
  changingPoint,
  changingReason
}]->(:Part)
```

`REPLACED_BY`는 `classification = "Change"`이고 Base/New 부품번호가 모두 유효할 때만 생성한다.

### 4.5 표준 필드 사전 관계

```cypher
(:SourceDocument)-[:HAS_SOURCE_COLUMN]->(:SourceColumn)
(:SourceColumn)-[:MAPS_TO {
  confidence,
  method,
  parserVersion
}]->(:CanonicalField)
```

## 5. Classification과 원본 기호 의미

`classification`이 변경 의미의 기준이다.

| Classification | 의미 | ChangeRecord 생성 |
|---|---|---|
| `Common` | 변경 없음 | no |
| `Change` | Base 부품이 New 부품으로 변경 | yes |
| `New` | 신규 부품 추가 | yes |
| `Delete` | Base 부품 삭제 | yes |

원본 기호는 raw 필드에 그대로 보존하고, 필요한 경우 effective 필드로 해석한다.

| 원본 값 | 의미 |
|---|---|
| `←` | Base/current 값과 동일 |
| `-` | 비어 있음 또는 해당 없음 |
| `X` | 삭제 |

현재 LG Master 데이터 기준:

- `New P/No = ←`: `effectivePartNo = Base P/No`
- `New P/No = X`: 삭제 부품. `effectivePartNo`는 비워둘 수 있음
- `Classification = Change`: Base P/No가 New P/No로 변경
- `Q'ty = ←`: 수량 동일

## 6. 에이전트용 대표 질의

### 6.1 변경부품 리스트

```cypher
MATCH (case:ReviewCase)-[:HAS_CHANGE_RECORD]->(cr:ChangeRecord)
OPTIONAL MATCH (cr)-[:DIRECTLY_CHANGES_PART]->(p:Part)
RETURN
  case.caseId AS caseId,
  cr.classification AS classification,
  cr.basePartNoRaw AS basePartNo,
  cr.newPartNoRaw AS newPartNo,
  p.canonicalPartNo AS canonicalPartNo,
  cr.changingPoint AS changingPoint,
  cr.changingReason AS changingReason
ORDER BY caseId, cr.changeId;
```

### 6.2 특정 변경의 상위 영향 경로

```cypher
MATCH (cr:ChangeRecord {changeId: $changeId})-[r:IMPACTS_LINE]->(line:BomLine)
RETURN
  cr.changeId AS changeId,
  r.distance AS distance,
  r.impactType AS impactType,
  line.rowNo AS rowNo,
  line.level AS level,
  line.partNameRaw AS partName,
  line.effectivePartNo AS effectivePartNo
ORDER BY r.distance;
```

### 6.3 rawJson fallback 조회

```cypher
MATCH (line:BomLine)
WHERE line.lineId = $lineId
RETURN line.rawJson AS rawJson;
```

## 7. MVP 적재 범위

현재 엑셀 기준 1차 MVP에서는 아래를 먼저 적재한다.

1. `ReviewCase`
2. `SourceDocument`
3. `CanonicalField`
4. `SourceColumn`
5. `BomLine`
6. `HAS_CHILD`
7. `RawPart`
8. `Part`
9. `RESOLVES_TO`
10. `ChangeRecord`
11. `DIRECTLY_CHANGES`
12. `IMPACTS_LINE`

MVP에서는 Base BOM과 Approved New BOM을 별도 Snapshot으로 분리하지 않는다.
