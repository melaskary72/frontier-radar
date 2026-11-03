"use client";
import type { AgentOps } from "@/lib/data";
import LineChart from "@/components/LineChart";

export default function AgentOpsCharts({ ops }: { ops: AgentOps }) {
  const cost = ops.cost.filter((c) => c.at).map((c) => ({
    t: Date.parse(c.at), y: c.cumulative_usd,
    label: `${c.at.replace("T", " ").slice(0, 16)} UTC: ${c.agent.replaceAll("_", " ")} $${c.usd.toFixed(4)}, total $${c.cumulative_usd.toFixed(4)}`,
  }));
  const lat = ops.runs.filter((r) => r.latency_ms).map((r) => ({
    t: Date.parse(r.started_at), y: (r.latency_ms ?? 0) / 1000,
    label: `${r.agent.replaceAll("_", " ")} at ${r.started_at.slice(0, 16)}: ${((r.latency_ms ?? 0) / 1000).toFixed(1)} s`,
  }));
  const ver = ops.verifier_trend.filter((v) => v.supported_rate !== null).map((v) => ({
    t: Date.parse(v.created_at), y: (v.supported_rate ?? 0) * 100,
    label: `Memo ${v.memo_id}: ${Math.round((v.supported_rate ?? 0) * 100)}% of ${v.checked} claims supported on first draft`,
  }));
  return (
    <div className="grid">
      <div className="card"><h3>Cumulative spend (USD)</h3><LineChart points={cost} step yLabel="USD" format={(v) => `$${v.toFixed(2)}`} ariaLabel={`Cumulative LLM spend, ${cost.length} ledger rows, total $${ops.ledger_total_usd.toFixed(2)}`} /></div>
      <div className="card"><h3>Latency per run (seconds)</h3><LineChart points={lat} dots yLabel="seconds" format={(v) => v.toFixed(0)} ariaLabel={`Latency of ${lat.length} agent runs`} /></div>
      <div className="card"><h3>Verifier: claims supported on first draft (%)</h3><LineChart points={ver} dots yMax={100} yLabel="%" format={(v) => `${v.toFixed(0)}`} ariaLabel={`Verifier supported rate across ${ver.length} memos`} /></div>
    </div>
  );
}
