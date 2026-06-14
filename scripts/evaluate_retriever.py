"""Retriever 평가 — 10개 쿼리 × 3 모달리티 × top-10 정량/정성 분석.

정량 지표:
  keyword_match    쿼리 토큰이 결과의 part_name / narrative에 등장 비율
  diversity_forms  top-K 결과의 unique form_id 수
  diversity_parts  top-K 결과의 unique part_no_new 수
  null_part        top-K 중 part_no_new가 NULL인 비율
  latency_ms       쿼리 응답 시간

각 쿼리는 expected_hint(도메인 기대치)와 함께 정의 → 결과를 보고 사람이 판단.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

os.environ.setdefault("ENABLE_EMBEDDING", "1")

from src.db.engine import make_engine, session_factory  # noqa: E402
from src.db.retrieve import hybrid_search, lexical_search, semantic_search  # noqa: E402


@dataclass
class Eval:
    query: str
    expected_hint: str           # 도메인 기대치 (사람 판단용)
    must_contain_any: list[str]  # 결과의 part_name OR narrative에 이중 하나는 있어야
    filters: dict[str, Any] = field(default_factory=dict)


EVALS: list[Eval] = [
    Eval(
        "패킹 내열 강화 변경",
        "Packing 또는 Seal 류 변경부품. 내열/규제 사유.",
        ["packing", "seal", "패킹", "내열"],
    ),
    Eval(
        "Controller Assembly 외관 변경",
        "Controller / Knob / Decor 류. 외관 = STS/BK STS 색상.",
        ["controller", "knob", "decor", "외관", "sts"],
    ),
    Eval(
        "PCB 회로 부품",
        "PCB Assembly. 변경/신규 모두.",
        ["pcb"],
    ),
    Eval(
        "도어 힌지 부품",
        "Door / Hinge 류.",
        ["door", "hinge", "도어", "힌지"],
    ),
    Eval(
        "Cavity 어셈블리",
        "Cavity Assembly 시리즈.",
        ["cavity"],
    ),
    Eval(
        "북유럽향 부품",
        "북유럽 시장 (EUR 또는 region 표기 있는 행).",
        ["북유럽", "eur"],
    ),
    Eval(
        "Sheet Steel STS 외관 변경",
        "Sheet,Steel(STS) 또는 외관 색상 변경.",
        ["sheet", "steel", "sts", "외관"],
    ),
    Eval(
        "Insulator 단열재",
        "Insulator / Insulation 류.",
        ["insulator", "insulation", "단열"],
    ),
    Eval(
        "Frame Door 신규 부품",
        "Frame,Door / Door Frame. event=New 비중↑.",
        ["frame", "door"],
        filters={"event": "New"},
    ),
    Eval(
        "변경 사유 안전 규제",
        "change_reason에 '규제' / 'safety' 키워드 포함되는 변경.",
        ["규제", "안전", "safety"],
    ),
]


def tokenize(text: str) -> list[str]:
    """소문자 + 단어 분리 (영문/한글)."""
    if not text:
        return []
    return re.findall(r"[A-Za-z]+|[가-힣]+", text.lower())


def keyword_hit_rate(hits: list[Any], must_contain_any: list[str]) -> float:
    """top-K 결과 중 part_name 또는 narrative에 쿼리 토큰 등장 비율."""
    if not hits:
        return 0.0
    needles = {n.lower() for n in must_contain_any}
    matched = 0
    for h in hits:
        haystack = " ".join(
            filter(None, [h.part_name or "", h.embedding_text or "", h.new_model or ""])
        ).lower()
        if any(n in haystack for n in needles):
            matched += 1
    return matched / len(hits)


def diversity(hits: list[Any], attr: str) -> int:
    return len({getattr(h, attr) for h in hits if getattr(h, attr) is not None})


def null_part_ratio(hits: list[Any]) -> float:
    if not hits:
        return 0.0
    return sum(1 for h in hits if h.part_no_new is None) / len(hits)


def run() -> None:
    eng = make_engine()
    Session = session_factory(eng)
    print(f"{'Query':<32} {'Mod':<10} {'KW@5':<7} {'KW@10':<7} {'Dform':<6} {'Dpart':<6} {'NULL%':<7} {'ms':<8}")
    print("-" * 100)

    # 누적 — 평균 산출용
    agg: dict[str, dict[str, list[float]]] = {
        m: {k: [] for k in ("kw5", "kw10", "dform", "dpart", "nullp", "ms")}
        for m in ("semantic", "lexical", "hybrid")
    }

    detail: list[dict] = []

    with Session() as s:
        for ev in EVALS:
            for mode_name, fn in [
                ("semantic", semantic_search),
                ("lexical", lexical_search),
                ("hybrid", hybrid_search),
            ]:
                t0 = time.perf_counter()
                hits = fn(s, ev.query, top_k=10, **ev.filters)
                ms = (time.perf_counter() - t0) * 1000

                kw5 = keyword_hit_rate(hits[:5], ev.must_contain_any)
                kw10 = keyword_hit_rate(hits, ev.must_contain_any)
                dform = diversity(hits, "form_id")
                dpart = diversity(hits, "part_no_new")
                nullp = null_part_ratio(hits)

                agg[mode_name]["kw5"].append(kw5)
                agg[mode_name]["kw10"].append(kw10)
                agg[mode_name]["dform"].append(dform)
                agg[mode_name]["dpart"].append(dpart)
                agg[mode_name]["nullp"].append(nullp)
                agg[mode_name]["ms"].append(ms)

                q_short = (ev.query[:30] + "...") if len(ev.query) > 30 else ev.query
                print(
                    f"{q_short:<32} {mode_name:<10} "
                    f"{kw5:<7.2f} {kw10:<7.2f} {dform:<6} {dpart:<6} "
                    f"{nullp:<7.2f} {ms:<8.1f}"
                )

                detail.append(
                    {
                        "query": ev.query,
                        "mode": mode_name,
                        "kw5": kw5,
                        "kw10": kw10,
                        "dform": dform,
                        "dpart": dpart,
                        "nullp": nullp,
                        "ms": ms,
                        "top3": [
                            (h.part_no_new, h.part_name, h.form_id) for h in hits[:3]
                        ],
                    }
                )
            print("-" * 100)

    # 모달리티별 평균
    print()
    print("=" * 100)
    print(f"{'Modality':<12} {'avg KW@5':<10} {'avg KW@10':<10} {'avg Dform':<10} {'avg Dpart':<10} {'avg NULL%':<10} {'avg ms':<10}")
    print("-" * 100)
    for mode in ("semantic", "lexical", "hybrid"):
        a = agg[mode]
        avg = lambda k: sum(a[k]) / len(a[k]) if a[k] else 0.0
        print(
            f"{mode:<12} "
            f"{avg('kw5'):<10.2f} {avg('kw10'):<10.2f} "
            f"{avg('dform'):<10.1f} {avg('dpart'):<10.1f} "
            f"{avg('nullp'):<10.2f} {avg('ms'):<10.1f}"
        )

    # 각 쿼리 top-3 모달리티별 한 줄
    print()
    print("=" * 100)
    print("각 쿼리 top-3 (모달리티별):")
    print("=" * 100)
    by_query: dict[str, dict[str, list]] = {}
    for d in detail:
        by_query.setdefault(d["query"], {})[d["mode"]] = d["top3"]
    for q, modes in by_query.items():
        print(f"\nQ: {q}")
        for mode in ("semantic", "lexical", "hybrid"):
            print(f"  [{mode:<8}]")
            for pno, pname, form in modes[mode]:
                p = pno or "?"
                pn = (pname or "-")[:40]
                print(f"     {p:<14} | {pn:<40} | {form}")


if __name__ == "__main__":
    run()
