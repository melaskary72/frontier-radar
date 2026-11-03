import type { Group } from "@/lib/data";

// Stacked contribution bar: each segment is a group's weighted contribution to the total
// (0 to 1 scale, so the bar's filled length is the score). 2px gaps between segments.
export default function StackedBar({ groups, values, total }: { groups: Group[]; values: Record<string, number>; total: number }) {
  const parts = groups.filter((g) => (values[g.key] ?? 0) > 0);
  const label = parts.map((g) => `${g.label} ${values[g.key].toFixed(2)}`).join(", ");
  return (
    <div className="stack" role="img" aria-label={`Score ${total.toFixed(2)}: ${label || "no contributions"}`}>
      {parts.map((g) => (
        <span
          key={g.key}
          title={`${g.label}: ${values[g.key].toFixed(3)} of max ${g.weight.toFixed(2)}`}
          style={{ width: `${values[g.key] * 100}%`, background: `var(--s-${g.key})` }}
        />
      ))}
    </div>
  );
}

export function GroupLegend({ groups }: { groups: Group[] }) {
  return (
    <div className="legend" aria-label="Score groups">
      {groups.map((g) => (
        <span key={g.key}>
          <i style={{ background: `var(--s-${g.key})` }} />
          {g.label} ({Math.round(g.weight * 100)}%)
        </span>
      ))}
    </div>
  );
}
