import SignalForm from "@/components/SignalForm";

export const metadata = { title: "Private signals" };

export default function SignalsPage() {
  return (
    <>
      <div className="page-head">
        <h1>Private signals</h1>
        <p>Warm intros, lab relationships, conference notes, and hiring notes: the relationship data that arrives before a seed-stage company leaves any public trace. Each entry becomes timestamped, attributed evidence that a memo can cite exactly like a public source.</p>
      </div>
      <div className="callout"><strong>Private, never published.</strong> Entries go to a local inbox on this machine, then into the local database with <code>radar signal inbox</code>. Both are kept out of git, and the static dashboard export withholds private excerpts. Bulk entries: fill the CSV template and run <code>radar signal import FILE.csv</code>.</div>
      <SignalForm />
    </>
  );
}
