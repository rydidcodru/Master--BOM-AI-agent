import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type {
  ApplyResult,
  BomRow,
  ChangeInput,
  DesignPhase,
  DesignRow,
  Master,
  MasterChange,
  RecommendResult,
} from "./types";

// Streamlit session_state 대체. 탭 간 공유 상태(추천 결과 → 반영 탭)를 담는다.
interface Store {
  changeRows: ChangeInput[];
  setChangeRows: (rows: ChangeInput[]) => void;

  recommendResults: RecommendResult[];
  setRecommendResults: (r: RecommendResult[]) => void;
  embeddingStatus: Record<string, unknown> | null;
  setEmbeddingStatus: (s: Record<string, unknown> | null) => void;

  applyResult: ApplyResult | null;
  setApplyResult: (r: ApplyResult | null) => void;

  // 변경점 작성 탭 작업 상태(탭 전환에도 유지). #5 피드백.
  dpRows: DesignRow[];
  setDpRows: (r: DesignRow[]) => void;
  dpPhase: DesignPhase;
  setDpPhase: (p: DesignPhase) => void;
  dpSummaries: Record<string, string>;
  setDpSummaries: (s: Record<string, string>) => void;
  dpFilename: string;
  setDpFilename: (f: string) => void;
  dpNextId: number;
  setDpNextId: (n: number) => void;

  // 페이지1 → 페이지2로 넘기는 master(변경점 + 부여번호)
  masterChanges: MasterChange[];
  setMasterChanges: (m: MasterChange[]) => void;

  baseBomRows: BomRow[] | null;
  setBaseBomRows: (r: BomRow[] | null) => void;
  baseBomSummary: Record<string, unknown> | null;
  setBaseBomSummary: (s: Record<string, unknown> | null) => void;

  masters: Master[];
  setMasters: (m: Master[]) => void;

  // 후보 검색 코퍼스: "sqlite"(현 모델 이력) | "lg"(lg change_event, 사유 풍부).
  corpus: string;
  setCorpus: (c: string) => void;
}

const StoreContext = createContext<Store | null>(null);

const DEFAULT_ROW: ChangeInput = {
  change_id: "change-1",
  slide_number: "",
  description: "Door Assembly",
  module_name: "Door",
  part_name: "Handle,Door",
  base_part_no: "",
  change_point: "Door 외관 색상 변경",
  change_reason: "BK STS에서 STS로 변경",
  change_type: "color",
  target_value: "",
  change_intent_keywords: "색상,color,외관",
  evidence: "",
};

export function StoreProvider({ children }: { children: ReactNode }) {
  const [changeRows, setChangeRows] = useState<ChangeInput[]>([{ ...DEFAULT_ROW }]);
  const [recommendResults, setRecommendResults] = useState<RecommendResult[]>([]);
  const [embeddingStatus, setEmbeddingStatus] = useState<Record<string, unknown> | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [dpRows, setDpRows] = useState<DesignRow[]>([]);
  const [dpPhase, setDpPhase] = useState<DesignPhase>("review");
  const [dpSummaries, setDpSummaries] = useState<Record<string, string>>({});
  const [dpFilename, setDpFilename] = useState("");
  const [dpNextId, setDpNextId] = useState(0);
  const [masterChanges, setMasterChanges] = useState<MasterChange[]>([]);
  const [baseBomRows, setBaseBomRows] = useState<BomRow[] | null>(null);
  const [baseBomSummary, setBaseBomSummary] = useState<Record<string, unknown> | null>(null);
  const [masters, setMasters] = useState<Master[]>([]);
  const [corpus, setCorpus] = useState<string>("lg");  // 기본: 사유 풍부한 lg

  const value = useMemo<Store>(
    () => ({
      corpus, setCorpus,
      changeRows, setChangeRows,
      recommendResults, setRecommendResults,
      embeddingStatus, setEmbeddingStatus,
      applyResult, setApplyResult,
      dpRows, setDpRows,
      dpPhase, setDpPhase,
      dpSummaries, setDpSummaries,
      dpFilename, setDpFilename,
      dpNextId, setDpNextId,
      masterChanges, setMasterChanges,
      baseBomRows, setBaseBomRows,
      baseBomSummary, setBaseBomSummary,
      masters, setMasters,
    }),
    [corpus, changeRows, recommendResults, embeddingStatus, applyResult, dpRows, dpPhase, dpSummaries, dpFilename, dpNextId, masterChanges, baseBomRows, baseBomSummary, masters]
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): Store {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used within StoreProvider");
  return ctx;
}

export const CHANGE_COLUMNS: (keyof ChangeInput)[] = [
  "change_id", "slide_number", "description", "module_name", "part_name",
  "base_part_no", "change_point", "change_reason", "change_type",
  "target_value", "change_intent_keywords", "evidence",
];
