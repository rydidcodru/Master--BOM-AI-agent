# build_rag.py
# 실행: python build_rag.py
# 필요: pip install pandas openpyxl chromadb python-dotenv openai tqdm

from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
import json

import pandas as pd
from dotenv import load_dotenv
from openai import AzureOpenAI
from tqdm import tqdm
import math
import tiktoken
import hashlib

import chromadb
from chromadb.config import Settings

from doc_packaging import make_index_docs_l1_chunks

# =========================
# DEBUG BUFFER (한 번에 보기)
# =========================
DEBUG = True
_DEBUG_LOG: list[str] = []

def dbg(msg: str):
    if DEBUG:
        _DEBUG_LOG.append(str(msg))

def dbg_flush():
    if DEBUG and _DEBUG_LOG:
        print("\n" + "="*90)
        print("[DEBUG DUMP]")
        print("\n".join(_DEBUG_LOG))
        print("="*90 + "\n")

# -----------------------------
# 경로/컬렉션 (현재 파일 기준)
# -----------------------------

load_dotenv(Path(__file__).resolve().parent / ".env")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data\history"
CHROMA_DIR = Path(os.getenv("HARA_CHROMA_DIR", str(BASE_DIR / "chroma_db")))
COLLECTION_NAME = "dev_part_master"

def sanitize_meta(meta: dict) -> dict:
    """
    Chroma metadatas는 값이 str/int/float/bool만 허용.
    dict/list 등은 JSON 문자열로 변환하고 None은 ""로 치환.
    """
    clean = {}
    for k, v in (meta or {}).items():
        if v is None:
            clean[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            clean[k] = v
        else:
            # dict/list 등은 문자열로 바꿔서 넣기
            clean[k] = json.dumps(v, ensure_ascii=False)
    return clean

# -----------------------------
# AOAI 임베딩
# -----------------------------
def get_aoai_client() -> Tuple[AzureOpenAI, str]:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    api_key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
    api_version = (os.getenv("AZURE_OPENAI_API_VERSION") or "").strip()
    embed_dep = (os.getenv("AZURE_DEPLOYMENT_TEXT_EMBEDDING_3_SMALL") or "").strip()

    if not (api_key and endpoint and api_version and embed_dep):
        raise RuntimeError("AOAI env 누락: AZURE_OPENAI_API_KEY/ENDPOINT/API_VERSION/DEPLOYMENT_TEXT_EMBEDDING_3_SMALL")

    client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
    return client, embed_dep

def embed_batch(texts: List[str]) -> List[List[float]]:
    aoai, dep = get_aoai_client()
    resp = aoai.embeddings.create(model=dep, input=texts)
    return [x.embedding for x in resp.data]

# -----------------------------
# Chroma
# -----------------------------

_COLLECTION = None

def get_collection():
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False, allow_reset=True),
    )

    # ✅ (테스트용) 깨진 인덱스 잔재 방지: 처음 1회만 reset 추천
    # client.reset()

    col = client.get_or_create_collection(name=COLLECTION_NAME)
    if col is None:
        raise RuntimeError("get_collection() returned None (check return/indent)")
    return col


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n], i

# -----------------------------
# Excel read (간단 헤더 탐지)
# -----------------------------
HEADER_KEYS = ["level", "레벨", "bom", "품번", "p/no", "new", "base", "qty", "수량", "desc", "품명", "change", "변경"]

def read_sheet_safely(xlsx_path: Path, sheet_name: str) -> pd.DataFrame:
    raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None, dtype=str)

    best_i, best_score = 0, -1
    scan = min(40, len(raw))
    for i in range(scan):
        row = raw.iloc[i].astype(str).fillna("")
        s = " ".join(row.tolist()).lower()
        score = sum(1 for k in HEADER_KEYS if k in s)
        if score > best_score:
            best_score = score
            best_i = i

    df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=best_i, dtype=str)
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df

# text-embedding-3-small 기준 (에러 메시지: 8192 tokens)
EMBED_MAX_TOKENS = 8192
EMBED_SAFETY_MARGIN = 256  # 여유
EMBED_CHUNK_TOKENS = EMBED_MAX_TOKENS - EMBED_SAFETY_MARGIN
EMBED_OVERLAP_TOKENS = 128

# 임베딩 모델 계열에 보통 잘 맞는 인코딩
_tokenizer = tiktoken.get_encoding("cl100k_base")

def _tok_len(s: str) -> int:
    return len(_tokenizer.encode(s or ""))

def chunk_text_by_tokens(text: str,
                         max_tokens: int = EMBED_CHUNK_TOKENS,
                         overlap_tokens: int = EMBED_OVERLAP_TOKENS) -> List[str]:
    """
    텍스트를 max_tokens 이하로 토큰 기준 분할 (overlap 포함)
    - 임베딩 길이 제한(8192) 초과 방지
    """
    text = text or ""
    toks = _tokenizer.encode(text)
    if len(toks) <= max_tokens:
        return [text]

    chunks: List[str] = []
    step = max_tokens - overlap_tokens
    if step <= 0:
        step = max_tokens

    for start in range(0, len(toks), step):
        end = min(start + max_tokens, len(toks))
        chunk = _tokenizer.decode(toks[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(toks):
            break
    return chunks

def expand_docs_by_tokens(docs: List[Dict[str, Any]],
                          max_tokens: int = EMBED_CHUNK_TOKENS) -> List[Dict[str, Any]]:
    """
    docs(= {id, text/documents, meta...}) 중 긴 문서를 자동 분할하여
    Chroma에 넣기 좋은 형태로 확장.
    """
    out: List[Dict[str, Any]] = []
    for d in docs:
        # make_index_docs_l1_chunks 결과 키가 무엇이든 대응 (text/documents)
        text = d.get("text") or d.get("document") or d.get("documents") or ""
        doc_id = d.get("id") or d.get("doc_id") or ""
        meta = d.get("meta") or d.get("metadata") or {}

        parts = chunk_text_by_tokens(text, max_tokens=max_tokens, overlap_tokens=EMBED_OVERLAP_TOKENS)
        if len(parts) == 1:
            out.append(d)
            continue

        # 분할된 조각마다 id suffix + 메타에 parent/chunk 정보 추가
        total = len(parts)
        for i, part in enumerate(parts):
            nd = dict(d)
            nd["text"] = part
            nd["id"] = f"{doc_id}__p{i+1:03d}of{total:03d}"
            nmeta = dict(meta)
            nmeta["parent_id"] = doc_id
            nmeta["chunk_index"] = i
            nmeta["chunk_total"] = total
            nd["meta"] = nmeta
            out.append(nd)

    return out

def embed_batch(texts: List[str], batch_size: int = 64) -> List[List[float]]:
    """
    Azure OpenAI 임베딩 호출.
    - 텍스트 1개라도 8192 토큰 초과하면 400 발생 
    - 여기서는 '이미 chunk된 텍스트'가 들어온다고 가정하고,
      혹시 모를 초과 텍스트는 명확히 로그로 잡아줌.
    """
    aoai, dep = get_aoai_client()

    # 혹시라도 초과 텍스트가 들어오면 어떤 게 문제인지 바로 알 수 있게
    too_long = [(i, _tok_len(t), (t[:120] + "…")) for i, t in enumerate(texts) if _tok_len(t) > EMBED_CHUNK_TOKENS]
    if too_long:
        # DEBUG 로그에 남기기 (현재 파일에 dbg()가 있음)
        for i, tl, preview in too_long[:10]:
            dbg(f"[EMBED_TOO_LONG] idx={i} tokens={tl} preview={preview}")
        raise ValueError(f"Embedding input too long: {len(too_long)} items exceed {EMBED_CHUNK_TOKENS} tokens")

    embs: List[List[float]] = []
    for batch, offset in chunked(texts, batch_size):
        resp = aoai.embeddings.create(model=dep, input=batch)
        embs.extend([x.embedding for x in resp.data])
    return embs

def build_stable_uid(d: dict, source_file: str = "", sheet: str = "") -> str:
    """
    문서 1개를 유니크하게 식별하는 안정적 ID 생성
    - source_file/sheet + meta(가능하면) + text 일부를 섞어서 md5
    - 기존 d["id"]가 있더라도 충돌 방지를 위해 seed에 포함
    """
    meta = d.get("meta") or {}
    text = (d.get("text") or "").strip()

    # meta에 있는 키들은 환경마다 다를 수 있어서 "있으면 쓰고 없으면 빈값"으로 처리
    row = meta.get("row") or meta.get("row_idx") or meta.get("excel_row") or ""
    level = meta.get("level") or meta.get("레벨") or ""
    parent = meta.get("parent_id") or ""
    chunk_i = meta.get("chunk_index") or ""
    old_id = d.get("id") or ""

    seed = "|".join(map(str, [
        source_file, sheet,
        row, level, parent, chunk_i,
        old_id,
        text[:600],   # 너무 길면 비용↑, 충돌 방지엔 이 정도면 충분
    ]))

    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def make_unique_ids_in_batch(ids: list[str]) -> list[str]:
    """
    Chroma는 upsert 1회 요청 내 ids가 유니크해야 함.
    중복이 있으면 __dupN suffix로 제거.
    """
    seen = {}
    out = []
    for _id in ids:
        n = seen.get(_id, 0)
        out_id = _id if n == 0 else f"{_id}__dup{n}"
        seen[_id] = n + 1
        out.append(out_id)
    return out

def fallback_make_docs_from_df(df: pd.DataFrame, source_file: str, sheet: str) -> List[Dict[str, Any]]:
    """
    doc_packaging이 터져도 RAG 구축이 멈추지 않게
    DataFrame 각 row를 1문서로 만드는 fallback.
    """
    docs: List[Dict[str, Any]] = []
    if df is None or not hasattr(df, "iterrows"):
        return docs

    # 값 정리
    df2 = df.copy()
    df2 = df2.fillna("")
    # 컬럼명도 문자열로
    df2.columns = [str(c).replace("\n", " ").strip() for c in df2.columns]

    for ridx, row in df2.iterrows():
        items = []
        for c in df2.columns:
            v = row.get(c, "")
            s = str(v).replace("\n", " ").strip()
            if not s or s.lower() == "nan":
                continue
            items.append(f"{c}: {s}")

        if not items:
            continue

        text = (
            f"[SOURCE_FILE] {source_file}\n"
            f"[SHEET] {sheet}\n"
            f"[ROW] {ridx}\n"
            + "\n".join(items)
        )

        # ✅ id는 파일/시트/row 기준으로 유니크하게
        doc_id = f"{source_file}::{sheet}::row{int(ridx) if str(ridx).isdigit() else ridx}"

        docs.append({
            "id": doc_id,
            "text": text,
            "meta": {
                "source_file": source_file,
                "sheet": sheet,
                "row": int(ridx) if str(ridx).isdigit() else str(ridx),
                "_dbg": "fallback_row_doc"
            }
        })
    return docs

# -----------------------------
# main
# -----------------------------
def main():
    dbg(f"BASE_DIR={BASE_DIR}")
    dbg(f"DATA_DIR={DATA_DIR} exists={DATA_DIR.exists()}")
    dbg(f"CHROMA_DIR={CHROMA_DIR}")

    xlsx_files = sorted([
        p for p in DATA_DIR.iterdir()
        if p.suffix.lower() in [".xlsx", ".xlsm"]
        and not p.name.startswith("~$")  # ✅ 임시파일 제외
    ])
    dbg(f"XLSX_FILES={len(xlsx_files)} -> {[p.name for p in xlsx_files[:5]]}")

    if not xlsx_files:
        dbg("NO XLSX. put *.xlsx into data/")
        dbg_flush()
        return

    col = get_collection()
    dbg(f"CHROMA_BEFORE_COUNT={col.count()}")

    all_docs: List[Dict[str, Any]] = []

    for xf in xlsx_files:
        try:
            xls = pd.ExcelFile(xf)
            dbg(f"[XLSX] {xf.name} sheets={xls.sheet_names}")
        except Exception as e:
            dbg(f"[WARN] failed open {xf.name}: {type(e).__name__}: {e}")
            continue

        for sh in xls.sheet_names:
            try:
                df = read_sheet_safely(xf, sh)
                dbg(f"[SHEET] {xf.name}/{sh} df_shape={df.shape} cols_head={list(df.columns)[:15]}")

                header_hint_text = "\n".join([str(c) for c in df.columns.tolist()]) + "\n" + df.head(30).to_string()

                # ✅ doc_packaging 실패하면 fallback으로 전환
                try:
                    docs = make_index_docs_l1_chunks(
                        df,
                        source_file=xf.name,
                        sheet=sh,
                        header_hint_text=header_hint_text,
                    )
                except Exception as e:
                    dbg(f"[DOC_PACK_FAIL] {xf.name}/{sh}: {type(e).__name__}: {e} -> fallback_make_docs_from_df")
                    docs = fallback_make_docs_from_df(df, source_file=xf.name, sheet=sh)

                # ✅ 토큰 기준 분할 (기존 사용 유지)
                docs = expand_docs_by_tokens(docs, max_tokens=EMBED_CHUNK_TOKENS)

                dbg(f"[DOCS] {xf.name}/{sh} -> {len(docs)} docs")
                if docs:
                    meta0 = docs[0].get("meta") or {}
                    dbg(f"[DOC_SAMPLE] id={docs[0].get('id')} meta_dbg={meta0.get('_dbg')}")
                    dbg(f"[DOC_TEXT_HEAD]\n{(docs[0].get('text') or '')[:250]}")

                all_docs.extend(docs)

            except Exception as e:
                dbg(f"[SHEET_WARN] failed {xf.name}/{sh}: {type(e).__name__}: {e}")
                continue

    dbg(f"ALL_DOCS_TOTAL={len(all_docs)}")
    if not all_docs:
        dbg("DOCS=0 => doc_packaging(레벨/헤더/컬럼) 문제 가능성 큼")
        dbg_flush()
        return

    BATCH = 16
    for batch, _ in tqdm(chunked(all_docs, BATCH), desc="Embedding+Upsert"):
        texts = [d.get("text") or "" for d in batch]
        metas = [sanitize_meta(d.get("meta") or {}) for d in batch]  # sanitize_meta는 Chroma 제약 대응 
        ids   = [str(d.get("id") or f"doc_{i:06d}") for i, d in enumerate(batch)]

        embs = embed_batch(texts)  # AOAI 임베딩 호출 

        assert len(ids) == len(texts) == len(metas) == len(embs), \
            f"len mismatch ids={len(ids)} texts={len(texts)} metas={len(metas)} embs={len(embs)}"

        col.upsert(ids=ids, documents=texts, embeddings=embs, metadatas=metas)

    dbg(f"CHROMA_AFTER_COUNT={col.count()}")
    dbg_flush()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        dbg(f"[FATAL] {type(e).__name__}: {e}")
        dbg_flush()
        raise