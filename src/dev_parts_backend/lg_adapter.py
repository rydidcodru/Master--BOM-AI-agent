"""[임시 어댑터] sqlite /changes/recommend → lg change_event(Postgres·BGE-M3) 검색.

env ``RECOMMEND_BACKEND=lg`` 일 때만 활성(기본 off). 사유가 풍부한 lg change_event
코퍼스에서 dense(reason)+parts로 후보를 회수해 sqlite와 동일한 형태로 돌려준다.
프론트/응답 계약은 그대로(부품 카드의 '과거 참조' 그대로 렌더).

lg 의존(src.db.*, BGE-M3 Ollama, Postgres)은 호출 시 지연 로딩하고 lg .env를 읽는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# lg 파이프라인 위치(필요시 env로 덮어쓰기).
_LG_ROOT = Path(os.environ.get(
    "LG_PIPELINE_DIR",
    r"C:\Users\keogh8526\LG_Data_pipeline\lg_data_pipeline",
))

_Session = None  # lazy singleton
_FILE_NAMES: dict[int, str] = {}  # file_id -> 출처 파일명 (source_files)


def _setup():
    global _Session, _FILE_NAMES
    if _Session is not None:
        return _Session
    if str(_LG_ROOT) not in sys.path:
        sys.path.insert(0, str(_LG_ROOT))  # 'src' 패키지가 lg를 가리키도록 최우선
    from dotenv import load_dotenv
    load_dotenv(_LG_ROOT / ".env")          # POSTGRES_*, OLLAMA_HOST 등
    os.environ["ENABLE_EMBEDDING"] = "1"     # BGE-M3 쿼리 임베딩 on
    from sqlalchemy import text as _sql  # type: ignore
    from src.db.engine import make_engine, session_factory  # type: ignore
    eng = make_engine()
    _Session = session_factory(eng)
    # 출처 파일명 맵(file_id → file_name). 컬럼명은 환경마다 달라 자동 탐지.
    try:
        with eng.connect() as c:
            cols = [r[0] for r in c.execute(_sql(
                "select column_name from information_schema.columns where table_name='source_files'"))]
            namecol = next((x for x in cols if "name" in x.lower()), None)
            if namecol:
                for r in c.execute(_sql(f"select file_id, {namecol} from source_files")):
                    if r[0] is not None:
                        _FILE_NAMES[int(r[0])] = str(r[1])
    except Exception:  # noqa: BLE001 — 출처 맵 실패해도 검색은 동작
        pass
    return _Session


def _txt(*vals: Any) -> str:
    return " ".join(str(v).strip() for v in vals if v and str(v).strip())


def _to_candidate(h: Any, ln: Any) -> dict[str, Any]:
    lvl = getattr(ln, "bom_level", None)
    cp = getattr(ln, "changepoint", None) or getattr(ln, "changing_point", None)
    fid = getattr(h, "file_id", None)
    source_file = _FILE_NAMES.get(int(fid)) if fid is not None else None
    source_file = source_file or getattr(h, "source_ref", None)
    return {
        "detail_id": int(h.event_id) * 10000 + int(getattr(ln, "seq", 0) or 0),
        "master_id": fid,
        "source_file": source_file,  # 출처 파일(스코어 옆 표시용)
        "part_name": getattr(ln, "part_name", None),
        "base_part_no": getattr(ln, "base_pno", None),
        "new_part_no": getattr(ln, "new_pno", None),
        "change_point": cp or getattr(h, "change_log", None),
        "change_reason": getattr(h, "change_reason", None) or getattr(h, "change_log", None),
        "level": (int(lvl) if lvl is not None else None),
        "bom_level": ("" if lvl is None else str(lvl)),
        "has_change_text": True,
        "classification": getattr(ln, "classification", None),
        "past_model": {
            "base_model": getattr(h, "base_model", None),
            "new_model": getattr(h, "new_model", None),
            "project_name": getattr(h, "event", None),
            "source_file": source_file, "source_sheet": None, "region": None,
        },
        "score": float(getattr(h, "score_semantic", None) or getattr(h, "score_rrf", 0) or 0),
        "score_reasons": ["lg change_event 사유 매칭(dense)"],
        "score_components": {
            "semantic": round(float(getattr(h, "score_semantic", 0) or 0), 4),
            "rrf": round(float(getattr(h, "score_rrf", 0) or 0), 6),
        },
    }


def recommend_via_lg(change: dict[str, Any], *, limit: int = 8) -> dict[str, Any]:
    """change(부품명·변경점·변경사유) → lg change_event 후보(같은 응답 형태)."""
    Session = _setup()
    from src.db.retrieve import lookup_lines_by_event, search_events  # type: ignore

    detail = change.get("change_point") or change.get("changing_point") or ""
    reason = change.get("change_reason") or change.get("changing_reason") or ""
    pname = change.get("part_name") or change.get("module_name") or ""
    query = _txt(detail, reason, pname) or _txt(pname) or _txt(detail, reason)

    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    if query:
        with Session() as s:
            hits = search_events(
                s, query, top_k=max(limit + 6, 14),
                semantic_weight=1.0, lexical_weight=0.0, sparse_weight=0.0, parts_weight=1.0,
                include_parts=True, use_sparse=False,
                # exclude_file_ids=[] → 제외 없이 전체 코퍼스 검색. (사용자: Compact(file 30)는
                # 다른 베이스 모델로 테스트하므로 유지해도 무방.) 특정 정답지 제외가 필요하면
                # 여기에 file_id 리스트를 주거나 None으로 두어 lg .env 게이트를 따르게 한다.
                exclude_file_ids=[],
            )
            hits = [h for h in hits if (getattr(h, "score_rrf", 0) or 0) > 0]
            for h in hits:
                taken = 0
                for ln in lookup_lines_by_event(s, h.event_id):
                    key = str(getattr(ln, "new_pno", None) or getattr(ln, "base_pno", None)
                              or getattr(ln, "part_name", None) or "").strip().upper()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    cands.append(_to_candidate(h, ln))
                    taken += 1
                    if taken >= 3:  # 이벤트(사유)당 최대 3개 → 여러 사유로 다양하게
                        break
                if len(cands) >= limit * 2:  # 넉넉히 모은 뒤 score로 정렬
                    break

    cands.sort(key=lambda c: (c.get("score") or 0.0), reverse=True)
    return {
        "change_id": change.get("change_id"),
        "input": change,
        "lookup_mode": "lg_change_event(dense+parts)",
        "candidates": cands[:limit],
    }
