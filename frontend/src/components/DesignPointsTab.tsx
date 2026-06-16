import { useMemo, useRef, useState, type DragEvent } from "react";
import { api } from "../api";
import { useStore } from "../store";
import type { Candidate, DesignPointRecord, DesignRow, MasterChange } from "../types";
import {
  DP_META, dpClass, effectiveNewPno, isTbd, levDepth, makeLev, downloadBlob, toCsv,
  hasChildrenAt, descendantCountAt, computeVisible,
} from "../util";

const MASTER_COLUMNS = [
  "no", "design_point", "lev", "part_name", "base_part_no", "new_part_no", "qty", "change_point",
];

export function DesignPointsTab({ onSendToApply }: { onSendToApply?: () => void }) {
  const store = useStore();
  // 작업 상태는 store에 보관 → 탭을 오가도 유지(#5).
  const rows = store.dpRows;
  const setRows = store.setDpRows;
  const phase = store.dpPhase;
  const setPhase = store.setDpPhase;
  const summaries = store.dpSummaries;
  const filename = store.dpFilename;

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set()); // 모듈 기본 접힘
  const [folded, setFolded] = useState<Set<number>>(new Set());          // 카드 하위 접힘(부모 _id)
  const pptxRef = useRef<HTMLInputElement>(null);
  const baseRef = useRef<HTMLInputElement>(null);

  // #2: 기존 품번 파악용 Base BOM을 ① 단계에서 PPT와 함께 올린다(store에 보관 → ②로 유지).
  async function onLoadBase() {
    const file = baseRef.current?.files?.[0];
    if (!file) { setErr("Base BOM 엑셀(.xlsx)을 선택하세요."); return; }
    setBusy(true); setErr(null);
    try {
      const parsed = await api.parseBaseBomXlsx(file);
      store.setBaseBomRows(parsed.rows ?? []);
      store.setBaseBomSummary(parsed.summary ?? null);
      setMsg(`Base BOM ${parsed.rows?.length ?? 0}행 불러옴: ${parsed.summary?.root_part_name ?? ""}`);
    } catch (e) {
      setErr(`Base BOM 파싱 실패: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  async function onExtract() {
    const file = pptxRef.current?.files?.[0];
    if (!file) { setErr("심의표 PPTX 파일을 선택하세요."); return; }
    setBusy(true); setErr(null); setMsg(null);
    try {
      const result = await api.extractPptxBom(file);
      const records = result.records ?? [];
      if (!records.length) { setErr("Design Points 표를 찾지 못했습니다. 슬라이드에 BOM 표가 있는지 확인하세요."); return; }
      // 검수 단계: New P/No 자동 분류(TBD/빨간 표시)는 아직 적용하지 않고 원본 그대로.
      setRows(records.map((r, i) => ({ ...r, _id: i, _newPno: "" })));
      store.setDpNextId(records.length);
      store.setDpSummaries(result.summaries ?? {});
      store.setDpFilename(result.filename ?? file.name);
      setPhase("review");
      setOpenGroups(new Set());
      setFolded(new Set());
      setMsg(`변경점 ${records.length}개 추출 — 모듈 ${result.design_points?.length ?? 0}개. 제대로 추출됐는지 확인하고 수정/추가/삭제하세요.`);
    } catch (e) {
      setErr(`추출 실패: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  // --- 검수 단계 편집 ---
  function setField(id: number, field: keyof DesignPointRecord, value: string) {
    setRows(rows.map((r) => (r._id === id ? { ...r, [field]: value } : r)));
  }
  function deleteRow(id: number) {
    setRows(rows.filter((r) => r._id !== id));
  }
  function addRow(designPoint: string, newPno = "") {
    const id = store.dpNextId;
    store.setDpNextId(id + 1);
    const blank: DesignRow = {
      _id: id, _newPno: newPno,
      no: "", design_point: designPoint, lev: "", part_name: "",
      base_part_no: "", new_part_no: "", uom: "", qty: "", change_point: "",
    };
    let lastIdx = -1;
    rows.forEach((r, i) => { if ((r.design_point || "") === designPoint) lastIdx = i; });
    if (lastIdx < 0) { setRows([...rows, blank]); return; }
    const copy = rows.slice();
    copy.splice(lastIdx + 1, 0, blank);
    setRows(copy);
  }
  // 작성 단계에서 부품 추가: 신규로 보이게 TBD 기본값, 해당 모듈은 펼친다.
  function addPartEdit(designPoint: string) {
    addRow(designPoint, "TBD");
    setOpenGroups((prev) => new Set(prev).add(designPoint));
  }

  // #6/#7: 행 위치/레벨/상위 부품 편집 (드래그앤드롭 대신 버튼/선택).
  function insertBelow(id: number) {
    const idx = rows.findIndex((r) => r._id === id);
    if (idx < 0) return;
    const src = rows[idx];
    const nid = store.dpNextId;
    store.setDpNextId(nid + 1);
    const blank: DesignRow = {
      _id: nid, _newPno: "",
      no: "", design_point: src.design_point, lev: src.lev || "", part_name: "",
      base_part_no: "", new_part_no: "", uom: "", qty: "", change_point: "",
    };
    const copy = rows.slice();
    copy.splice(idx + 1, 0, blank);
    setRows(copy);
  }
  function moveRow(id: number, dir: -1 | 1) {
    const idx = rows.findIndex((r) => r._id === id);
    const j = idx + dir;
    if (idx < 0 || j < 0 || j >= rows.length) return;
    const copy = rows.slice();
    [copy[idx], copy[j]] = [copy[j], copy[idx]];
    setRows(copy);
  }
  function setLev(id: number, lev: string) {
    setRows(rows.map((r) => (r._id === id ? { ...r, lev } : r)));
  }
  // --- 드래그 앤 드롭으로 카드 이동(위/아래/모듈 간) ---
  const [dragId, setDragId] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: number; pos: "before" | "after" } | null>(null);
  function onCardDragStart(id: number) { setDragId(id); }
  function onCardDragEnd() { setDragId(null); setDropTarget(null); }
  function onCardDragOver(id: number, e: DragEvent) {
    e.preventDefault();
    if (dragId == null || dragId === id) { if (dropTarget) setDropTarget(null); return; }
    const rect = e.currentTarget.getBoundingClientRect();
    const pos: "before" | "after" = e.clientY < rect.top + rect.height / 2 ? "before" : "after";
    if (!dropTarget || dropTarget.id !== id || dropTarget.pos !== pos) setDropTarget({ id, pos });
  }
  function onCardDrop(id: number, e: DragEvent) {
    e.preventDefault();
    if (dragId == null) { onCardDragEnd(); return; }
    const pos = dropTarget && dropTarget.id === id ? dropTarget.pos : "after";
    moveByDrag(dragId, id, pos);
    onCardDragEnd();
  }
  // 계층 인식 이동: 아래쪽 드롭=타겟의 하위(자식, depth+1), 위쪽=형제(동일 depth).
  // 끌어온 카드의 하위 부품들도 블록째 함께 이동하고 깊이를 상대적으로 재계산한다.
  function moveByDrag(did: number, tid: number, pos: "before" | "after") {
    if (did === tid) return;
    const list = rows.slice();
    const di = list.findIndex((r) => r._id === did);
    const ti0 = list.findIndex((r) => r._id === tid);
    if (di < 0 || ti0 < 0) return;

    const dragRow = list[di];
    const dragDepth = levDepth(dragRow.lev);
    const dragDp = dragRow.design_point || "";
    // 드래그 블록(자신 + 후손): 같은 모듈에서 더 깊은 연속 구간.
    let end = di + 1;
    while (end < list.length && (list[end].design_point || "") === dragDp && levDepth(list[end].lev) > dragDepth) end++;
    const block = list.slice(di, end);
    // 자기 자신/후손 위로는 못 넣는다.
    if (block.some((b) => b._id === tid)) return;

    const target = list[ti0];
    const targetDepth = levDepth(target.lev);
    const targetDp = target.design_point || "";
    const newDragDepth = pos === "after" ? targetDepth + 1 : targetDepth; // 아래=자식 / 위=형제
    const delta = newDragDepth - dragDepth;
    const moved = block.map((b) => ({
      ...b,
      design_point: targetDp,
      lev: makeLev(Math.max(1, levDepth(b.lev) + delta)),
    }));

    const without = [...list.slice(0, di), ...list.slice(end)];
    const tIdx = without.findIndex((r) => r._id === tid);
    const insertAt = pos === "after" ? tIdx + 1 : tIdx; // 자식이면 타겟 바로 뒤(첫 자식)
    setRows([...without.slice(0, insertAt), ...moved, ...without.slice(insertAt)]);
  }

  // 상위 부품 지정: 해당 행을 parent의 자식 깊이로 바꾸고 parent 바로 뒤로 이동.
  function setParent(id: number, parentId: number) {
    const parent = rows.find((r) => r._id === parentId);
    if (!parent || id === parentId) return;
    const newLev = makeLev(levDepth(parent.lev) + 1);
    const list = rows.slice();
    const ri = list.findIndex((r) => r._id === id);
    const [row] = list.splice(ri, 1);
    const pi = list.findIndex((r) => r._id === parentId);
    list.splice(pi + 1, 0, { ...row, lev: newLev, design_point: parent.design_point });
    setRows(list);
  }

  function confirmReview() {
    // 검수 완료: New P/No를 자동 분류(공용=base, 신규=TBD)하고 작성 단계로.
    setRows(rows.map((r) => ({ ...r, _newPno: effectiveNewPno(r.new_part_no, r.base_part_no) })));
    setOpenGroups(new Set());
    setFolded(new Set());
    setPhase("edit");
    setMsg("검수 완료 — 각 변경점의 New P/No를 확인·부여하세요. 새 번호가 필요하면 TBD로 둡니다.");
  }

  // --- 변경점 작성(edit) ---
  function setNewPno(id: number, value: string) {
    setRows(rows.map((r) => (r._id === id ? { ...r, _newPno: value } : r)));
  }

  const counts = useMemo(() => {
    const c = { new: 0, common: 0, changed: 0, tbd: 0 };
    rows.forEach((r) => {
      c[dpClass(r._newPno, r.base_part_no)]++;
      if (isTbd(r._newPno)) c.tbd++;
    });
    return c;
  }, [rows]);

  // design_point(모듈)별로 묶는다. 삽입 순서 유지.
  const groups = useMemo(() => {
    const map = new Map<string, DesignRow[]>();
    for (const r of rows) {
      if (phase === "edit" && onlyChanged && dpClass(r._newPno, r.base_part_no) === "common") continue;
      const key = r.design_point || "(모듈 미상)";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(r);
    }
    return [...map.entries()];
  }, [rows, onlyChanged, phase]);

  function toggleGroup(key: string) {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }
  function setAllGroups(open: boolean) {
    setOpenGroups(open ? new Set(groups.map(([k]) => k)) : new Set());
    if (open) setFolded(new Set());
  }
  function toggleFold(id: number) {
    setFolded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function buildMaster(): MasterChange[] {
    return rows.map((r) => ({
      no: String(r.no ?? ""),
      design_point: r.design_point,
      lev: r.lev,
      bom_level: r.lev,
      part_name: r.part_name,
      base_part_no: r.base_part_no,
      new_part_no: r._newPno,
      qty: r.qty,
      change_point: r.change_point,
    }));
  }

  function sendToApply() {
    store.setMasterChanges(buildMaster());
    store.setApplyResult(null);
    onSendToApply?.();
  }

  function onExportMaster() {
    const out = rows.map((r) => ({
      no: r.no, design_point: r.design_point, lev: r.lev, part_name: r.part_name,
      base_part_no: r.base_part_no, new_part_no: r._newPno, qty: r.qty, change_point: r.change_point,
    }));
    const blob = new Blob(["﻿" + toCsv(out, MASTER_COLUMNS)], { type: "text/csv;charset=utf-8" });
    downloadBlob(blob, `master_${(filename || "design_points").replace(/\.[^.]+$/, "")}.csv`);
  }

  return (
    <div className="tab">
      <h2>변경점 작성 (master)</h2>
      <p className="lede">
        심의표의 <b>Design Points(변경점)</b>를 그대로 master 초안으로 가져옵니다.
        먼저 <b>제대로 추출됐는지 검수</b>한 뒤, 각 변경점의 <b>New P/No</b>를 확인·부여하세요.
        새 번호가 필요하면 <b>TBD</b>로 둡니다.
      </p>
      {msg && <div className="banner banner-ok">{msg}</div>}
      {err && <div className="banner banner-error">{err}</div>}

      <div className="dp-uploads">
        <section className="panel dp-upload">
          <h3>① 심의표 PPTX → 변경점 추출</h3>
          <div className="inline">
            <input ref={pptxRef} type="file" accept=".pptx" />
            <button className="btn btn-primary" disabled={busy} onClick={onExtract}>
              {busy ? "추출 중…" : "Design Points 추출"}
            </button>
          </div>
        </section>

        <section className="panel dp-upload">
          <h3>② Base BOM (기존 품번) <span className="muted small">— 선택</span></h3>
          <p className="muted small" style={{ margin: "0 0 8px" }}>
            기존 품번을 함께 보려면 Base BOM 엑셀을 같이 올리세요. 퀵존 심의표는 PPT에 품번이 있어 생략 가능합니다.
          </p>
          <div className="inline">
            <input ref={baseRef} type="file" accept=".xlsx" />
            <button className="btn" disabled={busy} onClick={onLoadBase}>Base BOM 불러오기</button>
          </div>
          {store.baseBomRows && store.baseBomRows.length > 0 && (
            <div className="dp-base-status">
              ✓ Base BOM {store.baseBomRows.length}행 연결됨
              {store.baseBomSummary?.root_part_name ? ` · ${String(store.baseBomSummary.root_part_name)}` : ""}
              <span className="muted small"> — ② BOM 반영에서 그대로 사용됩니다.</span>
            </div>
          )}
        </section>
      </div>

      {rows.length > 0 && (
        <div className="dp-steps">
          <span className={`dp-step ${phase === "review" ? "active" : "done"}`}>① 추출 검수</span>
          <span className="dp-step-arrow">→</span>
          <span className={`dp-step ${phase === "edit" ? "active" : ""}`}>② 변경점 작성 (New P/No)</span>
        </div>
      )}

      {/* ─────────── 검수 단계 ─────────── */}
      {rows.length > 0 && phase === "review" && (
        <>
          <div className="dp-summary">
            <span className="muted small">추출 {rows.length}행 · 모듈 {groups.length}개 — 표가 제대로 읽혔는지 확인하세요.</span>
            <span className="spacer" />
            <button className="btn btn-primary" onClick={confirmReview}>검수 완료 — 변경점 작성 →</button>
          </div>

          {groups.map(([dp, list]) => (
            <section className="panel dp-group" key={dp}>
              <h3 className="dp-group-title">{dp} <span className="muted small">({list.length})</span></h3>
              {summaries[dp] && (
                <div className="dp-summary-box">
                  <span className="dp-summary-tag">변경점 요약</span>
                  <span className="dp-summary-text">{summaries[dp]}</span>
                </div>
              )}
              <div className="dp-review-table">
                <div className="dp-rv-head">
                  <span className="rv-no">No</span>
                  <span className="rv-lev">Lev</span>
                  <span className="rv-name">Part Name</span>
                  <span className="rv-pno">Base P/No</span>
                  <span className="rv-pno">New P/No</span>
                  <span className="rv-qty">Qty</span>
                  <span className="rv-cp">상세 변경점</span>
                  <span className="rv-act">위치/삭제</span>
                </div>
                {list.map((r, i) => (
                  <div className="dp-rv-row" key={r._id} style={{ paddingLeft: levDepth(r.lev) * 12 }}>
                    <input className="rv-no" value={r.no ?? ""} onChange={(e) => setField(r._id, "no", e.target.value)} />
                    <input className="rv-lev" title="Lev(깊이). 비우거나 .1 / ..2 형식" value={r.lev ?? ""} onChange={(e) => setField(r._id, "lev", e.target.value)} />
                    <input className="rv-name" value={r.part_name ?? ""} onChange={(e) => setField(r._id, "part_name", e.target.value)} />
                    <input className="rv-pno mono" value={r.base_part_no ?? ""} onChange={(e) => setField(r._id, "base_part_no", e.target.value)} />
                    <input className="rv-pno mono" value={r.new_part_no ?? ""} onChange={(e) => setField(r._id, "new_part_no", e.target.value)} />
                    <input className="rv-qty" value={r.qty ?? ""} onChange={(e) => setField(r._id, "qty", e.target.value)} />
                    <input className="rv-cp" value={r.change_point ?? ""} onChange={(e) => setField(r._id, "change_point", e.target.value)} />
                    <span className="rv-act">
                      <button className="rv-ico" title="위로" disabled={i === 0} onClick={() => moveRow(r._id, -1)}>↑</button>
                      <button className="rv-ico" title="아래로" disabled={i === list.length - 1} onClick={() => moveRow(r._id, 1)}>↓</button>
                      <button className="rv-ico" title="아래에 행 삽입" onClick={() => insertBelow(r._id)}>＋</button>
                      <button className="rv-ico rv-icodel" title="이 행 삭제" onClick={() => deleteRow(r._id)}>✕</button>
                    </span>
                  </div>
                ))}
                <button className="btn btn-ghost btn-addrow" onClick={() => addRow(dp)}>＋ 이 모듈에 행 추가</button>
              </div>
            </section>
          ))}

          <div className="dp-summary">
            <span className="spacer" />
            <button className="btn btn-primary" onClick={confirmReview}>검수 완료 — 변경점 작성 →</button>
          </div>
        </>
      )}

      {/* ─────────── 변경점 작성 단계 ─────────── */}
      {rows.length > 0 && phase === "edit" && (
        <>
          <div className="dp-summary">
            <button className="btn btn-ghost" onClick={() => setPhase("review")}>← 검수로</button>
            <span className="dp-chip dp-new">신규 {counts.new}</span>
            <span className="dp-chip dp-common">공용 {counts.common}</span>
            <span className="dp-chip dp-changed">공용/변경 {counts.changed}</span>
            <span className="dp-chip dp-tbd">⚠ TBD(번호 미정) {counts.tbd}</span>
            <label className="check"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} /> 공용(변경없음) 숨기기</label>
            <span className="spacer" />
            <button className="btn btn-ghost" onClick={() => setAllGroups(true)}>전체 펼치기</button>
            <button className="btn btn-ghost" onClick={() => setAllGroups(false)}>전체 접기</button>
            <button className="btn" onClick={onExportMaster}>master CSV</button>
            <button className="btn btn-primary" onClick={sendToApply}>② 마스터 산출로 보내기 →</button>
          </div>

          {groups.map(([dp, list]) => {
            const gOpen = openGroups.has(dp);
            const gc = { new: 0, common: 0, changed: 0, tbd: 0 };
            list.forEach((r) => { gc[dpClass(r._newPno, r.base_part_no)]++; if (isTbd(r._newPno)) gc.tbd++; });

            // 모듈 내부 부모-자식(레벨) 접힘 계산.
            const depths = list.map((r) => levDepth(r.lev));
            const foldKeys = new Set(list.filter((r) => folded.has(r._id)).map((r) => String(r._id)));
            const vis = computeVisible(depths, foldKeys, (i) => String(list[i]._id));

            return (
              <section className={`panel dp-group ${gOpen ? "" : "dp-group-collapsed"}`} key={dp}>
                <div className="dp-group-head" onClick={() => toggleGroup(dp)}>
                  <span className="dp-toggle">{gOpen ? "▾" : "▸"}</span>
                  <span className="dp-group-name">{dp}</span>
                  <span className="muted small">({list.length})</span>
                  <span className="dp-group-counts">
                    {gc.new > 0 && <span className="dp-mini dp-new">신규 {gc.new}</span>}
                    {gc.changed > 0 && <span className="dp-mini dp-changed">변경 {gc.changed}</span>}
                    {gc.common > 0 && <span className="dp-mini dp-common">공용 {gc.common}</span>}
                    {gc.tbd > 0 && <span className="dp-mini dp-tbd">⚠ TBD {gc.tbd}</span>}
                  </span>
                  {!gOpen && summaries[dp] && (
                    <span className="dp-group-summary muted small">— {summaries[dp].replace(/\n/g, " ")}</span>
                  )}
                </div>
                {gOpen && (
                  <>
                    {summaries[dp] && (
                      <div className="dp-summary-box">
                        <span className="dp-summary-tag">변경점 요약</span>
                        <span className="dp-summary-text">{summaries[dp]}</span>
                      </div>
                    )}
                    <div className="dp-list">
                      {list.map((r, i) => vis[i] && (
                        <ChangePointCard
                          key={r._id}
                          row={r}
                          moduleSummary={summaries[dp]}
                          depth={depths[i]}
                          hasChildren={hasChildrenAt(depths, i)}
                          folded={folded.has(r._id)}
                          descCount={descendantCountAt(depths, i)}
                          siblings={list}
                          dragging={dragId === r._id}
                          dropPos={dropTarget && dropTarget.id === r._id ? dropTarget.pos : null}
                          onToggleFold={() => toggleFold(r._id)}
                          onNewPno={(v) => setNewPno(r._id, v)}
                          onSetLev={(v) => setLev(r._id, v)}
                          onSetParent={(pid) => setParent(r._id, pid)}
                          onDragStart={() => onCardDragStart(r._id)}
                          onDragEnd={onCardDragEnd}
                          onDragOver={(e) => onCardDragOver(r._id, e)}
                          onDrop={(e) => onCardDrop(r._id, e)}
                        />
                      ))}
                    </div>
                    <button className="btn btn-ghost btn-addrow" onClick={() => addPartEdit(dp)}>＋ 이 모듈에 부품 추가</button>
                  </>
                )}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}

function pastModelLabel(c: Candidate): string {
  const pm = c.past_model || {};
  const model = [pm.base_model, pm.new_model].filter(Boolean).join(" → ");
  return pm.project_name || model || pm.source_file || "과거 모델";
}

function ChangePointCard({
  row, moduleSummary, depth, hasChildren, folded, descCount, siblings, dragging, dropPos,
  onToggleFold, onNewPno, onSetLev, onSetParent,
  onDragStart, onDragEnd, onDragOver, onDrop,
}: {
  row: DesignRow; moduleSummary?: string; depth: number; hasChildren: boolean; folded: boolean; descCount: number;
  siblings: DesignRow[];
  dragging: boolean; dropPos: "before" | "after" | null;
  onToggleFold: () => void; onNewPno: (v: string) => void;
  onSetLev: (v: string) => void; onSetParent: (parentId: number) => void;
  onDragStart: () => void; onDragEnd: () => void;
  onDragOver: (e: DragEvent) => void; onDrop: (e: DragEvent) => void;
}) {
  const cls = dpClass(row._newPno, row.base_part_no);
  const tbd = isTbd(row._newPno);
  const [refs, setRefs] = useState<Candidate[] | null>(null);
  const [refOpen, setRefOpen] = useState(false);
  const [refBusy, setRefBusy] = useState(false);
  const [structOpen, setStructOpen] = useState(false);
  const { corpus } = useStore();  // 사이드바에서 고른 검색 코퍼스(sqlite|lg)
  // [변경사유로 후보 추천] 전용 상태 — 사용자가 직접 입력(자동 채움 없음).
  const [reasonOpen, setReasonOpen] = useState(false);
  const [reasonQuery, setReasonQuery] = useState("");
  const [reasonRefs, setReasonRefs] = useState<Candidate[] | null>(null);
  const [reasonBusy, setReasonBusy] = useState(false);
  // 상위 후보: 같은 모듈의 다른 행(자기 자신 제외).
  const parentOptions = siblings.filter((s) => s._id !== row._id);

  async function loadRefs() {
    setRefOpen(true);
    if (refs !== null) return;
    setRefBusy(true);
    try {
      // 처음 폴더 코드 그대로 복원: 부품 기준, auto_embed=false(parts-only), corpus 미지정(sqlite 고정).
      // dense(의미)와 코퍼스 토글은 '변경사유로 후보 추천' 버튼에만 적용한다.
      const res = await api.recommend(
        [{ change_id: String(row.no || row._id), part_name: row.part_name, base_part_no: row.base_part_no, change_point: row.change_point, change_reason: row.change_point }],
        8, 0, false
      );
      setRefs(res.results?.[0]?.candidates ?? []);
    } catch {
      setRefs([]);
    } finally { setRefBusy(false); }
  }

  // [변경사유로 후보 추천] 사용자가 입력한 변경사유로 검색(부품정보도 함께). corpus 반영.
  async function loadReasonRefs() {
    const q = reasonQuery.trim();
    if (!q) return;
    setReasonBusy(true);
    try {
      const res = await api.recommend(
        [{ change_id: String(row.no || row._id), part_name: row.part_name, base_part_no: row.base_part_no, change_point: q, change_reason: q }],
        8, 0, true, corpus
      );
      setReasonRefs(res.results?.[0]?.candidates ?? []);
    } catch {
      setReasonRefs([]);
    } finally { setReasonBusy(false); }
  }

  return (
    <div
      className={`dp-card ${cls}-card ${tbd ? "tbd-card" : ""} ${dragging ? "dp-dragging" : ""} ${dropPos ? "dp-drop-" + dropPos : ""}`}
      style={{ marginLeft: depth * 16 }}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {dropPos && (
        <span className={`dp-drophint dp-drophint-${dropPos}`}>
          {dropPos === "after" ? "↳ 하위로" : "↑ 형제로"}
        </span>
      )}
      <div className="dp-row dp-header">
        <span
          className="dp-drag"
          title="드래그해서 위/아래로 이동"
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >⠿</span>
        {hasChildren ? (
          <button className="dp-fold" title={folded ? "하위 펼치기" : "하위 접기"} onClick={onToggleFold}>
            {folded ? "▸" : "▾"}
          </button>
        ) : (
          <span className="dp-fold-spacer" />
        )}
        <span className={`badge ${DP_META[cls].cls} badge-sm`}>{DP_META[cls].label}</span>
        <span className="dp-lev muted">{row.lev}</span>
        <span className="dp-part">{row.part_name || "-"}</span>
        {folded && descCount > 0 && <span className="dp-childcount">+{descCount} 하위</span>}
        <span className="dp-no muted small">#{row.no}</span>
      </div>

      <div className="dp-body">
        {row.change_point && <div className="dp-change">{row.change_point}</div>}
        <div className="dp-pno">
          <span className="mono dp-base">{row.base_part_no || "(신규)"}</span>
          <span className="dp-arrow">→</span>
          <input
            className={`mono dp-newinput ${tbd ? "tbd-input" : ""}`}
            value={row._newPno}
            placeholder="New P/No"
            onChange={(e) => onNewPno(e.target.value)}
          />
          {tbd && <span className="tbd-note">번호 미정 — 회의에서 부여</span>}
          {!tbd && cls === "common" && <span className="common-note">공용 — base 재사용</span>}
        </div>

        <div className="dp-foot">
          <button className="link" onClick={() => { if (refOpen) { setRefOpen(false); } else { setReasonOpen(false); loadRefs(); } }}>
            {refOpen ? "과거 참조 닫기" : "과거엔 어떻게 바뀌었나? 참조"}
          </button>
          <button className="link" onClick={() => setStructOpen((v) => !v)}>
            {structOpen ? "구조 닫기" : "Level/상위 부품 지정"}
          </button>
          <button className="link" onClick={() => { const nv = !reasonOpen; setReasonOpen(nv); if (nv) setRefOpen(false); }}>
            {reasonOpen ? "변경사유 추천 닫기" : "변경사유로 후보 추천"}
          </button>
        </div>

        {structOpen && (
          <div className="dp-struct">
            <label className="dp-struct-field">
              <span>Level</span>
              <input className="dp-struct-lev" value={row.lev ?? ""} placeholder=".1 / ..2"
                onChange={(e) => onSetLev(e.target.value)} />
            </label>
            <label className="dp-struct-field">
              <span>상위 부품</span>
              <select className="dp-struct-parent" value=""
                onChange={(e) => { if (e.target.value) onSetParent(Number(e.target.value)); }}>
                <option value="">선택해서 이 부품을 자식으로…</option>
                {parentOptions.map((p) => (
                  <option key={p._id} value={p._id}>{p.lev ? `${p.lev} ` : ""}{p.part_name || "(이름없음)"}</option>
                ))}
              </select>
            </label>
            <span className="muted small">상위를 고르면 이 부품이 그 아래 자식 레벨로 이동합니다.</span>
          </div>
        )}

        {reasonOpen && (
          <div className="dp-reason-search" style={{ marginTop: 6 }}>
            <textarea
              className="rv-cp"
              rows={2}
              style={{ width: "100%", fontSize: 12, boxSizing: "border-box" }}
              value={reasonQuery}
              placeholder={moduleSummary
                ? `변경사유 입력 (예: ${String(moduleSummary).slice(0, 40)})`
                : "변경사유 입력 (예: 제품 높이 축소에 따른 치수 변경)"}
              onChange={(e) => setReasonQuery(e.target.value)}
            />
            <button className="btn btn-mini" disabled={reasonBusy || !reasonQuery.trim()} onClick={loadReasonRefs}>
              {reasonBusy ? "검색 중…" : `변경사유로 검색 (${corpus})`}
            </button>
            {reasonRefs && (reasonRefs.length === 0 ? (
              <span className="muted small"> 결과 없음</span>
            ) : (
              <div className="dp-refs">
                {reasonRefs.map((c) => (
                  <div key={c.detail_id} className="dp-ref">
                    <div className="dp-ref-main">
                      <span className="dp-ref-lev muted small">{c.bom_level || (c.level != null ? `L${c.level}` : "")}</span>
                      <span className="dp-ref-name">{c.part_name || "-"}</span>
                      <span className="mono dp-ref-pno">{c.base_part_no || "-"} → {c.new_part_no || "-"}</span>
                      {c.new_part_no && (
                        <button className="btn-mini" onClick={() => onNewPno(String(c.new_part_no))}>이 번호 가져오기</button>
                      )}
                    </div>
                    <div className="dp-ref-meta">
                      <span className="dp-ref-model">📁 {pastModelLabel(c)}</span>
                      {(c.change_reason || c.change_point) && <span className="dp-ref-reason">{c.change_reason || c.change_point}</span>}
                      {c.score != null && <span className="muted small">score {c.score.toFixed(2)}</span>}
                      {c.source_file && <span className="muted small" title={c.source_file}>· 출처 {c.source_file}</span>}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {refOpen && (
          <div className="dp-refs">
            {refBusy ? <span className="muted small">불러오는 중…</span> :
              !refs || refs.length === 0 ? <span className="muted small">과거 참조 내역 없음</span> :
              refs.map((c) => (
                <div key={c.detail_id} className="dp-ref">
                  <div className="dp-ref-main">
                    <span className="dp-ref-lev muted small">{c.bom_level || (c.level != null ? `L${c.level}` : "")}</span>
                    <span className="dp-ref-name">{c.part_name || "-"}</span>
                    <span className="mono dp-ref-pno">{c.base_part_no || "-"} → {c.new_part_no || "-"}</span>
                    {c.new_part_no && (
                      <button className="btn-mini" onClick={() => onNewPno(String(c.new_part_no))}>이 번호 가져오기</button>
                    )}
                  </div>
                  <div className="dp-ref-meta">
                    <span className="dp-ref-model">📁 {pastModelLabel(c)}</span>
                    {(c.change_reason || c.change_point) && <span className="dp-ref-reason">{c.change_reason || c.change_point}</span>}
                    {c.score != null && <span className="muted small">score {c.score.toFixed(2)}</span>}
                    {c.source_file && <span className="muted small" title={c.source_file}>· 출처 {c.source_file}</span>}
                  </div>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
