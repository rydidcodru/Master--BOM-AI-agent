// FastAPI 클라이언트. 기본은 Vite 프록시(/api), 화면에서 절대 URL로 바꿀 수 있다.

import type {
  ApplyResult,
  BaseBomParseResult,
  BomDumpResult,
  BomSelection,
  ChangeInput,
  Master,
  MasterChange,
  PptxExtractResult,
  RecommendResponse,
  Stats,
  SubtreeSummary,
  RelatedPart,
} from "./types";

interface ApplyMasterPayload {
  base_master_id?: number | null;
  base_bom?: unknown[] | null;
  changes: MasterChange[];
  propagate_change_upward: boolean;
}

// dev(Vite): /api 프록시. prod(FastAPI가 정적 서빙): 같은 출처(빈 base).
const ENV_BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "");
let API_BASE = ENV_BASE ?? (import.meta.env.DEV ? "/api" : "");

export function setApiBase(url: string) {
  API_BASE = url.replace(/\/$/, "");
}
export function getApiBase(): string {
  return API_BASE;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      resolve(result.split(",")[1] ?? "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  stats: () => request<Stats>("/stats"),
  masters: () => request<Master[]>("/masters"),

  recommend: (changes: ChangeInput[], topK: number, relatedLimit: number, autoEmbed: boolean, corpus?: string) =>
    request<RecommendResponse>("/changes/recommend", {
      method: "POST",
      body: JSON.stringify({ changes, top_k: topK, related_limit: relatedLimit, auto_embed: autoEmbed, corpus }),
    }),

  async extractPptxChanges(file: File, useLlm: boolean, llmModel: string): Promise<PptxExtractResult> {
    const content_base64 = await fileToBase64(file);
    return request<PptxExtractResult>("/pptx/extract-changes", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content_base64, use_llm: useLlm, llm_model: llmModel || null }),
    });
  },

  async extractPptxBom(file: File): Promise<BomDumpResult> {
    const content_base64 = await fileToBase64(file);
    const result = await request<BomDumpResult>("/pptx/extract-bom", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content_base64 }),
    });
    return { ...result, filename: file.name };
  },

  async parseBaseBomXlsx(file: File): Promise<BaseBomParseResult> {
    const content_base64 = await fileToBase64(file);
    return request<BaseBomParseResult>("/bom/base/parse-xlsx", {
      method: "POST",
      body: JSON.stringify({ filename: file.name, content_base64 }),
    });
  },

  apply: (payload: {
    base_master_id?: number | null;
    base_bom?: unknown[] | null;
    selections: BomSelection[];
    propagate_change_upward: boolean;
  }) => request<ApplyResult>("/bom/apply", { method: "POST", body: JSON.stringify(payload) }),

  async applyExport(payload: {
    base_master_id?: number | null;
    base_bom?: unknown[] | null;
    selections: BomSelection[];
    propagate_change_upward: boolean;
  }): Promise<Blob> {
    const res = await fetch(`${API_BASE}/bom/apply/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`export 실패: ${res.status}`);
    return res.blob();
  },

  applyMaster: (payload: ApplyMasterPayload) =>
    request<ApplyResult>("/bom/apply-master", { method: "POST", body: JSON.stringify(payload) }),

  async applyMasterExport(payload: ApplyMasterPayload): Promise<Blob> {
    const res = await fetch(`${API_BASE}/bom/apply-master/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`export 실패: ${res.status}`);
    return res.blob();
  },

  // 개발부품마스터(템플릿 양식) 산출물 — fmt: xlsx | csv
  async masterExport(payload: ApplyMasterPayload, fmt: "xlsx" | "csv"): Promise<Blob> {
    const res = await fetch(`${API_BASE}/bom/master/export?fmt=${fmt}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`master export 실패: ${res.status}`);
    return res.blob();
  },

  related: (detailId: number, limit = 40) =>
    request<{ related_parts: RelatedPart[] }>(`/history/${detailId}/related?limit=${limit}`),
  connected: (detailId: number, limit = 20) =>
    request<{ connected_parts: RelatedPart[] }>(`/history/${detailId}/connected?limit=${limit}`),
  subtree: (detailId: number, limit = 50) =>
    request<SubtreeSummary>(`/history/${detailId}/subtree?limit=${limit}`),
};
