import { useState } from 'react';
import StepIndicator        from './components/StepIndicator';
import Step1Upload          from './pages/Step1Upload';
import Step2BomMatchReview  from './pages/Step2BomMatchReview';
import Step3HistoryReview   from './pages/Step2Review';      // 파일명 유지, 별칭으로 import
import Step4BomEdit         from './pages/Step3BomEdit';
import type { BomRow, ChangePoint, SelectionItem } from './types';

const CACHE_KEY = 'bom_pipeline_bommatch_cache';

interface BomMatchCache {
  changePoints: ChangePoint[];
  bomRows:      BomRow[];
  fixture?:     'quickzone' | 'compact_oven';
  savedAt:      string;  // ISO timestamp
}

export function saveBomMatchCache(data: Omit<BomMatchCache, 'savedAt'>) {
  // matched_subtree는 파생 데이터로 용량이 크므로 제외 (base_part_no로 재생성 가능)
  const slim = {
    ...data,
    changePoints: data.changePoints.map(({ matched_subtree: _sub, ...rest }) => rest),
    savedAt: new Date().toISOString(),
  };
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(slim));
  } catch (e) {
    console.warn('[cache] localStorage 저장 실패 (용량 초과?):', e);
  }
}

export function loadBomMatchCache(): BomMatchCache | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed: BomMatchCache = JSON.parse(raw);
    // matched_subtree가 없으면 빈 배열로 복원
    parsed.changePoints = parsed.changePoints.map(cp => ({
      matched_subtree: [],
      ...cp,
    }));
    return parsed;
  } catch {
    return null;
  }
}

export function clearBomMatchCache() {
  localStorage.removeItem(CACHE_KEY);
}

export default function App() {
  const [step,         setStep]         = useState(1);
  const [changePoints, setChangePoints] = useState<ChangePoint[]>([]);
  const [bomRows,      setBomRows]      = useState<BomRow[]>([]);
  const [selections,   setSelections]   = useState<SelectionItem[]>([]);
  const [baseBomFile,  setBaseBomFile]  = useState<File | undefined>(undefined);
  const [fixture,      setFixture]      = useState<'quickzone' | 'compact_oven' | undefined>(undefined);

  const isFullWidth = step === 4;

  return (
    <div style={{ minHeight: '100vh', background: '#f9fafb', display: 'flex', flexDirection: 'column' }}>
      <div style={{ background: '#fff', borderBottom: '1px solid #e5e7eb', padding: '0 32px' }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          <div style={{ padding: '14px 0 0', fontWeight: 800, fontSize: 18, color: '#111827' }}>
            BOM 변경 파이프라인
          </div>
          <StepIndicator current={step} />
        </div>
      </div>

      <div style={{
        flex: 1,
        maxWidth: 1400,
        width: '100%',
        margin: '0 auto',
        padding: isFullWidth ? '20px 24px' : '20px 32px',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        boxSizing: 'border-box',
      }}>
        {step === 1 && (
          <Step1Upload
            onDone={({ changePoints: cps, bomRows: brs, bomFile, fixture: fix }) => {
              setChangePoints(cps);
              setBomRows(brs);
              setBaseBomFile(bomFile);
              setFixture(fix);
              setStep(2);
            }}
          />
        )}

        {step === 2 && (
          <Step2BomMatchReview
            changePoints={changePoints}
            fixture={fixture}
            onBack={() => setStep(1)}
            onDone={(updated) => {
              setChangePoints(updated);
              setStep(3);
            }}
          />
        )}

        {step === 3 && (
          <Step3HistoryReview
            changePoints={changePoints}
            onBack={() => setStep(2)}
            onDone={(sels) => { setSelections(sels); setStep(4); }}
          />
        )}

        {step === 4 && (
          <Step4BomEdit
            changePoints={changePoints}
            selections={selections}
            bomRows={bomRows}
            baseBomFile={baseBomFile}
            fixture={fixture}
            onBack={() => setStep(3)}
          />
        )}
      </div>
    </div>
  );
}