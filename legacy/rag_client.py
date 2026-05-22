# rag_client.py
# - AOAI 임베딩 생성
# - Chroma(Persistent)에서 유사 문서 검색
# - 반환 포맷: [{id, dist, meta, text}, ...]
#
# ✅ app.py에서 호출 형태 통일:
#    retrieve_docs(query, top_k=..., filters={...})

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from openai import AzureOpenAI
from pathlib import Path

import chromadb
from chromadb.config import Settings

# -----------------------------
# Chroma 설정
# -----------------------------
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CHROMA_DIR = Path(os.getenv("HARA_CHROMA_DIR", str(BASE_DIR / "chroma_db")))
COLLECTION_NAME = "dev_part_master"

# -----------------------------
# AOAI 클라이언트
# -----------------------------
def get_aoai_client() -> Tuple[AzureOpenAI, str]:
    """
    ✅ (client, embedding_deployment_name) 반환
    """
    load_dotenv()
    api_key = (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
    api_version = (os.getenv("AZURE_OPENAI_API_VERSION") or "").strip()
    embed_dep = (os.getenv("AZURE_DEPLOYMENT_TEXT_EMBEDDING_3_SMALL") or "").strip()

    if not api_key or not endpoint or not api_version or not embed_dep:
        raise RuntimeError(
            "AOAI 환경변수 누락. "
            "AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_VERSION / "
            "AZURE_DEPLOYMENT_TEXT_EMBEDDING_3_SMALL 를 확인해 주세요."
        )

    client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=api_version,
    )
    return client, embed_dep


def embed_query(text: str) -> List[float]:
    """
    ✅ 단일 쿼리 임베딩 벡터 반환
    """
    aoai, dep = get_aoai_client()
    resp = aoai.embeddings.create(model=dep, input=[text])
    return resp.data[0].embedding


# -----------------------------
# Chroma 컬렉션 (캐시)
# -----------------------------
_COLLECTION = None

def get_collection():
    """
    ✅ collection 객체 반환 (없으면 생성)
    """
    global _COLLECTION
    if _COLLECTION is not None:
        return _COLLECTION

    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),  # ✅ Path -> str 변환
        settings=Settings(anonymized_telemetry=False),
    )

    # 없으면 생성, 있으면 가져오기
    try:
        col = client.get_collection(name=COLLECTION_NAME)
    except Exception:
        col = client.create_collection(name=COLLECTION_NAME)

    _COLLECTION = col
    return _COLLECTION


# -----------------------------
# filters -> where 변환
# -----------------------------
def _build_where(filters: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Chroma where 예시
    - {"model_prefix": "WSED"}  -> {"model_prefix": "WSED"}
    - {"product": "W"}         -> {"product": "W"}
    - {"platform": ["K","S"]}  -> {"platform": {"$in": ["K","S"]}}
    """
    if not filters:
        return None

    where: Dict[str, Any] = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue

        # list/tuple/set -> $in
        if isinstance(v, (list, tuple, set)):
            vv = [x for x in v if x is not None and str(x).strip() != ""]
            if not vv:
                continue
            where[k] = {"$in": vv}
        else:
            where[k] = v

    return where or None


# -----------------------------
# Retrieve
# -----------------------------
def retrieve_docs(
    query: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    ✅ app.py에서 쓰는 표준 인터페이스
    반환: [{id, dist, meta, text}, ...]
    """
    col = get_collection()
    q_emb = embed_query(query)
    where = _build_where(filters)

    # Chroma query
    res = col.query(
        query_embeddings=[q_emb],
        n_results=int(top_k),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    docs = (res.get("documents") or [[]])[0]

    out: List[Dict[str, Any]] = []
    for i in range(len(ids)):
        out.append({
            "id": ids[i],
            "dist": dists[i] if i < len(dists) else None,
            "meta": metas[i] if i < len(metas) else {},
            "text": docs[i] if i < len(docs) else "",
        })
    return out