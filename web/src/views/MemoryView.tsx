import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  getReview,
  listOutcomes,
  listReviews,
  postOutcome,
  type OutcomePayload,
  type ReviewPayload,
} from "../api";
import { CodeBlock } from "../components/CodeBlock";

type Props = {
  initialReviewId?: number | null;
};

export function MemoryView({ initialReviewId = null }: Props) {
  const [reviews, setReviews] = useState<ReviewPayload[]>([]);
  const [reviewId, setReviewId] = useState<number | "">("");
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [findingIndex, setFindingIndex] = useState<number | "">("");
  const [decision, setDecision] = useState<"accept" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [outcomes, setOutcomes] = useState<OutcomePayload[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loadingReview, setLoadingReview] = useState(false);

  const findings = review?.review_result.findings ?? [];
  const selected =
    typeof findingIndex === "number" ? findings[findingIndex] ?? null : null;

  const refreshOutcomes = useCallback(async (filePath: string) => {
    const body = await listOutcomes(filePath);
    setOutcomes(body.outcomes);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void listReviews(100)
      .then((body) => {
        if (cancelled) return;
        setReviews(body.reviews);
        const prefer =
          initialReviewId != null &&
          body.reviews.some((r) => r.id === initialReviewId)
            ? initialReviewId
            : body.reviews[0]?.id;
        if (prefer != null) setReviewId(prefer);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setListError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [initialReviewId]);

  useEffect(() => {
    if (reviewId === "") {
      setReview(null);
      setOutcomes([]);
      return;
    }
    let cancelled = false;
    setLoadingReview(true);
    setFormError(null);
    setFormOk(null);
    setFindingIndex("");
    setDecision(null);
    setReason("");
    void getReview(reviewId)
      .then(async (next) => {
        if (cancelled) return;
        setReview(next);
        if (next.review_result.findings.length > 0) setFindingIndex(0);
        await refreshOutcomes(next.file_path);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setFormError(err instanceof Error ? err.message : String(err));
        setReview(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingReview(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reviewId, refreshOutcomes]);

  const reviewOptions = useMemo(
    () =>
      reviews.map((r) => ({
        id: r.id,
        label: `#${r.id} · ${r.worker_name} · ${r.file_path}`,
      })),
    [reviews],
  );

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    setFormOk(null);
    if (!review || typeof findingIndex !== "number" || decision == null) {
      setFormError("Pick a finding and choose accept or reject.");
      return;
    }
    const trimmed = reason.trim();
    if (!trimmed) {
      setFormError("Reason is required.");
      return;
    }
    setSubmitting(true);
    try {
      await postOutcome({
        review_id: review.id,
        index: findingIndex,
        accepted: decision === "accept",
        reason: trimmed,
      });
      setFormOk(
        decision === "accept"
          ? "Recorded accepted outcome."
          : "Recorded rejected outcome.",
      );
      setReason("");
      setDecision(null);
      await refreshOutcomes(review.file_path);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <p className="app-eyebrow">Memory</p>
      <h1 className="app-title">Verified outcomes</h1>
      <p className="empty-detail" style={{ marginBottom: "1.25rem" }}>
        Explicit accept/reject + reason only — nothing is inferred or bulk-applied.
      </p>

      {listError ? <p className="error-text">{listError}</p> : null}

      <div className="decide-layout">
        <form className="card stack-gap" onSubmit={handleSubmit}>
          <div>
            <label className="field-label" htmlFor="memory-review">
              Review
            </label>
            <select
              id="memory-review"
              className="field-select"
              value={reviewId === "" ? "" : String(reviewId)}
              onChange={(e) => {
                const v = e.target.value;
                setReviewId(v ? Number(v) : "");
              }}
              disabled={reviews.length === 0}
            >
              {reviews.length === 0 ? (
                <option value="">No reviews available</option>
              ) : (
                reviewOptions.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))
              )}
            </select>
          </div>

          {loadingReview ? (
            <p className="empty-detail">
              <span className="spinner" aria-hidden="true" />
              Loading review…
            </p>
          ) : null}

          {review && findings.length === 0 ? (
            <p className="empty-detail">
              This review has no findings to decide on.
            </p>
          ) : null}

          {review && findings.length > 0 ? (
            <>
              <div>
                <label className="field-label" htmlFor="memory-finding">
                  Finding
                </label>
                <select
                  id="memory-finding"
                  className="field-select"
                  value={findingIndex === "" ? "" : String(findingIndex)}
                  onChange={(e) => {
                    const v = e.target.value;
                    setFindingIndex(v === "" ? "" : Number(v));
                  }}
                >
                  {findings.map((finding, index) => (
                    <option key={`${index}-${finding.finding_type}`} value={index}>
                      [{index}] {finding.finding_type} · {finding.confidence}%
                    </option>
                  ))}
                </select>
              </div>

              {selected ? (
                <div>
                  <p className="section-label">Evidence</p>
                  <div className="evidence-callout" role="note">
                    <span aria-hidden="true">◈</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <CodeBlock
                        code={selected.evidence}
                        filePath={review.file_path}
                        mode="auto"
                      />
                    </div>
                  </div>
                  <p className="section-label">Suggested fix</p>
                  <CodeBlock
                    code={selected.suggested_fix}
                    filePath={review.file_path}
                    mode="auto"
                  />
                </div>
              ) : null}

              <div>
                <p className="field-label">Decision</p>
                <div className="decide-actions">
                  <button
                    type="button"
                    className={[
                      "btn",
                      decision === "accept" ? "btn-accept" : "btn-ghost",
                    ].join(" ")}
                    onClick={() => setDecision("accept")}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className={[
                      "btn",
                      decision === "reject" ? "btn-reject is-selected" : "btn-ghost",
                    ].join(" ")}
                    onClick={() => setDecision("reject")}
                  >
                    Reject
                  </button>
                </div>
              </div>

              <div>
                <label className="field-label" htmlFor="memory-reason">
                  Reason (required)
                </label>
                <textarea
                  id="memory-reason"
                  className="field-textarea"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why accept or reject this finding?"
                  required
                />
              </div>

              <div>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={submitting || decision == null}
                >
                  {submitting ? (
                    <>
                      <span className="spinner" aria-hidden="true" />
                      Saving…
                    </>
                  ) : (
                    "Record outcome"
                  )}
                </button>
              </div>
            </>
          ) : null}

          {formError ? <p className="error-text">{formError}</p> : null}
          {formOk ? <p className="success-text">{formOk}</p> : null}
        </form>

        <div className="card">
          <p className="section-label">Prior outcomes</p>
          <p
            className="outcome-meta mono path-wrap"
            style={{ marginBottom: "0.85rem" }}
            title={review?.file_path || undefined}
          >
            {review?.file_path || "Select a review"}
          </p>
          {review && outcomes.length === 0 ? (
            <p className="empty-detail">No outcomes recorded for this file yet.</p>
          ) : null}
          {outcomes.map((outcome) => (
            <article key={outcome.id} className="outcome-card">
              <div className="outcome-card-header">
                <span
                  className={
                    outcome.accepted
                      ? "badge badge-accepted"
                      : "badge badge-needs_review"
                  }
                >
                  {outcome.accepted ? "Accepted" : "Rejected"}
                </span>
                <strong>{outcome.finding.finding_type}</strong>
              </div>
              <p className="outcome-meta">
                review #{outcome.review_id ?? "—"} ·{" "}
                {new Date(outcome.created_at).toLocaleString()}
              </p>
              <p className="outcome-reason">{outcome.reason}</p>
              {outcome.finding.evidence ? (
                <div style={{ marginTop: "0.65rem" }}>
                  <p className="section-label">Evidence</p>
                  <CodeBlock
                    code={outcome.finding.evidence}
                    filePath={outcome.file_path}
                    mode="auto"
                  />
                </div>
              ) : null}
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

