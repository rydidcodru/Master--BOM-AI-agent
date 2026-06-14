"""참고용_Extra_결과(최종 개발마스터) 기준 검색 성능 평가.

reference는 한 프로젝트(예: WSED7667M→WS9D7688M)의 최종 변경 결과(16컬럼 Extra 형식).
각 변경 행의 (변경점+변경사유)를 질의로 ``search_events``에 던져 두 지표를 본다:

  reason_hit@k : 회수된 이벤트의 change_log/reason이 그 행의 변경사유와 충분히 겹치는가
                 (rapidfuzz token_set_ratio ≥ threshold) — 의미상 같은 변경을 찾았는가.
  part_hit@k   : 회수된 이벤트의 change_line 부품번호가 그 행의 Base/New P/No를 포함하는가
                 — 정확히 그 부품 변경을 찾았는가(강한 신호).

ENABLE_EMBEDDING=1 + Ollama bge-m3(또는 lexical-only)면 hybrid로 평가.

Usage:
    python -m scripts.evaluate_against_reference --ref data/golden/reference_extra.xlsx --k 5
"""

from __future__ import annotations

import argparse
import pathlib
import re

import pandas as pd
from rapidfuzz import fuzz

from src.db.engine import make_engine, session_factory
from src.db.retrieve import lookup_lines_by_event, search_events

REF_DEFAULT = "data/golden/reference_extra.xlsx"


def _col_with(row: pd.Series, *keys: str) -> int | None:
    for j, x in enumerate(row):
        s = "" if pd.isna(x) else str(x)
        if any(k in s for k in keys):
            return j
    return None


def load_reference(path: pathlib.Path) -> list[dict]:
    """Extra 결과 시트 → 변경 행 [{point, reason, base, new, name}]."""
    df = pd.read_excel(path, header=None, dtype=str)
    hdr = None
    for i in range(min(14, len(df))):
        blob = " ".join("" if pd.isna(x) else str(x) for x in df.iloc[i])
        if "변경점" in blob and "변경사유" in blob:
            hdr = i
            break
    if hdr is None:
        raise SystemExit("reference header (변경점/변경사유) not found")
    h = df.iloc[hdr]
    c_point = _col_with(h, "변경점", "Changing Point")
    c_reason = _col_with(h, "변경사유", "Changing Reason")
    c_name = _col_with(h, "부품명", "Part Name")
    sub = df.iloc[hdr + 1] if hdr + 1 < len(df) else None
    c_base = _col_with(sub, "Base P/No") if sub is not None else None
    c_new = _col_with(sub, "New P/No") if sub is not None else None

    def g(row: pd.Series, c: int | None) -> str:
        if c is None:
            return ""
        v = row.iloc[c]
        return "" if pd.isna(v) else str(v).strip()

    cases: list[dict] = []
    for i in range(hdr + 2, len(df)):
        row = df.iloc[i]
        point, reason = g(row, c_point), g(row, c_reason)
        if not (point or reason):
            continue
        cases.append(
            {"point": point, "reason": reason, "base": g(row, c_base),
             "new": g(row, c_new), "name": g(row, c_name)}
        )
    return cases


def _norm_pno(p: str) -> str:
    return re.sub(r"\s+", "", str(p or "")).upper()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default=REF_DEFAULT)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--reason-thresh", type=int, default=60)
    ap.add_argument("--reason-only", action="store_true",
                    help="질의를 변경사유만으로(일반적 변경점 '부품 변경' 노이즈 제외)")
    args = ap.parse_args()

    cases = load_reference(pathlib.Path(args.ref))
    print(f"reference change cases: {len(cases)}")
    if not cases:
        return

    reason_hits = part_hits = part_cases = 0
    examples: list[tuple] = []
    SF = session_factory(make_engine())
    with SF() as s:
        for cs in cases:
            q = (cs["reason"] if args.reason_only else (cs["point"] + " " + cs["reason"])).strip()
            if not q:
                continue
            hits = search_events(s, q, top_k=args.k)
            rhit = bool(cs["reason"]) and any(
                fuzz.token_set_ratio(
                    f"{h.change_reason or ''} {h.change_log or ''}", cs["reason"]
                ) >= args.reason_thresh
                for h in hits
            )
            want = {_norm_pno(cs["base"]), _norm_pno(cs["new"])} - {"", "X"}
            phit = False
            if want:
                part_cases += 1
                for h in hits:
                    pnos = set()
                    for ln in lookup_lines_by_event(s, h.event_id):
                        pnos.add(_norm_pno(ln.new_pno))
                        pnos.add(_norm_pno(ln.base_pno))
                    if want & (pnos - {""}):
                        phit = True
                        break
            reason_hits += int(rhit)
            part_hits += int(phit)
            if len(examples) < 8:
                examples.append((q[:44], rhit, phit, (hits[0].change_reason or "")[:30] if hits else None))

    n = len(cases)
    print(f"reason_hit@{args.k}: {reason_hits}/{n} = {reason_hits / n:.3f}")
    print(f"part_hit@{args.k}:   {part_hits}/{part_cases} = "
          f"{(part_hits / part_cases) if part_cases else 0:.3f}  (cases with Base/New P/No)")
    print("\nexamples:")
    for q, rh, ph, top in examples:
        print(f"  reason_hit={rh!s:<5} part_hit={ph!s:<5} q={q!r}  top_reason={top!r}")


if __name__ == "__main__":
    main()
