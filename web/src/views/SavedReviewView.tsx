import { useEffect, useState } from "react";
import { getReview, type ReviewPayload } from "../api";
import { jobLink, navigate } from "../navigation";
import { FindingsView } from "./FindingsView";

/** A persisted worker result, independent of transient submission results. */
export function SavedReviewView({ reviewId }: { reviewId: number }) {
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getReview(reviewId)
      .then(result => { if (!cancelled) setReview(result); })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => { cancelled = true; };
  }, [reviewId]);

  if (error) return <div className="card">
    <p className="error-text" role="alert">{error}</p>
    <a className="btn btn-ghost" href="#/history">Open History</a>
  </div>;
  if (!review) return <p role="status">Loading saved review…</p>;
  const backLink = review.job_id ? jobLink(review.job_id) : "#/history/workers";
  return <FindingsView reviews={[review]} jobPath={review.file_path}
    onBack={() => navigate(backLink)} backLabel="History" />;
}
