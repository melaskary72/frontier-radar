import { promises as fs } from "fs";
import path from "path";

// Local intake for private signals. Appends one JSON line to <repo>/data/inbox/private_signals.jsonl,
// which `radar signal inbox` imports into the database. The inbox folder is gitignored. On a hosted
// deployment there is no shared backend yet, so the route refuses rather than dropping data silently.
const KINDS = ["warm_intro", "lab_relationship", "conference_note", "hiring_note", "other"];
const INBOX = path.join(process.cwd(), "..", "data", "inbox", "private_signals.jsonl");

export async function POST(req: Request) {
  if (process.env.VERCEL) {
    return Response.json(
      { ok: false, error: "Private signals are local only for now: this hosted dashboard has no private backend. Use `radar signal add` or a CSV import on your machine." },
      { status: 501 },
    );
  }
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return Response.json({ ok: false, error: "Request body must be JSON." }, { status: 400 });
  }
  const str = (k: string, max = 4000) => (typeof body[k] === "string" ? (body[k] as string).trim().slice(0, max) : "");
  const row = {
    company_name: str("company_name", 256),
    domain: str("domain", 256),
    kind: str("kind", 32),
    person: str("person", 256),
    note: str("note"),
    source: str("source", 256),
    occurred_on: str("occurred_on", 10),
    submitted_at: new Date().toISOString(),
  };
  const missing = ["company_name", "kind", "note", "source", "occurred_on"].filter((k) => !row[k as keyof typeof row]);
  if (missing.length) return Response.json({ ok: false, error: `Missing: ${missing.join(", ")}.` }, { status: 400 });
  if (!KINDS.includes(row.kind)) return Response.json({ ok: false, error: "Unknown kind." }, { status: 400 });
  if (!/^\d{4}-\d{2}-\d{2}$/.test(row.occurred_on)) return Response.json({ ok: false, error: "Date must be YYYY-MM-DD." }, { status: 400 });
  await fs.mkdir(path.dirname(INBOX), { recursive: true });
  await fs.appendFile(INBOX, JSON.stringify(row) + "\n", "utf8");
  return Response.json({ ok: true });
}
