import { useMemo, useState } from "react";
import type { ApplyResult, BomRow } from "../types";
import { rowKind, KIND_META, type ChangeKind, levDepth, hasChildrenAt, descendantCountAt, computeVisible } from "../util";
import { Badge } from "./Badge";

const FILTERS: { key: ChangeKind | "all"; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "change", label: "변경" },
  { key: "add", label: "추가" },
  { key: "delete", label: "삭제" },
  { key: "tbd", label: "미정(TBD)" },
];

type ViewMode = "tree" | "changes";

function depthOf(r: BomRow): number {
  return typeof r.bom_depth === "number" ? r.bom_depth : levDepth(r.bom_level);
}

export function BomResultView({ result }: { result: ApplyResult }) {
  const rows = result.new_bom ?? [];
  const [view, setView] = useState<ViewMode>("tree"); // 기본: 전체 부품 트리(피드백 #4)
  const [filter, setFilter] = useState<ChangeKind | "all">("all");
  const [folded, setFolded] = useState<Set<number>>(new Set());

  const counts = useMemo(() => {
    const c: Record<string, number> = { change: 0, add: 0, delete: 0, tbd: 0, keep: 0 };
    rows.forEach((r) => { c[rowKind(r)] = (c[rowKind(r)] ?? 0) + 1; });
    return c;
  }, [rows]);

  // 트리 계산: 프리오더 평면 BOM을 깊이로 중첩.
  const depths = useMemo(() => rows.map(depthOf), [rows]);
  const vis = useMemo(
    () => computeVisible(depths, new Set([...folded].map(String)), (i) => String(i)),
    [depths, folded]
  );
  // 각 행의 후손 중 변경/미정 개수(접었을 때 요약 표시).
  const descChanges = useMemo(() => rows.map((_, i) => {
    let n = 0;
    for (let j = i + 1; j < rows.length; j++) {
      if (depths[j] > depths[i]) { if (rowKind(rows[j]) !== "keep") n++; } else break;
    }
    return n;
  }), [rows, depths]);

  function toggleFold(i: number) {
    setFolded((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }
  function setAllFold(open: boolean) {
    if (open) { setFolded(new Set()); return; }
    const all = new Set<number>();
    rows.forEach((_, i) => { if (hasChildrenAt(depths, i)) all.add(i); });
    setFolded(all);
  }

  // 변경만 보기(구 동작): 변경/추가/삭제/미정 행만.
  const changed = rows
    .map((r, i) => ({ row: r, kind: rowKind(r), idx: i }))
    .filter((x) => x.kind !== "keep");
  const shownChanges = filter === "all" ? changed : changed.filter((x) => x.kind === filter);

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>BOM 반영 결과</h3>
        <div className="result-counts">
          <span className="rc">총 {rows.length}행</span>
          <Badge kind="change" small /> {counts.change}
          <Badge kind="add" small /> {counts.add}
          <Badge kind="delete" small /> {counts.delete}
          <Badge kind="tbd" small /> {counts.tbd}
        </div>
      </div>

      {counts.tbd > 0 && (
        <div className="banner banner-warn">
          ⚠ 미정(TBD) 부품 {counts.tbd}개 — 하위 변경으로 상위 Assembly 품번이 재발행 대상입니다. PLM 신번 부여 필요.
        </div>
      )}

      <div className="result-toolbar">
        <div className="seg">
          <button className={view === "tree" ? "active" : ""} onClick={() => setView("tree")}>전체 트리</button>
          <button className={view === "changes" ? "active" : ""} onClick={() => setView("changes")}>변경만</button>
        </div>
        {view === "tree" && (
          <>
            <span className="spacer" />
            <button className="btn btn-ghost" onClick={() => setAllFold(true)}>전체 펼치기</button>
            <button className="btn btn-ghost" onClick={() => setAllFold(false)}>전체 접기</button>
          </>
        )}
      </div>

      {view === "tree" ? (
        <div className="bom-tree">
          {rows.length === 0 ? <div className="muted">결과 행이 없습니다.</div> :
            rows.map((row, i) => vis[i] && (
              <TreeRow
                key={i}
                row={row}
                depth={depths[i]}
                hasChildren={hasChildrenAt(depths, i)}
                folded={folded.has(i)}
                descCount={descendantCountAt(depths, i)}
                descChanges={descChanges[i]}
                onToggle={() => toggleFold(i)}
              />
            ))}
        </div>
      ) : (
        <>
          <div className="filter-bar">
            {FILTERS.map((f) => (
              <button key={f.key} className={`chip ${filter === f.key ? "active" : ""}`} onClick={() => setFilter(f.key)}>
                {f.label}{f.key !== "all" ? ` ${counts[f.key] ?? 0}` : ` ${changed.length}`}
              </button>
            ))}
          </div>
          <div className="changed-list">
            {shownChanges.length === 0 ? <div className="muted">표시할 변경 행이 없습니다.</div> :
              shownChanges.map(({ row, kind, idx }) => <ChangedRow key={idx} row={row} kind={kind} />)}
          </div>
        </>
      )}

      {result.unmatched && result.unmatched.length > 0 && (
        <details className="unmatched">
          <summary>매칭 실패 {result.unmatched.length}건</summary>
          <pre className="json">{JSON.stringify(result.unmatched, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

function TreeRow({
  row, depth, hasChildren, folded, descCount, descChanges, onToggle,
}: {
  row: BomRow; depth: number; hasChildren: boolean; folded: boolean;
  descCount: number; descChanges: number; onToggle: () => void;
}) {
  const kind = rowKind(row);
  const changed = kind !== "keep";
  return (
    <div className={`trow ${changed ? KIND_META[kind].cls + "-row" : "keep-row"}`} style={{ paddingLeft: 8 + depth * 18 }}>
      {hasChildren ? (
        <button className="dp-fold" title={folded ? "하위 펼치기" : "하위 접기"} onClick={onToggle}>{folded ? "▸" : "▾"}</button>
      ) : (
        <span className="dp-fold-spacer" />
      )}
      <Badge kind={kind} small />
      <span className="trow-name">{row.part_name || "-"}</span>
      <span className="mono trow-pno">
        {row.base_part_no || "-"}
        {kind !== "delete" && <> → <b className={kind === "tbd" ? "tbd-pno" : ""}>{row.new_part_no || "-"}</b></>}
      </span>
      <span className="muted trow-lvl">{row.bom_level || ""}</span>
      {folded && descCount > 0 && (
        <span className="dp-childcount">+{descCount} 하위{descChanges > 0 ? ` · 변경 ${descChanges}` : ""}</span>
      )}
      {(row.changing_reason || row.changing_point) && (
        <span className="trow-reason">{row.changing_reason || row.changing_point}</span>
      )}
    </div>
  );
}

function ChangedRow({ row, kind }: { row: BomRow; kind: ChangeKind }) {
  const depth = typeof row.bom_depth === "number" ? row.bom_depth : 0;
  return (
    <div className={`crow ${KIND_META[kind].cls}-row`} style={{ paddingLeft: 12 + depth * 16 }}>
      <Badge kind={kind} small />
      <span className="crow-name">{row.part_name || "-"}</span>
      <span className="mono crow-pno">
        {row.base_part_no || "-"}
        {kind !== "delete" && <> → <b className={kind === "tbd" ? "tbd-pno" : ""}>{row.new_part_no || "-"}</b></>}
      </span>
      <span className="muted crow-lvl">{row.bom_level || ""}</span>
      {(row.changing_reason || row.changing_point) && (
        <span className="crow-reason">{row.changing_reason || row.changing_point}</span>
      )}
    </div>
  );
}
