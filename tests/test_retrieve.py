"""D-012 — retrieve 모듈 unit smoke test.

Postgres + Ollama 의존 — 단위부는 import/RRF/dataclass만 확인(SQLite는 word_similarity/
sparsevec 미지원이라 실제 SQL은 fake 백엔드로만 돈다). parts/sparse SQL의 실제 실행은
``test_parts_channel_live_postgres``(라이브 Postgres 있을 때만, 없으면 skip)에서 검증한다.
"""

from __future__ import annotations

import pytest

from src.db.retrieve import (
    EventHit,
    Hit,
    hybrid_search,
    lexical_search,
    lookup_lines_by_event,
    search_changes,
    search_events,
    semantic_search,
)


def test_module_imports():
    assert callable(semantic_search)
    assert callable(lexical_search)
    assert callable(hybrid_search)
    assert callable(search_changes)
    assert callable(search_events)
    assert callable(lookup_lines_by_event)


def test_event_hit_dataclass_defaults():
    e = EventHit(
        event_id=1,
        base_model="A",
        new_model="B",
        event="Change",
        change_log="모터 AC→BLDC",
        change_reason="BLDC 기능 추가",
        source_ref="change.xlsx",
        file_id=10,
    )
    assert e.score_semantic is None
    assert e.score_lexical is None
    assert e.score_sparse is None
    assert e.score_parts is None
    assert e.score_rrf is None
    assert e.rank_semantic is None
    assert e.rank_lexical is None
    assert e.rank_sparse is None
    assert e.rank_parts is None


def test_hit_dataclass_defaults():
    h = Hit(
        doc_id=1,
        part_no_new="AGG74419321",
        part_name="Packing",
        new_model="W7M",
        event="Change",
        region="EUR",
        form_id="changing_parts_list_96",
        file_id=10,
        embedding_text="narrative",
    )
    assert h.score_semantic is None
    assert h.score_lexical is None
    assert h.score_rrf is None
    assert h.rank_semantic is None
    assert h.rank_lexical is None


def test_rrf_math():
    """RRF 융합: doc이 두 모달리티에 동시 등장하면 점수 합산. 한쪽만이면 단일."""
    from src.db.retrieve import _RRF_K

    # 직접 hybrid_search를 호출하지 않고 RRF 계산식만 검증 (Postgres 없이 가능).
    rrf_k = _RRF_K
    sem_only = 1.0 / (rrf_k + 0 + 1)
    sem_and_lex = 1.0 / (rrf_k + 0 + 1) + 1.0 / (rrf_k + 0 + 1)
    assert sem_and_lex > sem_only
    # 가중치 적용: lex_w=0이면 lexical 기여 제거
    rrf_no_lex = 1.0 / (rrf_k + 0 + 1) + 0.0 / (rrf_k + 0 + 1)
    assert abs(rrf_no_lex - sem_only) < 1e-9
    # parts 채널(4번째 모달리티): rank_parts가 있으면 parts_weight/(k+rank+1) 가산.
    sem_and_parts = 1.0 / (rrf_k + 0 + 1) + 1.0 / (rrf_k + 0 + 1)
    assert sem_and_parts > sem_only


def test_parts_enabled_default_env(monkeypatch):
    """parts 채널 기본 on. SEARCH_INCLUDE_PARTS=0/false면 off."""
    from src.db.retrieve import _parts_enabled_default

    monkeypatch.delenv("SEARCH_INCLUDE_PARTS", raising=False)
    assert _parts_enabled_default() is True
    monkeypatch.setenv("SEARCH_INCLUDE_PARTS", "0")
    assert _parts_enabled_default() is False
    monkeypatch.setenv("SEARCH_INCLUDE_PARTS", "false")
    assert _parts_enabled_default() is False
    monkeypatch.setenv("SEARCH_INCLUDE_PARTS", "1")
    assert _parts_enabled_default() is True


def test_excluded_file_ids_default_env(monkeypatch):
    """SEARCH_EXCLUDE_FILE_IDS 파싱 — 쉼표/세미콜론 구분, 정수만, 중복 제거."""
    from src.db.retrieve import _excluded_file_ids_default

    monkeypatch.delenv("SEARCH_EXCLUDE_FILE_IDS", raising=False)
    assert _excluded_file_ids_default() == []
    monkeypatch.setenv("SEARCH_EXCLUDE_FILE_IDS", "30")
    assert _excluded_file_ids_default() == [30]
    monkeypatch.setenv("SEARCH_EXCLUDE_FILE_IDS", "30, 7 ; x ,30")
    assert _excluded_file_ids_default() == [30, 7]


def test_build_event_filter_excludes_file_ids():
    """exclude_file_ids → NULL-safe NOT IN 절 + 파라미터 바인딩. 빈 목록은 절 없음."""
    from src.db.retrieve import _build_event_filter_sql

    sql, params = _build_event_filter_sql(exclude_file_ids=[30, 7])
    assert "file_id IS NULL OR file_id NOT IN" in sql
    assert params["exf0"] == 30 and params["exf1"] == 7
    sql2, params2 = _build_event_filter_sql(exclude_file_ids=[])
    assert "NOT IN" not in sql2
    assert not any(k.startswith("exf") for k in params2)


def _live_pg_session_or_skip():
    """라이브 Postgres(+pg_trgm) 세션. 미가용이면 테스트 skip(운영 DB 무오염: flush만)."""
    from sqlalchemy import text as _t

    try:
        from src.ui.agent_client import load_agent_env

        load_agent_env()
    except Exception:  # noqa: BLE001 — .env 로더 없으면 환경변수만으로 시도
        pass
    try:
        from src.db.engine import make_engine, session_factory

        s = session_factory(make_engine())()
        s.execute(_t("SELECT 1"))
        s.execute(_t("SELECT word_similarity('a', 'a')"))  # pg_trgm 가용 확인
        return s
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"라이브 Postgres/pg_trgm 미가용 — parts SQL 통합 테스트 skip: {exc}")


def test_parts_channel_live_postgres(monkeypatch):
    """parts 채널 SQL을 실제 Postgres에서 실행 — 부품명이 쿼리에 있으면 그 event를 회수.

    base_model 필터로 격리하고, flush만 하고 finally에서 rollback(운영 데이터 무오염).
    """
    monkeypatch.setenv("ENABLE_EMBEDDING", "0")   # dense 스킵(헤르메틱·빠름)
    monkeypatch.setenv("SEARCH_INCLUDE_PARTS", "1")
    s = _live_pg_session_or_skip()

    from src.db.models import ChangeEvent, ChangeLine

    bm = "ZZTEST_PARTS_ONLY"
    pname = "ZZ_UNIQUE_PARTNAME_XYZ"
    try:
        ev = ChangeEvent(
            base_model=bm, new_model="ZZTEST2", event="Change",
            change_log="테스트 변경", change_reason="테스트 사유",
            raw_text="테스트 변경 테스트 사유", source_ref="pytest_parts.xlsx",
        )
        s.add(ev)
        s.flush()
        s.add(ChangeLine(
            event_id=ev.event_id, seq=1, part_name=pname,
            base_pno="ZZPNO123", new_pno="ZZPNO124", source_ref="pytest_parts.xlsx",
        ))
        s.flush()

        # 부품명이 쿼리에 포함 → parts 채널이 1.0으로 회수. base_model 필터로 우리 event만.
        hits = search_events(
            s, f"테스트 사유 {pname}", top_k=5, base_model=bm, use_sparse=False
        )
        assert len(hits) == 1
        h = hits[0]
        assert h.event_id == ev.event_id
        assert h.rank_parts is not None
        assert h.score_parts is not None and h.score_parts >= 0.5  # 정확 부품명 매칭

        # include_parts=False → parts 채널 비활성(점수 None). 우리 event는 lexical로만 잡히거나 미회수.
        hits_off = search_events(
            s, f"테스트 사유 {pname}", top_k=5, base_model=bm,
            use_sparse=False, include_parts=False,
        )
        assert all(x.score_parts is None and x.rank_parts is None for x in hits_off)
    finally:
        s.rollback()
        s.close()


def test_exclude_file_ids_live_postgres(monkeypatch):
    """exclude_file_ids로 특정 파일 출처가 검색에서 사라지는지 — 라이브 Postgres에서 검증.

    file_id=30("통합 개발부품Master Compact v1.1.xlsx") 한 이벤트의 사유+base_model로
    검색해, 제외 안 하면 등장 / 제외하면 사라짐을 확인. file_id=30 부재 시 skip.
    """
    from sqlalchemy import text as _t

    monkeypatch.setenv("ENABLE_EMBEDDING", "0")
    monkeypatch.delenv("SEARCH_EXCLUDE_FILE_IDS", raising=False)  # env 간섭 차단
    s = _live_pg_session_or_skip()
    try:
        # file_id=30 이벤트는 base_model이 비어 있어 사유 자체로 검색(self-match) — 가장 긴
        # 사유를 골라 변별력↑(코퍼스 전체 top-50 안에 자기 자신이 들어오도록).
        row = s.execute(_t(
            "SELECT change_reason FROM change_event "
            "WHERE file_id = 30 AND change_reason IS NOT NULL AND source_ref IS NOT NULL "
            "ORDER BY length(change_reason) DESC LIMIT 1"
        )).first()
        if row is None:
            pytest.skip("file_id=30 (Compact v1.1) corpus 없음 — 적재/파일ID 확인")
        q = (row.change_reason or "")[:40]
        incl = search_events(s, q, top_k=50, use_sparse=False, exclude_file_ids=[])
        excl = search_events(s, q, top_k=50, use_sparse=False, exclude_file_ids=[30])
        assert any(h.file_id == 30 for h in incl), "제외 안 하면 file_id=30이 등장해야"
        assert all(h.file_id != 30 for h in excl), "제외하면 file_id=30이 사라져야"
    finally:
        s.rollback()
        s.close()
