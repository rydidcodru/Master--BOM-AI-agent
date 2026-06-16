// FastAPI 응답/요청 타입. 백엔드 workflow.compact_candidate / apply 결과 형태와 맞춘다.

export interface PastModel {
  source_file?: string;
  source_sheet?: string;
  project_name?: string;
  base_model?: string;
  new_model?: string;
  region?: string;
}

export interface ScoreComponents {
  [key: string]: number | boolean | undefined;
}

export interface RelatedPart {
  relation?: string;
  detail_id?: number;
  part_name?: string;
  base_part_no?: string;
  new_part_no?: string;
  new_qty?: string;
  change_point?: string;
  change_reason?: string;
  classification?: string;
  level?: number | null;
  bom_level?: string;
}

export interface SubtreeSummary {
  root?: RelatedPart | null;
  descendant_count?: number;
  shown_count?: number;
  parts?: RelatedPart[];
}

export interface Candidate {
  detail_id: number;
  master_id?: number;
  source_file?: string;
  line_order?: number;
  level?: number | null;
  bom_level?: string;
  part_name?: string;
  base_part_no?: string;
  new_part_no?: string;
  change_point?: string;
  change_reason?: string;
  has_change_text?: boolean;
  classification?: string;
  past_model?: PastModel;
  score?: number;
  score_reasons?: string[];
  score_components?: ScoreComponents;
  related_parts?: RelatedPart[];
  candidate_subtree?: SubtreeSummary;
}

export interface ChangeInput {
  change_id?: string;
  slide_number?: string | number | null;
  description?: string;
  module_name?: string;
  part_name?: string;
  base_part_no?: string;
  change_point?: string;
  change_reason?: string;
  change_type?: string;
  target_value?: string;
  change_intent_keywords?: string;
  evidence?: string;
  [key: string]: unknown;
}

export interface RecommendResult {
  change_id?: string;
  input: ChangeInput;
  lookup_mode?: string;
  candidates: Candidate[];
}

export interface RecommendResponse {
  results: RecommendResult[];
  embedding_status?: Record<string, unknown>;
}

export type BomAction = "change" | "add" | "delete";

export interface BomSelection {
  change_id?: string;
  candidate_detail_id: number;
  action?: BomAction;
  target_base_part_no?: string;
  target_part_name?: string;
  change_point?: string;
  change_reason?: string;
  include_subtree?: boolean;
  bundle_detail_ids?: number[];
}

// apply 결과의 new_bom 한 행. 백엔드가 dict를 그대로 주므로 느슨하게 둔다.
export interface BomRow {
  detail_id?: number | null;
  line_order?: number;
  bom_level?: string;
  bom_depth?: number | null;
  part_type?: string;
  part_name?: string;
  base_part_no?: string;
  new_part_no?: string;
  base_qty?: string;
  new_qty?: string;
  changing_point?: string;
  changing_reason?: string;
  supplier?: string;
  classification?: string;
  applied_action?: string;
  [key: string]: unknown;
}

export interface ApplyResult {
  base_master_id?: number | null;
  new_bom: BomRow[];
  change_list: Record<string, unknown>[];
  unmatched: Record<string, unknown>[];
}

// 변경점 작성 탭의 편집 단계(탭 전환에도 유지하려 store에 보관).
export type DesignPhase = "review" | "edit";

// 편집 가능한 변경점 행(= master 초안 한 줄). DesignPointRecord + UI 상태.
export interface DesignRow extends DesignPointRecord {
  _id: number;
  _newPno: string; // 사용자가 부여/수정하는 New P/No (TBD 포함)
}

// 심의표 Design Points 표 한 행(충실 추출). 거의 master 초안.
export interface DesignPointRecord {
  slide_number?: number;
  design_point?: string;
  no?: string;
  lev?: string;
  base_part_no?: string;
  new_part_no?: string;
  part_name?: string;
  uom?: string;
  qty?: string;
  change_point?: string;
}

export interface BomDumpResult {
  columns?: string[];
  tables?: number;
  rows?: number;
  design_points?: string[];
  summaries?: Record<string, string>; // design_point(모듈) → 변경점 요약 문구
  records?: DesignPointRecord[];
  filename?: string;
}

// 페이지1에서 부여한 master 한 줄(변경점 + New P/No). → /bom/apply-master
export interface MasterChange {
  no?: string;
  design_point?: string;
  lev?: string;
  bom_level?: string;
  part_name?: string;
  base_part_no?: string;
  new_part_no?: string;
  qty?: string;
  change_point?: string;
  change_reason?: string;
  classification?: string;
  action?: string;
}

export interface Master {
  master_id: number;
  source_file?: string;
  source_sheet?: string;
  base_model?: string;
  new_model?: string;
  project_name?: string;
  region?: string;
  [key: string]: unknown;
}

export interface Stats {
  masters?: number;
  details?: number;
  embedded?: number;
  linked_parents?: number;
  [key: string]: unknown;
}

export interface PptxExtractResult {
  filename?: string;
  slide_count?: number;
  changes?: ChangeInput[];
  slides?: { slide_number?: number; text?: string }[];
  output_csv?: { path?: string; rows?: number; columns?: string[] };
  llm_status?: Record<string, unknown>;
}

export interface BaseBomParseResult {
  filename?: string;
  summary?: {
    rows?: number;
    max_depth?: number | null;
    substitutes?: number;
    root_part_no?: string;
    root_part_name?: string;
  };
  rows?: BomRow[];
}
