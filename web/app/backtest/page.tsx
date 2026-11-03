import { loadBacktest } from "@/lib/data";

export const metadata = { title: "Backtest" };

const pct = (x: number | null | undefined, d = 2) => (x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(d)}%`);
const ci = (c: number[] | null) => (c ? `${c[0].toFixed(2)} to ${c[1].toFixed(2)}` : "n/a");

export default async function BacktestPage() {
  const b = await loadBacktest();
  const p = b.protocol;
  const fd = b.formd;
  const pf = b.portfolio;
  return (
    <>
      <div className="page-head">
        <h1>Backtest</h1>
        <p>Two point-in-time tests, run only on data that existed at each cutoff. Every number below is copied from the logged run files; none is typed by hand.</p>
      </div>
      {b.status !== "results" || !fd || !pf ? (
        <div className="callout"><strong>{b.message}</strong> Nothing on this page is a placeholder number.</div>
      ) : (
        <>
          <div className="callout">
            <strong>Verdict: no reliable early signal yet.</strong> The radar&apos;s top 100 filed new Form Ds (Securities and Exchange Commission notices of a private raise) far more often than the average in-thesis entity, but that gap comes from what kind of entity it ranks, not from foresight. Compared like for like, the lift is small and its 95% interval includes 1. Separately, none of Anti Fund&apos;s 12 recent seed-stage portfolio companies had a public footprint the radar could see before their rounds.
          </div>

          <h2>Test 1: did the radar&apos;s top {fd.top} go on to raise?</h2>
          <p className="muted">Outcome: a new Form D (not an amendment) filed after the cutoff. Only cutoffs whose whole window had passed by {fd.data_end} are counted. Same name matcher for every group.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14 }}>
            {Object.entries(fd.horizons).map(([h, r]) => (
              <div className="card" key={h}>
                <h3 style={{ marginTop: 0 }}>{h === "6m" ? "Within 6 months" : "Within 12 months"} <span className="muted" style={{ fontWeight: 400, fontSize: "0.85rem" }}>({r.cutoffs} cutoffs)</span></h3>
                <table>
                  <tbody>
                    <tr><td>Radar top {fd.top}</td><td className="num">{pct(r.rates.radar.rate)}</td><td className="num muted">{r.rates.radar.hits} of {r.rates.radar.n.toLocaleString("en-US")}</td></tr>
                    <tr><td>Stars top {fd.top}</td><td className="num">{pct(r.rates.stars.rate)}</td><td className="num muted">{r.rates.stars.hits} of {r.rates.stars.n.toLocaleString("en-US")}</td></tr>
                    <tr><td>In-thesis universe (random)</td><td className="num">{pct(r.rates.random.rate, 3)}</td><td className="num muted">{r.rates.random.hits} of {r.rates.random.n.toLocaleString("en-US")}</td></tr>
                  </tbody>
                </table>
                <p style={{ marginBottom: 4 }}>Raw lift: <strong>{r.raw_lift.lift?.toFixed(1)}x</strong> <span className="muted">(95% interval {ci(r.raw_lift.ci95)})</span></p>
                <p style={{ marginTop: 0 }}>Lift vs same entity type: <strong>{r.standardized.lift?.toFixed(2)}x</strong> <span className="muted">(95% interval {ci(r.standardized.ci95)}; {r.standardized.companies_with_a_hit} companies account for every hit)</span></p>
              </div>
            ))}
          </div>
          <div className="card" style={{ marginTop: 14 }}>
            <h3 style={{ marginTop: 0 }}>Why the raw lift is misleading</h3>
            <p>The radar&apos;s top 100 is mostly organizations and federal awardees; the in-thesis universe is mostly individual GitHub accounts, which almost never file Form Ds. The like-for-like column expects each radar entry to raise at the rate of its own entity type in the same universe. On that basis the lift is near 1, all hits come from two or three companies, and the stars baseline has no hits at all.</p>
          </div>

          <h2>Test 2: Anti Fund&apos;s recent portfolio</h2>
          <p className="muted">Would the radar have ranked these companies before their rounds were announced? {pf.n_primary} primary (frontier) and {pf.n_secondary} secondary (applied AI) companies with reliable dates, plus 3 with low-confidence dates reported separately.</p>
          <div className="callout"><strong>Found in the top 100: {pf.found_top100}. Ever collected at all: {pf.ever_collected} of 12.</strong> None had a public footprint the thesis queries could see before its round. Most first repositories and launch posts arrived on or after announcement day; the one company with earlier public code (Pensive, applied AI) sits outside the frontier thesis by design. This is a coverage ceiling, not a ranking failure.</div>
          <div className="table-wrap card" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>Company</th><th>Set</th><th>Announced</th><th>T minus 90</th><th>T minus 30</th></tr></thead>
              <tbody>
                {Array.from(new Set(pf.rows.map((r) => r.company))).map((name) => {
                  const rs = pf.rows.filter((r) => r.company === name);
                  const at = (o: number) => { const r = rs.find((x) => x.offset_days === o); return r?.rank ? `rank ${r.rank}` : r?.in_universe ? "in universe" : "not in data"; };
                  return <tr key={name}><td>{name}{rs[0].included ? "" : " *"}</td><td>{rs[0].set}</td><td>{rs[0].announced_on}</td><td>{at(90)}</td><td>{at(30)}</td></tr>;
                })}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: "0.8rem" }}>* Low-confidence announcement date, excluded from the headline. Query audit: {b.query_audit?.distinct_queries} thesis queries, none naming a portfolio company: {b.query_audit?.passed ? "passed" : "failed"}. Run fingerprint {b.fingerprint}.</p>

          <div className="card" style={{ marginTop: 14 }}>
            <h2 style={{ marginTop: 0 }}>What would close the gap</h2>
            <p>For seed-stage frontier companies, the round comes before the public artifacts. The signals that come first are relational: founder network intros, university lab relationships, conference and event attendee lists, and private hiring data. Frontier Radar takes each as a new timestamped, cited evidence type, scored with the same point-in-time features. The public layer still earns its place after the first check: portfolio tracking, later-stage discovery, and memos where every claim traces to a source.</p>
          </div>
        </>
      )}
      <div className="card" style={{ marginTop: 14 }}>
        <h2 style={{ marginTop: 0 }}>Protocol</h2>
        <ul style={{ paddingLeft: 18 }}>
          <li>Cutoffs at T minus {p.offsets?.replace(/[()\s]/g, "").split(",").filter(Boolean).join(" and T minus ") ?? "90 and 30"} days, where T is the public announcement date (sources in <code>eval/backtest/rounds.csv</code>).</li>
          <li>Universe: entities first seen within {p.lookback_days ?? "365"} days before the cutoff, drawn only from thesis queries (topics, categories, keywords), never by company name.</li>
          <li>Point-in-time features only: every feature reads rows timestamped on or before the cutoff. Job boards, current citation counts, and live star feeds have no history and are excluded.</li>
          <li>Prescore with hindsight-free signals, enrich the top {p.enrich_top ?? "60"}, score the top {p.score_top ?? "600"}.</li>
          <li>Thesis filter is the trained classifier only, no LLM (large language model) judge: a model reading historical text may already know which companies later became famous, which would leak hindsight.</li>
          <li>Stars baseline uses today&apos;s star counts, which favors it with hindsight.</li>
          <li>Leakage guard: a test fails the build if any portfolio name or domain appears in the radar code or config.</li>
        </ul>
      </div>
    </>
  );
}
