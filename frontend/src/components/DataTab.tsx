import { useStore } from "../store";

export function DataTab() {
  const { masters, recommendResults, embeddingStatus } = useStore();
  return (
    <div className="tab">
      <h2>데이터 확인</h2>

      <section className="panel">
        <h3>Master 데이터 ({masters.length})</h3>
        {masters.length === 0 ? (
          <div className="muted">적재된 master가 없습니다.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>id</th><th>project</th><th>source</th><th>base→new</th><th>region</th></tr></thead>
              <tbody>
                {masters.map((m) => (
                  <tr key={m.master_id}>
                    <td>{m.master_id}</td>
                    <td>{m.project_name || "-"}</td>
                    <td className="small">{m.source_file || "-"}</td>
                    <td className="mono">{m.base_model || "-"} → {m.new_model || "-"}</td>
                    <td>{m.region || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {embeddingStatus && (
        <section className="panel">
          <h3>Embedding 상태</h3>
          <pre className="json">{JSON.stringify(embeddingStatus, null, 2)}</pre>
        </section>
      )}

      <section className="panel">
        <h3>현재 추천 결과 JSON</h3>
        {recommendResults.length === 0 ? (
          <div className="muted">아직 추천 결과가 없습니다.</div>
        ) : (
          <pre className="json json-tall">{JSON.stringify(recommendResults, null, 2)}</pre>
        )}
      </section>
    </div>
  );
}
