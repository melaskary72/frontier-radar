"use client";
import { useEffect, useState } from "react";

export type PipeItem = { company_id: number; name: string; stage: string; updated_at: string };
const KEY = "fr_pipeline";
export function readPipeline(): PipeItem[] {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
}
export function writePipeline(items: PipeItem[]) {
  try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* ignore */ }
}

export default function AddToPipeline({ id, name }: { id: number; name: string }) {
  const [stage, setStage] = useState<string | null>(null);
  useEffect(() => { setStage(readPipeline().find((p) => p.company_id === id)?.stage ?? null); }, [id]);
  if (stage) return <a className="btn" href="/pipeline">In pipeline: {stage === "IC" ? "IC (Investment Committee)" : stage}</a>;
  return (
    <button className="btn primary" onClick={() => {
      const items = readPipeline().filter((p) => p.company_id !== id);
      items.push({ company_id: id, name, stage: "Sourced", updated_at: new Date().toISOString() });
      writePipeline(items);
      setStage("Sourced");
    }}>Add to pipeline</button>
  );
}
