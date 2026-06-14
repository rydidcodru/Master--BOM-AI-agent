"""통제된 recall 테스트 — 내가 만든 정답(가데이터)을 DB에 심고, 내 쿼리로 얼마나 잘 찾나.

설계:
  · base BOM = file 19 (WSED7613S), 모듈 = Controller Assembly,Tact Dial (ACM76198404)
  · 그 모듈 **하위 트리에 실재하는 부품 6개**에 변경이 일어난 가짜 개발마스터
    (change_event + change_line)를 정답으로 DB에 심는다(source_ref='GT_SYNTH*', 끝에 삭제).
    코퍼스 임베딩은 실제 로더와 동일 경로(update_event_embeddings = embed(change_log+reason)).
  · 심은 정답과 **다른 표현(패러프레이즈)**의 쿼리로 검색해, 그 정답 event를 몇 등으로
    회수하는지 측정(recall@1/@5, MRR). 실제 309개 코퍼스 + 나머지 5개 정답이 distractor.
  · 채널 기여를 보려고 3조건: A 사유만(semantic+lexical) / B +부품명(parts 채널) /
    C +부품명+모듈+scope(①.5 구조 부스트 + ②.5 매핑). C는 매핑이 정답 노드로 가는지도 검증.
  · 끝에 GT_SYNTH 행 전부 DELETE(운영 코퍼스 무오염).

실행: ENABLE_EMBEDDING=1 필요(ST bge-m3 캐시됨). 약 1~3분.
"""

from __future__ import annotations

import os

os.environ["ENABLE_EMBEDDING"] = "1"
os.environ["SEARCH_INCLUDE_STRUCT"] = "0"  # A/B는 구조 미사용; C는 scope를 명시 주입

from sqlalchemy import text

from src.agent.intent.structurizer import intent_from_change
from src.agent.matching.scoring import MatchThresholds
from src.agent.orchestrator.backend import DbRetrievalBackend
from src.agent.orchestrator.orchestrate import propose_candidates
from src.agent.orchestrator.structure_scope import build_scope
from src.db.engine import make_engine, session_factory
from src.db.load_change_events import update_event_embeddings
from src.db.models import ChangeEvent, ChangeLine
from src.preprocess.normalize import canonicalize_part_name
from src.ui.agent_client import load_agent_env

load_agent_env()  # .env (SEARCH_EXCLUDE_FILE_IDS 등)
os.environ["ENABLE_EMBEDDING"] = "1"
os.environ["SEARCH_INCLUDE_STRUCT"] = "0"

BASE_MODEL = "WSED7613S"
MODULE = "Controller Assembly,Tact Dial"
TAG = "GT_SYNTH"

# (부품명, 실 하위트리 품번, part_type, 심을 변경내역, 심을 변경사유, 쿼리 변경내역(패러프레이즈), 쿼리 사유(패러프레이즈))
NEEDLES = [
    ("Decor,Control Panel", "MCR68449702", "PD Part",
     "컨트롤 패널 데코 색상 변경", "제품 외관 디자인 통일을 위해 데코 컬러 변경",
     "콘트롤 패널 외관 색상 변경 검토", "디자인 통일 목적의 외관 색 변경"),
    ("Harness,Single", "EAD34822982", "Harness",
     "단선 하네스 커넥터 변경", "접촉 불량 개선을 위해 하네스 커넥터 타입 변경",
     "하네스 커넥터 변경", "접점 신뢰성 개선을 위한 커넥터 교체"),
    ("Label,Barcode", "MEZ68891902", "Printing Material",
     "바코드 라벨 규격 변경", "신규 바코드 표준 적용을 위한 라벨 변경",
     "바코드 라벨 변경", "바코드 규격 표준화 적용"),
    ("LED Display Module", "EAV65042101", "Electronic Assy",
     "LED 디스플레이 모듈 사양 변경", "휘도 개선을 위해 LED 모듈 사양 변경",
     "LED 디스플레이 모듈 변경", "밝기 향상 목적의 디스플레이 모듈 사양 변경"),
    ("PCB Assembly,Sub", "EBR37653301", "PCB",
     "서브 PCB S/W 변경", "기능 추가를 위해 서브 PCB 소프트웨어 변경",
     "서브 PCB 변경", "신규 기능 반영을 위한 서브 기판 소프트웨어 갱신"),
    ("Window,Glass", "MKC67486203", "etc.",
     "윈도우 글라스 재질 변경", "내열성 강화를 위해 글라스 재질 변경",
     "윈도우 글라스 변경", "내열 성능 향상을 위한 유리 소재 변경"),
]


def _cleanup(s) -> int:
    n = s.execute(
        text("SELECT count(*) FROM change_event WHERE source_ref LIKE :p OR source_project='gt_synth'"),
        {"p": f"{TAG}%"},
    ).scalar_one()
    s.execute(text(
        "DELETE FROM change_line WHERE event_id IN "
        "(SELECT event_id FROM change_event WHERE source_ref LIKE :p OR source_project='gt_synth')"
    ), {"p": f"{TAG}%"})
    s.execute(text(
        "DELETE FROM change_event WHERE source_ref LIKE :p OR source_project='gt_synth'"
    ), {"p": f"{TAG}%"})
    s.commit()
    return int(n)


def _plant(s) -> list[int]:
    ids: list[int] = []
    for i, (pname, pno, ptype, log, reason, _qd, _qr) in enumerate(NEEDLES):
        ev = ChangeEvent(
            base_model=BASE_MODEL, new_model=BASE_MODEL, event="Change",
            change_log=log, change_reason=reason,
            raw_text=f"{log} {reason}",  # 로더와 동일: embed 대상
            source_ref=f"{TAG}_{i}", source_project="gt_synth", file_id=None,
        )
        s.add(ev)
        s.flush()
        s.add(ChangeLine(
            event_id=ev.event_id, seq=1, part_name=pname,
            part_name_canon=canonicalize_part_name(pname),
            base_pno=pno, new_pno=f"{pno[:-1]}Z", part_type=ptype,
            classification="Change", changepoint=log, source_ref=f"{TAG}_{i}",
        ))
        ids.append(ev.event_id)
    s.commit()
    return ids


def _rank_of(candidates, needle_id: int) -> int | None:
    for r, c in enumerate(candidates, 1):
        if c.event.event_id == needle_id:
            return r
    return None


def _search(intent, s, backend, scope=None):
    return propose_candidates(
        intent, session=s, backend=backend, write_log=False,
        per_query_top_k=20, max_candidates=20, min_candidates=1, max_reflection=0,
        scope=scope,
    ).candidates


def main() -> int:
    th = MatchThresholds.from_env()
    sf = session_factory(make_engine())
    backend = DbRetrievalBackend(sf)
    fail = 0
    with sf() as s:
        pre = _cleanup(s)
        if pre:
            print(f"(이전 잔여 GT_SYNTH {pre}행 정리)")
        ids = _plant(s)
        print(f"정답 {len(ids)}건 심음(event_id {ids[0]}~{ids[-1]}) · 코퍼스 임베딩 중...")
        update_event_embeddings(s, event_ids=ids)

        n_real = s.execute(text(
            "SELECT count(*) FROM change_event WHERE source_ref IS NOT NULL AND source_ref NOT LIKE :p"
        ), {"p": f"{TAG}%"}).scalar_one()
        print(f"코퍼스: 실제 {n_real}건 + 정답 {len(ids)}건 (distractor 포함)\n")

        scope = build_scope([MODULE], BASE_MODEL, s, th)
        print(f"[①.5] scope 노드 {len(scope.nodes) if scope else 0}개 "
              f"(C 조건 구조 부스트+매핑용)\n")

        stats = {k: {"r1": 0, "r5": 0, "mrr": 0.0} for k in ("A", "B", "C")}
        print(f"{'부품':<20} {'A(사유만)':<10} {'B(+부품명)':<11} {'C(+모듈+구조)':<13} {'②.5매핑'}")
        print("-" * 72)
        for i, (pname, pno, _pt, _l, _r, qd, qr) in enumerate(NEEDLES):
            nid = ids[i]
            # A — 사유/내용만
            iA = intent_from_change(change_detail=qd, change_reason=qr)
            rA = _rank_of(_search(iA, s, backend), nid)
            # B — + 부품명 (parts 채널)
            iB = intent_from_change(change_detail=qd, change_reason=qr, part_name=pname)
            rB = _rank_of(_search(iB, s, backend), nid)
            # C — + 모듈 + base_model + scope (①.5 구조 부스트 + ②.5 매핑)
            iC = intent_from_change(change_detail=qd, change_reason=qr,
                                    part_name=pname, base_model=BASE_MODEL,
                                    module_names=[MODULE])
            candsC = _search(iC, s, backend, scope=scope)
            rC = _rank_of(candsC, nid)

            # ②.5 매핑 — C에서 회수된 정답 후보의 라인이 정답 노드(pno)로 매핑되나
            map_ok = "-"
            if rC is not None:
                cand = candsC[rC - 1]
                ml = cand.mapping.lines[0] if (cand.mapping and cand.mapping.lines) else None
                tgt = (ml.target_node.pno if ml and ml.target_node else None)
                map_ok = "✅" + (tgt or "") if tgt == pno else f"❌{tgt}"

            for key, rk in (("A", rA), ("B", rB), ("C", rC)):
                if rk == 1:
                    stats[key]["r1"] += 1
                if rk is not None and rk <= 5:
                    stats[key]["r5"] += 1
                stats[key]["mrr"] += (1.0 / rk) if rk else 0.0

            def f(r):
                return f"#{r}" if r else "miss"
            print(f"{pname:<20} {f(rA):<10} {f(rB):<11} {f(rC):<13} {map_ok}")
            if rC is None or map_ok.startswith("❌"):
                fail += 1

        n = len(NEEDLES)
        print("\n채널별 집계 (정답 6건 기준):")
        print(f"{'조건':<16} {'recall@1':<10} {'recall@5':<10} {'MRR'}")
        for key, label in (("A", "사유만"), ("B", "+부품명"), ("C", "+모듈+구조")):
            d = stats[key]
            print(f"{key} {label:<13} {d['r1']}/{n} ({d['r1']/n:.0%})  "
                  f"{d['r5']}/{n} ({d['r5']/n:.0%})  {d['mrr']/n:.3f}")

        removed = _cleanup(s)
        print(f"\n정리: GT_SYNTH {removed}행 삭제(운영 코퍼스 복원)")
        remain = s.execute(text(
            "SELECT count(*) FROM change_event WHERE source_ref LIKE :p"
        ), {"p": f"{TAG}%"}).scalar_one()
        print(f"잔여 확인: {remain}행 (0이어야 정상)")
        fail += remain

    print("\n" + ("✅ 전체 PASS — 정답을 상위권으로 회수 + 매핑 정확"
                  if fail == 0 else f"⚠️ 점검 필요 ({fail}건)"))
    return fail


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
