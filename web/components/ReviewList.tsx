"use client";
import { useEffect, useState } from "react";
import type { ReviewItem } from "@/lib/data";

export default function ReviewList({ items }: { items: ReviewItem[] }) {
  const [d, setD] = useState<Record<number, "same" | "different">>({});
  useEffect(() => { try { setD(JSON.parse(localStorage.getItem("fr_review") || "{}")); } catch { /* ignore */ } }, []);
  const decide = (id: number, v: "same" | "different") => {
    const next = { ...d, [id]: v };
    setD(next);
    try { localStorage.setItem("fr_review", JSON.stringify(next)); } catch { /* ignore */ }
  };
  return (
    <div className="grid">
      {items.map((i) => (
        <div key={i.id} className="card">
          <div className="filters" style={{ marginBottom: 8 }}>
            <span className="pill plain">Name similarity {Math.round(i.score)}</span>
            {d[i.id] && <span className={`pill ${d[i.id] === "same" ? "good" : "bad"}`}>Marked {d[i.id]}</span>}
          </div>
          <div className="grid grid-2">
            <div><div className="muted" style={{ fontSize: "0.8rem" }}>A</div><strong style={{ wordBreak: "break-word" }}>{i.a_label}</strong></div>
            <div><div className="muted" style={{ fontSize: "0.8rem" }}>B</div><strong style={{ wordBreak: "break-word" }}>{i.b_label}</strong></div>
          </div>
          {i.rationale && <p className="muted" style={{ fontSize: "0.86rem", marginTop: 8 }}>{i.rationale}</p>}
          <div className="filters" style={{ marginTop: 10, marginBottom: 0 }}>
            <button className="btn" aria-pressed={d[i.id] === "same"} onClick={() => decide(i.id, "same")}>Same entity</button>
            <button className="btn" aria-pressed={d[i.id] === "different"} onClick={() => decide(i.id, "different")}>Different</button>
            <code className="muted">radar review --decide {i.id} {d[i.id] === "different" ? "n" : "y"}</code>
          </div>
        </div>
      ))}
    </div>
  );
}
