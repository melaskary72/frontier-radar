"use client";
import { useState } from "react";
import useWidth from "@/components/useWidth";

// Single-series line with a hover crosshair and tooltip. One axis, recessive grid.
export type Point = { t: number; y: number; label: string };

export default function LineChart({ points, yLabel, format, ariaLabel, step = false, dots = false, yMax }: { points: Point[]; yLabel: string; format: (v: number) => string; ariaLabel: string; step?: boolean; dots?: boolean; yMax?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [ref, W] = useWidth<HTMLDivElement>();
  if (points.length === 0) return <div className="empty">No data yet.</div>;
  const H = 220, L = 52, R = 12, T = 12, B = 28;
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const span = Math.max(t1 - t0, 3600_000);
  const ymax = yMax ?? (Math.max(...points.map((p) => p.y)) * 1.1 || 1);
  const x = (t: number) => L + ((t - t0) / span) * (W - L - R);
  const y = (v: number) => T + (1 - v / ymax) * (H - T - B);
  let d = "";
  points.forEach((p, i) => {
    if (i === 0) d += `M${x(p.t)},${y(p.y)}`;
    else d += step ? ` H${x(p.t)} V${y(p.y)}` : ` L${x(p.t)},${y(p.y)}`;
  });
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * ymax);
  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const tx = t0 + (((e.clientX - r.left) / r.width) * W - L) / (W - L - R) * span;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(p.t - tx) < Math.abs(points[best].t - tx)) best = i; });
    setHover(best);
  };
  const hp = hover !== null ? points[hover] : null;
  return (
    <div className="rel" ref={ref}>
      <svg className="chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        <g className="grid">{ticks.map((v) => <line key={v} x1={L} x2={W - R} y1={y(v)} y2={y(v)} />)}</g>
        {ticks.map((v) => <text key={`t${v}`} x={L - 6} y={y(v) + 4} textAnchor="end">{format(v)}</text>)}
        <text x={L} y={H - 6}>{new Date(t0).toISOString().slice(0, 10)}</text>
        <text x={W - R} y={H - 6} textAnchor="end">{new Date(t1).toISOString().slice(0, 10)}</text>
        <text x={12} y={T + 4} transform={`rotate(-90 12 ${T + 4})`} textAnchor="end">{yLabel}</text>
        {dots
          ? points.map((p, i) => <circle key={i} cx={x(p.t)} cy={y(p.y)} r={4} fill="var(--accent)" fillOpacity={0.7} stroke="var(--surface)" strokeWidth={2} />)
          : <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />}
        {hp && (
          <g>
            <line x1={x(hp.t)} x2={x(hp.t)} y1={T} y2={H - B} stroke="var(--muted)" strokeDasharray="3 3" />
            <circle cx={x(hp.t)} cy={y(hp.y)} r={5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
          </g>
        )}
        <rect x={L} y={T} width={W - L - R} height={H - T - B} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
      </svg>
      {hp && <div className="tip" style={{ left: `min(${(x(hp.t) / W) * 100}%, calc(100% - 260px))`, top: `${(y(hp.y) / H) * 100}%`, transform: "translate(8px, -110%)" }}>{hp.label}</div>}
    </div>
  );
}
