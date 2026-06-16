import { useRef, useState } from "react";
import { api } from "../api";
import { useStore, CHANGE_COLUMNS } from "../store";
import type { ChangeInput, RecommendResult } from "../types";
import { changeLabel, num, parseCsv } from "../util";

const COL_LABEL: Record<string, string> = {
  change_id: "ID", slide_number: "slide", description: "description",
  module_name: "module", part_name: "part_name", base_part_no: "base PNO",
  change_point: "변경점", change_reason: "변경사유", change_type: "type",
  target_value: "목표값", change_intent_keywords: "키워드", evidence: "evidence",
};

export function InputTab() {
  const store = useStore();
  const { changeRows, setChangeRows, recommendResults, setRecommendResults } = store;
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [useLlm, setUseLlm] = useState(true);
  const [llmModel, setLlmModel] = useState("gpt-4.1-mini");
  const [topK, setTopK] = useState(10);
  const [relatedLimit, setRelatedLimit] = useState(5);
  const [autoEmbed, setAutoEmbed] = useState(false);
  const pptxRef = useRef<HTMLInputElement>(null);
  const csvRef = useRef<HTMLInputElement>(null);

  function flash(message: string) { setMsg(message); setErr(null); }
  function fail(message: string) { setErr(message); setMsg(null); }

  function updateCell(rowIdx: number, col: string, value: string) {
    const next = changeRows.map((r, i) => (i === rowIdx ? { ...r, [col]: value } : r));
    setChangeRows(next);
  }
  function addRow() {
    setChangeRows([...changeRows, { change_id: `change-${changeRows.length + 1}` }]);
  }
  function removeRow(idx: number) {
    setChangeRows(changeRows.filter((_, i) => i !== idx));
  }

  async function onExtractPptx() {
    const file = pptxRef.current?.files?.[0];
    if (!file) return fail("먼저 PPTX 파일을 선택하세요.");
    setBusy("pptx");
    try {
      const result = await api.extractPptxChanges(file, useLlm, llmModel);
      const extracted = result.changes ?? [];
      if (extracted.length) {
        setChangeRows(extracted);
        setRecommendResults([]);
        flash(`${extracted.length}개 변경 입력을 추출해 표에 채웠습니다. (LLM: ${String((result.llm_status as any)?.status ?? "-")})`);
      } else fail("추출된 변경 입력이 없습니다. 슬라이드 텍스트/LLM 설정을 확인하세요.");
    } catch (e) {
      fail(`PPTX 추출 실패: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  async function onLoadCsv() {
    const file = csvRef.current?.files?.[0];
    if (!file) return fail("먼저 CSV 파일을 선택하세요.");
    const text = await file.text();
    const rows = parseCsv(text) as ChangeInput[];
    if (!rows.length) return fail("CSV에서 행을 읽지 못했습니다.");
    setChangeRows(rows);
    setRecommendResults([]);
    flash(`${rows.length}개 변경 입력을 CSV에서 불러왔습니다.`);
  }

  async function onRecommend() {
    const changes = changeRows.filter((r) =>
      ["description", "module_name", "part_name", "base_part_no", "change_point", "change_reason"].some(
        (k) => String((r as any)[k] ?? "").trim()
      )
    ).map((r, i) => ({ ...r, change_id: String(r.change_id || `change-${i + 1}`) }));
    if (!changes.length) return fail("추천할 변경 입력을 1개 이상 작성하세요.");
    setBusy("recommend");
    try {
      const res = await api.recommend(changes, topK, relatedLimit, autoEmbed);
      setRecommendResults(res.results ?? []);
      store.setEmbeddingStatus(res.embedding_status ?? null);
      store.setApplyResult(null);
      flash(`${changes.length}개 변경에 대한 후보를 만들었습니다. 이제 '② BOM 반영' 탭에서 후보를 선택하세요.`);
    } catch (e) {
      fail(`추천 실패: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="tab">
      <h2>변경 입력 및 추천</h2>
      {msg && <div className="banner banner-ok">{msg}</div>}
      {err && <div className="banner banner-error">{err}</div>}

      <section className="panel">
        <h3>PPTX / CSV 불러오기</h3>
        <div className="row">
          <div className="upload">
            <label>PPTX 심의표</label>
            <input ref={pptxRef} type="file" accept=".pptx" />
            <div className="inline">
              <label className="check"><input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} /> LLM 추출</label>
              <input className="sm" value={llmModel} onChange={(e) => setLlmModel(e.target.value)} />
              <button className="btn" disabled={busy === "pptx"} onClick={onExtractPptx}>
                {busy === "pptx" ? "추출 중…" : "변경 입력 추출"}
              </button>
            </div>
          </div>
          <div className="upload">
            <label>기존 추출 CSV</label>
            <input ref={csvRef} type="file" accept=".csv" />
            <button className="btn" onClick={onLoadCsv}>표에 불러오기</button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h3>변경 입력 표 ({changeRows.length}행)</h3>
          <button className="btn btn-ghost" onClick={addRow}>+ 행 추가</button>
        </div>
        <div className="table-scroll">
          <table className="edit-table">
            <thead>
              <tr>
                {CHANGE_COLUMNS.map((c) => <th key={String(c)}>{COL_LABEL[String(c)] ?? String(c)}</th>)}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {changeRows.map((row, idx) => (
                <tr key={idx}>
                  {CHANGE_COLUMNS.map((c) => (
                    <td key={String(c)}>
                      <input
                        value={String((row as any)[c] ?? "")}
                        onChange={(e) => updateCell(idx, String(c), e.target.value)}
                      />
                    </td>
                  ))}
                  <td><button className="btn-x" title="삭제" onClick={() => removeRow(idx)}>×</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="controls">
          <label>Top-K <input className="xs" type="number" min={1} max={50} value={topK} onChange={(e) => setTopK(Number(e.target.value))} /></label>
          <label>연관 부품 <input className="xs" type="number" min={0} max={200} value={relatedLimit} onChange={(e) => setRelatedLimit(Number(e.target.value))} /></label>
          <label className="check"><input type="checkbox" checked={autoEmbed} onChange={(e) => setAutoEmbed(e.target.checked)} /> 입력 자동 임베딩(느림/비용)</label>
          <button className="btn btn-primary" disabled={busy === "recommend"} onClick={onRecommend}>
            {busy === "recommend" ? "검색 중…" : "후보 추천 실행"}
          </button>
        </div>
      </section>

      {recommendResults.length > 0 && <RecommendPreview results={recommendResults} />}
    </div>
  );
}

function RecommendPreview({ results }: { results: RecommendResult[] }) {
  const [idx, setIdx] = useState(0);
  const result = results[Math.min(idx, results.length - 1)];
  const candidates = result?.candidates ?? [];
  return (
    <section className="panel">
      <h3>추천 후보 미리보기</h3>
      <select className="select" value={idx} onChange={(e) => setIdx(Number(e.target.value))}>
        {results.map((r, i) => (
          <option key={i} value={i}>
            {i + 1}. {changeLabel(r.input)} — 후보 {r.candidates?.length ?? 0}개
          </option>
        ))}
      </select>
      <div className="muted small">{result?.lookup_mode}</div>
      {candidates.length === 0 ? (
        <div className="banner banner-warn">후보가 없습니다. part_name/module/변경점 또는 base_part_no를 보강해보세요.</div>
      ) : (
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>#</th><th>part_name</th><th>base→new PNO</th><th>변경사유</th><th>score</th><th>level</th></tr></thead>
            <tbody>
              {candidates.map((c, i) => (
                <tr key={c.detail_id} className={i === 0 ? "top1" : ""}>
                  <td>{i + 1}{i === 0 ? " ★" : ""}</td>
                  <td>{c.part_name || "-"}</td>
                  <td className="mono">{c.base_part_no || "-"} → {c.new_part_no || "-"}</td>
                  <td>{c.change_reason || <span className="muted">(없음)</span>}</td>
                  <td className="mono">{num(c.score)}</td>
                  <td>{c.level ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint">후보 <b>선택과 반영</b>은 ② BOM 반영 탭에서 변경 카드로 진행합니다.</p>
    </section>
  );
}
