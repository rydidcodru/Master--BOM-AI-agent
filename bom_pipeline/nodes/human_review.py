"""
사용자 검토 노드.

각 change_point마다:
  1. 변경점 정보 + top-5 참고 케이스 출력
  2. 케이스별 변경 부품 목록 출력
  3. 연동부품 추가 제안 출력
  4. 사용자 입력 받아 human_selections 구성

human_selections 구조:
  [
    {
      "change_point_idx": 0,
      "part": "...",
      "base_part_no": "...",
      "new_part_no": "...",
      "selected_cases": [master_id, ...],    # 참고할 케이스
      "added_linked_parts": [                # 추가 확정된 연동부품
        {
          "part_name": "...",
          "base_part_no": "...",
          "new_part_no": "...",
          "change_type": "...",
        }
      ],
      "skipped": bool,
    }
  ]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from state import BOMPipelineState

# ── 출력 헬퍼 ─────────────────────────────────────────────────────────────

_SEP  = "─" * 70
_SEP2 = "═" * 70


def _print_header(idx: int, total: int, cp: dict) -> None:
    print(f"\n{_SEP2}")
    print(f"  변경점 [{idx + 1}/{total}]")
    print(f"  부품명   : {cp.get('part', '')}")
    print(f"  모듈     : {cp.get('module', '')}")
    print(f"  변경내역 : {cp.get('change_detail', '')}")
    print(f"  변경사유 : {cp.get('change_reason', '')}")
    print(f"  유형     : {cp.get('change_type', '')}  |  분야: {cp.get('discipline', '')}")
    print(f"  Base P/No: {cp.get('base_part_no', '(없음)')}")
    print(f"  New  P/No: {cp.get('new_part_no', '(없음)')}")
    print(_SEP2)


def _print_candidates(candidates: list[dict]) -> None:
    if not candidates:
        print("  (참고 이력 없음)")
        return

    for c in candidates:
        path_tag = "[직접이력]" if "직접이력" in c.get("select_reason", "") else "[유사패턴]"
        print(f"\n  {path_tag} 케이스 #{c['rank']}  master_id={c['master_id']}")
        print(f"  모델: {c.get('base_model', '')} → {c.get('new_model', '')}")
        print(f"  파일: {c.get('source_file', '')}")
        print(f"  선정이유: {c.get('select_reason', '')}")

        case_parts = c.get("case_parts", [])
        if case_parts:
            print(f"  변경부품 ({len(case_parts)}개):")
            for p in case_parts:
                pno_str = f"{p.get('base_part_no', ''):15s} → {p.get('new_part_no', ''):15s}"
                chg = p.get("changing_point", "")[:35]
                print(f"    [{p.get('bom_level', ''):4s}] {p.get('part_name', ''):32s} {pno_str} | {chg}")
        else:
            print("  변경부품: (없음)")

        linked = c.get("linked_parts", [])
        if linked:
            print(f"  연동부품 제안 ({len(linked)}개):")
            for lp in linked:
                pno_str = f"{lp.get('base_part_no', '') or '?':15s} → {lp.get('new_part_no', '') or '?':15s}"
                print(f"    [{lp.get('change_type', ''):4s}] {lp.get('part_name', ''):32s} {pno_str}")
                print(f"          이유: {lp.get('relevance_reason', '')}")

        print(_SEP)


def _ask_case_selection(candidates: list[dict]) -> list[int] | None:
    """참고할 케이스 master_id 선택. 엔터 = 전체 선택, 's' = 건너뜀."""
    if not candidates:
        return []

    rank_to_mid = {c["rank"]: c["master_id"] for c in candidates}
    ranks = sorted(rank_to_mid.keys())

    print(f"\n  참고할 케이스를 선택하세요 (번호 입력, 예: 1 3 5)")
    print(f"  엔터 = 전체 선택 ({', '.join(str(r) for r in ranks)})")
    print(f"  's' = 이 변경점 건너뛰기")

    while True:
        raw = input("  >> ").strip()
        if raw.lower() == "s":
            return None
        if raw == "":
            return [rank_to_mid[r] for r in ranks]
        try:
            chosen_ranks = [int(x) for x in raw.split()]
            valid = [r for r in chosen_ranks if r in rank_to_mid]
            if valid:
                return [rank_to_mid[r] for r in valid]
            print("  유효한 번호를 입력해주세요.")
        except ValueError:
            print("  숫자를 입력해주세요.")


def _collect_linked_parts(candidates: list[dict], selected_mids: list[int]) -> list[dict]:
    """선택된 케이스의 연동부품 후보를 모아서 추가 여부 확인."""
    seen: set[str] = set()
    all_linked: list[dict] = []
    for c in candidates:
        if c["master_id"] not in selected_mids:
            continue
        for lp in c.get("linked_parts", []):
            key = lp.get("part_name", "")
            if key and key not in seen:
                seen.add(key)
                all_linked.append(lp)

    if not all_linked:
        return []

    print(f"\n  연동부품 추가 여부를 선택하세요 ({len(all_linked)}개 후보):")
    for i, lp in enumerate(all_linked, 1):
        pno_str = f"{lp.get('base_part_no', '') or '?'} → {lp.get('new_part_no', '') or '?'}"
        print(f"  [{i}] [{lp.get('change_type', ''):4s}] {lp.get('part_name', ''):32s} ({pno_str})")
        print(f"       이유: {lp.get('relevance_reason', '')}")

    print(f"\n  추가할 번호 입력 (예: 1 2), 엔터 = 전체 추가, 'n' = 없음")

    while True:
        raw = input("  >> ").strip()
        if raw.lower() == "n":
            return []
        if raw == "":
            return all_linked
        try:
            chosen = [int(x) for x in raw.split()]
            valid = [all_linked[i - 1] for i in chosen if 1 <= i <= len(all_linked)]
            return valid
        except (ValueError, IndexError):
            print("  유효한 번호를 입력해주세요.")


# ── 노드 ──────────────────────────────────────────────────────────────────

def human_review_node(state: BOMPipelineState) -> dict:
    change_points = state.get("change_points", [])
    if not change_points:
        return {"human_selections": []}

    total = len(change_points)
    human_selections: list[dict] = []

    print(f"\n{_SEP2}")
    print(f"  BOM 변경점 검토 — 총 {total}개")
    print(f"  각 변경점마다 참고 케이스와 연동부품을 확인하고 선택해주세요.")
    print(_SEP2)

    for idx, cp in enumerate(change_points):
        candidates = cp.get("history_candidates", [])

        _print_header(idx, total, cp)
        _print_candidates(candidates)

        selected_mids = _ask_case_selection(candidates)

        if selected_mids is None:
            human_selections.append({
                "change_point_idx":   idx,
                "part":               cp.get("part", ""),
                "base_part_no":       cp.get("base_part_no", ""),
                "new_part_no":        cp.get("new_part_no", ""),
                "selected_cases":     [],
                "added_linked_parts": [],
                "skipped":            True,
            })
            print(f"  → 건너뜀")
            continue

        added_linked = _collect_linked_parts(candidates, selected_mids)

        human_selections.append({
            "change_point_idx":   idx,
            "part":               cp.get("part", ""),
            "base_part_no":       cp.get("base_part_no", ""),
            "new_part_no":        cp.get("new_part_no", ""),
            "selected_cases":     selected_mids,
            "added_linked_parts": [
                {
                    "part_name":    lp.get("part_name", ""),
                    "base_part_no": lp.get("base_part_no", ""),
                    "new_part_no":  lp.get("new_part_no", ""),
                    "change_type":  lp.get("change_type", ""),
                }
                for lp in added_linked
            ],
            "skipped": False,
        })

        n_cases  = len(selected_mids)
        n_linked = len(added_linked)
        print(f"  → 케이스 {n_cases}개 선택 | 연동부품 {n_linked}개 추가")

    skipped = len([s for s in human_selections if s["skipped"]])
    confirmed = total - skipped
    print(f"\n{_SEP2}")
    print(f"  검토 완료 — {confirmed}개 확정 / {skipped}개 건너뜀")
    print(_SEP2)

    return {"human_selections": human_selections}