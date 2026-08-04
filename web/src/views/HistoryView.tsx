import { useEffect, useMemo, useState } from "react";
import { listReviews, type ReviewPayload } from "../api";

const PAGE_SIZE = 20;

type Props = {
  onOpenReview: (review: ReviewPayload) => void;
};

function formatWhen(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

/** Short display path for table cells; full string stays on title/tooltip. */
function shortPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const marker = "benchmark/fixtures/";
  const idx = normalized.toLowerCase().indexOf(marker);
  if (idx >= 0) return normalized.slice(idx);
  const parts = normalized.split("/");
  if (parts.length <= 2) return normalized;
  return `…/${parts.slice(-2).join("/")}`;
}

export function HistoryView({ onOpenReview }: Props) {
  const [reviews, setReviews] = useState<ReviewPayload[]>([]);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listReviews(200)
      .then((body) => {
        if (cancelled) return;
        setReviews(body.reviews);
        setVisibleCount(PAGE_SIZE);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = useMemo(
    () => reviews.slice(0, visibleCount),
    [reviews, visibleCount],
  );
  const hasMore = visibleCount < reviews.length;

  return (
    <div>
      <p className="app-eyebrow">History</p>
      <h1 className="app-title">Previous reviews</h1>

      {loading ? (
        <p className="empty-detail">
          <span className="spinner" aria-hidden="true" />
          Loading reviews…
        </p>
      ) : null}
      {error ? <p className="error-text">{error}</p> : null}

      {!loading && !error && reviews.length === 0 ? (
        <div className="card">
          <p className="empty-detail">
            No persisted reviews yet. Submit a review first.
          </p>
        </div>
      ) : null}

      {!loading && reviews.length > 0 ? (
        <div className="card" style={{ padding: "0.75rem 1rem" }}>
          <p className="findings-summary" style={{ marginBottom: "0.5rem" }}>
            <span>
              Showing <strong>{visible.length}</strong> of{" "}
              <strong>{reviews.length}</strong> review
              {reviews.length === 1 ? "" : "s"}
            </span>
          </p>
          <div className="history-scroll">
            <table className="history-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>When</th>
                  <th>Worker</th>
                  <th>Path</th>
                  <th>Gate</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((review) => (
                  <tr key={review.id}>
                    <td className="mono history-col-id">{review.id}</td>
                    <td className="history-col-when">
                      {formatWhen(review.created_at)}
                    </td>
                    <td className="history-col-worker">{review.worker_name}</td>
                    <td
                      className="mono history-col-path"
                      title={review.file_path}
                    >
                      {shortPath(review.file_path)}
                    </td>
                    <td className="history-col-gate">
                      {review.review_result.coverage_status ===
                      "inconclusive" ? (
                        <span
                          className="badge badge-incomplete"
                          title="Logic review did not complete — not a clean result"
                        >
                          Incomplete
                        </span>
                      ) : review.accepted_count === 0 &&
                        review.needs_review_count === 0 ? (
                        <span className="badge badge-clean">Clean</span>
                      ) : (
                        <span className="badge badge-gate-summary">
                          {review.accepted_count} acc ·{" "}
                          {review.needs_review_count} review
                        </span>
                      )}
                    </td>
                    <td className="history-col-action">
                      <button
                        type="button"
                        className="btn btn-ghost btn-compact"
                        onClick={() => onOpenReview(review)}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore ? (
            <div className="toolbar" style={{ marginTop: "0.85rem", marginBottom: 0 }}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() =>
                  setVisibleCount((n) => Math.min(n + PAGE_SIZE, reviews.length))
                }
              >
                Load more ({reviews.length - visible.length} remaining)
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
