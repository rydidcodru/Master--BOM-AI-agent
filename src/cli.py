"""D-012 LG BOM 전처리 CLI.

``python -m src.cli <command>``. 명령 그룹:

    inventory                      raw 디렉토리 → file_inventory.parquet
    classify                       시트 단위 양식 분류
    narrativize                    단일 행 narrative 미리보기 (디버그)

    pipeline run [PATH]            classify→extract→normalize→narrativize→validate
                                   → dry_run/<run_id>/
    pipeline run --commit          + validation gate 통과 시 commit
    pipeline commit  --run-id ID   dry_run → committed
    pipeline rollback --run-id ID  committed → rolled_back

    quarantine list --run-id ID    격리된 행 조회

    db init                        ORM 테이블 + (Postgres) schema_dev_part_master.sql
    db load --run-id ID [--embed]  committed run → PG (source_files / ingestion_log /
                                   dev_part_master). --embed: embedding_dense 생성.
    db rollback --file-id ID       file_id 단위 DB 적재 원복 (CASCADE).
    db status                      ingestion_log 상태 요약.
    db verify [--file-id ID]       테이블별 카운트 출력.
    db reset --confirm             전체 데이터 삭제 (개발용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import typer
from sqlalchemy import delete, func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run, update_embeddings
from src.db.load_change_events import load_change_events, update_event_embeddings
from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.db.retrieve import Hit, hybrid_search, lexical_search, semantic_search
from src.db.rollback import rollback_file
from src.preprocess.classify import classify_dir, classify_file
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    COMMITTED_ROOT,
    commit_run,
    discover_raw_files,
    read_state,
    reprocess_quarantine,
    rollback_run,
    run_pipeline,
)
from src.preprocess.quarantine import list_quarantined
from src.utils.paths import INTERIM_DIR, RAW_DIR

app = typer.Typer(help="LG BOM D-012 전처리 CLI.")
pipeline_app = typer.Typer(help="dry-run / commit / rollback 사이클.")
quarantine_app = typer.Typer(help="격리된 행 조회.")
db_app = typer.Typer(help="PostgreSQL 적재 / 롤백 / 상태.")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(quarantine_app, name="quarantine")
app.add_typer(db_app, name="db")


# --- Top-level commands --------------------------------------------------


@app.command()
def inventory(
    raw_dir: Path = typer.Option(RAW_DIR, help="raw Excel 디렉토리."),
    output: Path = typer.Option(
        INTERIM_DIR / "file_inventory.parquet", help="출력 parquet."
    ),
) -> None:
    """raw 파일 인벤토리."""
    df = build_inventory(raw_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    if df.empty:
        typer.echo(f"No Excel files found under {raw_dir}.")
        return
    typer.echo(f"Inventory written to {output} ({len(df)} sheet rows).")


@app.command()
def classify(
    path: Path = typer.Argument(..., help="Excel 파일 또는 --all로 디렉토리."),
    all_files: bool = typer.Option(False, "--all", help="PATH를 디렉토리로 처리."),
) -> None:
    """시트 단위 양식 분류."""
    if all_files or path.is_dir():
        results = classify_dir(path)
        counts: dict[str, int] = {}
        for result in results:
            counts[result.form_version] = counts.get(result.form_version, 0) + 1
            flag = " [needs review]" if result.needs_review else ""
            typer.echo(
                f"{Path(result.file_path).name}: {result.form_version} "
                f"({result.confidence:.2f}){flag}  sheets={len(result.sheet_results)}"
            )
        typer.echo("\nForm-version counts:")
        for version, count in sorted(counts.items()):
            typer.echo(f"  {version}: {count}")
        return

    result, sheet_results = classify_file(path)
    typer.echo(f"{path.name}: {result.form_version} (confidence={result.confidence})")
    for sc in sheet_results:
        typer.echo(
            f"    {sc.sheet_name}: {sc.form_id} ({sc.confidence:.2f}) "
            f"max_col={sc.max_col} signals={','.join(sc.signals_matched)}"
        )


@app.command()
def narrativize(
    part_no: str = typer.Option(..., help="새 부품번호"),
    part_name: str = typer.Option("샘플 부품", help="부품명"),
    change_point: str = typer.Option("", help="변경점 자유텍스트"),
    change_reason: str = typer.Option("", help="변경 사유"),
    model_code: str = typer.Option("WSED7667M.ABMQEUR", help="모델 코드"),
    grade: str = typer.Option("Best-1", help="등급"),
    change_type: str = typer.Option("Change", help="New|Change|Carry-over"),
) -> None:
    """단일 row narrative 미리보기 (디버그용)."""
    from src.preprocess.narrativize import build_narrative

    dpm = {
        "part_no_new": part_no,
        "part_name": part_name,
        "new_model": model_code,
        "event": change_type,
        "change_point_raw": change_point or None,
        "change_reason_raw": change_reason or None,
    }
    extra = {"grade": grade}
    typer.echo(build_narrative(dpm, extra))


# --- pipeline sub-app ---------------------------------------------------


@pipeline_app.command("run")
def pipeline_run(
    path: Path = typer.Argument(RAW_DIR, help="Excel 파일 또는 디렉토리."),
    commit: bool = typer.Option(False, "--commit", help="validation 통과 시 commit."),
) -> None:
    """full pipeline run (6 steps)."""
    files = discover_raw_files(path) if path.is_dir() else [path]
    if not files:
        typer.echo(f"No Excel files found under {path}.")
        raise typer.Exit(code=1)
    mode: Literal["dry-run", "commit"] = "commit" if commit else "dry-run"
    result = run_pipeline(files, mode=mode)
    typer.echo(f"Run {result.run_id} [{result.status}]")
    typer.echo(
        f"  rows_in={result.rows_in} rows_out={result.rows_out} "
        f"quarantined={result.quarantine_count}"
    )
    agg = result.aggregate_validation
    if agg is not None:
        verdict = "ACCEPTABLE" if agg.is_acceptable() else "NOT ACCEPTABLE"
        typer.echo(f"  validation: {verdict}")
        if not agg.is_acceptable():
            typer.echo(f"  failing: {', '.join(agg.critical_failures())}")
    if result.report_path:
        typer.echo(f"  report: {result.report_path}")


@pipeline_app.command("review")
def pipeline_review(run_id: str = typer.Option(..., "--run-id")) -> None:
    """run state.json 출력."""
    state = read_state(run_id)
    if state is None:
        typer.echo(f"Unknown run_id: {run_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(state, indent=2, ensure_ascii=False, default=str))


@pipeline_app.command("commit")
def pipeline_commit(run_id: str = typer.Option(..., "--run-id")) -> None:
    """dry_run → committed (gate 통과 시)."""
    try:
        target = commit_run(run_id)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Commit failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Committed -> {target}")


@pipeline_app.command("rollback")
def pipeline_rollback(run_id: str = typer.Option(..., "--run-id")) -> None:
    """committed → rolled_back."""
    try:
        target = rollback_run(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"Rollback failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled back -> {target}")


# --- quarantine sub-app -------------------------------------------------


@quarantine_app.command("list")
def quarantine_list(run_id: str = typer.Option(..., "--run-id")) -> None:
    """격리된 행 조회."""
    rows = list_quarantined(run_id)
    if not rows:
        typer.echo(f"No quarantine records for {run_id}.")
        return
    typer.echo(f"{len(rows)} quarantined rows for {run_id}:")
    for row in rows[:50]:
        typer.echo(
            f"  [{row['severity']}] {row['source_file']}:{row['row_index']}  "
            f"{row['stage_failed']}: {row['fail_reason']}"
        )
    if len(rows) > 50:
        typer.echo(f"  ... and {len(rows) - 50} more.")


@quarantine_app.command("reprocess")
def quarantine_reprocess(run_id: str = typer.Option(..., "--run-id")) -> None:
    summary = reprocess_quarantine(run_id)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))


# --- db sub-app ---------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """ORM 테이블 + (Postgres) schema_dev_part_master.sql 적용."""
    engine = make_engine()
    init_db(engine)
    typer.echo(f"Initialized {engine.url}.")


@db_app.command("load")
def db_load(
    run_id: str | None = typer.Option(None, "--run-id", help="committed run id"),
    embed: bool = typer.Option(False, "--embed", help="embedding_dense 생성 (Ollama 필요)"),
) -> None:
    """committed run → PostgreSQL (source_files + ingestion_log + dev_part_master)."""
    if not run_id:
        typer.echo("--run-id required.")
        raise typer.Exit(code=1)
    run_dir = COMMITTED_ROOT / run_id
    if not run_dir.exists():
        typer.echo(f"No committed run: {run_id}")
        raise typer.Exit(code=1)
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        result = load_run(session, run_dir)
        typer.echo(f"Loaded {run_id}: {result.rows_inserted}")
        if embed:
            n = update_embeddings(session, file_ids=result.file_ids)
            typer.echo(f"Embeddings updated: {n}")
            # change_event는 load_run이 이미 적재함 → reason_embedding(검색 인덱스)만 채운다.
            ev_n = update_event_embeddings(session)
            typer.echo(f"Event embeddings updated: {ev_n}")


@db_app.command("rollback")
def db_rollback(file_id: int = typer.Option(..., "--file-id")) -> None:
    """file_id 단위 DB 적재 원복 (CASCADE)."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        try:
            result = rollback_file(session, file_id)
        except ValueError as exc:
            typer.echo(f"Rollback failed: {exc}")
            raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled back file_id={file_id}: {result.rows_deleted}")


@db_app.command("status")
def db_status() -> None:
    """ingestion_log 상태별 집계."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        rows = session.execute(
            select(IngestionLog.status, func.count())
            .group_by(IngestionLog.status)
        ).all()
    if not rows:
        typer.echo("No ingestion_log entries.")
        return
    for status, count in rows:
        typer.echo(f"  {status or '(null)'}: {count}")


@db_app.command("verify")
def db_verify(
    file_id: int | None = typer.Option(None, "--file-id"),
) -> None:
    """테이블별 row 카운트. --file-id 지정 시 해당 파일만."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        if file_id is None:
            sf = session.execute(select(func.count()).select_from(SourceFile)).scalar_one()
            log = session.execute(select(func.count()).select_from(IngestionLog)).scalar_one()
            dpm = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()
            typer.echo(f"source_files: {sf}")
            typer.echo(f"ingestion_log: {log}")
            typer.echo(f"dev_part_master: {dpm}")
            return
        log = session.execute(
            select(func.count())
            .select_from(IngestionLog)
            .where(IngestionLog.file_id == file_id)
        ).scalar_one()
        dpm = session.execute(
            select(func.count())
            .select_from(DevPartMaster)
            .where(DevPartMaster.file_id == file_id)
        ).scalar_one()
        typer.echo(f"file_id {file_id}: ingestion_log={log}, dev_part_master={dpm}")


@db_app.command("bom-edges")
def db_bom_edges(
    file_id: int | None = typer.Option(None, "--file-id", help="특정 파일만; 미지정 시 전체"),
) -> None:
    """BOM ag-grid 행 → bom_edge 엣지 적재 (walk_subtree 하위 전개 대상). idempotent."""
    from src.agent.repository.backfill import backfill_bom_edges
    from src.db.models import BomEdge

    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        n = backfill_bom_edges(session, file_id=file_id)
        total = session.execute(select(func.count()).select_from(BomEdge)).scalar_one()
    typer.echo(f"bom_edge inserted: {n} (total now {total})")


@db_app.command("change-events")
def db_change_events(
    file_id: int | None = typer.Option(None, "--file-id", help="특정 파일만; 미지정 시 전체"),
    embed: bool = typer.Option(False, "--embed", help="reason_embedding 생성 (Ollama 필요)"),
    recanon: bool = typer.Option(
        False,
        "--recanon",
        help="적재 없이 전 change_line.part_name → part_name_canon 백필만 수행"
        " (canonicalize_part_name, 멱등: 항상 덮어씀; 008)",
    ),
) -> None:
    """dev_part_master 변경행 → change_event/change_line(사유별 묶음) 적재.

    search_events(reason_embedding) 검색 인덱스의 코퍼스. idempotent(file_id 스코프 재생성).
    --embed로 reason_embedding(=embed(change_log+change_reason))까지 채운다.
    --recanon은 적재 대신 part_name_canon(008) 백필만 수행한다(멱등 — 항상 덮어씀).
    """
    from src.db.models import ChangeEvent

    engine = make_engine()
    Session = session_factory(engine)
    if recanon:
        from src.db.models import ChangeLine
        from src.preprocess.normalize import canonicalize_part_name

        with Session() as session:
            n = 0
            for line in session.execute(select(ChangeLine)).scalars():
                line.part_name_canon = (
                    canonicalize_part_name(line.part_name or "") or None
                )
                n += 1
            session.commit()
        typer.echo(f"part_name_canon backfilled: {n}")
        return
    with Session() as session:
        file_ids = [file_id] if file_id is not None else None
        res = load_change_events(session, file_ids=file_ids)
        total = session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one()
        msg = (
            f"change_event inserted: {res.events_inserted}, "
            f"change_line: {res.lines_inserted} (total events now {total})"
        )
        if embed:
            ev_n = update_event_embeddings(session)
            msg += f"; reason_embedding updated: {ev_n}"
    typer.echo(msg)


@db_app.command("feedback-report")
def db_feedback_report() -> None:
    """P8 — confirm_feedback 집계 리포트 (운영 중 자동 생성되는 recall/precision 대시보드).

    누적/채택률/채택 순위 분포(top1·top3·기타)+MRR / manual_added 비율(≒ 자동 검색 미회수율
    하한) / low_confidence 중 채택 비율 / rescued 후보 채택 비율. 수작업 골든셋 대신 이걸로
    판단한다. **이 CLI만 confirm_feedback을 읽는다**(검색·판정 경로는 미참조).
    """
    from src.db.models import ConfirmFeedback

    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        rows = session.execute(select(ConfirmFeedback)).scalars().all()
    n = len(rows)
    if n == 0:
        typer.echo("confirm_feedback 비어 있음 — 확정(FEEDBACK_LOG=1) 후 다시 보세요.")
        return

    n_adopted = sum(1 for r in rows if r.adopted_event_ids)
    n_lowconf = sum(1 for r in rows if r.low_confidence)
    n_lowconf_adopted = sum(1 for r in rows if r.low_confidence and r.adopted_event_ids)
    n_manual = sum(1 for r in rows if r.manual_added_event_ids)

    # 채택 순위 분포 + MRR — candidates digest의 rank로 채택 event의 최상위 순위 계산.
    top1 = top3 = other = 0
    mrr_sum = 0.0
    rescued_total = rescued_adopted = 0
    for r in rows:
        adopted = set(r.adopted_event_ids or [])
        cands = r.candidates or []
        for c in cands:
            if c.get("rescued"):
                rescued_total += 1
                if c.get("event_id") in adopted:
                    rescued_adopted += 1
        if not adopted:
            continue
        ranks = [c.get("rank") for c in cands if c.get("event_id") in adopted and c.get("rank")]
        if ranks:
            best = min(ranks)
            mrr_sum += 1.0 / best
            if best == 1:
                top1 += 1
            elif best <= 3:
                top3 += 1
            else:
                other += 1
        else:
            other += 1  # 채택됐지만 자동 후보에 없음(수동 보강 등)

    def pct(a: int, b: int) -> str:
        return f"{a}/{b} ({(a / b * 100) if b else 0:.0f}%)"

    # ASCII만 사용(Windows cp949 콘솔 인코딩 크래시 방지).
    typer.echo("== confirm_feedback report ==")
    typer.echo(f"누적 확정: {n}건")
    typer.echo(f"채택 발생: {pct(n_adopted, n)}")
    typer.echo(f"채택 순위: top1={top1} / top3(2~3)={top3} / 그 외-미발견={other}")
    typer.echo(f"MRR(채택 기준): {(mrr_sum / n_adopted) if n_adopted else 0:.3f}")
    typer.echo(f"수동 보강(manual_added) 포함: {pct(n_manual, n)}  (=> 자동 검색 미회수율 하한)")
    typer.echo(f"low_confidence 중 채택: {pct(n_lowconf_adopted, n_lowconf)}")
    typer.echo(f"rescued 후보 채택: {pct(rescued_adopted, rescued_total)}")
    # (선택 확장) eval replay — 기록된 query_text로 채널 on/off 재검색해 순위 변화 측정: TODO.


@db_app.command("reset")
def db_reset(
    confirm: bool = typer.Option(False, "--confirm", help="필수 확인 플래그"),
) -> None:
    """전체 데이터 삭제 (개발 편의용). 운영 환경 금지."""
    if not confirm:
        typer.echo("Refusing without --confirm.")
        raise typer.Exit(code=1)
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        session.execute(delete(DevPartMaster))
        session.execute(delete(IngestionLog))
        session.execute(delete(SourceFile))
        session.commit()
    typer.echo("All data deleted.")


# --- retrieve sub-app -----------------------------------------------------

retrieve_app = typer.Typer(help="dev_part_master 검색 (semantic / lexical / hybrid).")
app.add_typer(retrieve_app, name="retrieve")


def _print_hits(hits: list[Hit], mode: str) -> None:
    if not hits:
        typer.echo(f"  (no hits — {mode})")
        return
    for h in hits:
        scores = []
        if h.score_rrf is not None:
            scores.append(f"rrf={h.score_rrf:.4f}")
        if h.score_semantic is not None:
            scores.append(f"sem={h.score_semantic:.3f}")
        if h.score_lexical is not None:
            scores.append(f"lex={h.score_lexical:.3f}")
        head = " ".join(scores)
        typer.echo(
            f"  [{head}] {(h.part_no_new or '?'):<14} "
            f"| {(h.event or '-'):<10} "
            f"| {(h.new_model or '-')[:18]:<18} "
            f"| {(h.part_name or '-')[:32]:<32} "
            f"| {h.form_id}"
        )
        if h.embedding_text:
            typer.echo(f"         {h.embedding_text[:160]}")


@retrieve_app.command("semantic")
def retrieve_semantic(
    query: str = typer.Argument(..., help="자연어 쿼리."),
    top_k: int = typer.Option(10, "--top-k"),
    form_id: str | None = typer.Option(None, "--form-id"),
    event: str | None = typer.Option(None, "--event", help="New / Change / Carry-over"),
    region: str | None = typer.Option(None, "--region"),
) -> None:
    """벡터 cosine 거리 기준 검색 (HNSW)."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as s:
        hits = semantic_search(
            s, query, top_k=top_k, form_id=form_id, event=event, region=region
        )
    _print_hits(hits, "semantic")


@retrieve_app.command("lexical")
def retrieve_lexical(
    query: str = typer.Argument(..., help="키워드 또는 짧은 자연어."),
    top_k: int = typer.Option(10, "--top-k"),
    form_id: str | None = typer.Option(None, "--form-id"),
    event: str | None = typer.Option(None, "--event"),
    region: str | None = typer.Option(None, "--region"),
    min_sim: float = typer.Option(0.05, "--min-sim"),
) -> None:
    """pg_trgm word_similarity 기준 검색."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as s:
        hits = lexical_search(
            s,
            query,
            top_k=top_k,
            form_id=form_id,
            event=event,
            region=region,
            min_similarity=min_sim,
        )
    _print_hits(hits, "lexical")


@retrieve_app.command("hybrid")
def retrieve_hybrid(
    query: str = typer.Argument(..., help="자연어 쿼리."),
    top_k: int = typer.Option(10, "--top-k"),
    pool: int = typer.Option(30, "--pool", help="각 모달리티 raw top-N."),
    form_id: str | None = typer.Option(None, "--form-id"),
    event: str | None = typer.Option(None, "--event"),
    region: str | None = typer.Option(None, "--region"),
    sem_w: float = typer.Option(1.0, "--semantic-weight"),
    lex_w: float = typer.Option(1.0, "--lexical-weight"),
) -> None:
    """Semantic + Lexical RRF 융합 검색."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as s:
        hits = hybrid_search(
            s,
            query,
            top_k=top_k,
            candidate_pool=pool,
            semantic_weight=sem_w,
            lexical_weight=lex_w,
            form_id=form_id,
            event=event,
            region=region,
        )
    _print_hits(hits, "hybrid")


# --- agent sub-app (L1~L4 E2E) -------------------------------------------

agent_app = typer.Typer(help="BOM 변경 영향 분석 에이전트 (L1~L4 E2E).")
app.add_typer(agent_app, name="agent")


@agent_app.command("run")
def agent_run(
    text: str = typer.Argument(..., help="변경점/사유 자유텍스트."),
    max_candidates: int = typer.Option(10, "--max-candidates"),
) -> None:
    """L1~L2 헤드리스 propose — 구조화 → 후보 부품 세트(**정지**, 트리 전개 없음).

    확정·하위 전개는 UI 확정 게이트에서(절대원칙 #4). retrieval은 ENABLE_EMBEDDING=1 +
    Ollama + change_event 적재 필요(`db change-events --embed`).
    """
    from src.agent.orchestrator.backend import DbRetrievalBackend
    from src.agent.pipeline import propose_analysis

    engine = make_engine()
    Session = session_factory(engine)
    backend = DbRetrievalBackend(Session)
    with Session() as s:
        res = propose_analysis(text, session=s, backend=backend, min_candidates=1)

    ci = res.intent
    typer.echo(
        f"[L1] source={ci.source} conf={ci.confidence:.2f} "
        f"part_nos={ci.part_nos} models={ci.models} region={ci.region}"
    )
    typer.echo(f"     queries={ci.rewritten_queries}")
    typer.echo(
        f"[L2] candidates={len(res.candidates)} reflections={res.reflections} "
        f"tool_calls={res.tool_calls}"
    )
    for i, c in enumerate(res.candidates[:max_candidates], 1):
        ev = c.event
        pnos = ", ".join(ln.new_pno or ln.base_pno or "?" for ln in c.lines)
        typer.echo(f"  #{i} [{ev.change_reason or '-'}] {len(c.lines)}개 부품: {pnos}")
    typer.echo("  (확정·하위 전개는 UI 확정 게이트에서 — 절대원칙 #4)")


# --- app (Streamlit UI) launcher -----------------------------------------

ui_app = typer.Typer(help="Streamlit BOM Agent UI launcher (src/ui/).")
app.add_typer(ui_app, name="app")


@ui_app.command("run")
def app_run(
    port: int = typer.Option(8501, "--port"),
    host: str = typer.Option("localhost", "--host"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="브라우저 자동 열기."),
) -> None:
    """streamlit run src/ui/main.py 실행 — 통합 UI(검수 + L1~L4 영향분석 라우터).

    requires 'ui' extra: uv sync --extra ui
    """
    import subprocess
    import sys
    from pathlib import Path

    ui_app_path = Path(__file__).resolve().parent / "ui" / "main.py"
    if not ui_app_path.exists():
        typer.echo(f"UI app not found: {ui_app_path}")
        raise typer.Exit(code=1)

    # Streamlit 첫 실행 시 email prompt skip (대화형 stdin 비활성화)
    creds_dir = Path.home() / ".streamlit"
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds_file = creds_dir / "credentials.toml"
    if not creds_file.exists():
        creds_file.write_text('[general]\nemail = ""\n', encoding="utf-8")

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(ui_app_path),
        "--server.port", str(port),
        "--server.address", host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    typer.echo(f"Launching: {' '.join(cmd)}")
    typer.echo(f"  -> http://{host}:{port}")
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    subprocess.run(cmd, check=False)


@ui_app.command("agent")
def app_agent(
    port: int = typer.Option(8502, "--port"),
    host: str = typer.Option("localhost", "--host"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="브라우저 자동 열기."),
) -> None:
    """streamlit run src/ui/agent_app.py — L1~L4 변경 영향 분석 페이지.

    requires 'ui' extra. 검색에 ENABLE_EMBEDDING(자동) + Ollama bge-m3 필요.
    """
    import subprocess
    import sys
    from pathlib import Path

    page = Path(__file__).resolve().parent / "ui" / "agent_app.py"
    if not page.exists():
        typer.echo(f"agent_app not found: {page}")
        raise typer.Exit(code=1)

    creds_dir = Path.home() / ".streamlit"
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds_file = creds_dir / "credentials.toml"
    if not creds_file.exists():
        creds_file.write_text('[general]\nemail = ""\n', encoding="utf-8")

    cmd = [
        sys.executable, "-m", "streamlit", "run", str(page),
        "--server.port", str(port),
        "--server.address", host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    typer.echo(f"Launching agent UI: http://{host}:{port}")
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    app()
