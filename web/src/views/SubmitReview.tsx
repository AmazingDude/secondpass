import { useEffect, useRef, useState, type FormEvent } from "react";
import { getJob, submitReview, type JobPayload } from "../api";

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

export function SubmitReview({ onCompleted, initialPath = "" }: Props) {
  const [path, setPath] = useState(initialPath);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const doneRef = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    doneRef.current = false;
    let cancelled = false;

    const tick = async () => {
      try {
        const next = await getJob(jobId);
        if (cancelled) return;
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
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
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
        {error ? <p className="error-text">{error}</p> : null}
        {(jobId || job) && (
          <ol className="stage-list" aria-live="polite">
            {STAGES.map((stage, index) => {
              let state: "pending" | "active" | "done" = "pending";
              if (!status || status === "queued") {
                state = stage.id === "queued" ? "active" : "pending";
                if (stage.id === "queued" && status === "queued") state = "active";
              } else if (status === "running") {
                if (stage.id === "queued") state = "done";
                else if (stage.id === "running") state = "active";
              } else if (status === "completed") {
                state = "done";
              } else if (status === "failed") {
                if (index === 0) state = "done";
                else if (stage.id === "running") state = "active";
              }
              return (
                <li key={stage.id} className={`stage-item ${state}`}>
                  <span className="stage-num">{index + 1}</span>
                  <div>
                    <div className="stage-label">{stage.label}</div>
                    {stage.id === "queued" && jobId ? (
                      <div className="stage-meta mono">job_id {jobId}</div>
                    ) : null}
                    {stage.id === "running" && status === "running" ? (
                      <div className="stage-meta">
                        <span className="spinner" aria-hidden="true" />
                        Polling job status…
                      </div>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </form>
    </div>
  );
}
