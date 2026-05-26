"""파서 공통 유틸 — 파일 해싱, 값 정리, BOM 깊이 파싱 등."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def file_sha256(path: str | Path) -> str:
    """파일의 SHA256 해시 (source_files.file_hash, 중복 적재 방지용)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_cell(v: Any) -> Any:
    """엑셀 셀 값 정리.
    - None은 그대로
    - 문자열: strip, NBSP/탭/개행 정리, 빈 문자열은 None
    - 숫자: 그대로
    """
    if v is None:
        return None
    if isinstance(v, str):
        s = v.replace("\xa0", " ").replace("\u3000", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s if s else None
    return v


def clean_header(v: Any) -> str | None:
    """헤더 셀 정리: 줄바꿈/공백 압축, 끝의 (영문 부연) 제거 같은 정규화.

    예) '부품명\\nClass Desc.(Part Name)' → '부품명 Class Desc.'
    """
    if v is None:
        return None
    s = str(v).replace("\xa0", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s if s else None


def parse_bom_depth(level_raw: Any) -> int | None:
    """'.1', '..2', '...3' → 1, 2, 3. 점 개수 = 깊이."""
    if level_raw is None:
        return None
    s = str(level_raw).strip()
    if not s:
        return None
    m = re.match(r"^(\.+)", s)
    if m:
        return len(m.group(1))
    # 숫자만 있으면 그 자체가 깊이일 수도 (Group D 케이스)
    if s.isdigit():
        return int(s)
    return None


def parse_int(v: Any) -> int | None:
    """안전한 int 변환. 실패하면 None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        try:
            return int(v)
        except (ValueError, OverflowError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def normalize_yn(v: Any) -> str | None:
    """○/●/X/-/Y/N 등의 표기를 단일 문자로 정규화.
    ○, ●, O, o, Y, y → 'O'
    X, x, N, n → 'X'
    -, 공백 → None
    그 외 → 원본 첫 글자
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    first = s[0]
    if first in ("○", "●", "O", "o", "Y", "y", "◯", "◎"):
        return "O"
    if first in ("X", "x", "N", "n", "✕", "✗", "×"):
        return "X"
    return first[:1]


def is_empty_row(row: tuple) -> bool:
    """행이 사실상 비어있는지 (None과 공백문자열만 있으면 빈 행)."""
    return all(clean_cell(v) is None for v in row)
