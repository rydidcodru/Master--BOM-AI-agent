import base64
import binascii
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dev_parts_backend.config import get_settings
from dev_parts_backend.base_bom import base_bom_summary, parse_base_bom_xlsx
from dev_parts_backend.bom_table import bom_dump_csv_text, bom_dump_summary
from dev_parts_backend.change_extraction import extract_changes_from_pptx
from dev_parts_backend.db import connect, init_db
from dev_parts_backend.embeddings import DEFAULT_EMBEDDING_MODEL, enrich_changes_with_embeddings
from dev_parts_backend.module_mapping import enrich_changes_with_mapping
from dev_parts_backend.rag.pipeline import answer_stub
from dev_parts_backend.repository import get_tree, list_masters, search_details, stats
from dev_parts_backend.workflow import (
    apply_master_changes,
    apply_selected_changes,
    build_subtree_replacement_preview,
    get_candidate_subtree_summary,
    get_connected_parts,
    get_related_parts,
    recommend_change_candidates,
)
from dev_parts_backend.xlsx import workbook_bytes
from dev_parts_backend.master_template import (
    build_master_csv,
    build_master_xlsx,
    master_records_from_bom,
)


app = FastAPI(title="Dev Parts Backend", version="0.1.0")

# React 개발 서버(Vite, 기본 5173)와 분리 배포를 위해 CORS 허용.
# 사내 환경에서 포트가 바뀔 수 있어 localhost/127.0.0.1 전 포트를 정규식으로 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChangeInput(BaseModel):
    change_id: str | None = None
    slide_number: int | None = None
    description: str = ""
    module_name: str = ""
    change_point: str = ""
    change_reason: str = ""
    change_type: str = ""
    target_value: str = ""
    change_intent_keywords: list[str] | str = Field(default_factory=list)
    part_name: str = ""
    base_part_no: str = ""
    evidence: str = ""
    query_embedding: list[float] | None = None
    changing_reason_embedding: list[float] | None = None
    changing_point_embedding: list[float] | None = None
    part_name_embedding: list[float] | None = None
    combined_embedding: list[float] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RecommendRequest(BaseModel):
    changes: list[ChangeInput]
    top_k: int = Field(default=5, ge=1, le=50)
    related_limit: int = Field(default=20, ge=0, le=200)
    auto_embed: bool = True
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    # 코퍼스 선택: "sqlite"(현행 dev_parts.db) | "lg"(lg change_event, 사유 풍부).
    # 미지정 시 env RECOMMEND_BACKEND, 그것도 없으면 sqlite.
    corpus: str | None = None


class PptxExtractRequest(BaseModel):
    filename: str = ""
    content_base64: str
    use_llm: bool = True
    llm_model: str | None = None


class PptxBomRequest(BaseModel):
    filename: str = ""
    content_base64: str


class BaseBomXlsxRequest(BaseModel):
    filename: str = ""
    content_base64: str


class BomSelection(BaseModel):
    change_id: str | None = None
    candidate_detail_id: int
    action: str | None = Field(default=None, description="change, add, or delete. Defaults from candidate history.")
    target_base_part_no: str = ""
    target_part_name: str = ""
    change_point: str = ""
    change_reason: str = ""
    include_subtree: bool = False
    # add 액션 시 함께 추가할 연동 주변부품 detail_id(회의록 유형2, HITL 승인분).
    bundle_detail_ids: list[int] = Field(default_factory=list)


class BomApplyRequest(BaseModel):
    base_master_id: int | None = None
    base_bom: list[dict[str, Any]] | None = None
    selections: list[BomSelection]
    # 하위 부품 변경 시 상위 Assembly로 도미노 전파(회의록 유형1).
    propagate_change_upward: bool = True


class SubtreePreviewRequest(BaseModel):
    base_master_id: int
    selection: BomSelection


class MasterChange(BaseModel):
    """심의표 변경점 한 줄(= master 한 행). 페이지1에서 부여한 New P/No 포함."""
    no: str | int | None = None
    design_point: str = ""
    lev: str = ""
    bom_level: str = ""
    part_name: str = ""
    base_part_no: str = ""
    new_part_no: str = ""  # TBD / base 품번 / 실제 신번
    qty: str = ""
    change_point: str = ""
    change_reason: str = ""
    classification: str = ""
    action: str | None = None  # change/add/delete 명시(없으면 자동 추정)


class ApplyMasterRequest(BaseModel):
    base_master_id: int | None = None
    base_bom: list[dict[str, Any]] | None = None
    changes: list[MasterChange]
    propagate_change_upward: bool = True


def dump_model(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def get_conn():
    settings = get_settings()
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def read_stats(conn=Depends(get_conn)):
    return stats(conn)


@app.get("/masters")
def read_masters(conn=Depends(get_conn)):
    return list_masters(conn)


@app.get("/details/search")
def search(q: str = "", limit: int = Query(default=20, ge=1, le=100), conn=Depends(get_conn)):
    return search_details(conn, q, limit=limit)


@app.get("/masters/{master_id}/tree")
def tree(master_id: int, conn=Depends(get_conn)):
    return get_tree(conn, master_id)


@app.get("/rag/ask")
def ask(q: str, limit: int = Query(default=8, ge=1, le=50), conn=Depends(get_conn)):
    return answer_stub(conn, q, limit=limit)


@app.post("/changes/recommend")
def recommend_changes(request: RecommendRequest, conn=Depends(get_conn)):
    # 코퍼스 선택: 요청 corpus 우선, 없으면 env RECOMMEND_BACKEND, 그것도 없으면 sqlite.
    # lg → 사유 풍부한 lg change_event(Postgres·BGE-M3). OpenAI 임베딩/prefix 매핑 생략.
    import os as _os
    corpus = (request.corpus or _os.environ.get("RECOMMEND_BACKEND") or "sqlite").strip().lower()
    if corpus == "lg":
        from dev_parts_backend.lg_adapter import recommend_via_lg
        return {
            "results": [recommend_via_lg(dump_model(change), limit=request.top_k)
                        for change in request.changes],
            "embedding_status": {"status": "lg_change_event", "model": "bge-m3", "corpus": "lg"},
        }
    mapped_changes = enrich_changes_with_mapping([dump_model(change) for change in request.changes])
    changes, embedding_status = enrich_changes_with_embeddings(
        mapped_changes,
        model=request.embedding_model,
        enabled=request.auto_embed,
    )
    return {
        "results": [
            recommend_change_candidates(
                conn,
                change,
                limit=request.top_k,
                related_limit=request.related_limit,
            )
            for change in changes
        ],
        "embedding_status": embedding_status,
    }


@app.post("/pptx/extract-changes")
def extract_pptx_changes(request: PptxExtractRequest):
    try:
        pptx_bytes = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
    if not pptx_bytes:
        raise HTTPException(status_code=400, detail="PPTX content is empty")
    try:
        return extract_changes_from_pptx(
            pptx_bytes,
            filename=request.filename,
            use_llm=request.use_llm,
            model=request.llm_model,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def decode_pptx_base64(content_base64: str) -> bytes:
    try:
        pptx_bytes = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
    if not pptx_bytes:
        raise HTTPException(status_code=400, detail="PPTX content is empty")
    return pptx_bytes


def bom_csv_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if filename else "design_point_bom"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem).strip("._")
    return f"{safe or 'design_point_bom'}_design_point_bom.csv"


@app.post("/pptx/extract-bom")
def extract_pptx_bom(request: PptxBomRequest):
    """PPTX의 Design Points BOM 표를 충실하게 추출(JSON). 적재 없이 입력 데이터로 사용."""
    pptx_bytes = decode_pptx_base64(request.content_base64)
    try:
        summary = bom_dump_summary(pptx_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"filename": request.filename, **summary}


@app.post("/pptx/extract-bom/export")
def export_pptx_bom(request: PptxBomRequest):
    """동일 추출 결과를 CSV 파일로 다운로드."""
    pptx_bytes = decode_pptx_base64(request.content_base64)
    try:
        csv_text = bom_dump_csv_text(pptx_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = bom_csv_filename(request.filename)
    # utf-8-sig: Excel에서 한글 깨짐 방지(BOM 포함)
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/bom/base/parse-xlsx")
def parse_base_bom(request: BaseBomXlsxRequest):
    """Base BOM 원본 엑셀(.xlsx)을 apply 가능한 BOM rows로 파싱.

    회의록 INPUT의 Base BOM 축. 반환된 rows를 /bom/apply, /bom/apply/export의
    base_bom 인자로 그대로 넘기면 최종 마스터 BOM을 만든다.
    """
    try:
        xlsx_bytes = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64") from exc
    if not xlsx_bytes:
        raise HTTPException(status_code=400, detail="xlsx content is empty")
    try:
        rows = parse_base_bom_xlsx(xlsx_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=400, detail="Base BOM 표를 찾지 못했습니다(Part No/Description 헤더 확인).")
    return {"filename": request.filename, "summary": base_bom_summary(rows), "rows": rows}


@app.get("/history/{detail_id}/related")
def related_history_parts(
    detail_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    conn=Depends(get_conn),
):
    related = get_related_parts(conn, detail_id, limit=limit)
    if not related:
        raise HTTPException(status_code=404, detail="detail_id was not found")
    return {"detail_id": detail_id, "related_parts": related}


@app.get("/history/{detail_id}/subtree")
def history_subtree(
    detail_id: int,
    limit: int = Query(default=30, ge=1, le=500),
    conn=Depends(get_conn),
):
    """선택한 후보의 하위 부품 subtree 요약(온디맨드). 추천 시 1순위만 미리 계산하므로 보완용."""
    return get_candidate_subtree_summary(conn, detail_id, limit=limit)


@app.get("/history/{detail_id}/connected")
def connected_add_parts(
    detail_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    conn=Depends(get_conn),
):
    """신규 부품 추가 시 함께 추가할 연동 주변부품(형제+하위) 추천. 회의록 유형(2)."""
    connected = get_connected_parts(conn, detail_id, limit=limit)
    return {"detail_id": detail_id, "connected_parts": connected}


@app.post("/bom/apply")
def apply_bom_changes(request: BomApplyRequest, conn=Depends(get_conn)):
    try:
        return apply_selected_changes(
            conn,
            base_master_id=request.base_master_id,
            base_bom=request.base_bom,
            selections=[dump_model(selection) for selection in request.selections],
            propagate_change_upward=request.propagate_change_upward,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bom/apply-master")
def apply_master(request: ApplyMasterRequest, conn=Depends(get_conn)):
    """페이지1의 master(변경점)를 base BOM에 직접 반영(과거 후보 불필요)."""
    try:
        return apply_master_changes(
            conn,
            base_master_id=request.base_master_id,
            base_bom=request.base_bom,
            changes=[dump_model(change) for change in request.changes],
            propagate_change_upward=request.propagate_change_upward,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bom/apply-master/export")
def export_master(request: ApplyMasterRequest, conn=Depends(get_conn)):
    try:
        result = apply_master_changes(
            conn,
            base_master_id=request.base_master_id,
            base_bom=request.base_bom,
            changes=[dump_model(change) for change in request.changes],
            propagate_change_upward=request.propagate_change_upward,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = workbook_bytes([
        ("new_bom", result["new_bom"]),
        ("change_list", result["change_list"]),
        ("unmatched", result["unmatched"]),
    ])
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="new_bom.xlsx"'},
    )


@app.post("/bom/master/export")
def export_dev_part_master(
    request: ApplyMasterRequest,
    fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    conn=Depends(get_conn),
):
    """변경내역을 개발부품마스터 템플릿(example.xlsx 양식)에 채워 산출."""
    try:
        result = apply_master_changes(
            conn,
            base_master_id=request.base_master_id,
            base_bom=request.base_bom,
            changes=[dump_model(change) for change in request.changes],
            propagate_change_upward=request.propagate_change_upward,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    records = master_records_from_bom(result["new_bom"])
    if fmt == "csv":
        content = build_master_csv(records)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="dev_part_master.csv"'},
        )
    content = build_master_xlsx(records)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="dev_part_master.xlsx"'},
    )


@app.post("/bom/subtree-preview")
def subtree_preview(request: SubtreePreviewRequest, conn=Depends(get_conn)):
    try:
        return build_subtree_replacement_preview(
            conn,
            base_master_id=request.base_master_id,
            selection=dump_model(request.selection),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/bom/apply/export")
def export_bom_changes(request: BomApplyRequest, conn=Depends(get_conn)):
    try:
        result = apply_selected_changes(
            conn,
            base_master_id=request.base_master_id,
            base_bom=request.base_bom,
            selections=[dump_model(selection) for selection in request.selections],
            propagate_change_upward=request.propagate_change_upward,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = workbook_bytes(
        [
            ("new_bom", result["new_bom"]),
            ("change_list", result["change_list"]),
            ("unmatched", result["unmatched"]),
        ]
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="new_bom.xlsx"'},
    )


# 빌드된 React 프론트엔드(frontend/dist)가 있으면 같은 서버에서 정적 서빙한다.
# 그러면 Node 없이 `devparts serve` 하나로 UI까지 열 수 있다(배포/검토용).
# 모든 API 라우트가 위에 먼저 정의돼 있으므로 "/" mount는 나머지 경로만 처리한다.
_frontend_dist = Path("frontend/dist")
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
