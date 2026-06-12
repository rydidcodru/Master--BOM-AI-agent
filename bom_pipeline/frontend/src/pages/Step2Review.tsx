import { useState, useMemo, useEffect } from 'react';
import type { ChangePoint, SelectionItem, Candidate, DeletedPart } from '../types';

interface Props {
  changePoints: ChangePoint[];
  onBack: () => void;
  onDone: (selections: SelectionItem[]) => void;
}

export default function Step2Review({ changePoints, onBack, onDone }: Props) {
  const [openIdx, setOpenIdx] = useState<number>(0);

  // changePoints가 바뀔 때(history_search 완료 후)마다 체크 상태 재계산
  const defaultCaseChecks = useMemo(() => {
    const init: Record<string, boolean> = {};
    changePoints.forEach((cp, i) => {
      cp.history_candidates.forEach(c => {
        init[`${i}_${c.master_id}`] = true;
      });
    });
    return init;
  }, [changePoints]);

  const [caseChecks,   setCaseChecks]   = useState<Record<string, boolean>>(defaultCaseChecks);
  const [linkedChecks, setLinkedChecks] = useState<Record<string, boolean>>({});

  // changePoints 교체 시(history_search 결과 수신) 체크 상태 동기화
  useEffect(() => {
    setCaseChecks(defaultCaseChecks);
  }, [defaultCaseChecks]);

  function buildSelections(): SelectionItem[] {
    return changePoints.map((cp, i) => {
      const selectedCases = cp.history_candidates
        .filter(c => caseChecks[`${i}_${c.master_id}`])
        .map(c => c.master_id);

      const addedLinked = cp.history_candidates.flatMap(c =>
        c.linked_parts.filter(lp => linkedChecks[`${i}_${c.master_id}_${lp.part_name}`])
      );

      // 중복 제거
      const seenParts = new Set<string>();
      const uniqueLinked = addedLinked.filter(lp => {
        if (seenParts.has(lp.part_name)) return false;
        seenParts.add(lp.part_name);
        return true;
      });

      return {
        change_point_idx:   i,
        part:               cp.part,
        base_part_no:       cp.base_part_no,
        new_part_no:        cp.new_part_no,
        change_detail:      cp.change_detail,
        change_reason:      cp.change_reason,
        change_type:        cp.change_type,
        discipline:         cp.discipline,
        bom_level:          cp.bom_level,
        selected_cases:     selectedCases,
        added_linked_parts: uniqueLinked,
        skipped:            false,
      };
    });
  }

  const statusColor: Record<string, string> = {
    'Changing': '#d97706',
    'NEW':      '#16a34a',
    '삭제':     '#dc2626',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 700 }}>
          변경점 검토 <span style={{ fontSize: 14, color: '#6b7280', fontWeight: 400 }}>({changePoints.length}개)</span>
        </h2>
        <div style={{ display: 'flex', gap: 10 }}>
          <button style={outlineBtn} onClick={onBack}>← 이전</button>
          <button style={primaryBtn} onClick={() => onDone(buildSelections())}>
            BOM 편집으로 →
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {changePoints.map((cp, i) => (
          <div key={i} style={{
            border: '1px solid #e5e7eb',
            borderRadius: 10,
            marginBottom: 10,
            overflow: 'hidden',
            background: '#fff',
          }}>
            {/* 헤더 */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '12px 16px',
                cursor: 'pointer',
                background: openIdx === i ? '#f0f9ff' : '#fff',
                borderBottom: openIdx === i ? '1px solid #bae6fd' : 'none',
              }}
              onClick={() => setOpenIdx(openIdx === i ? -1 : i)}
            >
              <span style={{
                padding: '2px 8px',
                borderRadius: 10,
                fontSize: 11,
                fontWeight: 700,
                background: (statusColor[cp.change_type] ?? '#6b7280') + '22',
                color: statusColor[cp.change_type] ?? '#6b7280',
              }}>
                {cp.change_type || '변경'}
              </span>
              <span style={{ fontWeight: 600, fontSize: 14 }}>{cp.part}</span>
              <span style={{ color: '#6b7280', fontSize: 13, flex: 1 }}>
                {cp.change_detail.slice(0, 60)}{cp.change_detail.length > 60 ? '...' : ''}
              </span>
              <span style={{ color: '#9ca3af', fontSize: 12 }}>
                {cp.base_part_no || '(P/No 없음)'}
              </span>
              <span style={{ color: '#6b7280' }}>{openIdx === i ? '▲' : '▼'}</span>
            </div>

            {openIdx === i && (
              <div style={{ padding: '16px' }}>

                {/* ── 처리 계획 요약 박스 ── */}
                <PlanSummary cp={cp} />

                {/* ── 삭제 대상 부품 (deleted_parts) ── */}
                {cp.deleted_parts && cp.deleted_parts.length > 0 && (
                  <DeletedPartsList parts={cp.deleted_parts} />
                )}

                {/* ── 이력 케이스 ── */}
                {cp.history_candidates.length === 0 ? (
                  <div style={{
                    padding: '12px 14px',
                    background: '#f9fafb',
                    border: '1px solid #e5e7eb',
                    borderRadius: 8,
                    color: '#9ca3af',
                    fontSize: 13,
                  }}>
                    참고할 과거 이력이 없습니다. BOM 편집 화면에서 New P/No를 직접 입력해야 합니다.
                  </div>
                ) : (
                  <CandidateList
                    cpIdx={i}
                    candidates={cp.history_candidates}
                    caseChecks={caseChecks}
                    linkedChecks={linkedChecks}
                    onCaseToggle={(key, v) => setCaseChecks(p => ({ ...p, [key]: v }))}
                    onLinkedToggle={(key, v) => setLinkedChecks(p => ({ ...p, [key]: v }))}
                  />
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 처리 계획 요약 ──────────────────────────────────────────────────────────

function PlanSummary({ cp }: { cp: ChangePoint }) {
  const isFullScope = cp.change_scope === 'full';
  const subtreeCount = cp.matched_subtree?.length ?? 0;
  const deletedCount = cp.deleted_parts?.length ?? 0;
  const preserveCount = cp.preserve_parts?.length ?? 0;
  const hasNewPno = cp.new_part_no && cp.new_part_no !== 'TBD' && cp.new_part_no !== '';

  // 한 문장 요약 생성
  const summaryParts: string[] = [];
  if (cp.base_part_no) {
    summaryParts.push(`Base BOM에서 ${cp.base_part_no} (${cp.part}) 를 찾았습니다.`);
  } else {
    summaryParts.push(`Base BOM에서 매핑된 부품이 없습니다.`);
  }
  if (isFullScope && subtreeCount > 0) {
    summaryParts.push(
      `전체 교체 판단 — 하위 ${subtreeCount}개 행을 삭제하고` +
      (preserveCount > 0 ? ` (소재 ${preserveCount}개 보존)` : '') +
      ` 이력 케이스 기반으로 새 부품을 채웁니다.`
    );
  } else if (!isFullScope && deletedCount > 0) {
    summaryParts.push(`부분 변경 — 삭제 대상 ${deletedCount}개 부품만 제거합니다.`);
  } else if (!isFullScope) {
    summaryParts.push(`부분 변경 — ${hasNewPno ? `${cp.new_part_no}으로 Part No를 교체합니다.` : 'New P/No를 이력 케이스에서 확인하거나 직접 입력합니다.'}`);
  }

  return (
    <div style={{ marginBottom: 16 }}>

      {/* 요약 문장 */}
      <div style={{
        padding: '10px 14px',
        background: '#f0f9ff',
        border: '1px solid #bae6fd',
        borderRadius: 8,
        fontSize: 13,
        color: '#0369a1',
        lineHeight: 1.7,
        marginBottom: 12,
      }}>
        {summaryParts.map((s, i) => <span key={i}>{s} </span>)}
      </div>

      {/* 메타 정보 행 */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <MetaChip label="변경내역" value={cp.change_detail} />
        {cp.change_reason && <MetaChip label="변경사유" value={cp.change_reason} />}
        <MetaChip label="Base P/No" value={cp.base_part_no || '없음'} mono
          color={cp.base_part_no ? '#374151' : '#9ca3af'} />
        <MetaChip label="New P/No"
          value={hasNewPno ? cp.new_part_no : 'TBD'}
          mono
          color={hasNewPno ? '#16a34a' : '#f59e0b'} />
        {cp.match_reason && (
          <span style={{ fontSize: 11, color: '#6b7280', fontStyle: 'italic' }}>
            매핑 근거: {cp.match_reason}
          </span>
        )}
      </div>

      {/* 처리 흐름 스텝 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginTop: 14, flexWrap: 'wrap' }}>
        <FlowStep
          icon="📄"
          label="PPTX 추출"
          desc={cp.part}
          color="#6b7280"
        />
        <FlowArrow />
        <FlowStep
          icon="🔍"
          label="BOM 매핑"
          desc={cp.base_part_no ? `${cp.base_part_no} (${subtreeCount}개 행)` : '매핑 없음'}
          color={cp.base_part_no ? '#2563eb' : '#9ca3af'}
        />
        <FlowArrow />
        {isFullScope ? (
          <>
            <FlowStep
              icon="🗑"
              label="서브트리 삭제"
              desc={`${subtreeCount}개 행${preserveCount > 0 ? ` (${preserveCount}개 소재 보존)` : ''}`}
              color="#dc2626"
              highlight
            />
            <FlowArrow />
            <FlowStep
              icon="📋"
              label="이력 기반 재구성"
              desc="케이스 선택 후 적용"
              color="#16a34a"
            />
          </>
        ) : deletedCount > 0 ? (
          <>
            <FlowStep
              icon="🗑"
              label="지정 부품 삭제"
              desc={`${deletedCount}개 부품 제거`}
              color="#dc2626"
              highlight
            />
            <FlowArrow />
            <FlowStep
              icon="✏️"
              label="Part No 교체"
              desc={hasNewPno ? cp.new_part_no : 'TBD — 직접 입력'}
              color={hasNewPno ? '#16a34a' : '#f59e0b'}
            />
          </>
        ) : (
          <FlowStep
            icon="✏️"
            label="Part No 교체"
            desc={hasNewPno ? cp.new_part_no : 'TBD — 직접 입력'}
            color={hasNewPno ? '#16a34a' : '#f59e0b'}
          />
        )}
      </div>
    </div>
  );
}

function MetaChip({ label, value, mono, color }: {
  label: string; value: string; mono?: boolean; color?: string;
}) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 10px', borderRadius: 12,
      background: '#f3f4f6', border: '1px solid #e5e7eb',
      fontSize: 12,
    }}>
      <span style={{ color: '#9ca3af' }}>{label}</span>
      <span style={{
        fontFamily: mono ? 'monospace' : 'inherit',
        fontWeight: mono ? 600 : 400,
        color: color ?? '#374151',
      }}>{value}</span>
    </span>
  );
}

function FlowStep({ icon, label, desc, color, highlight }: {
  icon: string; label: string; desc: string; color: string; highlight?: boolean;
}) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '8px 14px',
      background: highlight ? '#fff1f2' : '#f9fafb',
      border: `1px solid ${highlight ? '#fca5a5' : '#e5e7eb'}`,
      borderRadius: 8,
      minWidth: 110,
      textAlign: 'center',
    }}>
      <span style={{ fontSize: 18, marginBottom: 2 }}>{icon}</span>
      <span style={{ fontSize: 11, fontWeight: 700, color, marginBottom: 2 }}>{label}</span>
      <span style={{ fontSize: 11, color: '#6b7280', maxWidth: 120, lineHeight: 1.4 }}>{desc}</span>
    </div>
  );
}

function FlowArrow() {
  return (
    <span style={{ color: '#d1d5db', fontSize: 18, margin: '0 4px', alignSelf: 'center' }}>→</span>
  );
}

function CandidateList({
  cpIdx, candidates, caseChecks, linkedChecks, onCaseToggle, onLinkedToggle,
}: {
  cpIdx: number;
  candidates: Candidate[];
  caseChecks: Record<string, boolean>;
  linkedChecks: Record<string, boolean>;
  onCaseToggle: (key: string, v: boolean) => void;
  onLinkedToggle: (key: string, v: boolean) => void;
}) {
  const [activeTab, setActiveTab] = useState(0);
  const c = candidates[activeTab];

  return (
    <div>
      {/* 케이스 탭 */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #e5e7eb', marginBottom: 12 }}>
        {candidates.map((cand, ti) => (
          <button
            key={ti}
            style={{
              padding: '6px 14px',
              border: 'none',
              background: 'none',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: activeTab === ti ? 700 : 400,
              color: activeTab === ti ? '#2563eb' : '#6b7280',
              borderBottom: activeTab === ti ? '2px solid #2563eb' : '2px solid transparent',
            }}
            onClick={() => setActiveTab(ti)}
          >
            케이스 #{cand.rank}
          </button>
        ))}
      </div>

      {/* 케이스 상세 */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#374151', marginBottom: 8 }}>
            <span style={{ fontWeight: 600 }}>{c.base_model} → {c.new_model}</span>
            <span style={{ color: '#9ca3af', marginLeft: 8 }}>{c.source_file}</span>
          </div>
          <div style={{
            background: '#f0f9ff',
            border: '1px solid #bae6fd',
            borderRadius: 6,
            padding: '6px 10px',
            fontSize: 12,
            color: '#0369a1',
            marginBottom: 10,
          }}>
            {c.select_reason}
          </div>

          {/* 변경 부품 테이블 */}
          {c.case_parts.length > 0 && (
            <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f9fafb' }}>
                  {['레벨', '부품명', 'Base P/No', 'New P/No', '변경내역'].map(h => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {c.case_parts.map((p, pi) => (
                  <tr key={pi} style={{ borderBottom: '1px solid #f3f4f6' }}>
                    <td style={tdStyle}>{p.bom_level}</td>
                    <td style={tdStyle}>{p.part_name}</td>
                    <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{p.base_part_no}</td>
                    <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{p.new_part_no}</td>
                    <td style={tdStyle}>{p.changing_point}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {/* 연동부품 */}
          {c.linked_parts.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', marginBottom: 6 }}>
                연동부품 제안
              </div>
              {c.linked_parts.map((lp, li) => {
                const lkey = `${cpIdx}_${c.master_id}_${lp.part_name}`;
                return (
                  <label key={li} style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    padding: '6px 10px',
                    background: '#f0fdf4',
                    border: '1px solid #bbf7d0',
                    borderRadius: 6,
                    marginBottom: 6,
                    cursor: 'pointer',
                    fontSize: 12,
                  }}>
                    <input
                      type="checkbox"
                      checked={!!linkedChecks[lkey]}
                      onChange={e => onLinkedToggle(lkey, e.target.checked)}
                      style={{ marginTop: 2 }}
                    />
                    <div>
                      <span style={{ fontWeight: 600 }}>[{lp.change_type}]</span> {lp.part_name}
                      <div style={{ color: '#6b7280', marginTop: 2 }}>{lp.relevance_reason}</div>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        {/* 케이스 참고 체크 */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
          <input
            type="checkbox"
            checked={!!caseChecks[`${cpIdx}_${c.master_id}`]}
            onChange={e => onCaseToggle(`${cpIdx}_${c.master_id}`, e.target.checked)}
          />
          이 케이스 참고
        </label>
      </div>
    </div>
  );
}

function DeletedPartsList({ parts }: { parts: DeletedPart[] }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        fontSize: 11, fontWeight: 700, color: '#dc2626',
        marginBottom: 6, textTransform: 'uppercase', letterSpacing: 1,
      }}>
        삭제 대상 부품 ({parts.length}개)
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {parts.map((p, i) => (
          <div key={i} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '4px 10px',
            background: '#fee2e2', border: '1px solid #fca5a5',
            borderRadius: 6, fontSize: 12,
          }}>
            <span style={{ color: '#dc2626', fontWeight: 700 }}>✕</span>
            <span style={{ fontFamily: 'monospace', color: '#7f1d1d' }}>{p.part_no}</span>
            <span style={{ color: '#374151' }}>{p.description}</span>
          </div>
        ))}
      </div>
    </div>
  );
}


const thStyle: React.CSSProperties = {
  padding: '4px 8px',
  textAlign: 'left',
  color: '#6b7280',
  fontWeight: 600,
  borderBottom: '1px solid #e5e7eb',
};

const tdStyle: React.CSSProperties = {
  padding: '4px 8px',
  color: '#374151',
};

const primaryBtn: React.CSSProperties = {
  padding: '8px 20px',
  background: '#2563eb',
  color: '#fff',
  border: 'none',
  borderRadius: 8,
  fontWeight: 700,
  fontSize: 14,
  cursor: 'pointer',
};

const outlineBtn: React.CSSProperties = {
  padding: '8px 16px',
  background: '#fff',
  color: '#374151',
  border: '1px solid #d1d5db',
  borderRadius: 8,
  fontWeight: 600,
  fontSize: 13,
  cursor: 'pointer',
};