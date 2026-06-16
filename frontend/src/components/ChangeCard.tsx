import { useEffect, useState } from "react";
import { api } from "../api";
import type { BomAction, BomSelection, Candidate, RecommendResult, RelatedPart } from "../types";
import { actionKind, changeLabel, num } from "../util";
import { Badge } from "./Badge";

interface Props {
  result: RecommendResult;
  included: boolean;
  onIncludedChange: (v: boolean) => void;
  onSelectionChange: (sel: BomSelection | null) => void;
}

const ACTIONS: BomAction[] = ["change", "add", "delete"];

export function ChangeCard({ result, included, onIncludedChange, onSelectionChange }: Props) {
  const candidates = result.candidates ?? [];
  const input = result.input ?? {};
  const changeId = String(result.change_id || (input as any).change_id || "");

  const [candIdx, setCandIdx] = useState(0);
  const [action, setAction] = useState<BomAction>("change");
  const [targetPno, setTargetPno] = useState("");
  const [targetName, setTargetName] = useState("");
  const [includeSubtree, setIncludeSubtree] = useState(false);
  const [bundleIds, setBundleIds] = useState<number[]>([]);
  const [related, setRelated] = useState<RelatedPart[] | null>(null);
  const [connected, setConnected] = useState<RelatedPart[] | null>(null);
  const [showRelated, setShowRelated] = useState(false);

  const candidate: Candidate | undefined = candidates[candIdx];

  // 후보가 바뀌면 target 기본값을 후보 값으로 맞춘다.
  useEffect(() => {
    if (candidate) {
      setTargetPno(candidate.base_part_no || "");
      setTargetName(candidate.part_name || "");
    }
  }, [candIdx, candidate?.detail_id]); // eslint-disable-line

  // 선택 상태를 부모로 올린다.
  useEffect(() => {
    if (!included || !candidate) {
      onSelectionChange(null);
      return;
    }
    onSelectionChange({
      change_id: changeId,
      candidate_detail_id: candidate.detail_id,
      action,
      target_base_part_no: targetPno,
      target_part_name: targetName,
      change_point: String(input.change_point || ""),
      change_reason: String(input.change_reason || ""),
      include_subtree: includeSubtree,
      bundle_detail_ids: bundleIds,
    });
  }, [included, candidate?.detail_id, action, targetPno, targetName, includeSubtree, bundleIds]); // eslint-disable-line

  async function loadRelated() {
    if (!candidate) return;
    setShowRelated(true);
    if (related === null) {
      try { setRelated((await api.related(candidate.detail_id, 40)).related_parts); }
      catch { setRelated([]); }
    }
  }
  async function loadConnected() {
    if (!candidate || connected !== null) return;
    try { setConnected((await api.connected(candidate.detail_id, 20)).connected_parts); }
    catch { setConnected([]); }
  }
  useEffect(() => { if (action === "add") loadConnected(); }, [action, candidate?.detail_id]); // eslint-disable-line

  const kind = actionKind(action);

  return (
    <div className={`card ${included ? "" : "card-off"}`}>
      <div className="card-head">
        <label className="card-include">
          <input type="checkbox" checked={included} onChange={(e) => onIncludedChange(e.target.checked)} />
        </label>
        <Badge kind={kind} />
        <div className="card-title">{changeLabel(input)}</div>
        <div className="card-actions">
          <select value={action} onChange={(e) => setAction(e.target.value as BomAction)}>
            {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
      </div>

      {candidate ? (
        <>
          <div className="diff">
            <div className="diff-box diff-before">
              <span className="diff-label">현재(base)</span>
              <span className="mono">{targetPno || "-"}</span>
              <span className="diff-name">{targetName || "-"}</span>
            </div>
            <div className="diff-arrow">→</div>
            <div className="diff-box diff-after">
              <span className="diff-label">{action === "delete" ? "삭제됨" : "변경 후"}</span>
              <span className="mono">{action === "delete" ? "—" : candidate.new_part_no || candidate.base_part_no || "-"}</span>
              <span className="diff-name">{candidate.part_name || "-"}</span>
            </div>
          </div>
          {(input.change_reason || input.change_point) && (
            <div className="card-reason">
              {input.change_point && <span><b>변경점</b> {input.change_point}</span>}
              {input.change_reason && <span><b>사유</b> {input.change_reason}</span>}
            </div>
          )}

          <div className="cand-list">
            <div className="cand-list-head">후보 선택 ({candidates.length})</div>
            {candidates.map((c, i) => (
              <label key={c.detail_id} className={`cand ${i === candIdx ? "cand-sel" : ""}`}>
                <input type="radio" name={`cand-${changeId}`} checked={i === candIdx} onChange={() => setCandIdx(i)} />
                <span className="cand-rank">{i === 0 ? "★1" : i + 1}</span>
                <span className="cand-name">{c.part_name || "-"}</span>
                <span className="mono cand-pno">{c.base_part_no || "-"} → {c.new_part_no || "-"}</span>
                <span className="cand-score">score {num(c.score)}</span>
                {i === 0 && <span className="cand-reco">추천</span>}
                {c.has_change_text === false && <span className="cand-nodata">변경텍스트 없음</span>}
              </label>
            ))}
          </div>

          <div className="card-targets">
            <label>Target base PNO <input className="mono" value={targetPno} onChange={(e) => setTargetPno(e.target.value)} /></label>
            <label>Target part <input value={targetName} onChange={(e) => setTargetName(e.target.value)} /></label>
            {action === "change" && (
              <label className="check"><input type="checkbox" checked={includeSubtree} onChange={(e) => setIncludeSubtree(e.target.checked)} /> 하위 subtree 포함 교체</label>
            )}
          </div>

          {action === "add" && connected && connected.length > 0 && (
            <div className="bundle">
              <div className="bundle-head">함께 추가할 연동 부품 (형제/하위)</div>
              {connected.map((c) => {
                const did = Number(c.detail_id);
                const checked = bundleIds.includes(did);
                return (
                  <label key={did} className="bundle-item">
                    <input type="checkbox" checked={checked} onChange={(e) =>
                      setBundleIds(e.target.checked ? [...bundleIds, did] : bundleIds.filter((x) => x !== did))
                    } />
                    <span className="rel-tag">{c.relation}</span>{c.part_name || "-"} <span className="mono muted">{c.base_part_no || "-"}</span>
                  </label>
                );
              })}
            </div>
          )}

          <div className="card-foot">
            <button className="link" onClick={() => (showRelated ? setShowRelated(false) : loadRelated())}>
              {showRelated ? "연관 부품 닫기" : "연관 부품 보기"}
            </button>
            {candidate.candidate_subtree?.descendant_count != null && (
              <span className="muted small">하위 {candidate.candidate_subtree.descendant_count}개</span>
            )}
          </div>
          {showRelated && related && (
            <div className="rel-list">
              {related.length === 0 ? <span className="muted">연관 부품 없음</span> :
                related.map((r) => (
                  <div key={r.detail_id} className="rel-row">
                    <span className="rel-tag">{r.relation}</span>
                    <span>{r.part_name || "-"}</span>
                    <span className="mono muted">{r.base_part_no || "-"}</span>
                  </div>
                ))}
            </div>
          )}
        </>
      ) : (
        <div className="banner banner-warn">선택 가능한 후보가 없습니다. ① 탭에서 입력을 보강해 다시 추천하세요.</div>
      )}
    </div>
  );
}
