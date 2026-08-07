import { useMemo, useState } from "react";
import { ArrowLeft } from "lucide-react";
import type { Finding, ReviewPayload } from "../api";
import { CodeBlock } from "../components/CodeBlock";

/** Matches app.confidence_gate.DEFAULT_THRESHOLD — presentation only. */
const GATE_THRESHOLD = 80;

export type DisplayFinding = Finding & {
  gate: "accepted" | "needs_review";
  worker_name: string;
  file_path: string;
  review_id: number;
  key: string;
};

export function flattenReviews(reviews: ReviewPayload[]): DisplayFinding[] {
  const rows: DisplayFinding[] = [];
  for (const review of reviews) {
    const gate = review.gate_result;
    for (const [bucket, items] of [
      ["accepted", gate.accepted] as const,
      ["needs_review", gate.needs_review] as const,
    ]) {
      items.forEach((finding, index) => {
        rows.push({
          ...finding,
          gate: bucket,
          worker_name: review.worker_name,
          file_path: review.file_path,
          review_id: review.id,
          key: `${review.id}-${bucket}-${index}-${finding.finding_type}`,
        });
      });
    }
  }
  return rows;
}

function groupByType(findings: DisplayFinding[]) {
  const map = new Map<string, DisplayFinding[]>();
  for (const finding of findings) {
    const list = map.get(finding.finding_type) || [];
    list.push(finding);
    map.set(finding.finding_type, list);
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

type Props = {
  reviews: ReviewPayload[];
  jobPath?: string;
  onBack?: () => void;
  backLabel?: string;
};

export function FindingsView({
  reviews,
  jobPath,
  onBack,
  backLabel = "Back",
}: Props) {
  const findings = useMemo(() => flattenReviews(reviews), [reviews]);
  const groups = useMemo(() => groupByType(findings), [findings]);
  const [selectedKey, setSelectedKey] = useState<string | null>(
    findings[0]?.key ?? null,
  );
  const selected = findings.find((f) => f.key === selectedKey) || null;

  const acceptedCount = findings.filter((f) => f.gate === "accepted").length;
  const needsCount = findings.filter((f) => f.gate === "needs_review").length;
  const coverageIncomplete = reviews.some(
    (r) => r.review_result.coverage_status === "inconclusive",
  );
  const claimUnverified = reviews.some(
    (r) => r.review_result.claim_status === "unverified",
  );
  const cleanWorkers = reviews.filter(
    (r) =>
      r.accepted_count === 0 &&
      r.needs_review_count === 0 &&
      r.review_result.coverage_status !== "inconclusive" &&
      r.review_result.claim_status !== "unverified",
  );

  return (
    <div>
      <p className="app-eyebrow">Findings</p>
      <h1 className="app-title">Review result</h1>

      {onBack ? (
        <div className="toolbar">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onBack}
            aria-label={`Back to ${backLabel}`}
          >
            <ArrowLeft size={16} strokeWidth={2} className="btn-icon" aria-hidden />
            {backLabel}
          </button>
        </div>
      ) : null}

      <div className="findings-summary">
        <span>
          Path:{" "}
          <span className="mono">{jobPath || reviews[0]?.file_path || "—"}</span>
        </span>
        <span>
          Workers: {reviews.map((r) => r.worker_name).join(", ") || "none"}
        </span>
        <span>
          Accepted: <strong>{acceptedCount}</strong>
        </span>
        <span>
          Needs review: <strong>{needsCount}</strong>
        </span>
        {coverageIncomplete ? (
          <span
            className="badge badge-incomplete"
            title="Logic review did not complete — not the same as a low-confidence finding"
          >
            Coverage incomplete
          </span>
        ) : null}
        {claimUnverified ? (
          <span
            className="badge badge-incomplete"
            title="Architecture claimed an issue that did not meet the evidence bar"
          >
            Evidence bar not met
          </span>
        ) : null}
        {cleanWorkers.length > 0 && findings.length > 0
          ? cleanWorkers.map((r) => (
              <span
                key={r.id}
                className="badge badge-clean"
                title={`${r.worker_name} clean`}
              >
                {r.worker_name}: clean
              </span>
            ))
          : null}
      </div>

      {reviews.length === 0 ? (
        <div className="card">
          <p className="empty-detail">
            No findings loaded yet — submit a review or open one from History.
          </p>
        </div>
      ) : findings.length === 0 && coverageIncomplete ? (
        <div className="card clean-state">
          <span className="badge badge-incomplete">Review incomplete</span>
          <p className="empty-detail">
            Coverage inconclusive — logic review could not complete (for
            example rate-limited). This is not a clean result, and it is not a
            low-confidence finding.
          </p>
        </div>
      ) : findings.length === 0 && claimUnverified ? (
        <div className="card clean-state">
          <span className="badge badge-incomplete">Evidence bar not met</span>
          <p className="empty-detail">
            Architecture flagged a possible issue that didn&apos;t meet the
            evidence bar — see the audit trail. This is not the same as a clean
            review, and the filtered claim is not listed as a finding.
          </p>
        </div>
      ) : findings.length === 0 ? (
        <div className="card clean-state">
          <span className="badge badge-clean">No issues found</span>
          <p className="empty-detail">
            Both workers reported clean (or nothing cleared the confidence
            gate).
          </p>
        </div>
      ) : (
        <div className="findings-layout">
          <div>
            {groups.map(([type, items]) => (
              <section key={type} className="finding-group">
                <div className="finding-group-header">
                  <h2 className="finding-group-title">{type}</h2>
                  <span className="count-badge">{items.length}</span>
                </div>
                {items.map((finding) => (
                  <button
                    key={finding.key}
                    type="button"
                    className={[
                      "finding-row",
                      `gate-${finding.gate}`,
                      selectedKey === finding.key ? "selected" : "",
                    ].join(" ")}
                    onClick={() => setSelectedKey(finding.key)}
                  >
                    <div className="finding-row-top">
                      <span
                        className={
                          finding.gate === "accepted"
                            ? "badge badge-accepted"
                            : "badge badge-needs_review"
                        }
                      >
                        {finding.gate === "accepted"
                          ? "Accepted"
                          : "Needs review"}
                      </span>
                      {finding.detection_method === "llm_reasoning" ? (
                        <span className="badge badge-ai">AI detection</span>
                      ) : null}
                      <span className="finding-title">
                        {finding.confidence}% · {finding.worker_name}
                      </span>
                    </div>
                    <div className="finding-row-meta mono">
                      {finding.file_path}
                    </div>
                  </button>
                ))}
              </section>
            ))}
          </div>

          <aside className="card detail-panel">
            {!selected ? (
              <p className="empty-detail">Select a finding to inspect.</p>
            ) : (
              <>
                <div className="detail-header">
                  <h2 className="detail-title">{selected.finding_type}</h2>
                  <span
                    className={
                      selected.gate === "accepted"
                        ? "badge badge-accepted"
                        : "badge badge-needs_review"
                    }
                  >
                    {selected.gate === "accepted"
                      ? "Accepted"
                      : "Needs review"}
                  </span>
                  {selected.detection_method === "llm_reasoning" ? (
                    <span className="badge badge-ai">AI detection</span>
                  ) : null}
                </div>
                <p className="detail-path mono">
                  {selected.file_path} · {selected.worker_name}
                </p>

                <div
                  className="confidence-meter"
                  role="meter"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={selected.confidence}
                  aria-label={`Confidence ${selected.confidence} percent; gate threshold ${GATE_THRESHOLD}`}
                >
                  <div className="confidence-meter-top">
                    <span className="section-label" style={{ margin: 0 }}>
                      Confidence
                    </span>
                    <span className="mono confidence-meter-value">
                      {selected.confidence}
                      <span className="confidence-meter-of">/100</span>
                    </span>
                  </div>
                  <div className="confidence-meter-track">
                    <div
                      className={[
                        "confidence-meter-fill",
                        selected.confidence >= GATE_THRESHOLD
                          ? "is-accepted"
                          : "is-needs-review",
                      ].join(" ")}
                      style={{
                        width: `${Math.min(100, Math.max(0, selected.confidence))}%`,
                      }}
                    />
                    <div
                      className="confidence-meter-tick"
                      style={{ left: `${GATE_THRESHOLD}%` }}
                      title={`Gate threshold ${GATE_THRESHOLD}`}
                    />
                  </div>
                  <p className="confidence-meter-hint">
                    Gate accepts ≥{GATE_THRESHOLD}; below stays needs review
                  </p>
                </div>

                <div className="evidence-callout" role="note">
                  <span aria-hidden="true">◈</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <strong>Evidence</strong>
                    <CodeBlock
                      code={selected.evidence}
                      filePath={selected.file_path}
                      mode="auto"
                    />
                  </div>
                </div>

                <p className="section-label">Suggested fix</p>
                <CodeBlock
                  code={selected.suggested_fix}
                  filePath={selected.file_path}
                  mode="auto"
                />
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
