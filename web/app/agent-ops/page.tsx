import { loadAgentOps } from "@/lib/data";
import AgentOpsCharts from "@/components/AgentOpsCharts";

export const metadata = { title: "Agent Ops" };
const CAP = 15;

export default async function AgentOpsPage() {
  const ops = await loadAgentOps();
  const ok = ops.runs.filter((r) => r.status === "ok").length;
  const trend = ops.verifier_trend.filter((v) => v.supported_rate !== null);
  const avgPass = trend.length ? trend.reduce((a, v) => a + (v.supported_rate ?? 0), 0) / trend.length : null;
  return (
    <>
      <div className="page-head">
        <h1>Agent Ops</h1>
        <p>Every LLM (large language model) call is traced: model, prompt version, tokens, latency, cost, and status. Spend is capped in code at ${CAP} and read from an append-only ledger.</p>
      </div>
      <div className="grid grid-4" style={{ marginBottom: 14 }}>
        <div className="card stat"><div className="num">${ops.ledger_total_usd.toFixed(2)}</div><div className="lbl">Total spend of ${CAP} cap ({((ops.ledger_total_usd / CAP) * 100).toFixed(1)}%)</div></div>
        <div className="card stat"><div className="num">{ops.runs.length}</div><div className="lbl">Agent runs traced</div></div>
        <div className="card stat"><div className="num">{ops.runs.length ? ((ok / ops.runs.length) * 100).toFixed(0) : 0}%</div><div className="lbl">Runs finished OK</div></div>
        <div className="card stat"><div className="num">{avgPass === null ? "n/a" : `${Math.round(avgPass * 100)}%`}</div><div className="lbl">Memo claims supported on first draft ({trend.length} memos)</div></div>
      </div>
      <AgentOpsCharts ops={ops} />
      <h2>By agent</h2>
      <div className="table-wrap">
        <table className="nowrap">
          <thead><tr><th>Agent</th><th>Model</th><th className="num">Runs</th><th className="num">OK</th><th className="num">Cost</th><th className="num">Latency p50</th><th className="num">Latency max</th></tr></thead>
          <tbody>
            {ops.by_agent.map((a) => (
              <tr key={a.agent}>
                <td><strong>{a.agent.replaceAll("_", " ")}</strong></td><td>{a.models.join(", ")}</td>
                <td className="num">{a.runs}</td><td className="num">{a.ok}</td><td className="num">${a.cost_usd.toFixed(4)}</td>
                <td className="num">{a.latency_p50_ms ? `${(a.latency_p50_ms / 1000).toFixed(1)} s` : "n/a"}</td>
                <td className="num">{a.latency_max_ms ? `${(a.latency_max_ms / 1000).toFixed(1)} s` : "n/a"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Prompt versions</h2>
      <div className="filters">{ops.prompt_versions.map((p) => <span key={`${p.agent}${p.version}`} className="pill plain">{p.version}: {p.runs} runs</span>)}</div>
      <h2>Failure log</h2>
      {ops.failures.length ? (
        <div className="table-wrap"><table><thead><tr><th>When</th><th>Agent</th><th>Status</th><th>Error</th></tr></thead><tbody>
          {ops.failures.map((f) => <tr key={`${f.db}${f.id}`}><td>{f.started_at.slice(0, 16)}</td><td>{f.agent}</td><td><span className="pill bad">{f.status}</span></td><td>{f.error}</td></tr>)}
        </tbody></table></div>
      ) : <div className="empty">No failed runs. Fallbacks and retries would appear here with their error text.</div>}
      <h2>Recent runs</h2>
      <div className="table-wrap">
        <table className="nowrap">
          <thead><tr><th>When (UTC)</th><th>Agent</th><th>Prompt</th><th className="num">Tokens in / out</th><th className="num">Latency</th><th className="num">Cost</th><th>Status</th></tr></thead>
          <tbody>
            {[...ops.runs].reverse().slice(0, 25).map((r) => (
              <tr key={`${r.db}${r.id}`}>
                <td>{r.started_at.replace("T", " ").slice(0, 16)}</td><td>{r.agent.replaceAll("_", " ")}</td><td className="mono">{r.prompt_version}</td>
                <td className="num">{r.input_tokens ?? 0} / {r.output_tokens ?? 0}</td>
                <td className="num">{r.latency_ms ? `${(r.latency_ms / 1000).toFixed(1)} s` : ""}</td>
                <td className="num">${r.cost_usd.toFixed(4)}</td>
                <td><span className={`pill ${r.status === "ok" ? "good" : "bad"}`}>{r.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
