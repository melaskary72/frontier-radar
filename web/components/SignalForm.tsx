"use client";
import { useState } from "react";

const KINDS: [string, string][] = [
  ["warm_intro", "Warm intro"],
  ["lab_relationship", "Lab relationship"],
  ["conference_note", "Conference or event note"],
  ["hiring_note", "Hiring note"],
  ["other", "Other"],
];

const CSS = `
.signal-form { display: grid; gap: 14px; max-width: 640px; }
.signal-form label { display: grid; gap: 6px; font-weight: 600; font-size: 0.92rem; }
.signal-form label .muted { font-weight: 400; }
.signal-form input, .signal-form textarea, .signal-form select { width: 100%; min-height: 44px; border-radius: 8px; border: 1px solid var(--rule); background: var(--surface); color: var(--ink); padding: 10px 12px; font: inherit; font-size: 1rem; box-sizing: border-box; }
.signal-form textarea { resize: vertical; }
`;

export default function SignalForm() {
  const today = new Date().toISOString().slice(0, 10);
  const [status, setStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setBusy(true);
    setStatus(null);
    try {
      const r = await fetch("/api/signals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
      const j = await r.json();
      if (r.ok && j.ok) {
        setStatus({ ok: true, msg: "Saved to the local inbox; run `radar signal inbox` to import." });
        form.reset();
      } else {
        setStatus({ ok: false, msg: j.error ?? `Request failed (${r.status}).` });
      }
    } catch {
      setStatus({ ok: false, msg: "Could not reach the local server." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card signal-form" onSubmit={submit}>
      <style>{CSS}</style>
      <label>Company<input name="company_name" required autoComplete="off" /></label>
      <label>Domain <span className="muted">(optional, improves matching)</span><input name="domain" placeholder="example.com" autoComplete="off" /></label>
      <label>Kind
        <select name="kind" required defaultValue="warm_intro">
          {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </label>
      <label>Person <span className="muted">(optional: founder or researcher the note is about)</span><input name="person" autoComplete="off" /></label>
      <label>Note<textarea name="note" required rows={5} /></label>
      <label>Source <span className="muted">(who told you)</span><input name="source" required autoComplete="off" /></label>
      <label>Date it happened<input name="occurred_on" type="date" required defaultValue={today} /></label>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "Saving" : "Save to local inbox"}</button>
        <a className="btn" href="/private_signals_template.csv" download>CSV template</a>
      </div>
      {status && <p role="status" className={status.ok ? "pill good" : "pill warn"} style={{ marginTop: 12 }}>{status.msg}</p>}
    </form>
  );
}
