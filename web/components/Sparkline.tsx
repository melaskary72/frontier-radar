// Tiny single-series trend; drawn only when there are 2 or more numeric points.
export default function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) return null;
  const W = 80, H = 22, min = Math.min(...values), max = Math.max(...values);
  const y = (v: number) => H - 3 - ((v - min) / (max - min || 1)) * (H - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1)) * (W - 4) + 2},${y(v)}`).join(" ");
  return (
    <svg width={W} height={H} role="img" aria-label={label} style={{ verticalAlign: "middle", marginLeft: 6 }}>
      <title>{label}</title>
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
