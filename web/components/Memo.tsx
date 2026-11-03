"use client";
import { useEffect, useState } from "react";
import type { Memo as MemoT, Snapshot } from "@/lib/data";

function VerdictBadge({ verdict, note }: { verdict: string | null; note: string | null }) {
  if (!verdict) return null;
  const label = verdict === "supported" ? "Supported by source" : verdict === "partial" ? "Partly supported, rewritten to match source" : verdict;
  return <span className={`vbadge ${verdict}`} role="img" aria-label={label} title={note ? `${label}: ${note}` : label} />;
}

export default function Memo({ memo }: { memo: MemoT }) {
  const [open, setOpen] = useState<Snapshot | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const v = memo.verifier as Record<string, number | string>;
  return (
    <div className="memo">
      <p className="muted" style={{ fontSize: "0.9rem" }}>{memo.intro}</p>
      <div className="filters" style={{ fontSize: "0.85rem" }}>
        <span className="pill good"><span className="vbadge supported" /> Supported: {String(v.supported ?? 0)}</span>
        <span className="pill warn"><span className="vbadge partial" /> Rewritten: {String(v.rewritten ?? 0)}</span>
        <span className="pill plain">Removed before publishing: {memo.removed_claims.length}</span>
        <span className="pill plain">Checked: {String(v.checked ?? memo.claims_total)}</span>
        <span className="muted">Prompts {String(v.prompt_versions ?? "")}</span>
      </div>
      {memo.sections.map((sec) => (
        <section key={sec.heading}>
          <h3 style={{ marginTop: 18 }}>{sec.heading}</h3>
          {splitParas(sec.sentences).map((para, i) => (
            <p key={i}>
              {para.map((s, j) => (
                <span key={j} className="sent">
                  {s.text}
                  {s.citations.map((c) => {
                    const snap = memo.snapshots[String(c)];
                    return (
                      <button key={c} className="cite" onClick={() => snap && setOpen(snap)} aria-label={`Open source ${c}${snap ? `, ${snap.kind}, fetched ${snap.fetched_at.slice(0, 10)}` : ""}`}>
                        {c}
                      </button>
                    );
                  })}
                  {s.citations.length > 0 && <VerdictBadge verdict={s.verdict} note={s.note} />}{" "}
                </span>
              ))}
            </p>
          ))}
        </section>
      ))}
      {memo.founder_call_questions.length > 0 && (
        <section>
          <h3 style={{ marginTop: 18 }}>Founder-call questions</h3>
          <ol>{memo.founder_call_questions.map((q, i) => <li key={i} style={{ marginBottom: 6 }}>{q}</li>)}</ol>
        </section>
      )}
      {memo.removed_claims.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary className="muted" style={{ cursor: "pointer", minHeight: 44, display: "flex", alignItems: "center" }}>
            Claims the verifier removed ({memo.removed_claims.length})
          </summary>
          <ul>{memo.removed_claims.map((c, i) => <li key={i}><s>{c.text}</s> <span className="muted">{c.note}</span></li>)}</ul>
        </details>
      )}
      {open && (
        <>
          <div className="drawer-backdrop" onClick={() => setOpen(null)} />
          <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Source ${open.id}`}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
              <div>
                <span className="pill teal">{open.kind}</span>
                <h3 style={{ marginTop: 8 }}>{open.title || open.source}</h3>
              </div>
              <button className="btn icon" onClick={() => setOpen(null)} aria-label="Close source">✕</button>
            </div>
            <div className="excerpt">{open.excerpt}</div>
            <dl className="kv">
              <dt>Fetched</dt><dd>{open.fetched_at.replace("T", " ").slice(0, 19)} UTC</dd>
              <dt>Snapshot</dt><dd>#{open.id} ({open.source}, HTTP {open.http_status})</dd>
              <dt>SHA-256</dt><dd className="mono">{open.sha256}</dd>
              <dt>Request</dt><dd className="mono">{open.url}</dd>
            </dl>
            <a className="btn primary" href={open.link} target="_blank" rel="noreferrer">Open the live source</a>
            <p className="muted" style={{ fontSize: "0.82rem", marginTop: 12 }}>
              The excerpt is cut from the cached raw response, not from derived tables, so it is exactly what the source returned on the fetch date.
            </p>
          </aside>
        </>
      )}
    </div>
  );
}

function splitParas<T extends { break?: boolean }>(xs: T[]): T[][] {
  const out: T[][] = [[]];
  for (const x of xs) {
    if (x.break) out.push([]);
    else out[out.length - 1].push(x);
  }
  return out.filter((p) => p.length);
}
