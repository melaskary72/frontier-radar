"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { readPipeline, writePipeline, type PipeItem } from "@/components/AddToPipeline";

const LABEL: Record<string, string> = { IC: "IC (Investment Committee)" };

export default function Kanban({ stages, seed, suggestions }: { stages: string[]; seed: { company_id: number; name: string; stage: string }[]; suggestions: { company_id: number; name: string }[] }) {
  const [items, setItems] = useState<PipeItem[] | null>(null);
  useEffect(() => {
    const local = readPipeline();
    const merged = [...local];
    for (const s of seed) if (!merged.some((m) => m.company_id === s.company_id)) merged.push({ ...s, updated_at: "" });
    setItems(merged);
  }, [seed]);
  const save = (next: PipeItem[]) => { setItems(next); writePipeline(next); };
  const move = (id: number, stage: string) => save((items ?? []).map((i) => i.company_id === id ? { ...i, stage, updated_at: new Date().toISOString() } : i));
  const remove = (id: number) => save((items ?? []).filter((i) => i.company_id !== id));
  const add = (s: { company_id: number; name: string }) => save([...(items ?? []), { ...s, stage: "Sourced", updated_at: new Date().toISOString() }]);
  if (items === null) return <div className="empty">Loading pipeline…</div>;
  const missing = suggestions.filter((s) => !items.some((i) => i.company_id === s.company_id));
  return (
    <>
      {missing.length > 0 && (
        <div className="callout teal">
          <strong>Ready to source:</strong> companies with a cited memo.{" "}
          <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
            {missing.map((s) => <button key={s.company_id} className="btn" onClick={() => add(s)}>+ {s.name}</button>)}
          </span>
        </div>
      )}
      <div className="kanban" role="list" aria-label="Pipeline stages">
        {stages.map((st) => {
          const col = items.filter((i) => i.stage === st);
          return (
            <section key={st} className="kcol" role="listitem" aria-label={LABEL[st] ?? st}>
              <h3><span>{LABEL[st] ?? st}</span><span>{col.length}</span></h3>
              {col.map((i) => (
                <div key={i.company_id} className="kcard">
                  <Link href={`/company/${i.company_id}`} style={{ fontWeight: 700 }}>{i.name}</Link>
                  {i.updated_at && <div className="muted" style={{ fontSize: "0.78rem" }}>moved {i.updated_at.slice(0, 10)}</div>}
                  <label className="visually-hidden" htmlFor={`st-${i.company_id}`}>Stage for {i.name}</label>
                  <select id={`st-${i.company_id}`} value={i.stage} onChange={(e) => move(i.company_id, e.target.value)}>
                    {stages.map((s) => <option key={s} value={s}>{LABEL[s] ?? s}</option>)}
                  </select>
                  <button className="btn" style={{ width: "100%", marginTop: 6, minHeight: 40 }} onClick={() => remove(i.company_id)}>Remove</button>
                </div>
              ))}
              {!col.length && <p className="muted" style={{ fontSize: "0.85rem" }}>Empty</p>}
            </section>
          );
        })}
      </div>
    </>
  );
}
