import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  Check,
  Circle,
  CornerDownRight,
  FileCode2,
  FolderTree,
  Loader2,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import {
  getJob,
  getJobAudit,
  previewReview,
  submitReview,
  type AuditEvent,
  type JobPayload,
  type ReviewOptions,
  type ReviewPreview,
} from "../api";
import { AgentTimeline } from "../components/AgentTimeline";
import { derivePipelineFromAudit } from "../pipelineTimeline";

const POLL_MS = 600;
const PREVIEW_DEBOUNCE_MS = 450;

const DEFAULT_OPTIONS: ReviewOptions = {
  workers: 2,
  max_files: 10,
  include_init: false,
  include: [],
  exclude: [],
};

function parsePatterns(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function friendlyPreviewError(message: string) {
  if (
    message.includes("review_id") &&
    (message.includes("preview") || message.includes("integer"))
  ) {
    return "Preview endpoint missing on the running API. Restart the FastAPI server so GET /reviews/preview is loaded.";
  }
  return message;
}

function displayPath(path: string, root?: string) {
  if (!root) return path;
  const normalizedPath = path.replaceAll("\\", "/");
  const normalizedRoot = root.replaceAll("\\", "/").replace(/\/$/, "");
  return normalizedPath.startsWith(`${normalizedRoot}/`)
    ? normalizedPath.slice(normalizedRoot.length + 1)
    : path;
}

const JOB_STAGES = [
  { id: "queued", label: "Job accepted" },
  { id: "running", label: "Security + Architecture review running" },
  { id: "completed", label: "Persisting results" },
] as const;

type Props = {
  onCompleted: (job: JobPayload) => void;
  initialPath?: string;
  /** True once findings for the finished job are loaded (user stays on Submit). */
  findingsReady?: boolean;
  onViewFindings?: () => void;
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
      return typeof detail.path === "string"
        ? `starting Security + Architecture · ${detail.path}`
        : "starting Security + Architecture review";
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
      return `${typeof detail.path === "string" ? `${detail.path} · ` : ""}${
        count("accepted_count") || "0"
      } accepted · ${
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
          <div className="audit-log-row-detail">
            <CornerDownRight
              className="audit-log-detail-icon"
              strokeWidth={2}
              aria-hidden
            />
            {argsTail}
          </div>
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
        <div className="audit-log-row-detail">
          <CornerDownRight
            className="audit-log-detail-icon"
            strokeWidth={2}
            aria-hidden
          />
          {stageDetail}
        </div>
      ) : null}
    </div>
  );
}

function JobStageIcon({
  state,
}: {
  state: "idle" | "active" | "done" | "warn";
}) {
  if (state === "done") {
    return <Check className="job-stage-icon" strokeWidth={2.5} aria-hidden />;
  }
  if (state === "warn") {
    return (
      <AlertTriangle className="job-stage-icon" strokeWidth={2.5} aria-hidden />
    );
  }
  if (state === "active") {
    return (
      <Loader2
        className="job-stage-icon job-stage-icon--spin"
        strokeWidth={2.5}
        aria-hidden
      />
    );
  }
  return (
    <Circle
      className="job-stage-icon job-stage-icon--idle"
      strokeWidth={2}
      aria-hidden
    />
  );
}

export function SubmitReview({
  onCompleted,
  initialPath = "",
  findingsReady = false,
  onViewFindings,
}: Props) {
  const [path, setPath] = useState(initialPath);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobPayload | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [workers, setWorkers] = useState(DEFAULT_OPTIONS.workers);
  const [maxFiles, setMaxFiles] = useState(DEFAULT_OPTIONS.max_files);
  const [includeInit, setIncludeInit] = useState(DEFAULT_OPTIONS.include_init);
  const [includeText, setIncludeText] = useState("");
  const [excludeText, setExcludeText] = useState("");
  const [preview, setPreview] = useState<ReviewPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const doneRef = useRef(false);
  const seenAuditIds = useRef(new Set<number>());
  const auditLogRef = useRef<HTMLDivElement>(null);

  const reviewOptions = useMemo<ReviewOptions>(
    () => ({
      workers: Math.max(1, workers),
      max_files: Math.max(1, maxFiles),
      include_init: includeInit,
      include: parsePatterns(includeText),
      exclude: parsePatterns(excludeText),
    }),
    [excludeText, includeInit, includeText, maxFiles, workers],
  );

  useEffect(() => {
    const trimmed = path.trim();
    if (
      !trimmed ||
      submitting ||
      job?.status === "queued" ||
      job?.status === "running"
    ) {
      if (!trimmed) {
        setPreview(null);
        setPreviewError(null);
      }
      setPreviewing(false);
      return;
    }
    let cancelled = false;
    setPreviewing(true);
    const timeout = window.setTimeout(() => {
      setPreviewError(null);
      void previewReview(trimmed, reviewOptions)
        .then((next) => {
          if (!cancelled) setPreview(next);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setPreview(null);
          setPreviewError(
            friendlyPreviewError(
              err instanceof Error ? err.message : String(err),
            ),
          );
        })
        .finally(() => {
          if (!cancelled) setPreviewing(false);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [job?.status, path, reviewOptions, submitting]);

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
    if (preview?.kind === "directory" && preview.selected_count === 0) {
      setError("No eligible Python files match the directory options.");
      return;
    }
    setSubmitting(true);
    try {
      const accepted = await submitReview(trimmed, reviewOptions);
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

  const fileProgress = useMemo(() => {
    if (preview?.kind !== "directory") return [];
    const states = new Map<
      string,
      { state: "queued" | "running" | "completed"; accepted?: number; needs?: number }
    >(
      preview.files.map((file) => [
        file,
        { state: "queued" as const },
      ]),
    );
    for (const event of auditEvents) {
      if (event.stage !== "review_start" && event.stage !== "review_complete") continue;
      const rawPath =
        typeof event.detail.path === "string" ? event.detail.path : null;
      if (!rawPath) continue;
      const file = displayPath(rawPath, preview.path);
      if (!states.has(file)) continue;
      if (event.stage === "review_start") {
        states.set(file, { state: "running" });
      } else {
        states.set(file, {
          state: "completed",
          accepted:
            typeof event.detail.accepted_count === "number"
              ? event.detail.accepted_count
              : 0,
          needs:
            typeof event.detail.needs_review_count === "number"
              ? event.detail.needs_review_count
              : 0,
        });
      }
    }
    return [...states.entries()].map(([file, value]) => ({ file, ...value }));
  }, [auditEvents, preview]);

  const completedFiles = fileProgress.filter(
    (item) => item.state === "completed",
  ).length;

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
            disabled={
              submitting ||
              polling ||
              previewing ||
              (preview?.kind === "directory" && preview.selected_count === 0)
            }
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
        <div className="path-preview-status" aria-live="polite">
          {previewing ? (
            <>
              <Loader2 className="inline-status-icon spin" aria-hidden />
              Checking path and eligible files…
            </>
          ) : preview?.kind === "file" ? (
            <>
              <FileCode2 className="inline-status-icon" aria-hidden />
              Single file · Security and Architecture will both run
            </>
          ) : preview?.kind === "directory" ? (
            <>
              <FolderTree className="inline-status-icon" aria-hidden />
              Directory · bounded concurrent file review
            </>
          ) : previewError ? (
            <>
              <AlertTriangle className="inline-status-icon" aria-hidden />
              {previewError}
            </>
          ) : null}
        </div>

        {preview?.kind === "directory" ? (
          <section className="directory-config-panel" aria-label="Directory review options">
            <div className="directory-config-heading">
              <div>
                <p className="section-label">
                  <SlidersHorizontal
                    className="section-label-icon"
                    aria-hidden
                  />
                  Directory controls
                </p>
                <p className="directory-config-copy">
                  Workers are concurrent file reviews. Every selected file still
                  runs both Security and Architecture.
                </p>
              </div>
              <span className="badge badge-neutral">alphabetical</span>
            </div>

            <div className="directory-control-grid">
              <label className="compact-field">
                <span className="field-label">Workers</span>
                <input
                  className="field-input"
                  type="number"
                  min={1}
                  value={workers}
                  onChange={(event) =>
                    setWorkers(Math.max(1, Number(event.target.value) || 1))
                  }
                  disabled={submitting || polling}
                />
              </label>
              <label className="compact-field">
                <span className="field-label">Max files</span>
                <input
                  className="field-input"
                  type="number"
                  min={1}
                  value={maxFiles}
                  onChange={(event) =>
                    setMaxFiles(Math.max(1, Number(event.target.value) || 1))
                  }
                  disabled={submitting || polling}
                />
              </label>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={includeInit}
                  onChange={(event) => setIncludeInit(event.target.checked)}
                  disabled={submitting || polling}
                />
                <span>
                  <strong>Include __init__.py</strong>
                  <small>Trivial package markers still stay excluded.</small>
                </span>
              </label>
            </div>

            <div className="directory-pattern-grid">
              <label>
                <span className="field-label">Include globs (optional)</span>
                <input
                  className="field-input mono"
                  value={includeText}
                  onChange={(event) => setIncludeText(event.target.value)}
                  placeholder="src/**, app/**/*.py"
                  disabled={submitting || polling}
                />
              </label>
              <label>
                <span className="field-label">Exclude globs (optional)</span>
                <input
                  className="field-input mono"
                  value={excludeText}
                  onChange={(event) => setExcludeText(event.target.value)}
                  placeholder="tests/**, **/generated.py"
                  disabled={submitting || polling}
                />
              </label>
            </div>

            <div className="selection-summary">
              <div>
                <strong>
                  {preview.selected_count} eligible file
                  {preview.selected_count === 1 ? "" : "s"} selected
                </strong>
                <span>
                  {preview.capped
                    ? `capped from ${preview.eligible_count}`
                    : `${preview.eligible_count} eligible total`}{" "}
                  · workers={workers}
                </span>
              </div>
              {previewing ? (
                <RefreshCw className="inline-status-icon spin" aria-label="Refreshing preview" />
              ) : null}
            </div>

            <div className="eligible-file-list" aria-label="Selected eligible files">
              {preview.files.length === 0 ? (
                <div className="directory-empty-state">
                  <FileCode2 aria-hidden />
                  <span>No eligible Python files match these options.</span>
                </div>
              ) : (
                preview.files.map((file, index) => (
                  <div className="eligible-file-row" key={file} title={file}>
                    <span className="file-order mono">{index + 1}</span>
                    <FileCode2 className="eligible-file-icon" aria-hidden />
                    <span className="mono path-wrap">{file}</span>
                  </div>
                ))
              )}
            </div>
          </section>
        ) : null}
        <p className="submit-hint">
          Security and Architecture both run for every file. Memory and web only
          light up when the Supervisor routes to them.
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
                        <JobStageIcon state={state} />
                      </span>
                      <span>{stage.label}</span>
                    </li>
                  );
                })}
              </ol>
            </div>

            {fileProgress.length > 0 ? (
              <div className="file-progress-panel">
                <div className="file-progress-heading">
                  <div>
                    <p className="section-label">Per-file progress</p>
                    <p className="file-progress-summary">
                      {completedFiles}/{fileProgress.length} complete · up to{" "}
                      {workers} concurrent file review
                      {workers === 1 ? "" : "s"}
                    </p>
                  </div>
                  <span className="badge badge-neutral">
                    Security + Architecture per file
                  </span>
                </div>
                <div className="file-progress-list">
                  {fileProgress.map((item) => (
                    <div
                      className={`file-progress-row file-progress-row--${item.state}`}
                      key={item.file}
                      title={item.file}
                    >
                      <span className="file-progress-mark" aria-hidden>
                        {item.state === "completed" ? (
                          <Check />
                        ) : item.state === "running" ? (
                          <Loader2 className="spin" />
                        ) : (
                          <Circle />
                        )}
                      </span>
                      <span className="mono path-wrap">{item.file}</span>
                      <span className="file-progress-state">
                        {item.state === "completed"
                          ? `${item.accepted ?? 0} accepted · ${item.needs ?? 0} needs review`
                          : item.state}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {status === "completed" && findingsReady && onViewFindings ? (
              <div className="submit-complete-banner">
                <div>
                  <strong>Review complete.</strong> Timeline and audit trail stay
                  here — open findings when you are ready.
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={onViewFindings}
                >
                  View findings
                </button>
              </div>
            ) : null}

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
