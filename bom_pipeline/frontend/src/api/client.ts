import axios from 'axios';
import type { BomRow, BomBuildResponse, SelectionItem, ChangePoint } from '../types';

const api = axios.create({ baseURL: 'http://localhost:8000/api' });

export interface BomMatchResult {
  change_points: ChangePoint[];
  bom_rows: BomRow[];
}

// ── STEP 1→2: parse_pptx + bom_match ────────────────────────────────────────

export async function runBomMatch(pptx: File, bom?: File): Promise<BomMatchResult> {
  const form = new FormData();
  form.append('pptx', pptx);
  if (bom) form.append('bom', bom);
  const res = await api.post('/pipeline/bom_match', form);
  return { change_points: res.data.change_points, bom_rows: res.data.bom_rows ?? [] };
}

export async function runBomMatchFixture(fixture: 'quickzone' | 'compact_oven'): Promise<BomMatchResult> {
  const form = new FormData();
  form.append('fixture', fixture);
  const res = await api.post('/pipeline/bom_match', form);
  return { change_points: res.data.change_points, bom_rows: res.data.bom_rows ?? [] };
}

// ── STEP 2→3: history_search ─────────────────────────────────────────────────

export async function runHistorySearch(
  changePoints: ChangePoint[],
  fixture?: 'quickzone' | 'compact_oven',
): Promise<ChangePoint[]> {
  const res = await api.post('/pipeline/history_search', {
    change_points: changePoints,
    ...(fixture ? { fixture } : {}),
  });
  return res.data.change_points;
}

// ── STEP 3→4: BOM 빌드 ───────────────────────────────────────────────────────

export async function buildBom(
  changePoints: ChangePoint[],
  selections: SelectionItem[],
  bomRows: BomRow[],
  fixture?: 'quickzone' | 'compact_oven',
): Promise<BomBuildResponse> {
  const res = await api.post('/bom/build', {
    change_points: changePoints,
    selections,
    bom_rows: bomRows,
    fixture: fixture ?? null,
  });
  return res.data;
}

// ── STEP 4: xlsx 다운로드 ────────────────────────────────────────────────────

export async function exportXlsx(
  rows: BomRow[],
  changePoints?: ChangePoint[],
  selections?: SelectionItem[],
  baseBomFile?: File,
  fixture?: 'quickzone' | 'compact_oven',
): Promise<Blob> {
  if (changePoints && selections) {
    const form = new FormData();
    form.append('req_json', JSON.stringify({
      change_points: changePoints,
      selections,
      bom_rows: rows,
      fixture: fixture ?? null,
    }));
    if (baseBomFile) form.append('base_bom', baseBomFile);
    const res = await api.post('/bom/export', form, { responseType: 'blob' });
    return res.data;
  }
  const res = await api.post('/bom/export/simple', rows, { responseType: 'blob' });
  return res.data;
}