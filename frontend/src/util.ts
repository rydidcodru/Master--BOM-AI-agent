import type { BomAction, BomRow } from "./types";

export type ChangeKind = "change" | "add" | "delete" | "tbd" | "keep";

export const KIND_META: Record<ChangeKind, { label: string; cls: string; icon: string }> = {
  change: { label: "변경", cls: "k-change", icon: "●" },
  add: { label: "추가", cls: "k-add", icon: "＋" },
  delete: { label: "삭제", cls: "k-delete", icon: "－" },
  tbd: { label: "미정(TBD)", cls: "k-tbd", icon: "⚠" },
  keep: { label: "변경없음", cls: "k-keep", icon: "·" },
};

/** apply 결과 new_bom 행의 변경 종류를 판정. 상위전파/미발번은 '미정(TBD)'. */
export function rowKind(row: BomRow): ChangeKind {
  const applied = String(row.applied_action || "").toLowerCase();
  const newPno = String(row.new_part_no || "").trim().toUpperCase();
  const cls = String(row.classification || "").toLowerCase();
  if (newPno === "TBD" || applied === "propagated_change" || cls.includes("전파")) return "tbd";
  if (applied === "delete") return "delete";
  if (applied === "add" || applied === "add_bundle") return "add";
  if (applied === "change" || applied.startsWith("subtree")) return "change";
  return "keep";
}

export function actionKind(action: BomAction | undefined): ChangeKind {
  if (action === "add") return "add";
  if (action === "delete") return "delete";
  return "change";
}

// 심의표 Design Points 분류(색): New P/No 마커로 추정.
//   TBD → 신규(빨강) / ←·〃·빈칸 → 공용(초록) / 실제 번호 → 공용·변경(노랑)
export type DpClass = "new" | "common" | "changed";
export const DP_META: Record<DpClass, { label: string; cls: string }> = {
  new: { label: "신규", cls: "dp-new" },
  common: { label: "공용", cls: "dp-common" },
  changed: { label: "공용/변경", cls: "dp-changed" },
};
const DITTO = new Set(["←", "〃", "同", "ditto", "-", "—"]);
export function isTbd(newPno: unknown): boolean {
  return String(newPno ?? "").trim().toUpperCase() === "TBD";
}
export function dpClass(newPno: unknown, basePno?: unknown): DpClass {
  const v = String(newPno ?? "").trim();
  if (isTbd(v)) return "new";
  if (!v || DITTO.has(v)) return "common";
  if (basePno && String(basePno).trim().toUpperCase() === v.toUpperCase()) return "common";
  return "changed";
}
// master에 실제로 들어갈 New P/No 값.
//   공용(←/〃/빈칸) → base p/no를 그대로 사용(부품 재사용이라 번호 동일).
//   신규(TBD) → 'TBD'(사용자가 회의에서 부여).
//   공용/변경 → 추출된 실제 번호.
export function effectiveNewPno(newPno: unknown, basePno: unknown): string {
  const raw = String(newPno ?? "").trim();
  if (isTbd(raw)) return "TBD";
  if (dpClass(raw, basePno) === "common") return String(basePno ?? "").trim() || raw;
  return raw;
}

export function levDepth(lev: unknown): number {
  const m = String(lev ?? "").match(/(\d+)\s*$/);
  return m ? Number(m[1]) : 0;
}

/** 깊이 → lev 문자열(".1", "..2", "...3" …). 상위 부품 지정/Level 수정 시 사용. */
export function makeLev(depth: number): string {
  if (depth <= 0) return "";
  return ".".repeat(depth) + depth;
}

/* ─────────── 깊이 기반 트리(프리오더 평면 리스트) 접기 헬퍼 ───────────
   BOM/변경점은 lev/bom_depth로 정렬된 프리오더 리스트라, 어떤 행의 자식은
   바로 뒤에 이어지는 '더 깊은' 연속 구간이다. 이를 이용해 부모-자식 접기를 계산. */

/** i 행 바로 뒤가 더 깊으면(자식이 있으면) true. */
export function hasChildrenAt(depths: number[], i: number): boolean {
  return i + 1 < depths.length && depths[i + 1] > depths[i];
}

/** i 행의 후손 개수(뒤로 이어지는 더 깊은 연속 구간). */
export function descendantCountAt(depths: number[], i: number): number {
  let n = 0;
  for (let j = i + 1; j < depths.length; j++) {
    if (depths[j] > depths[i]) n++; else break;
  }
  return n;
}

/** folded(접힌 행 key 집합)를 반영해 각 행의 표시 여부를 계산. */
export function computeVisible(depths: number[], folded: Set<string>, keyAt: (i: number) => string): boolean[] {
  const vis = new Array(depths.length).fill(true);
  let cutoff: number | null = null; // 이 깊이보다 깊은 행은 숨김
  for (let i = 0; i < depths.length; i++) {
    if (cutoff !== null && depths[i] > cutoff) { vis[i] = false; continue; }
    cutoff = null;
    vis[i] = true;
    if (folded.has(keyAt(i)) && hasChildrenAt(depths, i)) cutoff = depths[i];
  }
  return vis;
}

export function num(value: unknown, digits = 3): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return value.toFixed(digits);
}

export function truncate(value: unknown, limit = 80): string {
  const text = String(value ?? "").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function changeLabel(change: { description?: string; module_name?: string; part_name?: string; change_point?: string; change_reason?: string; slide_number?: unknown }): string {
  const head =
    String(change.part_name || "").trim() ||
    String(change.description || "").trim() ||
    String(change.module_name || "").trim() ||
    "모듈 미지정";
  const detail = String(change.change_point || "").trim() || String(change.change_reason || "").trim();
  const slide = String(change.slide_number ?? "").trim();
  return [slide ? `S${slide}` : "", head, detail ? truncate(detail, 50) : ""].filter(Boolean).join(" · ");
}

/** 객체 배열 → CSV 텍스트(Excel 한글용 BOM 포함은 호출측에서). */
export function toCsv(rows: Record<string, unknown>[], columns: string[]): string {
  const esc = (v: unknown) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.join(",");
  const body = rows.map((r) => columns.map((c) => esc(r[c])).join(",")).join("\n");
  return `${head}\n${body}\n`;
}

/** CSV 텍스트 → 객체 배열 (간단 파서, 따옴표 처리 포함). */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let field = "";
  let record: string[] = [];
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ",") { record.push(field); field = ""; }
    else if (ch === "\n") { record.push(field); rows.push(record); record = []; field = ""; }
    else if (ch === "\r") { /* skip */ }
    else field += ch;
  }
  if (field.length > 0 || record.length > 0) { record.push(field); rows.push(record); }
  if (rows.length === 0) return [];
  const header = rows[0];
  return rows.slice(1).filter((r) => r.some((c) => c.trim())).map((r) => {
    const obj: Record<string, string> = {};
    header.forEach((h, idx) => (obj[h.trim()] = (r[idx] ?? "").trim()));
    return obj;
  });
}
