import { useState } from 'react';
import { runBomMatch, runBomMatchFixture } from '../api/client';
import { useTimer } from '../hooks/useTimer';
import { saveBomMatchCache, loadBomMatchCache, clearBomMatchCache } from '../App';
import type { BomRow, ChangePoint } from '../types';

interface Props {
  onDone: (result: { changePoints: ChangePoint[]; bomRows: BomRow[]; bomFile?: File; fixture?: 'quickzone' | 'compact_oven' }) => void;
}

export default function Step1Upload({ onDone }: Props) {
  const [pptxFile, setPptxFile] = useState<File | null>(null);
  const [bomFile,  setBomFile]  = useState<File | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState('');
  const [doneTime, setDoneTime] = useState('');
  const timer = useTimer();

  const cache = loadBomMatchCache();

  async function handleRun(fix?: 'quickzone' | 'compact_oven') {
    setLoading(true);
    setDoneTime('');
    setError('');
    timer.start();
    try {
      const result = fix
        ? await runBomMatchFixture(fix)
        : await runBomMatch(pptxFile!, bomFile ?? undefined);
      timer.stop();
      setDoneTime(timer.formatted);
      // bom_match 결과 캐시 저장
      saveBomMatchCache({
        changePoints: result.change_points,
        bomRows:      result.bom_rows,
        fixture:      fix,
      });
      onDone({ changePoints: result.change_points, bomRows: result.bom_rows, bomFile: bomFile ?? undefined, fixture: fix });
    } catch (e: any) {
      timer.stop();
      setError(e?.message ?? '파이프라인 실행 중 오류가 발생했습니다.');
    } finally {
      setLoading(false);
    }
  }

  function handleLoadCache() {
    if (!cache) return;
    onDone({
      changePoints: cache.changePoints,
      bomRows:      cache.bomRows,
      fixture:      cache.fixture,
    });
  }

  return (
    <div style={{ maxWidth: 640, margin: '0 auto', padding: '32px 0' }}>
      <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>파일 업로드</h2>
      <p style={{ fontSize: 13, color: '#6b7280', marginBottom: 24 }}>
        PPTX와 Base BOM을 업로드하면 변경점을 추출하고 BOM에 매핑합니다.
      </p>

      <div style={cardStyle}>
        <Label>변경점 PPTX</Label>
        <input
          type="file"
          accept=".pptx"
          onChange={e => setPptxFile(e.target.files?.[0] ?? null)}
          style={fileInputStyle}
        />
        <Label style={{ marginTop: 16 }}>Base BOM (Excel, 선택)</Label>
        <input
          type="file"
          accept=".xlsx,.xls"
          onChange={e => setBomFile(e.target.files?.[0] ?? null)}
          style={fileInputStyle}
        />
        <button
          style={{ ...primaryBtn, marginTop: 20, opacity: pptxFile ? 1 : 0.5 }}
          disabled={!pptxFile || loading}
          onClick={() => handleRun()}
        >
          {loading ? '분석 중...' : 'BOM 매핑 실행'}
        </button>
      </div>

      {/* ── 캐시 불러오기 ── */}
      {cache && (
        <div style={{
          margin: '20px 0',
          padding: '14px 18px',
          background: '#f0fdf4',
          border: '1px solid #86efac',
          borderRadius: 10,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
        }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 13, color: '#15803d', marginBottom: 2 }}>
              저장된 BOM 매핑 결과가 있습니다
            </div>
            <div style={{ fontSize: 12, color: '#6b7280' }}>
              {cache.fixture ? `fixture: ${cache.fixture} · ` : ''}
              변경점 {cache.changePoints.length}개 ·{' '}
              {new Date(cache.savedAt).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} 저장
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <button
              style={{ ...primaryBtn, width: 'auto', padding: '8px 18px', background: '#16a34a', fontSize: 13 }}
              onClick={handleLoadCache}
            >
              이어서 진행 →
            </button>
            <button
              style={{ ...outlineBtn, padding: '8px 12px', fontSize: 12, color: '#9ca3af', borderColor: '#d1d5db' }}
              onClick={() => { clearBomMatchCache(); window.location.reload(); }}
            >
              삭제
            </button>
          </div>
        </div>
      )}

      <div style={{ textAlign: 'center', margin: '24px 0', color: '#9ca3af', fontSize: 13 }}>
        — 또는 테스트 데이터로 실행 —
      </div>

      <div style={{ display: 'flex', gap: 12 }}>
        <button
          style={{ ...outlineBtn, flex: 1 }}
          disabled={loading}
          onClick={() => handleRun('quickzone')}
        >
          {loading ? '...' : '퀵존레인지 Fixture'}
        </button>
        <button
          style={{ ...outlineBtn, flex: 1 }}
          disabled={loading}
          onClick={() => handleRun('compact_oven')}
        >
          {loading ? '...' : 'Compact Oven Fixture'}

        </button>
      </div>

      {error && (
        <div style={{ marginTop: 16, padding: '10px 14px', background: '#fee2e2', borderRadius: 8, color: '#dc2626', fontSize: 13 }}>
          {error}
        </div>
      )}

      {loading && (
        <div style={{ marginTop: 24, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
          <div style={{ marginBottom: 6 }}>⏳ PPTX 파싱 → BOM 매핑 중...</div>
          <div style={{
            display: 'inline-block',
            padding: '4px 16px',
            background: '#eff6ff',
            border: '1px solid #bfdbfe',
            borderRadius: 20,
            color: '#1d4ed8',
            fontWeight: 700,
            fontSize: 18,
            fontVariantNumeric: 'tabular-nums',
            letterSpacing: 1,
          }}>
            {timer.formatted}
          </div>
        </div>
      )}

      {!loading && doneTime && (
        <div style={{ marginTop: 16, textAlign: 'center', fontSize: 12, color: '#16a34a' }}>
          ✅ 완료 — 소요 시간: {doneTime}
        </div>
      )}
    </div>
  );
}

function Label({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: '#374151', ...style }}>{children}</div>;
}

const cardStyle: React.CSSProperties = {
  background: '#fff',
  border: '1px solid #e5e7eb',
  borderRadius: 12,
  padding: '24px',
};

const fileInputStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  fontSize: 13,
  padding: '8px 0',
};

const primaryBtn: React.CSSProperties = {
  width: '100%',
  padding: '10px 0',
  background: '#2563eb',
  color: '#fff',
  border: 'none',
  borderRadius: 8,
  fontWeight: 700,
  fontSize: 14,
  cursor: 'pointer',
};

const outlineBtn: React.CSSProperties = {
  padding: '10px 0',
  background: '#fff',
  color: '#2563eb',
  border: '1px solid #2563eb',
  borderRadius: 8,
  fontWeight: 600,
  fontSize: 13,
  cursor: 'pointer',
};