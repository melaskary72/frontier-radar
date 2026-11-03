import { loadLPUpdate, loadPortfolio, type PortfolioFactPoint } from "@/lib/data";
import Sparkline from "@/components/Sparkline";

export const metadata = { title: "Portfolio Stats" };

const COLS: [string, string][] = [
  ["open_roles", "Open roles"],
  ["github_pushed_90d", "GitHub repos pushed, 90 days"],
  ["github_stars", "GitHub stars"],
  ["tranco_rank", "Tranco web rank"],
  ["form_d_filing", "Latest Form D"],
  ["spv_form_d_24m", "Form D filings by vehicles named for the company, 24 months"],
  ["press_round", "Latest reported round"],
];

function Cell({ series, label }: { series?: PortfolioFactPoint[]; label: string }) {
  if (!series?.length) return <td className="muted">n/a</td>;
  const last = series[series.length - 1];
  const nums = series.map((s) => Number(s.value)).filter((v) => Number.isFinite(v));
  return (
    <td className={nums.length === series.length ? "num" : undefined}>
      <a href={last.source_url} target="_blank" rel="noreferrer" title={last.text ?? `${label}, as of ${last.as_of}`}>{typeof last.value === "number" ? last.value.toLocaleString("en-US") : last.value}</a>
      {nums.length === series.length && <Sparkline values={nums} label={`${label} trend, ${series.length} points`} />}
      <div className="muted" style={{ fontSize: "0.72rem" }}>{last.as_of}</div>
    </td>
  );
}

export default async function PortfolioPage() {
  const [p, lp] = await Promise.all([loadPortfolio(), loadLPUpdate()]);
  return (
    <>
      <div className="page-head">
        <h1>Portfolio Stats</h1>
        <div className="callout"><strong>{p.label}</strong> Weekly public proxies for the portfolio listed on antifund.com: hiring, code activity, web rank, SEC (Securities and Exchange Commission) Form D filings, and press-reported rounds. Every value links to its source.</div>
      </div>
      {p.companies.length === 0 ? (
        <div className="empty">No portfolio data yet. The portfolio collector writes companies and facts; then run <code>uv run radar export</code>.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Company</th><th>Entry</th>{COLS.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
            <tbody>
              {p.companies.map((c) => (
                <tr key={c.id}>
                  <td><a href={c.source_url} target="_blank" rel="noreferrer"><strong>{c.name}</strong></a>{c.sector && <div className="muted" style={{ fontSize: "0.78rem" }}>{c.sector}</div>}</td>
                  <td>{[c.first_invested_year, c.stage_at_entry].filter(Boolean).join(", ") || "n/a"}</td>
                  {COLS.map(([k, l]) => <Cell key={k} series={c.facts[k]} label={l} />)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2>LP Update Draft</h2>
      {lp ? (
        <div className="card memo">
          {lp.title && <h3>{lp.title}</h3>}
          {lp.generated_at && <p className="muted" style={{ fontSize: "0.85rem" }}>Generated {lp.generated_at}. Every sentence cites a tracked public fact.</p>}
          {lp.sections.map((s) => (
            <section key={s.heading}>
              <h3 style={{ marginTop: 14 }}>{s.heading}</h3>
              {s.sentences.map((x, i) => (
                <p key={i}>{x.text} {x.sources.map((u, j) => <a key={j} className="cite" href={u} target="_blank" rel="noreferrer" aria-label={`Source ${j + 1}`}>{j + 1}</a>)}</p>
              ))}
            </section>
          ))}
        </div>
      ) : <div className="empty">No LP (limited partner) update draft generated yet. It is written from tracked facts only, every sentence cited.</div>}
    </>
  );
}
