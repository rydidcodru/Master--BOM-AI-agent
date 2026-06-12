"""PPTX 슬라이드 텍스트/테이블 추출."""
from __future__ import annotations

import re
from pathlib import Path

RELEVANT_KEYWORDS = [
    "변경점", "변경내역", "변경 내역",
    "유첨1", "유첨3", "개발 변경",
    "Base P/No", "BASE P/NO", "base p/no",
    "Part Name", "PART NAME",
]


def _shape_text(shape) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    lines = []
    for p in shape.text_frame.paragraphs:
        run_texts = []
        for r in p.runs:
            try:
                if getattr(r.font, "strike", False):
                    continue
            except Exception:
                pass
            run_texts.append(r.text or "")
        line = "".join(run_texts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _table_to_markdown(table) -> str:
    rows = []
    for row in table.rows:
        cells = [re.sub(r"\s+", " ", cell.text or "").strip() for cell in row.cells]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _is_relevant(title: str, content: str) -> bool:
    blob = f"{title}\n{content}"
    return any(kw.lower() in blob.lower() for kw in RELEVANT_KEYWORDS)


def _has_pno_columns(content: str) -> bool:
    """Base P/No + New P/No 컬럼이 있는 상세 표 슬라이드인지 판단."""
    lower = content.lower()
    has_base = "base p/no" in lower
    has_new = "new p/no" in lower
    has_part = "part name" in lower
    return has_base and (has_new or has_part)


def extract_slides(pptx_path: str | Path) -> list[dict]:
    try:
        from pptx import Presentation
    except ImportError as e:
        raise RuntimeError("python-pptx 필요: pip install python-pptx") from e

    pptx_path = Path(pptx_path)
    prs = Presentation(pptx_path)
    results = []

    for i, slide in enumerate(prs.slides, 1):
        texts, tables = [], []
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                md = _table_to_markdown(shape.table)
                if md:
                    tables.append(md)
            elif getattr(shape, "has_text_frame", False):
                txt = _shape_text(shape)
                if txt:
                    texts.append(txt)

        title = texts[0] if texts else ""
        content = "\n\n".join(texts + tables)

        if not _is_relevant(title, content):
            continue

        results.append({
            "slide": i,
            "title": title,
            "content": content,
            "has_pno": _has_pno_columns(content),
            "source_pptx": pptx_path.name,
        })

    return results


def load_pptx(pptx_path: str | Path) -> list[dict]:
    return extract_slides(pptx_path)