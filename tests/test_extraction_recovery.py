"""추출 실패 보강 회귀 — Best/Better(영어 헤더) 분류·매핑 + change_type 완화.

CIS/동유럽 Best/Better 시트(file 26/27/28)가 0행 추출되던 문제:
  ① 분류기가 시트명 "Best"/"Better"·66col을 못 잡음 → v1_2_template 시그니처 확장.
  ② column_dictionary에 영어 헤더 'Changing Point'/'Changing Reason' 별칭 부재 → 추가.
  ③ '구분' 컬럼이 부품카테고리(Assy/단품)인 양식에서 change_type 매핑 실패 → quarantine→set_null.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.preprocess.column_dict import load_column_dictionary

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_english_changing_aliases_mapped():
    cd = load_column_dictionary()
    assert cd.lookup("Changing Point") == "change_point"
    assert cd.lookup("Changing Reason") == "change_reason"
    # 기존 한글 별칭 비파괴
    assert cd.lookup("변경점") == "change_point"
    assert cd.lookup("변경 사유") == "change_reason"
    # 영문 기존(Change Point/Reason)도 유지
    assert cd.lookup("Change Point") == "change_point"


def test_change_type_onfail_relaxed_to_set_null():
    norm = yaml.safe_load((CONFIG / "normalization.yaml").read_text(encoding="utf-8"))
    # change_type 매핑 실패가 행 통째 quarantine을 유발하지 않도록 set_null (검색키 아님).
    assert norm["fields"]["change_type"]["on_fail"] == "set_null"


def test_v1_2_signature_covers_bare_best_better():
    sigs = yaml.safe_load((CONFIG / "form_signatures.yaml").read_text(encoding="utf-8"))
    cfg = sigs["forms"]["v1_2_template_59"]
    pats = cfg["sheet_name_patterns"]
    assert "^Best$" in pats and "^Better$" in pats and "^Good$" in pats
    lo, hi = cfg["max_col_range"]
    assert lo <= 66 <= hi  # 66col Best/Better 포함
