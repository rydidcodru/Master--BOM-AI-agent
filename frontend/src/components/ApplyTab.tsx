import { useMemo, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { dpClass, isTbd, downloadBlob } from "../util";
import { BomResultView } from "./BomResultView";

export function ApplyTab() {
  const store = useStore();
  const { masterChanges, masters, baseBomRows, baseBomSummary, applyResult } = store;
  const [selectedMasterId, setSelectedMasterId] = useState<number | null>(null);
  const [propagate, setPropagate] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const counts = useMemo(() => {
    const c = { new: 0, common: 0, changed: 0, tbd: 0 };
    masterChanges.forEach((m) => {
      c[dpClass(m.new_part_no, m.base_part_no)]++;
      if (isTbd(m.new_part_no)) c.tbd++;
    });
    return c;
  }, [masterChanges]);

  const hasUploadedBase = !!(baseBomRows && baseBomRows.length > 0);
  const baseReady = hasUploadedBase || selectedMasterId != null;

  function basePayload() {
    return hasUploadedBase
      ? { base_bom: baseBomRows as unknown[] }
      : { base_master_id: selectedMasterId };
  }
  function payload() {
    return { ...basePayload(), changes: masterChanges, propagate_change_upward: propagate };
  }

  async function onApply() {
    if (!masterChanges.length) { setErr("먼저 ① 변경점 작성에서 master를 보내세요."); return; }
    if (!baseReady) { setErr("Base BOM이 필요합니다. ① 에서 올리거나 아래에서 SQLite master를 고르세요."); return; }
    setBusy(true); setErr(null);
    try {
      const result = await api.applyMaster(payload());
      store.setApplyResult(result);
      const skipped = (result as any).skipped_common ?? 0;
      setMsg(`산출 완료: BOM ${result.new_bom.length}행, 변경 ${result.change_list.length}건` +
        (skipped ? `, 공용(변화없음) ${skipped}건 건너뜀` : "") +
        (result.unmatched.length ? `, 미매칭 ${result.unmatched.length}건` : ""));
    } catch (e) {
      setErr(`산출 실패: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  async function onExportMaster(fmt: "xlsx" | "csv") {
    if (!masterChanges.length || !baseReady) { setErr("master와 Base BOM을 먼저 준비하세요."); return; }
    setBusy(true); setErr(null);
    try {
      const blob = await api.masterExport(payload(), fmt);
      downloadBlob(blob, `개발부품마스터.${fmt}`);
    } catch (e) {
      setErr(`개발부품마스터 다운로드 실패: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  async function onExportBom() {
    if (!masterChanges.length || !baseReady) { setErr("master와 Base BOM을 먼저 준비하세요."); return; }
    setBusy(true); setErr(null);
    try {
      const blob = await api.applyMasterExport(payload());
      downloadBlob(blob, "적용된_BOM.xlsx");
    } catch (e) {
      setErr(`적용 BOM 다운로드 실패: ${(e as Error).message}`);
    } finally { setBusy(false); }
  }

  if (!masterChanges.length) {
    return (
      <div className="tab">
        <h2>개발부품마스터 산출</h2>
        <div className="banner banner-warn">
          먼저 <b>① 변경점 작성</b>에서 변경점을 추출·번호 부여한 뒤
          <b> 「② BOM 반영으로 보내기 →」</b> 버튼을 누르세요.
        </div>
      </div>
    );
  }

  return (
    <div className="tab">
      <h2>개발부품마스터 산출</h2>
      <p className="lede">
        ① 에서 작성한 <b>변경점(master)</b>과 <b>Base BOM</b>으로
        <b> 개발부품마스터</b>(변경내역을 템플릿 양식에 채움)와 <b>적용된 BOM</b>을 산출합니다.
        공용(변경없음)은 건너뛰고, 하위 변경은 상위 Assembly로 전파해 미정(TBD)으로 표시합니다.
      </p>
      {msg && <div className="banner banner-ok">{msg}</div>}
      {err && <div className="banner banner-error">{err}</div>}

      <section className="panel">
        <div className="panel-head">
          <h3>변경점 master (① 에서 받음)</h3>
          <div className="result-counts">
            <span className="dp-chip dp-new">신규 {counts.new}</span>
            <span className="dp-chip dp-common">공용 {counts.common}</span>
            <span className="dp-chip dp-changed">변경 {counts.changed}</span>
            <span className="dp-chip dp-tbd">⚠ TBD {counts.tbd}</span>
            <span className="muted small">총 {masterChanges.length}행</span>
          </div>
        </div>
      </section>

      <section className="panel">
        <h3>Base BOM</h3>
        {hasUploadedBase ? (
          <div className="dp-base-status">
            ✓ Base BOM {baseBomRows!.length}행 연결됨 (① 변경점 작성에서 업로드)
            {baseBomSummary?.root_part_name ? ` · ${String(baseBomSummary.root_part_name)}` : ""}
          </div>
        ) : (
          <>
            <div className="banner banner-warn" style={{ marginTop: 0 }}>
              ① 변경점 작성 탭에서 Base BOM을 올리면 자동 연결됩니다. 또는 아래에서 SQLite master를 base로 선택하세요.
            </div>
            <select className="select" value={selectedMasterId ?? ""} onChange={(e) => setSelectedMasterId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">SQLite master 선택…</option>
              {masters.map((m) => (
                <option key={m.master_id} value={m.master_id}>
                  {m.master_id} · {m.project_name || m.source_file} · {m.base_model} → {m.new_model}
                </option>
              ))}
            </select>
          </>
        )}
        <label className="check" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={propagate} onChange={(e) => setPropagate(e.target.checked)} />
          하위 변경 시 상위 Assembly로 전파(미정/TBD 표시)
        </label>
      </section>

      <div className="apply-bar">
        <button className="btn btn-primary" disabled={busy} onClick={onApply}>master 산출 / BOM 반영</button>
        <span className="apply-bar-sep" />
        <button className="btn" disabled={busy} onClick={() => onExportMaster("xlsx")}>개발부품마스터 (xlsx)</button>
        <button className="btn" disabled={busy} onClick={() => onExportMaster("csv")}>개발부품마스터 (csv)</button>
        <button className="btn" disabled={busy} onClick={onExportBom}>적용된 BOM (xlsx)</button>
      </div>

      {applyResult && <BomResultView result={applyResult} />}
    </div>
  );
}
