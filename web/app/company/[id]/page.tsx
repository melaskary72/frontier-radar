import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { companyIds, loadCompany, loadRadar, SECTOR_LABEL } from "@/lib/data";
import StackedBar, { GroupLegend } from "@/components/StackedBar";
import Timeline from "@/components/Timeline";
import Memo from "@/components/Memo";
import AddToPipeline from "@/components/AddToPipeline";

export async function generateStaticParams() {
  return (await companyIds()).map((id) => ({ id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const c = await loadCompany((await params).id);
  return { title: c?.name ?? "Company" };
}

export default async function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [c, radar] = await Promise.all([loadCompany(id), loadRadar()]);
  if (!c) notFound();
  const values = Object.fromEntries(c.contributions.map((x) => [x.group, x.contribution]));
  return (
    <>
      <p style={{ marginBottom: 8 }}><a href="/">← Radar</a></p>
      <div className="page-head" style={{ display: "flex", flexWrap: "wrap", gap: 16, justifyContent: "space-between", alignItems: "start" }}>
        <div style={{ minWidth: 0 }}>
          <h1>{c.name}</h1>
          <div className="filters" style={{ marginBottom: 0 }}>
            <span className="pill teal">Rank {c.rank}</span>
            {c.sector && <span className="pill plain">{SECTOR_LABEL[c.sector] ?? c.sector}</span>}
            {c.kind === "lab" && <span className="pill plain">Lab</span>}
            {c.memo && <span className="pill gold">Cited memo</span>}
          </div>
        </div>
        <AddToPipeline id={c.id} name={c.name} />
      </div>

      <div className="grid grid-4" style={{ marginBottom: 14 }}>
        <div className="card stat"><div className="num">{c.total.toFixed(2)}</div><div className="lbl">Score, 0 to 1, as of {c.as_of}</div></div>
        <div className="card stat"><div className="num" style={{ fontSize: "1.1rem", wordBreak: "break-all" }}>{c.domain ?? "none found"}</div><div className="lbl">Domain</div></div>
        <div className="card stat"><div className="num" style={{ fontSize: "1.1rem", wordBreak: "break-all" }}>{c.github_org ? <a href={`https://github.com/${c.github_org}`} target="_blank" rel="noreferrer">{c.github_org}</a> : "none"}</div><div className="lbl">GitHub account</div></div>
        <div className="card stat"><div className="num" style={{ fontSize: "1.1rem" }}>{c.first_seen_at.slice(0, 10)}</div><div className="lbl">First public trace{c.location ? `, ${c.location}` : ""}</div></div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h2 style={{ marginTop: 0 }}>Why it ranks here</h2>
        <StackedBar groups={radar.groups} values={values} total={c.total} />
        <div style={{ marginTop: 10 }}><GroupLegend groups={radar.groups} /></div>
        {c.why_now && <p style={{ marginTop: 12 }}><strong>Why now:</strong> {c.why_now.text}</p>}
        <h3 style={{ marginTop: 14 }}>Top evidence</h3>
        <ul style={{ paddingLeft: 18, margin: 0 }}>
          {c.top_evidence.map((e, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              {e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.label.replace(/=True/g, " yes").replace(/=False/g, " no")}</a> : e.label.replace(/=True/g, " yes").replace(/=False/g, " no")}
              <span className="muted"> ({e.feature.replaceAll("_", " ")}{e.at ? `, ${e.at}` : ""})</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h2 style={{ marginTop: 0 }}>Signals timeline</h2>
        <Timeline events={c.timeline} />
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <h2 style={{ marginTop: 0 }}>Memo</h2>
        {c.memo ? <Memo memo={c.memo} /> : (
          <div className="empty">No memo yet. Memos are written for the top five each week; every sentence is cited and checked by a separate verifier agent.</div>
        )}
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Evidence ({c.evidence.length})</h2>
        <p className="muted" style={{ fontSize: "0.88rem" }}>Each item is cut from a cached raw response with its fetch date and SHA-256 hash.</p>
        <div className="grid">
          {c.evidence.map((e) => (
            <div key={e.snapshot_id} style={{ borderTop: "1px solid var(--rule)", paddingTop: 10 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                <span className="pill plain">{e.kind}</span>
                {e.url ? <a href={e.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600, wordBreak: "break-all" }}>{e.title}</a> : <strong>{e.title}</strong>}
                <span className="muted" style={{ fontSize: "0.82rem" }}>fetched {e.fetched_at} · snapshot #{e.snapshot_id}</span>
              </div>
              <p className="muted" style={{ fontSize: "0.86rem", margin: "6px 0 0", wordBreak: "break-word" }}>{e.excerpt.slice(0, 320)}{e.excerpt.length > 320 ? "…" : ""}</p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
