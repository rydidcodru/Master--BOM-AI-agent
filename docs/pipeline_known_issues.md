# BOM 파이프라인 알려진 문제점

> 검증 기준: `input/WSED7613S.ASTQEUR@CVZ.EKHQ 1.0 (1).xlsx` → `output/KWS7D4731S.ASTQEUZ@CVZ.EKHQ 1.0.xlsx`  
> 파이프라인 감지율: .1 레벨 24개 중 10개 감지 (42%)

---

## 1. 미감지 14개 분류

### 1-A. 치수 파생 변경 (6개) — 구조적 한계

| 품번 | 부품명 | 미감지 이유 |
|------|--------|------------|
| AEV30168712 | Insulator Assembly | PPTX에 직접 언급 없음 |
| AEV73730205 | Insulator Assembly | PPTX에 직접 언급 없음 |
| MGC66413903 | Panel,Side | PPTX에 직접 언급 없음 |
| MGC66413904 | Panel,Side | PPTX에 직접 언급 없음 |
| MGJ67449001 | Plate,Base | PPTX에 직접 언급 없음 |
| MCK71658102 | Cover,Rear | PPTX에 직접 언급 없음 |

**원인:** Out Case(ACQ30856201) 치수 변경의 파생 부품들. PPTX 변경점 문서에 명시되지 않아 bom_match 단계에서 감지 불가. history_search의 linked_parts로 제안되어야 하지만 아래 1-B 이유로 동작하지 않음.

**해결 조건:** 히스토리 DB에 "Out Case 치수 변경 시 Panel,Side / Plate,Base / Insulator Assembly도 함께 교체"된 케이스가 존재해야 패턴 학습 가능. 현재 12개 케이스에는 해당 패턴 없음.

---

### 1-B. 포장/서비스 부품 (7개) — 코드 + 데이터 복합 문제

| 품번 | 부품명 |
|------|--------|
| AGM30206601 | Non Prod,Parts Assembly,SVC |
| AGM30206701 | Non Prod,Parts Assembly,SVC |
| AGM30206801 | Non Prod,Parts Assembly,SVC |
| MAY70002707 | Box |
| MEZ67849806 | Label,Carton |
| MFL70959911 | Manual,Service |
| MFZ67394503 | Packing,Gasket |

**원인 1 — 코드:** `_SKIP_KEYWORDS`에 `"label"`, `"carton"`, `"manual"`, `"bag"`, `"gasket"` 등이 포함되어 있어 `_format_case_list()`에서 LLM에게 전달하는 케이스 텍스트에서 이 부품들이 제거됨. LLM이 히스토리에서 이 부품들의 존재 자체를 볼 수 없음.

**원인 2 — 데이터:** 히스토리 DB에 `Label,Carton`, `Manual,Service`, `Packing,Gasket`이 변경된 케이스는 존재하지만, 변경 이유가 전부 "모델명 변경", "라벨 변경", "공용품"임. "제품 치수 변경 → 포장 교체"라는 인과 패턴이 DB에 없음.

**원인 3 — BOM 구조:** 포장류(`Box`, `Label,Carton` 등)와 치수 변경 대상(`Out Case`, `Panel,Side` 등)은 모두 루트 직계 `.1` 레벨 sibling 관계. 트리 계층 구조로 자동 파생할 수 없음.

```
WSED7613S (루트 0)
├── ACQ30856201  Cover Assembly,Rear  .1  ← 치수 변경 대상
├── MGC66413903  Panel,Side           .1  ← 치수 파생
├── MAY70002707  Box                  .1  ← 포장 (sibling)
├── MEZ67849806  Label,Carton         .1  ← 포장 (sibling)
└── MFL70959911  Manual,Service       .1  ← 서비스 (sibling)
```

**해결 조건:** "전체 치수 변경 이벤트 시 포장류를 함께 교체"라는 규칙을 도메인 지식으로 하드코딩하거나, 해당 인과 패턴이 담긴 히스토리 케이스가 DB에 추가되어야 함.

---

### 1-C. 기타 (1개)

| 품번 | 부품명 | 비고 |
|------|--------|------|
| AAA31682507 | Accessory Assembly,Base | PPTX 미언급, 히스토리 패턴 불명확 |

---

## 2. preserve_parts 오보존 1건

| 품번 | 부품명 | 문제 |
|------|--------|------|
| RAA33956539 | Sheet,Steel(GI) | Out Case full replace 시 LLM이 preserve_parts로 지정했으나 실제 output BOM에서는 삭제됨 |

**원인:** `Sheet,Steel(GI)`은 `_expand_preserve_by_desc`의 소재 그룹(`coil`, `steel`)에 매칭되어 확장 보존됨. 그러나 이 품번은 `MCK71658101(Cover,Rear)`의 하위 원자재로, Out Case 교체 시 함께 삭제되어야 하는 부품임.

**구조:**
```
ACQ30856201  Cover Assembly,Rear  .1
└── MCK71658101  Cover,Rear          ..2
    └── RAA33956539  Sheet,Steel(GI)    ..2  ← 삭제 대상인데 보존됨
```

---

## 3. history_search linked_parts = 0 문제

파이프라인 실행 시 9개 change_point 전체에서 `linked_parts: 0개` 반환됨.

**원인 1 — `_SKIP_KEYWORDS` 이중 차단:**
- `_format_case_list()` 내부에서 `_is_skip()` 필터링 → LLM이 포장/서비스 부품을 케이스 텍스트에서 못 봄
- STEP D에서 LLM 응답의 `linked_parts`에도 동일 `_is_skip()` 재적용

**원인 2 — 데이터 부재:**
치수 변경 → 포장 교체 인과 관계가 담긴 케이스가 DB 12개 중 없음.

**현재 `_SKIP_KEYWORDS`에서 문제가 되는 항목:**

| 키워드 | 차단되는 부품 |
|--------|--------------|
| `"label"` | Label,Carton, Label,Energy |
| `"carton"` | Label,Carton |
| `"manual"` | Manual,Service, Manual,Owners, Manual,Installation |
| `"bag"` | Bag,Envelope, Bag,Sheet |
| `"gasket"` | Packing,Gasket |

체결류(Nut, Screw, Washer, Bolt, Rivet)만 남기고 나머지를 제거하면 코드 문제는 해결되나, 데이터 문제로 인해 패턴 추론은 여전히 불가.

---

## 4. 현재 감지 가능한 범위 요약

| 카테고리 | 감지 방법 | 현재 상태 |
|----------|-----------|-----------|
| PPTX 직접 언급 부품 | bom_match | ✅ 정상 동작 |
| PPTX 명시 삭제 (Conv Heater 등) | bom_match deleted_parts | ✅ 정상 동작 |
| 과거 이력에 동일 품번 변경 패턴 | history_search 경로1 | ✅ 동작, DB 케이스 의존 |
| 치수 파생 구조 변경 | history_search linked_parts | ❌ DB 패턴 없음 |
| 포장/서비스 연동 교체 | history_search linked_parts | ❌ 코드 차단 + DB 패턴 없음 |
| 원자재 보존 (Paint,Powder, Coil) | write_bom preserve_parts | ✅ 정상 동작 (일부 오보존) |