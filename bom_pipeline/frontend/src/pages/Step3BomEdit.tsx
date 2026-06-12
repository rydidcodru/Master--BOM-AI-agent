import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import {
  ModuleRegistry,
  ClientSideRowModelModule,
  type ColDef,
  type ICellRendererParams,
  type GridApi,
  type IRowNode,
} from 'ag-grid-community';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

import type { BomRow, ChangePoint, SelectionItem } from '../types';
import ChangePanel from '../components/ChangePanel';
import { buildBom, exportXlsx } from '../api/client';

ModuleRegistry.registerModules([ClientSideRowModelModule]);

interface Props {
  changePoints: ChangePoint[];
  selections: SelectionItem[];
  bomRows: BomRow[];
  baseBomFile?: File;
  fixture?: 'quickzone' | 'compact_oven';
  onBack: () => void;
}

// ── 상태 색상 팔레트 ──────────────────────────────────────────────────────
const PALETTE: Record<string, { bg: string; border: string; text: string }> = {
  '변경': { bg: '#fffbeb', border: '#f59e0b', text: '#b45309' },
  '추가': { bg: '#f0fdf4', border: '#22c55e', text: '#15803d' },
  '삭제': { bg: '#fff1f2', border: '#f87171', text: '#b91c1c' },
};

// ── 셀 렌더러: 상태 배지 ────────────────────────────────────────────────
function StatusCell({ value }: ICellRendererParams<BomRow>) {
  const p = PALETTE[value as string];
  if (!value || !p) return <span style={{ color: '#d1d5db' }}>—</span>;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '1px 8px', borderRadius: 12, fontSize: 11, fontWeight: 700,
      background: p.bg, color: p.text, border: `1px solid ${p.border}`,
    }}>
      {value}
    </span>
  );
}

// ── 셀 렌더러: 확정 상태 아이콘 ─────────────────────────────────────────
function ConfirmCell({ data }: ICellRendererParams<BomRow>) {
  if (!data?.change_status) return null;
  if (data.excluded)  return <span style={{ color: '#9ca3af', fontSize: 13 }}>⊘ 제외</span>;
  if (data.confirmed) return <span style={{ color: '#16a34a', fontSize: 13 }}>✓ 확정</span>;
  return <span style={{ color: '#f59e0b', fontSize: 13 }}>⚠ 미확정</span>;
}

// ── 셀 렌더러: Part No (변경 행은 before → after 표시) ──────────────────
function PartNoCell({ data }: ICellRendererParams<BomRow>) {
  if (!data) return null;
  const isChange = data.change_status === '변경';
  if (isChange && data.original_part_no && data.original_part_no !== data.part_no) {
    return (
      <span style={{ fontFamily: 'monospace', fontSize: 11 }}>
        <span style={{ color: '#9ca3af', textDecoration: 'line-through' }}>{data.original_part_no}</span>
        <span style={{ color: '#6b7280', margin: '0 4px' }}>→</span>
        <span style={{ color: '#16a34a', fontWeight: 600 }}>{data.part_no}</span>
      </span>
    );
  }
  const color = data.change_status === '추가' ? '#15803d'
              : data.change_status === '삭제' ? '#9ca3af' : '#111827';
  return (
    <span style={{ fontFamily: 'monospace', fontSize: 12, color,
      textDecoration: data.change_status === '삭제' ? 'line-through' : 'none' }}>
      {data.part_no || '—'}
    </span>
  );
}

export default function Step3BomEdit({ changePoints, selections, bomRows, baseBomFile, fixture, onBack }: Props) {
  const [rows,      setRows]      = useState<BomRow[]>([]);
  const [summary,   setSummary]   = useState({ 변경: 0, 추가: 0, 삭제: 0, 미확정: 0 });
  const [selected,  setSelected]  = useState<BomRow | null>(null);
  const [showAll,   setShowAll]   = useState(false);
  const [loading,   setLoading]   = useState(true);
  const [exporting, setExporting] = useState(false);
  const gridRef   = useRef<AgGridReact<BomRow>>(null);
  const apiRef    = useRef<GridApi<BomRow> | null>(null);

  // 변경 행 목록 (순서 고정)
  const changedRowIds = useMemo(
    () => rows.filter(r => r.change_status !== '' && !r.excluded).map(r => r.row_id),
    [rows],
  );

  useEffect(() => {
    buildBom(changePoints, selections, bomRows, fixture)
      .then(res => { setRows(res.rows); setSummary(res.summary); })
      .finally(() => setLoading(false));
  }, []);

  // 미확정 수 실시간 계산
  const unconfirmedCount = useMemo(
    () => rows.filter(r => r.change_status !== '' && !r.confirmed && !r.excluded).length,
    [rows],
  );

  // 표시 행 필터
  const displayRows = useMemo(
    () => showAll ? rows : rows.filter(r => r.change_status !== ''),
    [rows, showAll],
  );

  // 행 업데이트 헬퍼
  const updateRow = useCallback((rowId: string, patch: Partial<BomRow>) => {
    setRows(prev => prev.map(r => r.row_id === rowId ? { ...r, ...patch } : r));
    setSelected(prev => prev?.row_id === rowId ? { ...prev, ...patch } as BomRow : prev);
  }, []);

  function handleConfirm(rowId: string, confirmedPno: string) {
    updateRow(rowId, { confirmed: true, excluded: false, new_part_no_confirmed: confirmedPno });
  }

  function handleExclude(rowId: string) {
    updateRow(rowId, { excluded: true, confirmed: false });
    setSelected(null);
  }

  function handleUndoExclude(rowId: string) {
    updateRow(rowId, { excluded: false });
  }

  // prev / next 변경 행 탐색
  function navigateTo(direction: 'prev' | 'next') {
    const currentIdx = selected ? changedRowIds.indexOf(selected.row_id) : -1;
    let nextIdx = direction === 'next' ? currentIdx + 1 : currentIdx - 1;
    nextIdx = Math.max(0, Math.min(nextIdx, changedRowIds.length - 1));
    const targetId = changedRowIds[nextIdx];
    const target   = rows.find(r => r.row_id === targetId) ?? null;
    setSelected(target);
    // 그리드에서 해당 행으로 스크롤
    if (target && apiRef.current) {
      apiRef.current.forEachNode((node: IRowNode<BomRow>) => {
        if (node.data?.row_id === targetId) {
          apiRef.current!.ensureNodeVisible(node, 'middle');
          node.setSelected(true, true);
        }
      });
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await exportXlsx(rows, changePoints, selections, baseBomFile, fixture);
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href     = url;
      a.download = 'new_bom.xlsx';
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  }

  // AG Grid 컬럼 정의
  const colDefs: ColDef<BomRow>[] = [
    {
      field: 'change_status',
      headerName: '상태',
      width: 72,
      pinned: 'left',
      cellRenderer: StatusCell,
      cellStyle: { display: 'flex', alignItems: 'center' },
    },
    {
      headerName: '확정',
      width: 80,
      pinned: 'left',
      cellRenderer: ConfirmCell,
      cellStyle: { display: 'flex', alignItems: 'center' },
    },
    { field: 'lvl',         headerName: '레벨', width: 54, pinned: 'left' },
    {
      field: 'part_no',
      headerName: 'Part No',
      width: 190,
      pinned: 'left',
      cellRenderer: PartNoCell,
      cellStyle: { display: 'flex', alignItems: 'center' },
    },
    { field: 'description', headerName: '부품명', flex: 1, minWidth: 200 },
    { field: 'qty',         headerName: '수량',   width: 56 },
    { field: 'uom',         headerName: '단위',   width: 56 },
    { field: 'maker',       headerName: '메이커', width: 110 },
    { field: 'part_type',   headerName: '유형',   width: 80 },
    {
      field: 'change_note',
      headerName: 'AI 참고 메모',
      flex: 2,
      minWidth: 240,
      cellStyle: { fontSize: 12, color: '#6b7280', whiteSpace: 'normal', lineHeight: '1.45' },
      wrapText: true,
      autoHeight: true,
    },
  ];

  const rowClassRules = {
    'bom-row-changed':  (p: { data?: BomRow }) => p.data?.change_status === '변경' && !p.data?.excluded,
    'bom-row-added':    (p: { data?: BomRow }) => p.data?.change_status === '추가' && !p.data?.excluded,
    'bom-row-deleted':  (p: { data?: BomRow }) => p.data?.change_status === '삭제' && !p.data?.excluded,
    'bom-row-excluded': (p: { data?: BomRow }) => !!p.data?.excluded,
    'bom-row-confirmed':(p: { data?: BomRow }) => !!p.data?.confirmed && !p.data?.excluded,
  };

  const currentIdx = selected ? changedRowIds.indexOf(selected.row_id) : -1;
  const canExport  = unconfirmedCount === 0;

  return (
    <>
      <style>{`
        .bom-row-changed  { background-color: #fffbeb !important; }
        .bom-row-changed:hover { background-color: #fef3c7 !important; }
        .bom-row-added    { background-color: #f0fdf4 !important; }
        .bom-row-added:hover { background-color: #dcfce7 !important; }
        .bom-row-deleted  { background-color: #fff1f2 !important; opacity: 0.75; }
        .bom-row-excluded { background-color: #f9fafb !important; opacity: 0.5; }
        .bom-row-confirmed.bom-row-changed { background-color: #f0fdf4 !important; }
        .ag-row-selected  { outline: 2px solid #2563eb !important; outline-offset: -2px; }
        .ag-cell          { align-items: center !important; }
      `}</style>

      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

        {/* ── 상단 툴바 ── */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          marginBottom: 10, flexWrap: 'wrap',
          background: '#fff', padding: '8px 0',
        }}>
          <button style={outlineBtn} onClick={onBack}>← 검토로 돌아가기</button>

          {/* 요약 배지 */}
          <SummaryBadge label="변경" count={summary.변경} color="#b45309" bg="#fffbeb" border="#f59e0b" />
          <SummaryBadge label="추가" count={summary.추가} color="#15803d" bg="#f0fdf4" border="#22c55e" />
          <SummaryBadge label="삭제" count={summary.삭제} color="#b91c1c" bg="#fff1f2" border="#f87171" />

          {/* 미확정 카운터 */}
          {unconfirmedCount > 0 && (
            <span style={{
              padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 700,
              background: '#fef3c7', color: '#b45309', border: '1px solid #f59e0b',
            }}>
              ⚠ 미확정 {unconfirmedCount}건
            </span>
          )}
          {unconfirmedCount === 0 && rows.length > 0 && (
            <span style={{
              padding: '4px 12px', borderRadius: 20, fontSize: 12, fontWeight: 700,
              background: '#f0fdf4', color: '#15803d', border: '1px solid #22c55e',
            }}>
              ✓ 모두 확정됨
            </span>
          )}

          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', marginLeft: 8 }}>
            <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
            전체 BOM 표시 ({rows.length}행)
          </label>

          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {/* prev/next 변경 행 이동 */}
            {changedRowIds.length > 0 && (
              <>
                <button style={navBtn} onClick={() => navigateTo('prev')} disabled={currentIdx <= 0}>
                  ◀ 이전 변경
                </button>
                <span style={{ fontSize: 12, color: '#6b7280', alignSelf: 'center' }}>
                  {currentIdx >= 0 ? `${currentIdx + 1} / ${changedRowIds.length}` : `${changedRowIds.length}건`}
                </span>
                <button style={navBtn} onClick={() => navigateTo('next')} disabled={currentIdx >= changedRowIds.length - 1}>
                  다음 변경 ▶
                </button>
              </>
            )}

            <button
              style={{
                ...primaryBtn,
                opacity:   canExport ? 1 : 0.45,
                cursor:    canExport ? 'pointer' : 'not-allowed',
                background: canExport ? '#16a34a' : '#9ca3af',
              }}
              disabled={!canExport || exporting}
              onClick={handleExport}
              title={canExport ? '' : `미확정 항목 ${unconfirmedCount}건을 모두 확정 또는 제외하세요`}
            >
              {exporting ? '생성 중...' : '⬇ xlsx 다운로드'}
            </button>
          </div>
        </div>

        {/* ── 본문: 그리드 + 슬라이드 드로어 ── */}
        <div style={{
          flex: 1, display: 'flex', overflow: 'hidden',
          border: '1px solid #e5e7eb', borderRadius: 10, position: 'relative',
        }}>
          {loading ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280' }}>
              BOM 데이터 불러오는 중...
            </div>
          ) : (
            <>
              <div
                className="ag-theme-alpine"
                style={{
                  flex: 1, overflow: 'auto',
                  transition: 'margin-right 0.22s ease',
                  marginRight: selected ? 360 : 0,
                }}
              >
                <AgGridReact<BomRow>
                  ref={gridRef}
                  rowData={displayRows}
                  columnDefs={colDefs}
                  rowClassRules={rowClassRules as any}
                  rowSelection="single"
                  rowHeight={42}
                  headerHeight={38}
                  defaultColDef={{ resizable: true, sortable: false }}
                  getRowId={p => p.data.row_id}
                  domLayout="autoHeight"
                  onGridReady={p => { apiRef.current = p.api; }}
                  onRowClicked={e => {
                    if (e.data?.change_status) setSelected(e.data);
                  }}
                />
              </div>

              {/* ── 슬라이드 드로어 ── */}
              <div style={{
                position: 'absolute', top: 0, right: 0,
                width: 360, height: '100%',
                transform: selected ? 'translateX(0)' : 'translateX(100%)',
                transition: 'transform 0.22s ease',
                boxShadow: '-4px 0 20px rgba(0,0,0,0.10)',
                background: '#fff',
                borderLeft: '1px solid #e5e7eb',
                borderRadius: '0 10px 10px 0',
                zIndex: 10,
                display: 'flex', flexDirection: 'column',
              }}>
                {/* 드로어 헤더 */}
                <div style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 14px',
                  borderBottom: '1px solid #e5e7eb',
                  background: '#f9fafb',
                  borderRadius: '0 10px 0 0',
                  gap: 8,
                }}>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <button style={navBtn} onClick={() => navigateTo('prev')} disabled={currentIdx <= 0}>◀</button>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>
                      {currentIdx >= 0 ? `${currentIdx + 1} / ${changedRowIds.length}` : ''}
                    </span>
                    <button style={navBtn} onClick={() => navigateTo('next')} disabled={currentIdx >= changedRowIds.length - 1}>▶</button>
                  </div>
                  <span style={{ fontWeight: 700, fontSize: 13, color: '#111827', flex: 1, textAlign: 'center', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {selected?.description || selected?.part_no || ''}
                  </span>
                  <button
                    onClick={() => setSelected(null)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: '#6b7280', padding: '0 4px', lineHeight: 1 }}
                  >×</button>
                </div>

                {/* 드로어 본문 */}
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  {selected && (
                    <ChangePanel
                      row={selected}
                      changePoints={changePoints}
                      selections={selections}
                      onConfirm={(rowId, pno) => handleConfirm(rowId, pno)}
                      onExclude={(rowId) => handleExclude(rowId)}
                      onUndo={(rowId) => handleUndoExclude(rowId)}
                    />
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function SummaryBadge({ label, count, color, bg, border }: {
  label: string; count: number; color: string; bg: string; border: string;
}) {
  if (!count) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '3px 10px', borderRadius: 12, fontSize: 12, fontWeight: 600,
      background: bg, color, border: `1px solid ${border}`,
    }}>
      {label} {count}
    </span>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: '7px 18px', background: '#16a34a', color: '#fff',
  border: 'none', borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: 'pointer',
};
const outlineBtn: React.CSSProperties = {
  padding: '7px 14px', background: '#fff', color: '#374151',
  border: '1px solid #d1d5db', borderRadius: 8, fontWeight: 600, fontSize: 13, cursor: 'pointer',
};
const navBtn: React.CSSProperties = {
  padding: '4px 10px', background: '#f3f4f6', color: '#374151',
  border: '1px solid #d1d5db', borderRadius: 6, fontSize: 12, cursor: 'pointer',
};