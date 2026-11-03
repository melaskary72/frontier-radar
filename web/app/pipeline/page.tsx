import { loadPipeline, loadRadar } from "@/lib/data";
import Kanban from "@/components/Kanban";

export const metadata = { title: "Pipeline" };

export default async function PipelinePage() {
  const [pipe, radar] = await Promise.all([loadPipeline(), loadRadar()]);
  const suggestions = radar.rows.filter((r) => r.has_memo).map((r) => ({ company_id: r.id, name: r.name }));
  return (
    <>
      <div className="page-head">
        <h1>Pipeline</h1>
        <p>Deals moved from the Radar or a company page. Stages run from Sourced to IC (Investment Committee). Saved in this browser until a shared backend is wired in.</p>
      </div>
      <Kanban stages={pipe.stages} seed={pipe.items} suggestions={suggestions} />
    </>
  );
}
