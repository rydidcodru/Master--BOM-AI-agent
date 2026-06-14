"""정답(ground truth) 1건으로 ①.5 구조 스코프 → ②.5 매핑이 하위 트리 부품을
제대로 끌어오는지 실데이터로 끝까지 검증.

정답 케이스 (실데이터):
  base_model = WSED7613S  → BOM file 19
  module     = Controller Assembly,Tact Dial (ACM76198404, 얕은 노드)
  event 2078 (외관 변경 STS→BK STS)의 change_line 5건 — 그중
    · Decor,Control Panel  base_pno=MCR68450803  → 하위 트리에 동일 품번 노드 존재(pno 정확일치)
    · Label,Barcode/Window,Glass/Print Assembly/Sheet,Steel(STS) → 이름 canon 매칭으로 존재
  음성 대조: "Compressor Motor"(트리에 없음) → dropped 여야 함.

프로덕션 코드 그대로 사용: build_scope(①.5) → map_event_to_scope(②.5).
"""

from __future__ import annotations

import os

os.environ.setdefault("ENABLE_EMBEDDING", "0")  # 매칭은 임베딩 불필요(trgm/pno)

from src.agent.mapping.mapper import map_event_to_scope
from src.agent.matching.scoring import MatchThresholds
from src.agent.orchestrator.structure_scope import build_scope
from src.db.engine import make_engine, session_factory
from src.db.models import ChangeLine
from src.db.retrieve import lookup_lines_by_event

GT_EVENT = 2078
GT_BASE_MODEL = "WSED7613S"
GT_MODULE = "Controller Assembly,Tact Dial"
GT_PNO_EXACT = "MCR68450803"  # Decor,Control Panel — 하위 트리 동일 품번(정답)


def main() -> int:
    th = MatchThresholds.from_env()
    S = session_factory(make_engine())
    fail = 0
    with S() as s:
        # ── ①.5 구조 스코프 구축 (모듈명 + base_model → 하위 트리 S) ──
        scope = build_scope([GT_MODULE], GT_BASE_MODEL, s, th)
        if scope is None:
            print("FAIL: build_scope 가 None — 스코프 구축 실패")
            return 1
        print(f"[①.5] ScopeIndex 구축: 노드 {len(scope.nodes)}개 · "
              f"part_type {len(scope.types)}종")
        in_scope = GT_PNO_EXACT in scope.by_pno
        print(f"      정답 노드 {GT_PNO_EXACT}(Decor,Control Panel) 스코프 포함: {in_scope}")
        fail += 0 if in_scope else 1

        # ── ②.5 실제 이벤트 라인을 S에 매핑 ──
        lines = lookup_lines_by_event(s, GT_EVENT)
        print(f"\n[②.5] event {GT_EVENT} change_line {len(lines)}건 매핑:")
        me = map_event_to_scope(lines, scope)
        print(f"      coherence={me.coherence}")
        by_pno: dict[str, object] = {}
        for ln, ml in zip(lines, me.lines):
            tgt = ml.target_node.pno if ml.target_node else (ml.proposed_pno or "-")
            ev = ml.evidence[0].kind if ml.evidence else "-"
            print(f"      · {ln.part_name:<22} base={ln.base_pno or '-':<12} "
                  f"→ {ml.status:<11} action={ml.proposed_action or '-':<7} "
                  f"target={tgt:<12} via={ev} conf={ml.confidence}")
            by_pno[ln.part_name or ""] = ml

        # ── 정답 검증 ──
        print("\n[검증]")
        # 1) Decor,Control Panel → pno_exact, 정답 노드로 매칭
        decor = by_pno.get("Decor,Control Panel")
        ok1 = (
            decor is not None
            and decor.status == "matched"
            and decor.target_node is not None
            and decor.target_node.pno == GT_PNO_EXACT
            and any(e.kind == "pno_exact" for e in decor.evidence)
        )
        print(f"  1) Decor,Control Panel → pno_exact matched {GT_PNO_EXACT}: "
              f"{'PASS' if ok1 else 'FAIL'}")
        fail += 0 if ok1 else 1

        # 2) 하위 트리에 이름이 실재하는 라인들이 최소 1건 이상 matched (canon 포함)
        matched_names = {
            (ln.part_name or "")
            for ln, ml in zip(lines, me.lines)
            if ml.status == "matched"
        }
        ok2 = "Label,Barcode" in matched_names or "Sheet,Steel(STS)" in matched_names
        print(f"  2) 이름 canon 매칭으로도 하위 트리 부품 회수(Label/Sheet 등): "
              f"{'PASS' if ok2 else 'FAIL'} (matched={sorted(matched_names)})")
        fail += 0 if ok2 else 1

        # 3) 음성 대조 — 트리에 없는 부품은 끌어오지 않음(dropped)
        decoy = map_event_to_scope(
            [ChangeLine(event_id=0, seq=1, part_name="Compressor Motor",
                        base_pno="ZZ_NOPE_999", classification="Change")],
            scope,
        )
        ok3 = decoy.lines[0].status in ("dropped", "ambiguous") and \
            decoy.lines[0].target_node is None
        print(f"  3) 음성 대조 'Compressor Motor'(트리 밖) → 미회수: "
              f"{'PASS' if ok3 else 'FAIL'} (status={decoy.lines[0].status})")
        fail += 0 if ok3 else 1

    print("\n" + ("✅ 전체 PASS — 하위 트리 매칭/회수 정상" if fail == 0
                  else f"❌ {fail}건 FAIL"))
    return fail


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
