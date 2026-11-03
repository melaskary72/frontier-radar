import { loadReview } from "@/lib/data";
import ReviewList from "@/components/ReviewList";

export const metadata = { title: "Review Queue" };

export default async function ReviewPage() {
  const { items } = await loadReview();
  return (
    <>
      <div className="page-head">
        <h1>Review Queue</h1>
        <p>Entity matches the resolver could not settle on its own: same or similar names without a shared domain or GitHub account. A human decision here becomes a hard constraint on the next run (<code>radar review --decide ID y|n</code>).</p>
      </div>
      {items.length ? <ReviewList items={items} /> : <div className="empty">Nothing waiting for review.</div>}
    </>
  );
}
