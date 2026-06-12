import { useRef, useState } from 'react';
import type { BomRow, ChangePoint, SelectionItem, CasePart } from '../types';

interface Props {
  row: BomRow | null;
  changePoints: ChangePoint[];
  selections: SelectionItem[];
  onConfirm: (rowId: string, confirmedPno: string) => void;
  onExclude: (rowId: string) => void;
  onUndo:    (rowId: string) => void;
}

export default function ChangePanel({ row, changePoints, selections, onConfirm, onExclude, onUndo }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [activeCaseIdx, setActiveCaseIdx] = useState(0);

  if (!row || !row.change_status) return null;

  const lookupPno = row.original_part_no || row.part_no;
  const cp  = changePoints.find(c => c.base_part_no === lookupPno);
  const sel = selections.find(s => s.base_part_no === lookupPno);

  const PALETTE: Record<string, { bg: string; border: string; text: string }> = {
    '변경': { bg: '#fffbeb', border: '#f59e0b', text: '#b45309' },
    '추가': { bg: '#f0fdf4', border: '#22c55e', text: '#15803d' },
    '삭제': { bg: '#fff1f2', border: '#f87171', text: '#b91c1c' },
  };
  const pal = PALETTE[row.change_status] ?? { bg: '#f9fafb', border: '#d1d5db', text: '#374151' };

  const selectedCandidates = cp?.history_candidates.filter(
    c => sel?.selected_cases.includes(c.master_id)
  ) ?? [];

  const isDelete    = row.change_status === '삭제';
  const isExcluded  = row.excluded;
  const isConfirmed = row.confirmed;
  const isFullScope = cp?.change_scope === 'full';
  const subtreeCount = cp?.matched_subtree?.length ?? 0;

  const activeCase = selectedCandidates[activeCaseIdx] ?? null;

  return (
    <div style={{ padding: '16px 14px', background: '#fafafa', height: '100%', boxSizing: 'border-box', overflowY: 'auto' }}>

      {/* ── 상태 헤더 ── */}
      <div style={{
        padding: '10px 12px', borderRadius: 8,
        background: pal.bg, border: `1px solid ${pal.border}`, marginBottom: 14,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{
              display: 'inline-block', padding: '1px 8px', borderRadius: 12,
              fontSize: 11, fontWeight: 700, color: pal.text, background: pal.border + '33',
            }}>
              {row.change_status}
            </span>
            {isFullScope && (
              <span style={{
                display: 'inline-block', padding: '1px 8px', borderRadius: 12,
                fontSize: 11, fontWeight: 700, color: '#b91c1c', background: '#fee2e2',
              }}>
                전체 교체
              </span>
            )}
          </div>
          {isConfirmed && !isExcluded && <span style={{ fontSize: 12, color: '#15803d', fontWeight: 600 }}>✓ 확정됨</span>}
          {isExcluded  && <span style={{ fontSize: 12, color: '#9ca3af', fontWeight: 600 }}>⊘ 제외됨</span>}
        </div>
        <div style={{ fontWeight: 700, fontSize: 14, color: '#111827', marginTop: 4 }}>
          {row.description || row.part_no}
        </div>
        {row.change_status === '변경' && row.original_part_no && row.original_part_no !== row.part_no ? (
          <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4, fontFamily: 'monospace' }}>
            <span style={{ textDecoration: 'line-through', color: '#9ca3af' }}>{row.original_part_no}</span>
            <span style={{ margin: '0 6px' }}>→</span>
            <span style={{ color: '#15803d', fontWeight: 600 }}>{row.part_no}</span>
          </div>
        ) : (
          <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 2, fontFamily: 'monospace' }}>{row.part_no}</div>
        )}
      </div>

      {/* ── 변경 내역 요약 ── */}
      {cp && (
        <Section title="변경 내역">
          <div style={{
            background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8,
            padding: '10px 12px', fontSize: 12, lineHeight: 1.7,
          }}>
            <div><span style={labelStyle}>변경내역</span>{cp.change_detail}</div>
            {cp.change_reason && <div><span style={labelStyle}>변경사유</span>{cp.change_reason}</div>}
            {cp.discipline    && <div><span style={labelStyle}>분야</span>{cp.discipline}</div>}
          </div>
        </Section>
      )}

      {/* ── BOM 영향 범위 ── */}
      {cp && (
        <Section title="Base BOM 영향 범위">
          <div style={{
            background: isFullScope ? '#fff1f2' : '#fffbeb',
            border: `1px solid ${isFullScope ? '#fca5a5' : '#fde68a'}`,
            borderRadius: 8, padding: '10px 12px', fontSize: 12,
          }}>
            {isFullScope ? (
              <>
                <div style={{ fontWeight: 700, color: '#b91c1c', marginBottom: 6 }}>
                  🗑 하위 {subtreeCount}개 행 전체 교체
                </div>
                <div style={{ color: '#6b7280', lineHeight: 1.6 }}>
                  <b style={{ color: '#374151', fontFamily: 'monospace' }}>{lookupPno}</b> 하위 트리 전체가 삭제되고,
                  아래 선택된 이력 케이스의 변경 내역을 기반으로 새 부품이 채워집니다.
                  {(cp.preserve_parts?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 6, color: '#b45309' }}>
                      ※ 소재 {cp.preserve_parts!.length}개는 교체 후에도 보존됩니다
                      <span style={{ fontFamily: 'monospace', marginLeft: 4, fontSize: 11 }}>
                        ({cp.preserve_parts!.slice(0, 2).join(', ')}{cp.preserve_parts!.length > 2 ? ` 외 ${cp.preserve_parts!.length - 2}개` : ''})
                      </span>
                    </div>
                  )}
                </div>
              </>
            ) : (cp.deleted_parts?.length ?? 0) > 0 ? (
              <>
                <div style={{ fontWeight: 700, color: '#b45309', marginBottom: 6 }}>
                  ✏ 부분 변경 — 아래 {cp.deleted_parts!.length}개 부품 삭제
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {cp.deleted_parts!.map((dp, i) => (
                    <span key={i} style={{
                      padding: '2px 8px', borderRadius: 6,
                      background: '#fee2e2', border: '1px solid #fca5a5',
                      fontSize: 11, fontFamily: 'monospace', color: '#b91c1c',
                    }}>
                      {dp.part_no || dp.description}
                    </span>
                  ))}
                </div>
              </>
            ) : (
              <div style={{ color: '#374151', lineHeight: 1.6 }}>
                ✏ <b style={{ fontFamily: 'monospace' }}>{lookupPno}</b> 행의 Part No만 교체됩니다.
                나머지 BOM 구조는 유지됩니다.
              </div>
            )}
          </div>
        </Section>
      )}

      {/* ── New P/No 확정 입력 ── */}
      {!isDelete && !isExcluded && (
        <Section title="New P/No 확정">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              ref={inputRef}
              key={row.row_id}
              defaultValue={row.new_part_no_confirmed || row.new_part_no_suggested}
              placeholder="TBD"
              style={inputStyle}
            />
            <button style={confirmBtn} onClick={() => onConfirm(row.row_id, inputRef.current?.value ?? '')}>
              확정
            </button>
          </div>
          {row.new_part_no_suggested && row.new_part_no_suggested !== 'TBD' && row.new_part_no_suggested !== row.new_part_no_confirmed && (
            <div style={{ fontSize: 11, color: '#6b7280', marginTop: 4 }}>
              추천: <span style={{ fontFamily: 'monospace' }}>{row.new_part_no_suggested}</span>
            </div>
          )}
        </Section>
      )}

      {isDelete && !isExcluded && (
        <Section title="삭제 확인">
          <button style={{ ...confirmBtn, background: '#b91c1c' }} onClick={() => onConfirm(row.row_id, '')}>
            삭제 확정
          </button>
        </Section>
      )}

      {/* ── 이력 케이스 기반 변경 가이드 ── */}
      {selectedCandidates.length > 0 && (
        <Section title="이력 케이스 기반 변경 가이드">

          {/* 케이스 탭 */}
          {selectedCandidates.length > 1 && (
            <div style={{ display: 'flex', gap: 0, marginBottom: 10, borderBottom: '1px solid #e5e7eb' }}>
              {selectedCandidates.map((c, ti) => (
                <button key={ti} onClick={() => setActiveCaseIdx(ti)} style={{
                  padding: '4px 12px', border: 'none', background: 'none', cursor: 'pointer',
                  fontSize: 12, fontWeight: activeCaseIdx === ti ? 700 : 400,
                  color: activeCaseIdx === ti ? '#2563eb' : '#6b7280',
                  borderBottom: activeCaseIdx === ti ? '2px solid #2563eb' : '2px solid transparent',
                }}>
                  케이스 #{c.rank}
                </button>
              ))}
            </div>
          )}

          {activeCase && (
            <div>
              {/* 케이스 메타 */}
              <div style={{
                background: '#f0f9ff', border: '1px solid #bae6fd',
                borderRadius: 8, padding: '8px 12px', marginBottom: 10, fontSize: 12,
              }}>
                <div style={{ fontWeight: 700, color: '#0369a1', marginBottom: 2 }}>
                  {activeCase.base_model} → {activeCase.new_model}
                </div>
                <div style={{ color: '#374151', lineHeight: 1.6 }}>{activeCase.select_reason}</div>
              </div>

              {/* 이 케이스에서 어떻게 바뀌었는지 */}
              {activeCase.case_parts.length > 0 && (
                <CasePartsTable parts={activeCase.case_parts} currentPno={lookupPno} />
              )}

              {/* 연동부품 */}
              {sel && sel.added_linked_parts.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: '#15803d', marginBottom: 6 }}>
                    함께 변경 권장 (연동부품)
                  </div>
                  {sel.added_linked_parts.map((lp, i) => (
                    <div key={i} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      background: '#f0fdf4', border: '1px solid #bbf7d0',
                      borderRadius: 6, padding: '6px 10px', marginBottom: 4, fontSize: 12,
                    }}>
                      <span>
                        <span style={{ fontWeight: 600, color: '#15803d' }}>[{lp.change_type}]</span>
                        <span style={{ marginLeft: 6 }}>{lp.part_name}</span>
                      </span>
                      {lp.relevance_reason && (
                        <span style={{ fontSize: 11, color: '#6b7280', maxWidth: 140, textAlign: 'right', lineHeight: 1.4 }}>
                          {lp.relevance_reason}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </Section>
      )}

      {/* 이력 없을 때 */}
      {selectedCandidates.length === 0 && row.change_note && (
        <Section title="AI 참고 메모">
          <div style={{
            background: '#fefce8', border: '1px solid #fde68a',
            borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#92400e', lineHeight: 1.6,
          }}>
            {row.change_note}
          </div>
        </Section>
      )}

      {/* ── 하단 액션 ── */}
      <div style={{ borderTop: '1px solid #e5e7eb', paddingTop: 12, marginTop: 8, display: 'flex', gap: 8 }}>
        {!isExcluded ? (
          <button style={excludeBtn} onClick={() => onExclude(row.row_id)}>⊘ 이 항목 제외</button>
        ) : (
          <button style={undoBtn} onClick={() => onUndo(row.row_id)}>↩ 제외 취소</button>
        )}
      </div>
    </div>
  );
}

// ── 이력 케이스 변경 부품 테이블 ───────────────────────────────────────────

function CasePartsTable({ parts }: { parts: CasePart[]; currentPno?: string }) {
  const changed = parts.filter(p => p.base_part_no && p.new_part_no && p.base_part_no !== p.new_part_no);
  const deleted = parts.filter(p => p.base_part_no && !p.new_part_no);
  const added   = parts.filter(p => !p.base_part_no && p.new_part_no);

  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', marginBottom: 6 }}>
        이 케이스에서 변경된 부품 목록 — 현재 BOM에도 동일하게 적용됩니다
      </div>
      <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: '#f3f4f6' }}>
            <th style={th}>구분</th>
            <th style={th}>부품명</th>
            <th style={th}>Before</th>
            <th style={th}>After</th>
            <th style={th}>변경내역</th>
          </tr>
        </thead>
        <tbody>
          {changed.map((p, i) => (
            <tr key={`c${i}`} style={{ borderBottom: '1px solid #f3f4f6', background: '#fffbeb' }}>
              <td style={td}><Badge label="변경" color="#b45309" bg="#fef3c7" /></td>
              <td style={td}>{p.part_name}</td>
              <td style={{ ...td, fontFamily: 'monospace', color: '#9ca3af', textDecoration: 'line-through' }}>{p.base_part_no}</td>
              <td style={{ ...td, fontFamily: 'monospace', color: '#15803d', fontWeight: 600 }}>{p.new_part_no}</td>
              <td style={{ ...td, color: '#6b7280' }}>{p.changing_point}</td>
            </tr>
          ))}
          {deleted.map((p, i) => (
            <tr key={`d${i}`} style={{ borderBottom: '1px solid #f3f4f6', background: '#fff1f2' }}>
              <td style={td}><Badge label="삭제" color="#b91c1c" bg="#fee2e2" /></td>
              <td style={td}>{p.part_name}</td>
              <td style={{ ...td, fontFamily: 'monospace', color: '#b91c1c' }}>{p.base_part_no}</td>
              <td style={{ ...td, color: '#9ca3af' }}>—</td>
              <td style={{ ...td, color: '#6b7280' }}>{p.changing_point}</td>
            </tr>
          ))}
          {added.map((p, i) => (
            <tr key={`a${i}`} style={{ borderBottom: '1px solid #f3f4f6', background: '#f0fdf4' }}>
              <td style={td}><Badge label="추가" color="#15803d" bg="#dcfce7" /></td>
              <td style={td}>{p.part_name}</td>
              <td style={{ ...td, color: '#9ca3af' }}>—</td>
              <td style={{ ...td, fontFamily: 'monospace', color: '#15803d', fontWeight: 600 }}>{p.new_part_no}</td>
              <td style={{ ...td, color: '#6b7280' }}>{p.changing_point}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {changed.length + deleted.length + added.length === 0 && (
        <div style={{ color: '#9ca3af', fontSize: 12, padding: '8px 0' }}>이 케이스에 부품 변경 상세 내역이 없습니다.</div>
      )}
    </div>
  );
}

function Badge({ label, color, bg }: { label: string; color: string; bg: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 8,
      fontSize: 10, fontWeight: 700, color, background: bg,
    }}>{label}</span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: '#9ca3af', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  color: '#9ca3af', marginRight: 6, minWidth: 48, display: 'inline-block',
};
const th: React.CSSProperties = {
  padding: '4px 6px', textAlign: 'left', color: '#6b7280',
  fontWeight: 600, borderBottom: '1px solid #e5e7eb', fontSize: 10,
};
const td: React.CSSProperties = {
  padding: '4px 6px', color: '#374151', verticalAlign: 'top',
};
const inputStyle: React.CSSProperties = {
  flex: 1, padding: '6px 10px', border: '1px solid #d1d5db',
  borderRadius: 6, fontSize: 13, fontFamily: 'monospace',
};
const confirmBtn: React.CSSProperties = {
  padding: '6px 14px', background: '#2563eb', color: '#fff',
  border: 'none', borderRadius: 6, cursor: 'pointer',
  fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap',
};
const excludeBtn: React.CSSProperties = {
  padding: '6px 14px', background: '#fff', color: '#9ca3af',
  border: '1px solid #e5e7eb', borderRadius: 6, cursor: 'pointer', fontSize: 12,
};
const undoBtn: React.CSSProperties = {
  padding: '6px 14px', background: '#eff6ff', color: '#2563eb',
  border: '1px solid #bfdbfe', borderRadius: 6, cursor: 'pointer', fontSize: 12,
};
