import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  CheckCircle2,
  Database,
  Diamond,
  TriangleAlert,
} from "lucide-react";
import {
  getReview,
  listOutcomes,
  listReviews,
  postOutcome,
  type OutcomePayload,
  type ReviewPayload,
} from "../api";
import { CodeBlock } from "../components/CodeBlock";
import { ReviewCombobox } from "../components/ReviewCombobox";

type Props = {
  initialReviewId?: number | null;
};

type OutcomeNotice = {
  tone: "success" | "warning";
  title: string;
  detail: string;
};

function fileName(path: string) {
  return path.replaceAll("\\", "/").split("/").pop() || path;
}

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
  const [formOk, setFormOk] = useState<OutcomeNotice | null>(null);
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
        workerName: r.worker_name,
        filePath: r.file_path,
        fileName: fileName(r.file_path),
        createdAt: r.created_at,
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
      const outcome = await postOutcome({
        review_id: review.id,
        index: findingIndex,
        accepted: decision === "accept",
        reason: trimmed,
      });
      setFormOk(formatOutcomeStatus(outcome.accepted, outcome.memory_promotion));
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
            <ReviewCombobox
              label="Review result"
              options={reviewOptions}
              value={reviewId}
              onChange={setReviewId}
              disabled={reviews.length === 0}
            />
            {review ? (
              <div className="selected-option-detail">
                <div>
                  <span className="badge badge-neutral">
                    {review.worker_name}
                  </span>
                  <span className="outcome-meta">
                    Review #{review.id} ·{" "}
                    {new Date(review.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="mono path-wrap" title={review.file_path}>
                  {review.file_path}
                </p>
              </div>
            ) : null}
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
                  Finding to decide
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
                    <option
                      key={`${index}-${finding.finding_type}`}
                      value={index}
                    >
                      {index + 1}. {finding.finding_type} · {finding.confidence}%
                      · {finding.detection_method}
                    </option>
                  ))}
                </select>
                {selected ? (
                  <div className="finding-selection-summary">
                    <span className="badge badge-neutral">
                      {selected.confidence}% confidence
                    </span>
                    <span className="badge badge-neutral">
                      {selected.detection_method}
                    </span>
                    <strong>{selected.finding_type}</strong>
                  </div>
                ) : null}
              </div>

              {selected ? (
                <div>
                  <p className="section-label">Evidence</p>
                  <div className="evidence-callout" role="note">
                    <Diamond className="evidence-callout-icon" aria-hidden />
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
                      decision === "reject"
                        ? "btn-reject is-selected"
                        : "btn-ghost",
                    ].join(" ")}
                    onClick={() => setDecision("reject")}
                  >
                    Reject
                  </button>
                </div>
                <div className="memory-save-note" role="note">
                  <Database aria-hidden />
                  <div>
                    <strong>What gets saved?</strong>
                    <p>
                      Accept writes SQLite always and may promote a concise
                      Chroma lesson. Reject writes SQLite only and never updates
                      Chroma. Prior outcomes stay append-only.
                    </p>
                  </div>
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
          {formOk ? (
            <div
              className={`outcome-status-notice outcome-status-notice--${formOk.tone}`}
              role="status"
            >
              {formOk.tone === "success" ? (
                <CheckCircle2 aria-hidden />
              ) : (
                <TriangleAlert aria-hidden />
              )}
              <div>
                <strong>{formOk.title}</strong>
                <p>{formOk.detail}</p>
              </div>
            </div>
          ) : null}
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
            <p className="empty-detail">
              No outcomes recorded for this file yet.
            </p>
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

function formatOutcomeStatus(
  accepted: boolean,
  promotion?: {
    status: string;
    reason?: string | null;
  } | null,
): OutcomeNotice {
  if (!accepted) {
    return {
      tone: "success",
      title: "Rejected outcome recorded",
      detail: "Saved to SQLite only. Rejects do not update Chroma.",
    };
  }
  if (!promotion) {
    return {
      tone: "success",
      title: "Accepted outcome recorded",
      detail: "Saved to SQLite.",
    };
  }
  if (promotion.status === "saved") {
    return {
      tone: "success",
      title: "Accepted outcome recorded",
      detail: "Saved to SQLite and promoted a lesson into Chroma.",
    };
  }
  if (promotion.status === "skipped") {
    return {
      tone: "warning",
      title: "Accepted outcome recorded",
      detail: `Saved to SQLite. Chroma lesson skipped: ${
        promotion.reason || "a near-duplicate already exists"
      }.`,
    };
  }
  return {
    tone: "warning",
    title: "Accepted outcome recorded",
    detail:
      "Saved to SQLite, but the Chroma lesson could not be saved after the outcome write.",
  };
}
