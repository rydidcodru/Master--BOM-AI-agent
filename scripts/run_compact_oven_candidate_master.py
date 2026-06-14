"""Compact Oven PPT → **후보 검색(agentic RAG)** 으로 만든 개발 master → 정답지 채점.

`run_compact_oven_to_master.py`(결정론 base↔new BOM diff)와 대비되는 **예측 경로**:
new BOM(정답 구조)을 보지 않고, PPT 변경 리스트(변경내용+변경사유)만으로 **과거
change_event 코퍼스**를 검색해 유사 과거 부품을 후보로 회수 → master 조립.

핵심 제약(사용자 요구): **정답지(file_id 30)가 검색에 잡히면 안 된다.**
  - SEARCH_EXCLUDE_FILE_IDS=30 게이트가 file_id 30(통합 master v1.1)을 검색에서 제외.
  - 본 하네스는 매 후보의 event.file_id를 검사해 30이 나오면 **즉시 실패**(누수 증거).
  - 추가로 대표 변경 몇 건에 대해 exclude 유/무 검색을 비교해 누수 차단을 *실증*한다.

기대: 후보 품번은 다른 개발(사우디/호주/이집트 등)의 것이라 정답 품번과 일치하지
않는다(정답이 정상 제외되므로). 따라서 exact pno 일치는 낮고, 부품명/모듈 겹침으로
"참고용 후보"의 유용성을 본다. 결정론 base↔new diff와의 격차가 곧 '검색만으로 가능한 한계'.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(_ROOT / ".env"))

# 결정론 모드(기본 on) — structurize/rerank의 LLM 호출을 끈다(검색·임베딩은 유지).
# llm_enabled()는 ANTHROPIC_API_KEY 존재만으로 True가 되므로, 제공자를 ollama로 돌리고
# ENABLE_LLM을 비워 LLM 경로를 비활성화한다(Claude 0콜·빠름·재현가능). CAND_DETERMINISTIC=0이면 풀 파이프라인.
_DETERMINISTIC = os.environ.get("CAND_DETERMINISTIC", "1").strip().lower() not in ("0", "false", "no", "")
if _DETERMINISTIC:
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ.pop("ENABLE_LLM", None)

from src.agent.accuracy import score_rows, load_master_rows  # noqa: E402
from src.agent.docgen.master_writer import MasterRow, write_master_xlsx  # noqa: E402
from src.agent.ppt.extractor import extract_change_review_from_pptx_bytes  # noqa: E402
from src.agent.pipeline import propose_analysis  # noqa: E402
from src.agent.orchestrator.backend import DbRetrievalBackend  # noqa: E402
from src.db.engine import make_engine, session_factory  # noqa: E402

_PROJ = _ROOT.parent
_CO = _PROJ / "Master--BOM-AI-agent" / "agent" / "Compact Oven"
PPT = _PROJ / "NXI 개발 유형 및 등급 확정 심의회 Compact Oven 0404 (1) (1).pptx"
ORACLE = _PROJ / "통합 개발부품Master Compact v1.1.xlsx"
OUT_DIR = _ROOT / "out"
OUT_XLSX = OUT_DIR / "compact_oven_candidate_master.xlsx"
ANSWER_FILE_ID = 30  # 정답지 file_id — 후보에 절대 나오면 안 됨.

# 분류(change_line) → 정답지 어휘.
_CLS_MAP = {"new": "New", "신규": "New", "change": "New", "변경": "New",
            "delete": "Delete", "삭제": "Delete", "common": "Common", "기존": "Common"}


def _to_master_row(ln, reason: str) -> MasterRow:
    base = (ln.base_pno or "").strip()
    new = (ln.new_pno or "").strip()
    cls_raw = (ln.classification or "").strip().lower()
    cls = _CLS_MAP.get(cls_raw, "New")  # 후보=변경 제안이므로 기본 New
    lvl = ""
    if ln.bom_level is not None:
        lvl = "." * int(ln.bom_level) + str(int(ln.bom_level))
    return MasterRow(
        bom_level=lvl, part_type=(ln.part_type or ""), base_pno=base,
        new_pno=(new or "<발번대기>"), part_name=(ln.part_name or ""),
        qty_base=("" if ln.qty_base is None else str(ln.qty_base)),
        qty_new=("" if ln.qty_new is None else str(ln.qty_new)),
        changing_point=(ln.changepoint or "부품 변경"),
        changing_reason=(reason or "-"), supplier=(ln.supplier or "-"),
        classification=cls,
    )


def main() -> int:
    for p in (PPT, ORACLE):
        if not p.exists():
            print(f"[ERR] 입력 없음: {p}", file=sys.stderr)
            return 2
    OUT_DIR.mkdir(exist_ok=True)

    excl = os.environ.get("SEARCH_EXCLUDE_FILE_IDS", "")
    rerank_env = os.environ.get("RERANK_LLM", "0")
    print(f"[cfg] SEARCH_EXCLUDE_FILE_IDS={excl!r} RERANK_LLM={rerank_env!r} "
          f"ENABLE_EMBEDDING={os.environ.get('ENABLE_EMBEDDING')!r}", file=sys.stderr)

    doc = extract_change_review_from_pptx_bytes(PPT.read_bytes())
    changes = doc.get("detailed_changes") or []
    print(f"[ppt] detailed_changes={len(changes)}", file=sys.stderr)

    # 후보 검색 — 결정론 구조화(llm=None) + RERANK_LLM env 따름. 매 후보 file_id 누수검사.
    engine = make_engine()
    Session = session_factory(engine)
    backend = DbRetrievalBackend(Session)

    max_events = int(os.environ.get("CAND_EVENTS_PER_CHANGE", "1"))
    # 같은 검색에서 두 사유 출처를 동시 평가:
    #   ppt   = 심의회 PPT 변경사유(현 master 칸 텍스트)
    #   event = RAG가 회수한 과거 이벤트의 *자기* change_reason (검색 품질 자체)
    rows_ppt: list[MasterRow] = []
    rows_event: list[MasterRow] = []
    seen: set[str] = set()
    leak_hits: list[dict] = []
    per_change: list[dict] = []
    full_pipeline = not _DETERMINISTIC  # CAND_DETERMINISTIC=0 → structurize+rerank(Claude)
    print(f"[mode] {'FULL pipeline (structurize+rerank, Claude)' if full_pipeline else 'deterministic pure-RAG (LLM 0)'}",
          file=sys.stderr)
    with Session() as s:
        for i, ch in enumerate(changes):
            detail = str(ch.get("change_detail") or "").strip()
            reason = str(ch.get("change_reason") or "").strip()  # PPT 사유
            text = (detail + " | " + reason).strip(" |") or detail or reason
            if not text:
                continue
            # 후보 회수 — (event_reason, file_id, event_id, lines) 표준화. lines=None이면 lazy 조회.
            if full_pipeline:
                res = propose_analysis(text, session=s, backend=backend,
                                       min_candidates=1, max_candidates=5)
                cands = [(c.event.change_reason, getattr(c.event, "file_id", None),
                          c.event.event_id, c.lines) for c in res.candidates]
            else:
                hits = backend.search_events(text, top_k=5)  # 순수 RAG(LLM 0콜)
                cands = [(getattr(h, "change_reason", ""), getattr(h, "file_id", None),
                          h.event_id, None) for h in hits]
            # 누수 검사 — 어떤 후보든 정답 file_id면 기록(절대 0이어야).
            for (_r, fid, eid, _l) in cands:
                if fid == ANSWER_FILE_ID:
                    leak_hits.append({"change": i, "event_id": eid, "file_id": fid})
            n_lines = 0
            for (ev_reason_raw, _fid, eid, lines) in cands[:max_events]:
                ev_reason = str(ev_reason_raw or "").strip()  # 과거 이벤트 자기 사유
                line_list = lines if lines is not None else backend.lookup_lines_by_event(eid)
                for ln in line_list:
                    key = ((ln.new_pno or ln.base_pno or ln.part_name or "")).strip().upper()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows_ppt.append(_to_master_row(ln, reason))
                    rows_event.append(_to_master_row(ln, ev_reason))
                    n_lines += 1
            per_change.append({"i": i, "text": text[:60], "n_candidates": len(cands),
                               "lines_taken": n_lines})

    # 채점 — 정답지 대비(변경 유니버스 한정, 부품명 매칭 위주). 행/부품은 동일, 사유만 다름.
    oracle_rows = load_master_rows(str(ORACLE))
    result = score_rows([r.__dict__ for r in rows_ppt], oracle_rows)        # 기존(PPT 사유)
    result_event = score_rows([r.__dict__ for r in rows_event], oracle_rows)  # RAG 이벤트 사유
    rows = rows_event  # 산출 master 기본 = RAG 이벤트 사유본

    # 산출 xlsx도 남김(검수용).
    try:
        OUT_XLSX.write_bytes(write_master_xlsx(rows, str(ORACLE)))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] xlsx write skipped: {e}", file=sys.stderr)

    def _rsim(r):
        d = r["diagnostic"]["changing_reason"]
        return {"mean_similarity": round(d["mean_similarity"] or 0, 3), "method": d["method"], "n": d["n"]}

    report = {
        "path": "candidate_search (PPT→past change_event corpus, NO new BOM)",
        "rerank_llm": rerank_env,
        "leakage": {
            "answer_file_id": ANSWER_FILE_ID,
            "exclude_env": excl,
            "candidate_hits_on_answer_file": len(leak_hits),  # MUST be 0
            "hits": leak_hits[:10],
        },
        "produced_master": {"rows": len(rows), "path": str(OUT_XLSX)},
        "changing_reason_compare": {
            "ppt_reason": _rsim(result),         # master 칸=PPT 심의회 사유
            "event_reason": _rsim(result_event),  # master 칸=RAG 회수 과거 이벤트 사유
        },
        "accuracy": result_event,
        "accuracy_ppt_reason_variant": result,
        "n_changes_searched": len(per_change),
    }
    out_json = OUT_DIR / "compact_oven_candidate_accuracy.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("path", "leakage", "produced_master", "n_changes_searched")},
                     ensure_ascii=False, indent=2))
    a = result
    g = a["gating"]
    print("\n[accuracy vs v1.1 - candidate-search master]")
    print("  recall matched=%d/%d  produced_inscope=%d" % (
        a["row_matching"]["matched"], a["scope"]["oracle_inscope_rows"], a["scope"]["produced_inscope_rows"]))
    print("  base_pno=%.3f new_pno=%.3f classification=%.3f  part_name=%.3f" % (
        g["base_pno"]["acc"] or 0, g["new_pno"]["acc"] or 0, g["classification"]["acc"] or 0,
        a["diagnostic"]["part_name"]["match_rate"] or 0))
    print(f"\n[LEAK CHECK] candidate hits on answer file_id 30 = {len(leak_hits)} (must be 0)", file=sys.stderr)
    print(f"[OK] report={out_json}", file=sys.stderr)
    return 0 if not leak_hits else 3


if __name__ == "__main__":
    raise SystemExit(main())
