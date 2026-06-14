"""accuracy.scorer 단위 테스트 — 합성 produced/oracle 행으로 채점 코어 검증.

xlsx 불필요: score_rows()가 dict 리스트를 직접 받는다.
"""

from __future__ import annotations

from src.agent.accuracy.scorer import (
    load_master_rows,
    score,
    score_rows,
)


def _row(
    base_pno="",
    new_pno="",
    part_name="",
    classification="",
    changing_point="",
    changing_reason="",
    bom_level="",
    part_type="",
    qty_base="",
    _source_row=None,
):
    return {
        "base_pno": base_pno,
        "new_pno": new_pno,
        "part_name": part_name,
        "classification": classification,
        "changing_point": changing_point,
        "changing_reason": changing_reason,
        "bom_level": bom_level,
        "part_type": part_type,
        "qty_base": qty_base,
        "_source_row": _source_row,
    }


# ── (a) 동일 in-scope 행 → gating ~1.0, match_rate 1.0 ────────────────────
def test_identical_inscope_perfect():
    rows = [
        _row("AAA111", "AAA222", "Plate,Upper", "New", "구조 변경", "후면 변경"),
        _row("BBB111", "BBB222", "Sheet,Steel", "Change", "사이즈 축소", "길이 축소"),
        _row("CCC111", "X", "Bracket", "Delete", "부품 삭제", "삭제"),
    ]
    res = score_rows(rows, rows)
    assert res["row_matching"]["match_rate"] == 1.0
    assert res["row_matching"]["matched"] == 3
    assert res["row_matching"]["unmatched_oracle"] == 0
    assert res["row_matching"]["unmatched_produced"] == 0
    assert res["gating"]["base_pno"]["acc"] == 1.0
    assert res["gating"]["classification"]["acc"] == 1.0
    # new_pno 분모는 '실제 new P/No'가 있는 행만(Delete의 X는 제외) → 2행
    assert res["gating"]["new_pno"]["total"] == 2
    assert res["gating"]["new_pno"]["acc"] == 1.0
    assert res["gating"]["overall_gating_acc"] == 1.0
    assert res["errors"] == []


# ── (b) 분류 신규 vs New 정규화 동등 → 매칭 ────────────────────────────────
def test_classification_korean_english_normalize_equal():
    oracle = [_row("AAA111", "AAA222", "Plate", "New")]
    produced = [_row("AAA111", "AAA222", "Plate", "신규")]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 1
    assert res["gating"]["classification"]["acc"] == 1.0
    # 변경/Change, 삭제/Delete, 기존/Common 도 동등
    assert score_rows(
        [_row("B", "C", "n", "변경")], [_row("B", "C", "n", "Change")]
    )["gating"]["classification"]["acc"] == 1.0


# ── (c) 잘못된 new_pno → new_pno acc 패널티, base_pno는 여전히 매칭 ─────────
def test_wrong_new_pno_penalized_base_still_matched():
    oracle = [_row("AAA111", "AAA222", "Plate", "New")]
    produced = [_row("AAA111", "WRONG99", "Plate", "New")]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 1
    assert res["gating"]["base_pno"]["acc"] == 1.0  # base 여전히 정답
    assert res["gating"]["new_pno"]["acc"] == 0.0  # new 틀림
    assert res["gating"]["new_pno"]["total"] == 1
    new_errs = [e for e in res["errors"] if e["type"] == "new_pno"]
    assert len(new_errs) == 1
    assert new_errs[0]["column"] == "new_pno"


# ── (d) base_pno 충돌 → 이분 매칭이 올바른 쌍 매칭(dict overwrite 아님) ─────
def test_base_pno_collision_bipartite_matches_right_pairs():
    # 같은 base_pno가 2번, 서로 다른 new_pno. dict 키였다면 하나가 덮어써짐.
    oracle = [
        _row("DUP000", "NEW_A", "Part Alpha", "New"),
        _row("DUP000", "NEW_B", "Part Beta", "Change"),
    ]
    # produced는 순서를 뒤집어 제공 — 매칭이 이름/분류로 올바른 쌍을 찾아야 함
    produced = [
        _row("DUP000", "NEW_B", "Part Beta", "Change"),
        _row("DUP000", "NEW_A", "Part Alpha", "New"),
    ]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 2
    assert res["row_matching"]["unmatched_oracle"] == 0
    assert res["row_matching"]["unmatched_produced"] == 0
    # 올바른 쌍이면 new_pno 둘 다 정답
    assert res["gating"]["new_pno"]["acc"] == 1.0
    assert res["gating"]["new_pno"]["total"] == 2
    assert res["gating"]["classification"]["acc"] == 1.0
    assert res["gating"]["overall_gating_acc"] == 1.0


def test_base_pno_collision_wrong_newpno_caught():
    # 충돌 그룹에서 한 쪽 new_pno가 틀리면 정확히 그 1건만 패널티.
    oracle = [
        _row("DUP000", "NEW_A", "Part Alpha", "New"),
        _row("DUP000", "NEW_B", "Part Beta", "Change"),
    ]
    produced = [
        _row("DUP000", "NEW_A", "Part Alpha", "New"),
        _row("DUP000", "WRONG", "Part Beta", "Change"),
    ]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 2
    assert res["gating"]["new_pno"]["total"] == 2
    assert res["gating"]["new_pno"]["matched"] == 1  # 한 건만 정답


# ── (e) 정답 행이 produced에 없음 → recall loss(unmatched_oracle=1) ─────────
def test_missing_oracle_row_recall_loss():
    oracle = [
        _row("AAA111", "AAA222", "Plate", "New"),
        _row("ZZZ999", "ZZZ888", "Missing Part", "Change"),
    ]
    produced = [_row("AAA111", "AAA222", "Plate", "New")]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 1
    assert res["row_matching"]["unmatched_oracle"] == 1
    assert res["row_matching"]["unmatched_produced"] == 0
    assert res["row_matching"]["match_rate"] == 0.5  # max(1,2)=2 분모
    rec_errs = [e for e in res["errors"] if e["type"] == "unmatched_oracle"]
    assert len(rec_errs) == 1


def test_extra_produced_row_precision_loss():
    oracle = [_row("AAA111", "AAA222", "Plate", "New")]
    produced = [
        _row("AAA111", "AAA222", "Plate", "New"),
        _row("EXTRA01", "EXTRA02", "Hallucinated", "New"),
    ]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["unmatched_produced"] == 1
    assert res["row_matching"]["unmatched_oracle"] == 0
    prec_errs = [e for e in res["errors"] if e["type"] == "unmatched_produced"]
    assert len(prec_errs) == 1


# ── (f) Common 행은 게이팅 분모에서 제외, 별도 coverage 집계 ───────────────
def test_common_rows_excluded_from_gating():
    oracle = [
        _row("AAA111", "AAA222", "Plate", "New"),
        _row("COM001", "←", "Common Part 1", "Common"),
        _row("COM002", "←", "Common Part 2", "Common"),
        _row("COM003", "←", "Common Part 3", "기존"),  # 한국어 Common
    ]
    produced = [
        _row("AAA111", "AAA222", "Plate", "New"),
        _row("COM001", "←", "Common Part 1", "Common"),
        _row("COM002", "←", "Common Part 2", "Common"),
        # COM003은 produced에서 누락 → carryover coverage 떨어짐(게이팅 무관)
    ]
    res = score_rows(produced, oracle)
    # in-scope는 New 1행뿐 → 게이팅 분모가 Common에 희석되지 않음
    assert res["scope"]["oracle_inscope_rows"] == 1
    assert res["scope"]["produced_inscope_rows"] == 1
    assert res["gating"]["base_pno"]["total"] == 1
    assert res["gating"]["classification"]["total"] == 1
    # Common carryover는 별도 보고
    cov = res["common_carryover_coverage"]
    assert cov["oracle_common_rows"] == 3
    assert cov["produced_common_rows"] == 2
    assert cov["covered"] == 2
    assert abs(cov["coverage"] - (2 / 3)) < 1e-9


# ── new_pno 마커('←','없음','-','X','') = base와 동일(변경 없음) ──────────
def test_new_pno_nochange_markers():
    # 정답 new_pno가 마커류 → '실제 new P/No' 아님 → new_pno 게이팅 분모 제외
    oracle = [
        _row("AAA111", "←", "P", "Change"),
        _row("BBB111", "없음", "Q", "Change"),
        _row("CCC111", "X", "R", "Delete"),
    ]
    res = score_rows(oracle, oracle)
    assert res["gating"]["new_pno"]["total"] == 0  # 실제 new 없음
    assert res["row_matching"]["matched"] == 3
    assert res["gating"]["base_pno"]["acc"] == 1.0


# ── 진단: changing_point 3-class는 분류에서 파생(자유 한글 무시) ───────────
def test_changing_point_3class_from_classification():
    # 변경점 자유 텍스트가 완전히 달라도 분류가 같으면 3-class 일치
    oracle = [_row("AAA111", "AAA222", "P", "New", "사이즈 축소")]
    produced = [_row("AAA111", "AAA222", "P", "New", "하위 부품 변경")]
    res = score_rows(produced, oracle)
    assert res["diagnostic"]["changing_point_3class"]["agreement"] == 1.0


# ── 진단: part_name 토큰셋 >=0.85 매칭 ────────────────────────────────────
def test_part_name_token_set_diagnostic():
    oracle = [_row("AAA111", "AAA222", "Plate,Upper Bending", "New")]
    # 토큰 순서/구분자만 다름 → 토큰셋 동일 → 1.0
    produced = [_row("AAA111", "AAA222", "Upper Plate Bending", "New")]
    res = score_rows(produced, oracle)
    assert res["diagnostic"]["part_name"]["match_rate"] == 1.0


# ── changing_reason 진단: 기본 difflib 메서드 ─────────────────────────────
def test_changing_reason_difflib_method(monkeypatch):
    monkeypatch.delenv("ENABLE_EMBEDDING", raising=False)
    oracle = [_row("AAA111", "AAA222", "P", "New", changing_reason="길이 축소")]
    produced = [_row("AAA111", "AAA222", "P", "New", changing_reason="길이 축소")]
    res = score_rows(produced, oracle)
    assert res["diagnostic"]["changing_reason"]["method"] == "difflib"
    assert res["diagnostic"]["changing_reason"]["mean_similarity"] == 1.0
    assert res["diagnostic"]["changing_reason"]["n"] == 1


# ── 빈 입력 / 양쪽 비었을 때 ──────────────────────────────────────────────
def test_empty_inputs():
    res = score_rows([], [])
    assert res["row_matching"]["match_rate"] == 1.0
    assert res["row_matching"]["matched"] == 0
    assert res["gating"]["overall_gating_acc"] is None
    assert res["scope"]["oracle_inscope_rows"] == 0


# ── score_rows가 MasterRow 객체도 받는지 ──────────────────────────────────
def test_score_rows_accepts_masterrow():
    from src.agent.accuracy.scorer import MasterRow

    oracle = [MasterRow(base_pno="AAA111", new_pno="AAA222", part_name="P", classification="New")]
    produced = [MasterRow(base_pno="AAA111", new_pno="AAA222", part_name="P", classification="New")]
    res = score_rows(produced, oracle)
    assert res["row_matching"]["matched"] == 1
    assert res["gating"]["overall_gating_acc"] == 1.0
