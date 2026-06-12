import { useState } from 'react';
import { runHistorySearch } from '../api/client';
import { useTimer } from '../hooks/useTimer';
import type { ChangePoint } from '../types';

interface Props {
  changePoints: ChangePoint[];
  fixture?: 'quickzone' | 'compact_oven';
  onBack: () => void;
  onDone: (changePoints: ChangePoint[]) => void;
}

const SCOPE_LABEL: Record<string, { label: string; bg: string; color: string }> = {
  full:    { label: '전체 교체', bg: '#fee2e2', color: '#dc2626' },
  partial: { label: '부분 변경', bg: '#fef3c7', color: '#d97706' },
};

const CONF_LABEL: Record<string, { label: string; color: string }> = {
  high:   { label: '높음', color: '#16a34a' },
  medium: { label: '중간', color: '#d97706' },
  low:    { label: '낮음', color: '#dc2626' },
};

const TYPE_COLOR: Record<string, string> = {
  Changing: '#d97706',
  NEW:      '#16a34a',
  '삭제':   '#dc2626',
};

export default function Step2BomMatchReview({ changePoints, fixture, onBack, onDone }: Props) {
  const [openIdx,     setOpenIdx]     = useState<number>(-1);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState('');
  const [doneTime,    setDoneTime]    = useState('');
  const timer = useTimer();

  const mapped   = changePoints.filter(cp => cp.base_part_no).length;
  const unmapped = changePoints.filter(cp => !cp.base_part_no).length;

  async function handleHistorySearch() {
    setLoading(true);
    setDoneTime('');
    setError('');
    timer.start();
    try {
      const updated = await runHistorySearch(changePoints, fixture);
      timer.stop();
      setDoneTime(timer.formatted);
      onDone(updated);
    } catch (e: any) {
      timer.stop();
      setError(e?.message ?? '이력 검색 중 오류가 발생했습니다.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 헤더 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
            BOM 매핑 결과 확인
            <span style={{ fontSize: 13, color: '#6b7280', fontWeight: 400, marginLeft: 8 }}>
              ({changePoints.length}개 변경점)
            </span>
          </h2>
          <p style={{ fontSize: 12, color: '#6b7280', margin: '4px 0 0' }}>
            매핑된 결과를 검토한 후 이력 검색을 실행하세요.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button style={outlineBtn} onClick={onBack}>← 이전</button>
          <button
            style={{ ...primaryBtn, opacity: loading ? 0.7 : 1 }}
            disabled={loading}
            onClick={handleHistorySearch}
          >
            {loading ? '이력 검색 중...' : '이력 검색 실행 →'}
          </button>
        </div>
      </div>

      {/* 매핑 현황 한 줄 요약 */}
      <div style={{ fontSize: 13, color: '#6b7280', marginBottom: 12 }}>
        BOM 매핑 완료{' '}
        <span style={{ fontWeight: 700, color: '#16a34a' }}>{mapped}개</span>
        {unmapped > 0 && (
          <span style={{ marginLeft: 8, color: '#dc2626' }}>/ 미매핑 {unmapped}개</span>
        )}
      </div>

      {error && (
        <div style={{ padding: '10px 14px', background: '#fee2e2', borderRadius: 8, color: '#dc2626', fontSize: 13, marginBottom: 12 }}>
          {error}
        </div>
      )}

      {loading && (
        <div style={{ padding: '14px 16px', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8, fontSize: 13, color: '#1d4ed8', marginBottom: 12, textAlign: 'center' }}>
          <div style={{ marginBottom: 6 }}>⏳ 과거 이력 검색 중... 약 20~30분 소요됩니다.</div>
          <div style={{
            display: 'inline-block',
            padding: '4px 20px',
            background: '#fff',
            border: '1px solid #bfdbfe',
            borderRadius: 20,
            fontWeight: 700,
            fontSize: 20,
            fontVariantNumeric: 'tabular-nums',
            letterSpacing: 1,
          }}>
            {timer.formatted}
          </div>
        </div>
      )}

      {!loading && doneTime && (
        <div style={{ padding: '8px 12px', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 8, fontSize: 12, color: '#16a34a', marginBottom: 12, textAlign: 'center' }}>
          ✅ 이력 검색 완료 — 소요 시간: <strong>{doneTime}</strong>
        </div>
      )}

      {/* 변경점 목록 */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {changePoints.map((cp) => {
          const realIdx = changePoints.indexOf(cp);
          const scope = SCOPE_LABEL[cp.change_scope ?? ''];
          const conf  = CONF_LABEL[cp.match_confidence ?? ''];
          const isOpen = openIdx === realIdx;

          return (
            <div key={realIdx} style={{
              border: '1px solid #e5e7eb',
              borderRadius: 10,
              marginBottom: 8,
              overflow: 'hidden',
              background: '#fff',
            }}>
              {/* 행 헤더 */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 14px',
                  cursor: 'pointer',
                  background: isOpen ? '#f0f9ff' : '#fff',
                  borderBottom: isOpen ? '1px solid #bae6fd' : 'none',
                }}
                onClick={() => setOpenIdx(isOpen ? -1 : realIdx)}
              >
                {/* 순번 */}
                <span style={{ fontSize: 11, color: '#9ca3af', minWidth: 28 }}>#{realIdx + 1}</span>

                {/* change_type 배지 */}
                <span style={{
                  padding: '2px 7px', borderRadius: 10, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                  background: (TYPE_COLOR[cp.change_type] ?? '#6b7280') + '22',
                  color: TYPE_COLOR[cp.change_type] ?? '#6b7280',
                }}>
                  {cp.change_type || '변경'}
                </span>

                {/* scope 배지 */}
                {scope && (
                  <span style={{
                    padding: '2px 7px', borderRadius: 10, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
                    background: scope.bg, color: scope.color,
                  }}>
                    {scope.label}
                  </span>
                )}

                {/* 모듈 > 부품명 */}
                <span style={{ fontWeight: 600, fontSize: 13 }}>
                  {cp.module !== cp.part ? `${cp.module} › ` : ''}
                  <span style={{ color: '#111827' }}>{cp.part}</span>
                </span>

                {/* 변경내역 요약 */}
                <span style={{ color: '#6b7280', fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {cp.change_detail.slice(0, 60)}{cp.change_detail.length > 60 ? '...' : ''}
                </span>

                {/* Base P/No */}
                <span style={{ color: '#9ca3af', fontSize: 11, fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                  {cp.base_part_no || '(P/No 없음)'}
                </span>

                {/* 매핑 신뢰도 */}
                {conf && (
                  <span style={{ fontSize: 11, color: conf.color, whiteSpace: 'nowrap' }}>
                    신뢰도: {conf.label}
                  </span>
                )}

                <span style={{ color: '#9ca3af', fontSize: 12 }}>{isOpen ? '▲' : '▼'}</span>
              </div>

              {/* 펼침 상세 */}
              {isOpen && (
                <div style={{ padding: '14px 16px' }}>
                  {/* 기본 정보 */}
                  <div style={{ display: 'flex', gap: 24, marginBottom: 14, flexWrap: 'wrap' }}>
                    <KV label="모듈"     value={cp.module} />
                    <KV label="변경내역" value={cp.change_detail} />
                    <KV label="변경사유" value={cp.change_reason} />
                    <KV label="분야"     value={cp.discipline} />
                    <KV label="Base P/No" value={cp.base_part_no || '(없음)'} mono />
                    <KV label="New P/No"  value={cp.new_part_no  || '(없음)'} mono />
                    <KV label="BOM 레벨" value={cp.bom_level} />
                  </div>

                  {/* 매핑 근거 */}
                  {cp.match_reason && (
                    <div style={{
                      background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: 8,
                      padding: '8px 12px', fontSize: 12, color: '#0369a1', marginBottom: 12,
                    }}>
                      💡 매핑 근거: {cp.match_reason}
                    </div>
                  )}

                  {/* 삭제 대상 */}
                  {cp.deleted_parts && cp.deleted_parts.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: '#dc2626', marginBottom: 6 }}>
                        삭제 대상 ({cp.deleted_parts.length}개)
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {cp.deleted_parts.map((p: { part_no: string; description: string }, di: number) => (
                          <span key={di} style={{
                            padding: '3px 10px', background: '#fee2e2', border: '1px solid #fca5a5',
                            borderRadius: 6, fontSize: 12, fontFamily: 'monospace', color: '#7f1d1d',
                          }}>
                            ✕ {p.part_no} {p.description}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 보존 소재 */}
                  {cp.preserve_parts && cp.preserve_parts.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: '#0369a1', marginBottom: 6 }}>
                        보존 소재 ({cp.preserve_parts.length}개)
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {cp.preserve_parts.map((pno: string, pi: number) => (
                          <span key={pi} style={{
                            padding: '3px 10px', background: '#eff6ff', border: '1px solid #bfdbfe',
                            borderRadius: 6, fontSize: 12, fontFamily: 'monospace', color: '#1e40af',
                          }}>
                            {pno}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* matched_subtree 미리보기 (최대 5개) */}
                  {cp.matched_subtree && cp.matched_subtree.length > 0 && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', marginBottom: 6 }}>
                        매핑된 BOM 트리 (상위 {Math.min(cp.matched_subtree.length, 5)}개
                        {cp.matched_subtree.length > 5 ? ` / 전체 ${cp.matched_subtree.length}개` : ''})
                      </div>
                      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead>
                          <tr style={{ background: '#f9fafb' }}>
                            {['레벨', 'Part No', '부품명', '수량'].map(h => (
                              <th key={h} style={thStyle}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {cp.matched_subtree.slice(0, 5).map((row: { lvl: string; part_no: string; description: string; qty: string }, ri: number) => (
                            <tr key={ri} style={{ borderBottom: '1px solid #f3f4f6' }}>
                              <td style={tdStyle}>{row.lvl}</td>
                              <td style={{ ...tdStyle, fontFamily: 'monospace' }}>{row.part_no}</td>
                              <td style={tdStyle}>{row.description}</td>
                              <td style={tdStyle}>{row.qty}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  if (!value) return null;
  return (
    <div style={{ fontSize: 12 }}>
      <span style={{ color: '#6b7280' }}>{label}: </span>
      <span style={{ fontFamily: mono ? 'monospace' : 'inherit', fontWeight: mono ? 500 : 400, color: '#111827' }}>
        {value}
      </span>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: '4px 8px', textAlign: 'left', color: '#6b7280',
  fontWeight: 600, borderBottom: '1px solid #e5e7eb', fontSize: 11,
};
const tdStyle: React.CSSProperties = { padding: '4px 8px', color: '#374151' };

const primaryBtn: React.CSSProperties = {
  padding: '8px 20px', background: '#2563eb', color: '#fff',
  border: 'none', borderRadius: 8, fontWeight: 700, fontSize: 14, cursor: 'pointer',
};
const outlineBtn: React.CSSProperties = {
  padding: '8px 14px', background: '#fff', color: '#374151',
  border: '1px solid #d1d5db', borderRadius: 8, fontWeight: 600, fontSize: 13, cursor: 'pointer',
};