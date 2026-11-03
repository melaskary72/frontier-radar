"use client";
import { useMemo, useState } from "react";
import type { TimelineEvent } from "@/lib/data";
import useWidth from "@/components/useWidth";

// One time axis, one lane per signal kind. Identity is the lane (position), so all dots use
// the accent hue; size encodes weekly count. Hover or focus a dot for its detail.
const LANES: [string, string[]][] = [
  ["Repositories", ["repo"]],
  ["Stars", ["star"]],
  ["Forks", ["fork"]],
  ["Commits", ["commit"]],
  ["Issues and pull requests", ["issue", "pr"]],
  ["Releases", ["release"]],
  ["Papers", ["paper"]],
  ["Launches", ["launch"]],
  ["Awards", ["award"]],
  ["Filings", ["filing"]],
  ["Hires", ["hire", "affiliation"]],
];

export default function Timeline({ events }: { events: TimelineEvent[] }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [ref, W] = useWidth<HTMLDivElement>();
  const lanes = useMemo(() => LANES.filter(([, kinds]) => events.some((e) => kinds.includes(e.kind))), [events]);
  if (!events.length || !lanes.length) return <div className="empty">No dated signals for this company.</div>;
  const times = events.map((e) => Date.parse(e.at)).filter((t) => !Number.isNaN(t));
  const min = Math.min(...times), max = Math.max(...times);
  const span = Math.max(max - min, 86400000 * 30);
  const L = W < 520 ? 112 : 160, R = 16, LANE = 30, T = 10;
  const H = T + lanes.length * LANE + 28;
  const x = (t: number) => L + ((t - min) / span) * (W - L - R);
  const maxCount = Math.max(1, ...events.map((e) => e.count ?? 1));
  const ticks: number[] = [];
  const start = new Date(min); start.setUTCDate(1);
  const months = Math.max(1, Math.round(span / (86400000 * 30)));
  const step = months > 24 ? 6 : months > 10 ? 3 : months > 5 && W < 520 ? 2 : 1;
  for (let d = new Date(start); d.getTime() <= min + span; d.setUTCMonth(d.getUTCMonth() + step)) {
    if (d.getTime() >= min) ticks.push(d.getTime());
  }
  return (
    <div className="rel" ref={ref} onMouseLeave={() => setTip(null)}>
      <svg className="chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Signals timeline, ${events.length} events from ${new Date(min).toISOString().slice(0, 10)} to ${new Date(max).toISOString().slice(0, 10)}`}>
        <g className="grid">
          {ticks.map((t) => <line key={t} x1={x(t)} x2={x(t)} y1={T} y2={H - 24} />)}
        </g>
        {ticks.map((t) => (
          <text key={`l${t}`} x={x(t)} y={H - 8} textAnchor="middle">{new Date(t).toISOString().slice(0, 7)}</text>
        ))}
        {lanes.map(([label, kinds], i) => {
          const y = T + i * LANE + LANE / 2;
          return (
            <g key={label}>
              <text x={0} y={y + 4} style={{ fill: "var(--ink)", fontSize: W < 520 ? 11 : 12 }}>{W < 520 && label.length > 16 ? label.replace("Issues and pull requests", "Issues and PRs") : label}</text>
              <line x1={L} x2={W - R} y1={y} y2={y} stroke="var(--rule)" />
              {events.filter((e) => kinds.includes(e.kind)).map((e, j) => {
                const cx = x(Date.parse(e.at));
                const r = 4 + 6 * Math.sqrt((e.count ?? 1) / maxCount);
                const show = () => setTip({ x: (cx / W) * 100, y: ((y - 12) / H) * 100, text: `${e.at.slice(0, 10)}: ${e.label}` });
                return (
                  <g key={j} tabIndex={0} onMouseEnter={show} onFocus={show} onBlur={() => setTip(null)} style={{ outline: "none" }}>
                    <circle cx={cx} cy={y} r={Math.max(r, 12)} fill="transparent" />
                    <circle cx={cx} cy={y} r={r} fill="var(--accent)" fillOpacity={0.75} stroke="var(--surface)" strokeWidth={2} />
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      {tip && <div className="tip" style={{ left: `min(${tip.x}%, calc(100% - 260px))`, top: `${tip.y}%`, transform: "translateY(-100%)" }}>{tip.text}</div>}
    </div>
  );
}
