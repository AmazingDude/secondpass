import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  getJob,
  getJobAudit,
  submitReview,
  type AuditEvent,
  type JobPayload,
} from "../api";

const POLL_MS = 600;

const STAGES = [
  { id: "queued", label: "Job accepted" },
  { id: "running", label: "Security + Architecture review running" },
  { id: "completed", label: "Persisting results" },
] as const;

type Props = {
  onCompleted: (job: JobPayload) => void;
  initialPath?: string;
};

const STAGE_LABELS: Record<string, string> = {
  review_start: "review started",
  prompt_io: "model prompt recorded",
  schema_validation: "finding schema validated",
  confidence_gate: "confidence gate applied",
  chroma_save_skip: "supervisor left lesson memory unchanged",
  review_persisted: "review result persisted",
  review_complete: "review complete",
};

function formatTime(timestamp: string) {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function eventKind(event: AuditEvent): "stage" | "agent_event" | "tool" {
  if (event.kind === "stage" || event.kind === "agent_event" || event.kind === "tool") {
    return event.kind;
  }
  if (event.stage === "agent_event") return "agent_event";
  if (event.stage === "tool_call") return "tool";
  return "stage";
}

function formatStageDetail(event: AuditEvent) {
  const detail = event.detail || {};
  const count = (key: string) =>
    typeof detail[key] === "number" ? String(detail[key]) : null;

  switch (event.stage) {
    case "review_start":
      return "starting Security + Architecture review";
    case "schema_validation":
      return `${count("finding_count") || "0"} finding(s) validated`;
    case "confidence_gate": {
      const accepted = count("accepted_count") || "0";
      const needsReview = count("needs_review_count") || "0";
      return `${accepted} accepted · ${needsReview} needs review`;
    }
    case "review_persisted":
      return `review #${detail.review_id ?? "—"} persisted`;
    case "review_complete":
      return `${count("accepted_count") || "0"} accepted · ${
        count("needs_review_count") || "0"
      } needs review`;
    case "prompt_io":
      return `${detail.storage === "redacted_summary" ? "redacted " : ""}prompt I/O`;
    default:
      return STAGE_LABELS[event.stage] || event.stage.replaceAll("_", " ");
  }
}

/** Prefer query=/path= over raw args= JSON when present (CLI-like density). */
function formatToolArgsTail(argsRaw: unknown): string {
  if (typeof argsRaw !== "string" || !argsRaw) return "args=";
  try {
    const parsed = JSON.parse(argsRaw) as {
      args?: unknown[];
      kwargs?: Record<string, unknown>;
    };
    const kwargs = parsed.kwargs || {};
    for (const key of ["query", "path", "file_path", "paths"]) {
      if (kwargs[key] != null) {
        const value = String(kwargs[key]);
        const clipped = value.length > 120 ? `${value.slice(0, 117)}...` : value;
        return `${key}=${clipped}`;
      }
    }
  } catch {
    /* fall through */
  }
  return `args=${argsRaw}`;
}

/** Dense one-liner matching CLI stderr spirit ([agent]/[tool]/[stage]). */
function formatCliLine(event: AuditEvent): string {
  const time = formatTime(event.timestamp);
  const kind = eventKind(event);
  const detail = event.detail || {};

  if (kind === "agent_event") {
    const agent = String(detail.agent || event.worker_name || "system");
    const message = String(detail.message || "");
    return `[agent] ${time} | agent=${agent} | ${message}`;
  }

  if (kind === "tool") {
    const agent = String(detail.agent || event.worker_name || "system");
    const tool = String(detail.tool || "unknown");
    const status =
      typeof detail.status === "string"
        ? detail.status
        : detail.ok === false
          ? "error"
          : "ok";
    const duration =
      typeof detail.duration_ms === "number"
        ? `duration_ms=${Math.round(detail.duration_ms)}`
        : null;
    const parts = [
      `[tool] ${time}`,
      `agent=${agent}`,
      `tool=${tool}`,
      status,
      ...(duration ? [duration] : []),
      formatToolArgsTail(detail.args),
    ];
    return parts.join(" | ");
  }

  const worker = event.worker_name || "system";
  const label = STAGE_LABELS[event.stage] || event.stage;
  return `[stage] ${time} | worker=${worker} | ${label} | ${formatStageDetail(event)}`;
}

export function SubmitReview({ onCompleted, initialPath = "" }: Props) {
  const [path, setPath] = useState(initialPath);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobPayload | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const doneRef = useRef(false);
  const seenAuditIds = useRef(new Set<number>());
  const auditLogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!auditLogRef.current) return;
    auditLogRef.current.scrollTop = auditLogRef.current.scrollHeight;
  }, [auditEvents.length]);

  useEffect(() => {
    if (!jobId) return;
    doneRef.current = false;
    let cancelled = false;

    const tick = async () => {
      const [jobResult, auditResult] = await Promise.allSettled([
        getJob(jobId),
        getJobAudit(jobId),
      ]);
      if (cancelled) return;

      // The audit route returns 404 until the first SQLite audit event exists.
      // That is normal at job start, so only append a successful response.
      if (auditResult.status === "fulfilled") {
        const newEvents = auditResult.value.events.filter(
          (event) => !seenAuditIds.current.has(event.id),
        );
        if (newEvents.length > 0) {
          newEvents.forEach((event) => seenAuditIds.current.add(event.id));
          setAuditEvents((current) => [...current, ...newEvents]);
        }
      }

      if (jobResult.status === "rejected") {
        setError(
          jobResult.reason instanceof Error
            ? jobResult.reason.message
            : String(jobResult.reason),
        );
        return;
      }

      const next = jobResult.value;
      setJob(next);
      if (next.status === "completed") {
        if (!doneRef.current) {
          doneRef.current = true;
          onCompleted(next);
        }
        return;
      }
      if (next.status === "failed") {
        setError(next.error || "Review job failed");
        return;
      }
      window.setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      cancelled = true;
    };
  }, [jobId, onCompleted]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setJob(null);
    setJobId(null);
    setAuditEvents([]);
    seenAuditIds.current.clear();
    doneRef.current = false;
    const trimmed = path.trim();
    if (!trimmed) {
      setError("Enter a file or directory path.");
      return;
    }
    setSubmitting(true);
    try {
      const accepted = await submitReview(trimmed);
      setJobId(accepted.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const status = job?.status;
  const polling = Boolean(jobId) && status !== "failed" && status !== "completed";
  const hasPersistedAudit = auditEvents.some(
    (event) =>
      event.stage === "review_persisted" || event.stage === "review_complete",
  );
  const statusLabel =
    status === "failed"
      ? "Review failed"
      : status === "completed"
        ? "Review complete"
        : status === "running"
          ? "Review running"
          : status === "queued"
            ? "Job queued"
            : "Submitting job";

  return (
    <div>
      <p className="app-eyebrow">Submit</p>
      <h1 className="app-title">Run a review</h1>

      <form className="card" onSubmit={handleSubmit}>
        <label className="field-label" htmlFor="path">
          File or directory path
        </label>
        <input
          id="path"
          className="field-input mono"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="benchmark/fixtures/notes_idor.py"
          disabled={submitting || polling}
          autoComplete="off"
        />
        <div style={{ marginTop: "1rem" }}>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || polling}
          >
            {submitting || polling ? (
              <>
                <span className="spinner" aria-hidden="true" />
                {submitting ? "Submitting…" : "Reviewing…"}
              </>
            ) : (
              "Submit review"
            )}
          </button>
        </div>
        {error && !(jobId || job) ? <p className="error-text">{error}</p> : null}
        {(jobId || job) && (
          <section
            className={`job-status-panel ${status === "failed" ? "failed" : ""}`}
            aria-live="polite"
          >
            <div className="job-status-heading">
              <div>
                <p className="section-label">Job status</p>
                <p className="job-status-label" key={statusLabel}>
                  {statusLabel}
                </p>
              </div>
              <span className="job-status-id mono">job_id {jobId}</span>
            </div>

            {error ? (
              <div className="job-failure-banner" role="alert">
                <strong>Review failed.</strong> {error}
              </div>
            ) : null}

            <ol className="stage-list">
              {STAGES.map((stage, index) => {
                let state: "pending" | "active" | "done" = "pending";
                if (!status || status === "queued") {
                  state = stage.id === "queued" ? "active" : "pending";
                } else if (status === "running") {
                  if (stage.id === "queued") state = "done";
                  else if (stage.id === "running") {
                    state = hasPersistedAudit ? "done" : "active";
                  } else if (hasPersistedAudit) {
                    state = "active";
                  }
                } else if (status === "completed") {
                  state = "done";
                } else if (status === "failed") {
                  if (index === 0) state = "done";
                  else if (stage.id === "running") state = "active";
                }
                return (
                  <li key={stage.id} className={`stage-item ${state}`}>
                    <span className="stage-num" aria-hidden="true">
                      {state === "done" ? "✓" : state === "active" ? "…" : index + 1}
                    </span>
                    <div>
                      <div className="stage-label">{stage.label}</div>
                      {state === "active" ? (
                        <div className="stage-meta">
                          <span className="spinner" aria-hidden="true" />
                          {stage.id === "running"
                            ? "Events stream below as work completes…"
                            : "Waiting for review worker…"}
                        </div>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ol>

            <div className="audit-log-shell">
              <div className="audit-log-heading">
                <span className="section-label">Live log</span>
                <span className="audit-log-count">
                  {auditEvents.length ? `${auditEvents.length} events` : "connecting"}
                </span>
              </div>
              <div className="audit-log mono" ref={auditLogRef}>
                {auditEvents.length === 0 ? (
                  <p className="audit-log-waiting">
                    <span className="spinner" aria-hidden="true" />
                    waiting for events…
                  </p>
                ) : (
                  auditEvents.map((event) => (
                    <p
                      className={`audit-log-line audit-log-line--${eventKind(event)}`}
                      key={event.id}
                    >
                      {formatCliLine(event)}
                    </p>
                  ))
                )}
              </div>
            </div>
          </section>
        )}
      </form>
    </div>
  );
}
