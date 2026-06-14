"""Compact Oven base+new BOM → 통합 개발부품 Master(v1.1 형식) 산출 → 정확도 채점.

헤드리스 측정 파이프라인(결정론):
  1. base/new BOM xlsx 파싱(parse_base_bom)
  2. base↔new 집합 diff(diff_boms) → 분류 + 매칭 New P/No
  3. 정답지 형식 행 매핑(rows_from_diff) + 변경사유(K) best-effort 결합(옵션)
  4. v1.1 템플릿 서식보존 출력(write_master_xlsx)
  5. 정답지(v1.1) 대비 채점(accuracy.score) — 변경 유니버스 한정 게이팅/진단

실행: lgdp venv python scripts/run_compact_oven_to_master.py
정답지를 입력 BOM과 함께 비교해 base_pno/new_pno(게이팅)·recall·분류·진단을 출력.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# repo 루트(src 패키지) 경로 보장.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# .env 로드 — 채점의 changing_reason 방식(ENABLE_EMBEDDING → embedding_cosine, 의미 유사도)
# 을 후보검색 하네스와 동일하게 맞춘다(없으면 difflib로 텍스트만 비교 → 과소평가).
try:
    from dotenv import load_dotenv

    load_dotenv(str(_ROOT / ".env"))
except Exception:  # noqa: BLE001
    pass

from src.agent.accuracy import load_master_rows, score, score_rows  # noqa: E402
from src.agent.basebom.diff import diff_boms, diff_summary  # noqa: E402
from src.agent.basebom.parser import parse_base_bom  # noqa: E402
from src.agent.basebom.refine import refine_diff, refine_summary  # noqa: E402
from src.agent.docgen.master_writer import rows_from_diff, write_master_xlsx  # noqa: E402
from src.agent.docgen.reason_join import ppt_reason_map  # noqa: E402
from src.agent.ppt.extractor import extract_change_review_from_pptx_bytes  # noqa: E402

# ── 입력 경로 (Compact Oven) ──
_PROJ = _ROOT.parent  # ...\LG_Data_pipeline
_CO = _PROJ / "Master--BOM-AI-agent" / "agent" / "Compact Oven"
BASE_BOM = _CO / "WSED7613S.ASTQEUR@CVZ.EKHQ 1.0 (1)(base_bom).xlsx"
NEW_BOM = _CO / "KWS7D4731S.ASTQEUZ@CVZ.EKHQ 1.0(new_bom).xlsx"
PPT = _PROJ / "NXI 개발 유형 및 등급 확정 심의회 Compact Oven 0404 (1) (1).pptx"
ORACLE = _PROJ / "통합 개발부품Master Compact v1.1.xlsx"
OUT_DIR = _ROOT / "out"
OUT_XLSX = OUT_DIR / "compact_oven_master_produced.xlsx"


def _ppt_reasons(refined) -> dict[str, str]:
    """PPT 변경 서사 → 변경 부품 사유 결합(결정론, no LLM, no leak). PPT 없으면 {}."""
    if not PPT.exists():
        return {}
    doc = extract_change_review_from_pptx_bytes(PPT.read_bytes())
    changed = [d for d in refined if d.classification != "Common"]
    return ppt_reason_map(changed, doc.get("detailed_changes") or [])


def _score_subset(rows, oracle_rows) -> dict:
    """MasterRow 리스트 → score_rows(정답지 행 캐시 재사용)."""
    return score_rows([r.__dict__ for r in rows], oracle_rows)


def _summ_acc(a: dict) -> dict:
    g = a["gating"]
    return {
        "recall": f"{a['row_matching']['matched']}/{a['scope']['oracle_inscope_rows']}",
        "produced_inscope": a["scope"]["produced_inscope_rows"],
        "match_rate": round(a["row_matching"]["match_rate"], 3),
        "base_pno": round(g["base_pno"]["acc"] or 0, 3),
        "new_pno": round(g["new_pno"]["acc"] or 0, 3),
        "classification": round(g["classification"]["acc"] or 0, 3),
        "part_name": round(a["diagnostic"]["part_name"]["match_rate"] or 0, 3),
        "changing_reason": round(a["diagnostic"]["changing_reason"]["mean_similarity"] or 0, 3),
    }


def main() -> int:
    for p in (BASE_BOM, NEW_BOM, ORACLE):
        if not p.exists():
            print(f"[ERR] 입력 없음: {p}", file=sys.stderr)
            return 2
    OUT_DIR.mkdir(exist_ok=True)

    base = parse_base_bom(BASE_BOM.read_bytes(), file_name=BASE_BOM.name)
    new = parse_base_bom(NEW_BOM.read_bytes(), file_name=NEW_BOM.name)
    oracle_rows = load_master_rows(str(ORACLE))

    # 1) raw diff  2) refine(가지치기·신뢰도)  3) PPT 사유 보강
    diff = diff_boms(base, new)
    refined = refine_diff(diff, base, new)
    reasons = _ppt_reasons(refined)

    # 변형 A: BEFORE (raw diff, 사유 없음) — 기준선
    rows_before = rows_from_diff(diff)
    acc_before = _score_subset(rows_before, oracle_rows)

    # 변형 B: AFTER (refine + 사유) — 전체(최대 recall)
    rows_full = rows_from_diff(refined, reasons=reasons)
    acc_full = _score_subset(rows_full, oracle_rows)

    # 변형 C: HIGH-CONF (refine + 사유, 저신뢰 new-only 제외) — 정밀도 지향
    drop_low = {id(d) for d in refined if d.confidence == "low"}
    refined_hc = [d for d in refined if id(d) not in drop_low]
    rows_hc = rows_from_diff(refined_hc, reasons=reasons)
    acc_hc = _score_subset(rows_hc, oracle_rows)

    # 산출 master(전체=AFTER)를 v1.1 서식으로 출력.
    OUT_XLSX.write_bytes(write_master_xlsx(rows_full, str(ORACLE)))

    report = {
        "inputs": {
            "base_bom": {"model": base.model, "rows": len(base.rows)},
            "new_bom": {"model": new.model, "rows": len(new.rows)},
            "oracle": ORACLE.name,
        },
        "diff_summary": diff_summary(diff),
        "refine_summary": refine_summary(refined),
        "reasons_filled": len(reasons),
        "rows": {"before": len(rows_before), "after_full": len(rows_full), "high_conf": len(rows_hc)},
        "accuracy": {
            "A_before_rawdiff": _summ_acc(acc_before),
            "B_after_refine_reasons": _summ_acc(acc_full),
            "C_high_conf": _summ_acc(acc_hc),
        },
        "accuracy_full_detail": acc_full,
        "produced_master": str(OUT_XLSX),
    }
    out_json = OUT_DIR / "compact_oven_accuracy.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 콘솔(cp949 안전) 요약.
    print("diff changed=%d -> refine changed=%d (high_conf rows=%d)" % (
        report["diff_summary"]["changed"], report["refine_summary"]["changed"], len(rows_hc)))
    print("refine changed_by_confidence:", report["refine_summary"]["changed_by_confidence"])
    print("reasons_filled:", len(reasons))
    for k, v in report["accuracy"].items():
        print(f"[{k}] {v}")
    print(f"[OK] report={out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
