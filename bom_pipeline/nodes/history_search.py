"""
과거 변경이력 검색 노드.

[경로 1] base_part_no 있음
  → SQL 직접 조회 → master_id 기준 케이스 그룹핑

[경로 2] base_part_no 없음
  → FTS5 + 임베딩 하이브리드 검색 → 케이스 그룹핑

공통:
  → LLM: top-5 케이스 선정 + 연동부품 판단
  → history_candidates 채움
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from langchain_openai import ChatOpenAI
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import fetch_history_by_part_no, hybrid_search, fts_search
from state import BOMPipelineState

# 체결/라벨류 제외 키워드
_SKIP_KEYWORDS = {
    "nut", "screw", "washer", "bolt", "rivet", "label", "barcode",
    "rating", "carton", "warranty", "card", "manual", "bag", "tape",
    "clip", "pin", "ring", "seal", "gasket", "film", "band",
}

# ── LLM 프롬프트 ──────────────────────────────────────────────────────────

_SYSTEM = """당신은 LG전자 오븐/주방가전 BOM 변경이력 분석 전문가입니다.

현재 변경점 하나와 과거 변경 케이스 목록이 주어집니다.
아래 두 가지를 한 번에 수행하세요:

1. 현재 변경점과 가장 유사한 과거 케이스 top-5 선정 및 선정이유 작성
2. 각 케이스에서 현재 변경점 목록에 누락됐을 가능성이 있는 연동부품 판단

반환 형식 (JSON 배열, 설명 없이):
[
  {{
    "master_id": 1,
    "rank": 1,
    "select_reason": "동일한 Motor 교체 패턴, 동시에 Fan/Bracket 변경된 사례",
    "linked_parts": [
      {{
        "part_name": "Fan Assembly",
        "base_part_no": "5901W1E002G",
        "new_part_no": "ADP75673301",
        "change_type": "변경",
        "relevance_reason": "Motor 교체 시 Fan도 함께 변경되는 패턴"
      }}
    ]
  }}
]

규칙:
- rank는 1(가장 유사)~5
- [직접이력] 케이스는 동일 품번의 과거 변경이므로 우선 고려할 것
- [유사패턴] 케이스는 변경 내용이 유사한 간접 참고 사례
- select_reason: 현재 변경점과 유사한 이유 1문장 (직접이력/유사패턴 여부 명시)
- linked_parts: 현재 change_points 목록에 없는 부품 중 이번에도 필요할 것 같은 것만
- 체결류(Nut, Screw, Washer), 회로용 라벨(Label,Barcode / Label,Rating / Label,Circuit) 은 linked_parts 제외
- 단, 치수 변경 / 모델명 변경이 포함된 케이스라면 포장·서비스 부품도 반드시 검토할 것:
  Box, Label,Carton, Manual,Service, Packing,Gasket, Non Prod,Parts Assembly,SVC 등이
  케이스 내에 존재한다면 linked_parts에 포함하고 relevance_reason에 "치수/모델 변경 시 연동 교체 필요" 명시
- 후보 케이스 5개 미만이면 있는 것만 반환
- linked_parts 없으면 빈 배열 []"""

_HUMAN = """=== 현재 변경점 ===
부품명: {part}
변경내역: {change_detail}
변경사유: {change_reason}
변경유형: {change_type}
Base P/No: {base_part_no}

=== 현재 변경점 목록 (이미 포함된 부품들) ===
{current_parts_text}

=== 과거 케이스 후보 ===
{case_list_text}

top-5 케이스를 선정하고 연동부품을 판단해주세요."""

_chain = None


def _is_rate_limit(exc: BaseException) -> bool:
    msg = str(exc)
    return "429" in msg or "rate_limit_exceeded" in msg or "Rate limit" in msg


_llm_retry = retry(
    retry=retry_if_exception(_is_rate_limit),
    wait=wait_exponential(multiplier=1, min=3, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


def _get_chain():
    global _chain
    if _chain is None:
        llm = ChatOpenAI(model="gpt-4o", temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
            ("human", _HUMAN),
        ])
        _chain = prompt | llm | JsonOutputParser()
    return _chain


def _safe_parse(raw) -> list[dict]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("items", "results", "data"):
            if isinstance(raw.get(k), list):
                return raw[k]
    if isinstance(raw, str):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return []


# ── 임베딩 생성 ───────────────────────────────────────────────────────────

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client


def _make_embedding(text: str) -> list[float]:
    resp = _get_openai().embeddings.create(
        model="text-embedding-3-small",
        input=[text],
    )
    return resp.data[0].embedding


# ── 케이스 그룹핑 ─────────────────────────────────────────────────────────

def _is_skip(part_name: str) -> bool:
    lower = part_name.lower()
    return any(k in lower for k in _SKIP_KEYWORDS)


def _group_by_master(rows: list[dict]) -> dict[int, dict]:
    """
    DB 조회 결과를 master_id 기준으로 그룹핑.
    반환: {master_id: {meta, parts: [...]}}
    """
    groups: dict[int, dict] = {}
    for r in rows:
        mid = r["master_id"]
        if mid not in groups:
            groups[mid] = {
                "master_id":  mid,
                "base_model": r.get("base_model", ""),
                "new_model":  r.get("new_model", ""),
                "source_file": r.get("source_file", ""),
                "search_path": r.get("search_path", "hybrid"),
                "parts": [],
            }
        if r.get("changing_point") or r.get("base_part_no"):
            groups[mid]["parts"].append({
                "part_name":      r.get("part_name", ""),
                "base_part_no":   r.get("base_part_no", ""),
                "new_part_no":    r.get("new_part_no", ""),
                "changing_point": r.get("changing_point", ""),
                "changing_reason": r.get("changing_reason", ""),
                "bom_level":      r.get("bom_level", ""),
            })
    return groups


def _format_case_list(groups: dict[int, dict], current_pnos: set[str]) -> str:
    lines = []
    for mid, g in groups.items():
        path_tag = "[직접이력]" if g.get("search_path") in ("exact", "prefix") else "[유사패턴]"
        lines.append(
            f"{path_tag} [케이스 master_id={mid}] "
            f"{g['base_model']} → {g['new_model']} | {g['source_file']}"
        )
        relevant = [
            p for p in g["parts"]
            if not _is_skip(p["part_name"])
            and p["part_name"]
        ]
        if not relevant:
            lines.append("  (변경 부품 없음)")
        for p in relevant[:20]:
            marker = " *" if p["base_part_no"] not in current_pnos else ""
            lines.append(
                f"  [{p['bom_level']}] {p['part_name']:30s} "
                f"{p['base_part_no']:15s} → {p['new_part_no']:15s} "
                f"| {p['changing_point'][:30]}{marker}"
            )
        lines.append("")
    return "\n".join(lines)


def _format_current_parts(change_points: list[dict]) -> str:
    lines = []
    for cp in change_points:
        pno = cp.get("base_part_no", "")
        if pno:
            lines.append(f"  {pno:18s} | {cp.get('part', '')}")
    return "\n".join(lines) if lines else "  (없음)"


# ── 노드 ──────────────────────────────────────────────────────────────────

def _search_one(
    i: int,
    cp: dict,
    current_pnos: set[str],
    change_points: list[dict],
) -> tuple[int, dict]:
    """change_point 1개에 대해 검색 + LLM 처리. (병렬 실행용)"""
    base_pno      = cp.get("base_part_no", "")
    part          = cp.get("part", "")
    change_detail = cp.get("change_detail", "")
    change_reason = cp.get("change_reason", "")

    print(f"[history_search] [{i}] {part} | {change_detail[:40]}")

    # ── STEP A: 후보 이력 확보 ──────────────────────────────────────────
    seen_ids: set[int] = set()
    rows: list[dict] = []

    if base_pno:
        path1_rows = fetch_history_by_part_no(base_pno)
        exact_cnt  = sum(1 for r in path1_rows if r.get("search_path") == "exact")
        prefix_cnt = sum(1 for r in path1_rows if r.get("search_path") == "prefix")
        print(f"  [{i}] 경로1 (SQL): {len(path1_rows)}건 (exact={exact_cnt} / prefix={prefix_cnt})")
        for r in path1_rows:
            seen_ids.add(r["detail_id"])
        rows.extend(path1_rows)

    query_text = f"{part} {change_detail} {change_reason}".strip()
    try:
        query_emb  = _make_embedding(query_text)
        path2_rows = hybrid_search(query_text, query_emb, top_k=30)
    except Exception as e:
        print(f"  [{i}] [WARNING] 임베딩 생성 실패: {e}")
        path2_rows = fts_search(query_text, limit=30)
    for r in path2_rows:
        if r["detail_id"] not in seen_ids:
            seen_ids.add(r["detail_id"])
            r["search_path"] = "hybrid"
            rows.append(r)
    print(f"  [{i}] 경로2 (hybrid): {len(path2_rows)}건 | 합산 {len(rows)}건")

    if not rows:
        print(f"  [{i}] → 이력 없음, 스킵")
        cp["history_candidates"] = []
        return i, cp

    # ── STEP B: 케이스 그룹핑 ────────────────────────────────────────
    groups = _group_by_master(rows)
    print(f"  [{i}] → {len(groups)}개 케이스 그룹")

    # ── STEP C: LLM top-5 선정 + 연동부품 판단 ───────────────────────
    try:
        raw = _llm_retry(_get_chain().invoke)({
            "part":               part,
            "change_detail":      change_detail,
            "change_reason":      change_reason,
            "change_type":        cp.get("change_type", ""),
            "base_part_no":       base_pno,
            "current_parts_text": _format_current_parts(change_points),
            "case_list_text":     _format_case_list(groups, current_pnos),
        })
        results = _safe_parse(raw)
    except Exception as e:
        print(f"  [{i}] [WARNING] LLM 호출 실패: {e}")
        results = []

    # ── STEP D: history_candidates 구성 ──────────────────────────────
    candidates = []
    for r in results[:5]:
        mid  = r.get("master_id")
        meta = groups.get(mid, {})
        case_parts = [
            {
                "part_name":       p.get("part_name", ""),
                "base_part_no":    p.get("base_part_no", ""),
                "new_part_no":     p.get("new_part_no", ""),
                "bom_level":       p.get("bom_level", ""),
                "changing_point":  p.get("changing_point", ""),
                "changing_reason": p.get("changing_reason", ""),
            }
            for p in meta.get("parts", [])
            if not _is_skip(p.get("part_name", "")) and p.get("part_name")
        ]
        linked = [
            {
                "part_name":        lp.get("part_name", ""),
                "base_part_no":     lp.get("base_part_no", ""),
                "new_part_no":      lp.get("new_part_no", ""),
                "change_type":      lp.get("change_type", ""),
                "relevance_reason": lp.get("relevance_reason", ""),
            }
            for lp in r.get("linked_parts", [])
            if not _is_skip(lp.get("part_name", ""))
        ]
        candidates.append({
            "master_id":    mid,
            "base_model":   meta.get("base_model", ""),
            "new_model":    meta.get("new_model", ""),
            "source_file":  meta.get("source_file", ""),
            "rank":         r.get("rank", len(candidates) + 1),
            "select_reason": r.get("select_reason", ""),
            "case_parts":   case_parts,
            "linked_parts": linked,
        })

    linked_total = sum(len(c["linked_parts"]) for c in candidates)
    print(f"  [{i}] → top-{len(candidates)}개 케이스 | 연동부품 후보 {linked_total}개")

    cp["history_candidates"] = candidates
    return i, cp


def history_search_node(state: BOMPipelineState) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    change_points = state.get("change_points", [])
    if not change_points:
        return {"change_points": []}

    current_pnos: set[str] = {
        cp.get("base_part_no", "")
        for cp in change_points
        if cp.get("base_part_no")
    }

    # 최대 8개 스레드로 병렬 처리 (OpenAI API rate limit 고려)
    max_workers = min(8, len(change_points))
    results_map: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_search_one, i, cp, current_pnos, change_points): i
            for i, cp in enumerate(change_points)
        }
        for future in as_completed(futures):
            try:
                idx, updated_cp = future.result()
                results_map[idx] = updated_cp
            except Exception as e:
                idx = futures[future]
                print(f"[history_search] [{idx}] [ERROR] {e}")
                cp = change_points[idx]
                cp["history_candidates"] = []
                results_map[idx] = cp

    # 원래 순서로 복원
    updated = [results_map[i] for i in range(len(change_points))]
    return {"change_points": updated}
