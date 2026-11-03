import "server-only";
import { promises as fs } from "fs";
import path from "path";

// All data comes from static JSON written by `uv run radar export` (radar/export.py).
const DATA = path.join(process.cwd(), "public", "data");

async function read<T>(rel: string, fallback: T): Promise<T> {
  try {
    return JSON.parse(await fs.readFile(path.join(DATA, rel), "utf8")) as T;
  } catch {
    return fallback;
  }
}

export type Group = { key: string; label: string; weight: number };
export type EvidenceChip = { feature: string; label: string; url?: string | null; at?: string | null };
export type RadarRow = {
  id: number; rank: number; name: string; sector: string | null; kind: string; domain: string | null;
  github_org: string | null; total: number; groups: Record<string, number>; top_evidence: EvidenceChip[];
  why_now: string | null; has_memo: boolean;
};
export type RadarData = { as_of: string | null; generated_at: string; groups: Group[]; sectors: string[]; rows: RadarRow[] };

export type Snapshot = {
  id: number; source: string; url: string; fetched_at: string; sha256: string; http_status: number;
  kind: string; title: string; link: string; excerpt: string;
};
export type Sentence = { text: string; citations: number[]; claim_id: number | null; verdict: string | null; note: string | null; break?: boolean };
export type Memo = {
  id: number; version: number; created_at: string; intro: string;
  sections: { heading: string; sentences: Sentence[] }[];
  founder_call_questions: string[]; snapshots: Record<string, Snapshot>;
  verifier: Record<string, unknown>; removed_claims: { text: string; verdict: string; note: string }[]; claims_total: number;
};
export type TimelineEvent = { kind: string; at: string; label: string; count?: number; url?: string | null };
export type Evidence = { snapshot_id: number; kind: string; title: string; excerpt: string; url: string | null; fetched_at: string; sha256: string | null };
export type CompanyData = {
  id: number; name: string; kind: string; sector: string | null; domain: string | null; github_org: string | null;
  location: string | null; first_seen_at: string; as_of: string; rank: number; total: number;
  contributions: { group: string; contribution: number; share: number }[];
  top_evidence: (EvidenceChip & { weight: number; snapshot_id?: number })[];
  why_now: { text: string; status: string } | null; evidence: Evidence[]; timeline: TimelineEvent[]; memo: Memo | null;
};

export type AgentRun = {
  db: string; id: number; agent: string; model: string; prompt_version: string; started_at: string;
  latency_ms: number | null; input_tokens: number | null; output_tokens: number | null; cost_usd: number; status: string; error: string | null;
};
export type AgentOps = {
  runs: AgentRun[]; cost: { at: string; agent: string; usd: number; cumulative_usd: number }[]; ledger_total_usd: number;
  by_agent: { agent: string; runs: number; ok: number; cost_usd: number; models: string[]; latency_p50_ms: number | null; latency_max_ms: number | null }[];
  prompt_versions: { agent: string; version: string; runs: number }[]; failures: AgentRun[];
  verifier_trend: { memo_id: number; company_id: number; created_at: string; supported_rate: number | null; checked: number | null }[];
};
export type PortfolioFactPoint = { as_of: string; value: string | number; text?: string; source_url: string };
export type Portfolio = {
  label: string; as_of: string | null;
  companies: { id: number; name: string; sector: string | null; first_invested_year: number | null; stage_at_entry: string | null; source_url: string; facts: Record<string, PortfolioFactPoint[]> }[];
};
export type LPUpdate = { generated_at?: string; title?: string; sections: { heading: string; sentences: { text: string; sources: string[] }[] }[] };
type Lift = { lift: number | null; ci95: number[] | null; p_lift_le_1: number | null };
type FormDHorizon = { cutoffs: number; rates: Record<"radar" | "stars" | "random", { hits: number; n: number; rate: number | null }>;
  raw_lift: Lift; standardized: Lift & { observed_hits: number; expected_hits_at_in_thesis_rate_of_same_kind: number; companies_with_a_hit: number; companies: number } };
export type Backtest = { status: string; message: string; protocol: Record<string, string>; fingerprint?: string; generated?: string;
  formd?: { data_end: string; top: number; horizons: Record<string, FormDHorizon> };
  portfolio?: { n_primary: number; n_secondary: number; found_top100: number; ever_collected: number;
    rows: { company: string; set: string; announced_on: string; included: boolean; offset_days: number; ever_collected: boolean; in_universe: boolean; rank: number | null }[] };
  query_audit?: { passed: boolean; distinct_queries: number } };
export type ReviewItem = { id: number; a_key: string; b_key: string; a_label: string; b_label: string; score: number; rationale: string | null; created_at: string };

export const loadRadar = () => read<RadarData>("radar.json", { as_of: null, generated_at: "", groups: [], sectors: [], rows: [] });
export const loadCompany = (id: string) => read<CompanyData | null>(`companies/${Number(id)}.json`, null);
export const loadAgentOps = () => read<AgentOps>("agent_ops.json", { runs: [], cost: [], ledger_total_usd: 0, by_agent: [], prompt_versions: [], failures: [], verifier_trend: [] });
export const loadPortfolio = () => read<Portfolio>("portfolio.json", { label: "Public data only. Not Anti Fund's actual marks or metrics.", as_of: null, companies: [] });
type LPCitation = { fact_id: number; company: string; fact_type: string; source_url: string };
type LPRaw = { title?: string; as_of?: string; label?: string; section_titles?: Record<string, string>;
  sections: Record<string, { text: string; citations: LPCitation[] }[]> | LPUpdate["sections"] };

// portfolio/lp_update.py writes sections keyed by id with per-sentence citations; normalize here.
export async function loadLPUpdate(): Promise<LPUpdate | null> {
  const raw = await read<LPRaw | null>("lp_update.json", null);
  if (!raw) return null;
  if (Array.isArray(raw.sections)) return raw as LPUpdate;
  const titles = raw.section_titles ?? {};
  const sections = Object.entries(raw.sections).map(([key, sentences]) => ({
    heading: titles[key] ?? key,
    sentences: sentences.map((x) => ({ text: x.text, sources: Array.from(new Set(x.citations.map((c) => c.source_url))) })),
  }));
  return { title: raw.title, generated_at: raw.as_of, sections };
}
export const loadBacktest = () => read<Backtest>("backtest.json", { status: "pending", message: "Results pending.", protocol: {} });
export const loadPipeline = () => read<{ stages: string[]; items: { company_id: number; name: string; stage: string }[] }>("pipeline.json", { stages: [], items: [] });
export const loadReview = () => read<{ items: ReviewItem[] }>("review_queue.json", { items: [] });

export async function companyIds(): Promise<string[]> {
  try {
    return (await fs.readdir(path.join(DATA, "companies"))).filter((f) => f.endsWith(".json")).map((f) => f.replace(".json", ""));
  } catch {
    return [];
  }
}

export const SECTOR_LABEL: Record<string, string> = {
  robotics: "Robotics", defense: "Defense", energy: "Energy", space: "Space", manufacturing: "Manufacturing", semiconductors: "Semiconductors",
};
