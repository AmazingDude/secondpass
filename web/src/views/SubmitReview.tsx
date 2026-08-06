import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  getJob,
  getJobAudit,
  submitReview,
  type AuditEvent,
  type JobPayload,
} from "../api";
import { AgentTimeline } from "../components/AgentTimeline";
import { derivePipelineFromAudit } from "../pipelineTimeline";

const POLL_MS = 600;

const JOB_STAGES = [
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

function formatToolArgsTail(argsRaw: unknown): string | null {
  if (typeof argsRaw !== "string" || !argsRaw) return null;
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
  const clipped =
    argsRaw.length > 140 ? `${argsRaw.slice(0, 137)}...` : argsRaw;
  return `args=${clipped}`;
}

function AuditLogRow({ event }: { event: AuditEvent }) {
  const kind = eventKind(event);
  const detail = event.detail || {};
  const time = formatTime(event.timestamp);

  if (kind === "agent_event") {
    const agent = String(detail.agent || event.worker_name || "system");
    const message = String(detail.message || "");
    return (
      <div className="audit-log-row audit-log-row--agent">
        <div className="audit-log-row-main">
          <span className="audit-tok-kind">[agent]</span>
          <span className="audit-tok-time">{time}</span>
          <span className="audit-tok-meta">agent={agent}</span>
          <span className="audit-tok-msg">{message}</span>
        </div>
      </div>
    );
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
        ? Math.round(detail.duration_ms)
        : null;
    const argsTail = formatToolArgsTail(detail.args);
    return (
      <div className="audit-log-row audit-log-row--tool">
        <div className="audit-log-row-main">
          <span className="audit-tok-kind">[tool]</span>
          <span className="audit-tok-time">{time}</span>
          <span className="audit-tok-meta">agent={agent}</span>
          <span className="audit-tok-fn">tool={tool}</span>
          <span className="audit-tok-msg">{status}</span>
          {duration != null ? (
            <span className="audit-tok-meta">duration_ms={duration}</span>
          ) : null}
        </div>
        {argsTail ? (
          <div className="audit-log-row-detail">↳ {argsTail}</div>
        ) : null}
      </div>
    );
  }

  const worker = event.worker_name || "system";
  const label = STAGE_LABELS[event.stage] || event.stage;
  const stageDetail = formatStageDetail(event);
  return (
    <div className="audit-log-row audit-log-row--stage">
      <div className="audit-log-row-main">
        <span className="audit-tok-kind">[stage]</span>
        <span className="audit-tok-time">{time}</span>
        <span className="audit-tok-meta">worker={worker}</span>
        <span className="audit-tok-msg">{label}</span>
      </div>
      {stageDetail ? (
        <div className="audit-log-row-detail">↳ {stageDetail}</div>
      ) : null}
    </div>
  );
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
  const running = submitting || polling || status === "queued" || status === "running";
  const hasPersistedAudit = auditEvents.some(
    (event) =>
      event.stage === "review_persisted" || event.stage === "review_complete",
  );

  const pipeline = useMemo(
    () => derivePipelineFromAudit(auditEvents, status),
    [auditEvents, status],
  );

  const statusLabel =
    status === "failed"
      ? "failed"
      : status === "completed"
        ? "completed"
        : status === "running"
          ? "running"
          : status === "queued"
            ? "queued"
            : submitting
              ? "submitting"
              : "idle";

  function jobStageState(
    stageId: (typeof JOB_STAGES)[number]["id"],
  ): "idle" | "active" | "done" | "warn" {
    if (status === "failed") {
      if (stageId === "queued") return "done";
      if (stageId === "running") return "warn";
      return "idle";
    }
    if (status === "completed") return "done";
    if (!status || status === "queued" || submitting) {
      return stageId === "queued" ? "active" : "idle";
    }
    if (status === "running") {
      if (stageId === "queued") return "done";
      if (stageId === "running") return hasPersistedAudit ? "done" : "active";
      return hasPersistedAudit ? "active" : "idle";
    }
    return "idle";
  }

  return (
    <div className="submit-layout">
      <header className="submit-page-heading">
        <p className="app-eyebrow">Submit</p>
        <h1 className="app-title">Run a review</h1>
      </header>

      <form className="card submit-form-card" onSubmit={handleSubmit}>
        <label className="field-label" htmlFor="path">
          File or directory path
        </label>
        <div className="submit-path-row">
          <input
            id="path"
            className="field-input mono"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="benchmark/fixtures/notes_idor.py"
            disabled={submitting || polling}
            autoComplete="off"
          />
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
        <p className="submit-hint">
          Supervisor decides which workers and tools run. Memory and web only
          light up when routing calls them.
        </p>
        {error && !(jobId || job) ? <p className="error-text">{error}</p> : null}
      </form>

      {(jobId || job) && (
        <div
          className={`submit-run-grid ${status === "failed" ? "is-failed" : ""}`}
          aria-live="polite"
        >
          <section className="card submit-progress-card">
            <div className="job-status-heading">
              <div>
                <p className="section-label">Run progress</p>
                <p className="job-status-label" key={statusLabel}>
                  {statusLabel === "idle" ? "idle" : statusLabel}
                </p>
              </div>
              <div className="submit-status-meta">
                <span className="job-status-id mono">job_id {jobId}</span>
                <span
                  className={`submit-status-pill submit-status-pill--${statusLabel}`}
                >
                  {statusLabel}
                </span>
              </div>
            </div>

            {error ? (
              <div className="job-failure-banner" role="alert">
                <strong>Review failed.</strong> {error}
              </div>
            ) : null}

            <div className="job-stage-inset">
              <p className="section-label">Job status</p>
              <ol className="job-stage-checklist">
                {JOB_STAGES.map((stage) => {
                  const state = jobStageState(stage.id);
                  return (
                    <li
                      key={stage.id}
                      className={`job-stage-check job-stage-check--${state}`}
                    >
                      <span className="job-stage-mark" aria-hidden="true">
                        {state === "done"
                          ? "✓"
                          : state === "warn"
                            ? "!"
                            : state === "active"
                              ? "›"
                              : "·"}
                      </span>
                      <span>{stage.label}</span>
                    </li>
                  );
                })}
              </ol>
            </div>

            <AgentTimeline
              states={pipeline.states}
              info={pipeline.info}
              running={running && status !== "completed" && status !== "failed"}
              doneCount={pipeline.doneCount}
              totalCount={pipeline.totalCount}
              percent={pipeline.percent}
            />
          </section>

          <section className="card submit-log-card">
            <div className="audit-log-heading">
              <span className="section-label">Live events</span>
              <span className="audit-log-count">
                {auditEvents.length
                  ? `${auditEvents.length} lines`
                  : "connecting"}
              </span>
            </div>
            <div className="audit-log mono" ref={auditLogRef}>
              {auditEvents.length === 0 ? (
                <p className="audit-log-waiting">
                  <span className="spinner" aria-hidden="true" />
                  waiting for a job · events stream here
                </p>
              ) : (
                auditEvents.map((event) => (
                  <AuditLogRow key={event.id} event={event} />
                ))
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
