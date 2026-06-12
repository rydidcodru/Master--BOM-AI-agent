export interface DeletedPart {
  part_no: string;
  description: string;
  lvl: string;
  reason: string;
}

export interface MatchedSubtreePart {
  part_no: string;
  lvl: string;
  parent_no: string;
  description: string;
  qty: string;
  uom: string;
}

export interface ChangePoint {
  module: string;
  part: string;
  change_detail: string;
  change_reason: string;
  discipline: string;
  change_type: string;
  concern: string;
  source_pptx: string;
  evidence_slide: number;
  bom_level: string;
  base_part_no: string;
  new_part_no: string;
  // bom_match 결과
  change_scope?: 'full' | 'partial';
  match_confidence?: 'high' | 'medium' | 'low';
  match_reason?: string;
  matched_subtree?: MatchedSubtreePart[];
  preserve_parts?: string[];
  deleted_parts?: DeletedPart[];
  // history_search 결과
  history_candidates: Candidate[];
}

export interface Candidate {
  master_id: number;
  base_model: string;
  new_model: string;
  source_file: string;
  rank: number;
  select_reason: string;
  case_parts: CasePart[];
  linked_parts: LinkedPart[];
}

export interface CasePart {
  part_name: string;
  base_part_no: string;
  new_part_no: string;
  bom_level: string;
  changing_point: string;
  changing_reason: string;
}

export interface LinkedPart {
  part_name: string;
  base_part_no: string;
  new_part_no: string;
  change_type: string;
  relevance_reason: string;
}

export interface SelectionItem {
  change_point_idx: number;
  part: string;
  base_part_no: string;
  new_part_no: string;
  change_detail: string;
  change_reason: string;
  change_type: string;
  discipline: string;
  bom_level: string;
  selected_cases: number[];
  added_linked_parts: LinkedPart[];
  skipped: boolean;
}

export interface BomRow {
  part_no: string;
  lvl: string;
  parent_no: string;
  description: string;
  qty: string;
  uom: string;
  maker: string;
  part_type: string;
  change_status: '' | '변경' | '추가' | '삭제';
  change_note: string;
  new_part_no_suggested: string;
  new_part_no_confirmed: string;
  original_part_no: string;
  row_id: string;
  confirmed: boolean;
  excluded: boolean;
}

export interface BomBuildResponse {
  rows: BomRow[];
  summary: { 변경: number; 추가: number; 삭제: number; 미확정: number };
}