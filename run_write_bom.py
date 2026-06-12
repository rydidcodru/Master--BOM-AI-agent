"""
history_search fixture → write_bom 실행 스크립트.

입력:
  - bom_pipeline/fixtures/quickzone_history.json  (history_search 결과)
  - 시연참고용/LTIS7338XE.ARSLLGA@CVZ.EKHQ 1.0.xlsx  (base BOM)

출력:
  - output/quickzone_new_bom.xlsx
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bom_pipeline.nodes.write_bom import write_bom

HISTORY_JSON  = "bom_pipeline/fixtures/quickzone_history.json"
BASE_BOM_PATH = "시연참고용/LTIS7338XE.ARSLLGA@CVZ.EKHQ 1.0.xlsx"
OUTPUT_PATH   = "output/quickzone_new_bom.xlsx"

Path("output").mkdir(exist_ok=True)

with open(HISTORY_JSON, encoding="utf-8") as f:
    hist = json.load(f)

# ── change_points: history_search 결과 그대로 ─────────────────────────────────
change_points = hist

# ── selections: linked_parts를 added_linked_parts로 flatten ──────────────────
# history_candidates[*].linked_parts → 부품명 dedup 후 added_linked_parts로 노출
def flatten_linked_parts(hc_list: list) -> list:
    seen = set()
    result = []
    for c in hc_list:
        for lp in c.get("linked_parts", []):
            key = lp.get("part_name", "")
            if key and key not in seen:
                seen.add(key)
                result.append(lp)
    return result

selections = []
for item in hist:
    hc = item.get("history_candidates", [])
    sel = {
        "base_part_no":         item.get("base_part_no", ""),
        "new_part_no_confirmed": item.get("new_part_no", ""),
        "change_detail":        item.get("change_detail", ""),
        "added_linked_parts":   flatten_linked_parts(hc),
        "skipped":              False,
    }
    selections.append(sel)

# ── 실행 ─────────────────────────────────────────────────────────────────────
print(f"[run_write_bom] base BOM: {BASE_BOM_PATH}")
print(f"[run_write_bom] change_points: {len(change_points)}개")
linked_total = sum(len(s["added_linked_parts"]) for s in selections)
print(f"[run_write_bom] added_linked_parts 총 후보: {linked_total}개")

write_bom(
    base_bom_path=BASE_BOM_PATH,
    change_points=change_points,
    selections=selections,
    output_path=OUTPUT_PATH,
)
print(f"[run_write_bom] 저장: {OUTPUT_PATH}")