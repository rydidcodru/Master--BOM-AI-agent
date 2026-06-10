from __future__ import annotations

from state import BOMSearchState

SEP = "=" * 60


def output_node(state: BOMSearchState) -> dict:
    results = state.get("search_results", [])
    if not results:
        print("\n[결과 없음] 추출된 변경점이 없습니다.")
        return {}

    print(f"\n\n{SEP}")
    print(f"  BOM 변경점 유사 이력 검색 결과  ({len(results)}개 항목)")
    print(SEP)

    for sr in results:
        cp = sr["change_point"]
        ranked = sr.get("ranked", [])
        pool_size = len(sr.get("candidates", []))
        retry = sr.get("retry_count", 0)

        print(f"\n[{cp.get('source_pptx', '')}]  {cp.get('module', '')} > {cp.get('part', '')}")
        print(f"  canonical  : {cp.get('canonical_part', '')}")
        print(f"  변경내역   : {cp.get('change_detail', '')}")
        print(f"  변경사유   : {cp.get('change_reason', '')}")
        print(f"  Neo4j 후보 : {pool_size}건 (시도 {retry + 1}회) → LLM 선정 {len(ranked)}건")

        if not ranked:
            print("  └ 유사 이력 없음")
            continue

        for i, r in enumerate(ranked):
            prefix = "  └" if i == len(ranked) - 1 else "  ├"
            base = r.get("basePartNoRaw", "") or ""
            new  = r.get("newPartNoRaw", "")  or ""
            pno  = f"{base} → {new}" if base or new else ""
            level = r.get("level", "")
            part_type = r.get("partType", "")
            print(
                f"{prefix} [{r.get('rank', i+1)}위] "
                f"{r.get('classification', ''):8s} | "
                f"Lv.{level} | "
                f"{str(r.get('partName', '')):30s} | "
                f"{str(r.get('modelName', '')):20s}"
            )
            if part_type:
                print(f"       partType: {part_type}")
            print(f"       point  : {r.get('changingPoint', '')}")
            print(f"       reason : {r.get('changingReason', '')}")
            if pno:
                print(f"       P/No   : {pno}")
            print(f"       근거   : {r.get('similarity_reason', '')}")

    print(f"\n{SEP}\n")
    return {}
