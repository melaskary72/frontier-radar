"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { RadarData } from "@/lib/data";
import StackedBar from "@/components/StackedBar";

const SECTOR: Record<string, string> = {
  robotics: "Robotics", defense: "Defense", energy: "Energy", space: "Space", manufacturing: "Manufacturing", semiconductors: "Semiconductors",
};
const PAGE = 50;
// Feature labels come from Python ("tests=True"); show them in plain words.
const plain = (s: string) => s.replace(/=True/g, " yes").replace(/=False/g, " no");

type Votes = Record<string, 1 | -1>;

export default function RadarList({ data }: { data: RadarData }) {
  const [sector, setSector] = useState<string>("all");
  const [signal, setSignal] = useState<string>("all");
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [votes, setVotes] = useState<Votes>({});

  useEffect(() => {
    try { setVotes(JSON.parse(localStorage.getItem("fr_votes") || "{}")); } catch { /* storage unavailable */ }
  }, []);
  const vote = (id: number, v: 1 | -1) => {
    const next = { ...votes };
    if (next[id] === v) delete next[id]; else next[id] = v;
    setVotes(next);
    try { localStorage.setItem("fr_votes", JSON.stringify(next)); } catch { /* ignore */ }
  };

  const rows = useMemo(() => data.rows.filter((r) =>
    (sector === "all" || r.sector === sector) &&
    (signal === "all" || (signal === "memo" ? r.has_memo : (r.groups[signal] ?? 0) > 0)) &&
    (!q || `${r.name} ${r.domain ?? ""} ${r.github_org ?? ""}`.toLowerCase().includes(q.toLowerCase()))
  ), [data.rows, sector, signal, q]);

  return (
    <>
      <div className="filters" role="group" aria-label="Filters">
        <div className="seg scroll" aria-label="Sector">
          {["all", ...data.sectors].map((s) => (
            <button key={s} aria-pressed={sector === s} onClick={() => { setSector(s); setShown(PAGE); }}>
              {s === "all" ? "All sectors" : SECTOR[s] ?? s}
            </button>
          ))}
        </div>
      </div>
      <div className="filters">
        <label className="visually-hidden" htmlFor="signal">Signal</label>
        <select id="signal" value={signal} onChange={(e) => { setSignal(e.target.value); setShown(PAGE); }}>
          <option value="all">Any signal</option>
          {data.groups.map((g) => <option key={g.key} value={g.key}>Has {g.label.toLowerCase()}</option>)}
          <option value="memo">Has a cited memo</option>
        </select>
        <label className="visually-hidden" htmlFor="q">Search</label>
        <input id="q" type="search" placeholder="Search name, domain, GitHub" value={q} onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} />
        <span className="muted" style={{ fontSize: "0.85rem" }}>{rows.length} shown</span>
      </div>
      <div className="radar-list">
        {rows.slice(0, shown).map((r) => (
          <article key={r.id} className="card radar-row">
            <div className="rank" aria-label={`Rank ${r.rank}`}>{r.rank}</div>
            <div style={{ minWidth: 0 }}>
              <Link className="name" href={`/company/${r.id}`}>{r.name}</Link>
              <div className="meta">
                {r.sector && <span className="pill teal">{SECTOR[r.sector] ?? r.sector}</span>}
                {r.kind === "lab" && <span className="pill plain">Lab</span>}
                {r.has_memo && <span className="pill gold">Cited memo</span>}
                {r.domain && <span className="muted" style={{ fontSize: "0.82rem" }}>{r.domain}</span>}
                {!r.domain && r.github_org && <span className="muted" style={{ fontSize: "0.82rem" }}>github.com/{r.github_org}</span>}
              </div>
              <StackedBar groups={data.groups} values={r.groups} total={r.total} />
              {r.why_now && <p className="why"><strong>Why now:</strong> {r.why_now}</p>}
              <div className="chips">
                {r.top_evidence.map((e, i) => e.url
                  ? <a key={i} className="chip" href={e.url} target="_blank" rel="noreferrer" title={plain(e.label)}>{plain(e.label)}</a>
                  : <span key={i} className="chip" title={plain(e.label)}>{plain(e.label)}</span>)}
              </div>
            </div>
            <div className="side">
              <div className="score">{r.total.toFixed(2)}<small>score, 0 to 1</small></div>
              <div className="votes">
                <button className="btn icon" aria-label={`Thumbs up ${r.name}`} aria-pressed={votes[r.id] === 1} onClick={() => vote(r.id, 1)}>▲</button>
                <button className="btn icon" aria-label={`Thumbs down ${r.name}`} aria-pressed={votes[r.id] === -1} onClick={() => vote(r.id, -1)}>▼</button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {shown < rows.length && (
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <button className="btn" onClick={() => setShown(shown + PAGE)}>Show {Math.min(PAGE, rows.length - shown)} more</button>
        </div>
      )}
      <p className="muted" style={{ fontSize: "0.82rem", marginTop: 16 }}>
        Votes are saved in this browser for now; the weekly refit reads them once a feedback endpoint is deployed.
      </p>
    </>
  );
}
