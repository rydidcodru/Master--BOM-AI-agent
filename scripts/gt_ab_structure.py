"""A/B 성능비교 — 기존(사유+부품) 검색 vs +하위트리 구조 부스트.

설계(구조 부스트가 의미 있는 상황 = 같은 사유가 여러 모듈에 걸쳐 사유만으론 모듈을
못 가릴 때, 게다가 '혼동' 후보가 사유로는 더 그럴듯할 때):
  · 모듈 M = Controller Assembly,Tact Dial (file 19, WSED7613S)
  · 정답 6건: change_line 부품이 **M 하위 트리 안**. 사유는 쿼리의 **약한 변형**(사유 매칭 낮음).
  · 혼동 6건: 부품이 **M 밖**(Cavity/Cover/Hinge). 사유는 쿼리와 **동일**(사유 매칭 높음 — 함정).
  · 코퍼스 오염 방지용 고유 토큰(ZZABTEST)으로 실 코퍼스와 안 섞이게.
  · 쿼리에 부품명 미포함(구조 신호만 변별하도록). 임베딩 off(동일 사유라 dense 변별 0).
A(기존): SEARCH_INCLUDE_STRUCT off → 혼동이 사유로 위에 옴(정답 묻힘).
B(구조): scope 주입 → 정답(부품 ∈ M 하위트리)이 부스트로 상승.
정답 12건 태깅 후 측정·삭제(운영 무오염).
"""

from __future__ import annotations

import os

os.environ["ENABLE_EMBEDDING"] = "0"  # 동일 사유 → dense 변별 0, 빠르게 lexical+구조만

from sqlalchemy import text

from src.agent.intent.structurizer import intent_from_change
from src.agent.matching.scoring import MatchThresholds
from src.agent.orchestrator.backend import DbRetrievalBackend
from src.agent.orchestrator.orchestrate import propose_candidates
from src.agent.orchestrator.structure_scope import build_scope
from src.db.engine import make_engine, session_factory
from src.db.models import ChangeEvent, ChangeLine
from src.preprocess.normalize import canonicalize_part_name
from src.ui.agent_client import load_agent_env

load_agent_env()
os.environ["ENABLE_EMBEDDING"] = "0"

TAG = "ZZABTEST"
BM = "WSED7613S"
MODULE = "Controller Assembly,Tact Dial"
Q_REASON = f"{TAG} 하위 구성 변경에 따른 품번 변경"   # 혼동이 그대로 사용(강한 사유 매칭)
POS_REASON = f"{TAG} 구성 관련 수정 진행"             # 정답은 약한 변형(사유 매칭 낮음)

POS = [("Bag,Envelope", "MAF00108301"), ("Connector,Wafer", "6630A90004B"),
       ("Controller Assembly,Sub", "ACM76203204"), ("Decor,Control Panel", "MCR68450803"),
       ("Flux", "7245ZB0004A"), ("Harness,Single", "EAD34822982")]
CONF = [("Bracket,Hinge", "MAZ67870702"), ("Cavity Assembly,Coating", "ABU74574508"),
        ("Cavity Assembly,welding", "ABU74574408"), ("Cover Assembly,Top", "ACQ30627901"),
        ("Cover,Base", "MCK71658301"), ("Cover,Heater", "MCK71660201")]


def _cleanup(s) -> None:
    s.execute(text("DELETE FROM change_line WHERE source_ref LIKE :p"), {"p": f"{TAG}%"})
    s.execute(text("DELETE FROM change_event WHERE source_ref LIKE :p"), {"p": f"{TAG}%"})
    s.commit()


def _plant(s, parts, reason, kind) -> dict[int, str]:
    out: dict[int, str] = {}
    for i, (pname, pno) in enumerate(parts):
        ev = ChangeEvent(base_model=BM, new_model=BM, event="Change",
                         change_log=reason, change_reason=reason, raw_text=reason,
                         source_ref=f"{TAG}_{kind}_{i}", source_project="gt_ab", file_id=None)
        s.add(ev); s.flush()
        s.add(ChangeLine(event_id=ev.event_id, seq=1, part_name=pname,
                         part_name_canon=canonicalize_part_name(pname),
                         base_pno=pno, new_pno=pno, classification="Change",
                         changepoint="품번 변경", source_ref=f"{TAG}_{kind}_{i}"))
        out[ev.event_id] = kind
    s.commit()
    return out


def _ranks(cands, kind_of):
    """fused 후보에서 정답/혼동의 순위(1-based) 목록."""
    pos, conf = [], []
    for r, c in enumerate(cands, 1):
        k = kind_of.get(c.event.event_id)
        if k == "pos":
            pos.append(r)
        elif k == "conf":
            conf.append(r)
    return pos, conf


def _summ(pos, conf, n=6):
    top = lambda rs: sum(1 for r in rs if r <= n)
    mean = lambda rs: round(sum(rs) / len(rs), 1) if rs else None
    return f"정답 top{n}={top(pos)}/6 평균순위={mean(pos)}  |  혼동 top{n}={top(conf)}/6 평균순위={mean(conf)}"


def main() -> int:
    th = MatchThresholds.from_env()
    sf = session_factory(make_engine())
    backend = DbRetrievalBackend(sf)
    with sf() as s:
        _cleanup(s)
        kind_of = {**{e: "pos" for e in _plant(s, POS, POS_REASON, "pos")},
                   **{e: "conf" for e in _plant(s, CONF, CONF and Q_REASON, "conf")}}
        intent = intent_from_change(change_detail="구성 변경", change_reason=Q_REASON,
                                    base_model=BM, module_names=[MODULE])
        scope = build_scope([MODULE], BM, s, th)
        print(f"정답 6 + 혼동 6 심음. scope 노드={len(scope.nodes) if scope else 0}")
        print(f"쿼리 사유='{Q_REASON}' (혼동과 동일 / 정답은 약한 변형)\n")

        kw = dict(session=s, backend=backend, write_log=False, dedup_by_reason=False,
                  min_candidates=1, max_reflection=0, per_query_top_k=40, max_candidates=40)

        os.environ["SEARCH_INCLUDE_STRUCT"] = "0"
        a = propose_candidates(intent, scope=None, **kw)
        pa, ca = _ranks(a.candidates, kind_of)

        os.environ["SEARCH_INCLUDE_STRUCT"] = "1"
        b = propose_candidates(intent, scope=scope, **kw)
        pb, cb = _ranks(b.candidates, kind_of)

        print("A 기존(사유+부품, 구조 off):", _summ(pa, ca))
        print("B 구조 부스트(+하위트리)   :", _summ(pb, cb))
        # 정답이 혼동보다 위에 오나(평균순위 작을수록 위).
        def gap(p, c):
            mp = sum(p) / len(p) if p else 99
            mc = sum(c) / len(c) if c else 99
            return mc - mp  # +면 정답이 혼동보다 위
        print(f"\n정답-혼동 순위 우위(클수록 정답이 위): A={gap(pa, ca):+.1f}  →  B={gap(pb, cb):+.1f}")
        _cleanup(s)
        remain = s.execute(text("SELECT count(*) FROM change_event WHERE source_ref LIKE :p"),
                           {"p": f"{TAG}%"}).scalar_one()
        print(f"정리: 잔여 {remain}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
