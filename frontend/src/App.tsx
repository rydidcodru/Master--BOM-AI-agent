import { useEffect, useState } from "react";
import { api, getApiBase, setApiBase } from "./api";
import { useStore } from "./store";
import type { Stats } from "./types";
import { DesignPointsTab } from "./components/DesignPointsTab";
import { ApplyTab } from "./components/ApplyTab";
import { DataTab } from "./components/DataTab";

type TabKey = "design" | "apply" | "data";
const TABS: { key: TabKey; label: string }[] = [
  { key: "design", label: "① 변경점 작성 (master)" },
  { key: "apply", label: "② 개발부품마스터 산출" },
  { key: "data", label: "③ 데이터 확인" },
];

export function App() {
  const [tab, setTab] = useState<TabKey>("design");
  const [apiUrl, setApiUrl] = useState(getApiBase());
  const [health, setHealth] = useState<"ok" | "fail" | "checking">("checking");
  const [stats, setStats] = useState<Stats>({});
  const { setMasters, corpus, setCorpus } = useStore();

  async function refresh() {
    setHealth("checking");
    try {
      await api.health();
      setHealth("ok");
      setStats(await api.stats());
      setMasters(await api.masters());
    } catch {
      setHealth("fail");
      setStats({});
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyApiUrl() {
    setApiBase(apiUrl);
    refresh();
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1 className="brand">Dev Parts<br />BOM Workflow</h1>
        <div className="field">
          <label>FastAPI URL</label>
          <input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} spellCheck={false} />
          <button className="btn" onClick={applyApiUrl}>연결</button>
        </div>

        <div className={`conn conn-${health}`}>
          {health === "ok" ? "● 백엔드 연결됨" : health === "fail" ? "● 연결 실패" : "○ 확인 중…"}
        </div>

        <div className="stat-grid">
          <div className="stat"><span>{stats.masters ?? 0}</span>Masters</div>
          <div className="stat"><span>{stats.details ?? 0}</span>Details</div>
          <div className="stat"><span>{stats.embedded ?? 0}</span>Embeddings</div>
          <div className="stat"><span>{stats.linked_parents ?? 0}</span>Parents</div>
        </div>

        <button className="btn btn-ghost" onClick={refresh}>데이터 새로고침</button>

        <div className="field" style={{ marginTop: 12 }}>
          <label>후보 검색 코퍼스</label>
          <select value={corpus} onChange={(e) => setCorpus(e.target.value)}>
            <option value="lg">lg change_event (사유 풍부·BGE-M3)</option>
            <option value="sqlite">sqlite (현 모델 이력)</option>
          </select>
          <span className="muted small">
            {corpus === "lg" ? "변경사유 의미 매칭이 강함(부품 안 맞아도 사유로 회수)" : "현 dev_parts.db 이력에서 회수"}
          </span>
        </div>

        <nav className="nav">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`nav-item ${tab === t.key ? "active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <p className="hint">
          변경 입력 → 후보 추천 → <b>변경 카드에서 후보 선택</b> → BOM 반영.
          미정(TBD) 부품은 <span className="badge k-tbd badge-sm"><span className="badge-icon">⚠</span>미정</span> 으로 표시됩니다.
        </p>
      </aside>

      <main className="content">
        {health === "fail" && (
          <div className="banner banner-error">
            FastAPI 백엔드에 연결하지 못했습니다. <code>uv run devparts serve</code> 실행 여부와 URL을 확인하세요.
          </div>
        )}
        {tab === "design" && <DesignPointsTab onSendToApply={() => setTab("apply")} />}
        {tab === "apply" && <ApplyTab />}
        {tab === "data" && <DataTab />}
      </main>
    </div>
  );
}
