import { loadRadar } from "@/lib/data";
import RadarList from "@/components/RadarList";
import { GroupLegend } from "@/components/StackedBar";

export default async function RadarPage() {
  const data = await loadRadar();
  return (
    <>
      <div className="page-head">
        <h1>Radar</h1>
        <p>
          Companies ranked by an explainable score built from public signals: code, papers, launches, filings, and
          federal awards. Scored as of <strong>{data.as_of ?? "no run yet"}</strong>, {data.rows.length} scored
          companies. Each bar shows how much each signal group adds to the score.
        </p>
        <GroupLegend groups={data.groups} />
      </div>
      {data.rows.length ? <RadarList data={data} /> : <div className="empty">No scored run exported yet. Run <code>uv run radar export</code>.</div>}
    </>
  );
}
