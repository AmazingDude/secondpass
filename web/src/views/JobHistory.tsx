import { useEffect, useState } from "react";
import {
  getJob, getSavedRun, listJobs,
  type JobPage, type JobPayload, type JobSummary, type ReviewPayload, type SavedRunDetail,
} from "../api";

type Props = { onOpenReview: (review: ReviewPayload) => void };

function statusLabel(status: JobSummary["execution_status"]) {
  return status[0].toUpperCase() + status.slice(1);
}

function JobDetail({ jobId, onBack, onOpenReview }: Props & { jobId: string; onBack: () => void }) {
  const [job, setJob] = useState<JobPayload | null>(null);
  const [detail, setDetail] = useState<SavedRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getJob(jobId), getSavedRun(jobId)])
      .then(([savedJob, savedDetail]) => {
        if (!cancelled) { setJob(savedJob); setDetail(savedDetail); }
      })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [jobId]);

  async function loadMore() {
    if (!detail || detail.next_before_review_id === null) return;
    setLoading(true);
    setError(null);
    try {
      const next = await getSavedRun(jobId, detail.snapshot_review_id, detail.next_before_review_id);
      if (next) setDetail({ ...next, reviews: [...detail.reviews, ...next.reviews] });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setLoading(false); }
  }

  return <div className="card">
    <button className="btn btn-ghost" type="button" onClick={onBack}>Back to jobs</button>
    <h2>Job <span className="mono">{jobId}</span></h2>
    {job ? <>
      <p className="mono">{job.path}</p>
      <p>Execution: {statusLabel(job.status)} · Analysis coverage: unknown</p>
      <p className="empty-detail">This is a saved status, not live monitoring. Return to jobs to refresh.</p>
      {job.error ? <p className="error-text">{job.error}</p> : null}
    </> : null}
    {loading ? <p role="status">Loading saved results…</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
    {!loading && !error && !detail?.reviews.length ?
      <p className="empty-detail">No saved worker results. This does not mean the analysis was clean.</p> : null}
    {detail?.reviews.map(review => <p key={review.id}>
      <button type="button" className="btn btn-ghost" onClick={() => onOpenReview(review)}>
        Open saved review {review.id} · {review.worker_name} · {review.file_path}
      </button>
    </p>)}
    {detail?.next_before_review_id != null ?
      <button type="button" className="btn btn-ghost" disabled={loading} onClick={() => void loadMore()}>
        Load more saved results
      </button> : null}
  </div>;
}

export function JobHistory({ onOpenReview }: Props) {
  const [refresh, setRefresh] = useState(0);
  const [page, setPage] = useState<JobPage | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listJobs().then(result => { if (!cancelled) setPage(result); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [refresh]);

  async function loadMore() {
    if (!page || page.next_before_sequence === null) return;
    setLoading(true);
    setError(null);
    try {
      const next = await listJobs(page.snapshot_sequence, page.next_before_sequence);
      setPage({ ...next, jobs: [...page.jobs, ...next.jobs] });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { setLoading(false); }
  }

  if (selected) return <JobDetail key={selected} jobId={selected} onBack={() => {
    setSelected(null);
    setRefresh(value => value + 1);
  }} onOpenReview={onOpenReview} />;
  return <>
    <p className="empty-detail">Newest recorded requests first. Execution status is separate from analysis coverage.</p>
    <button type="button" className="btn btn-ghost" disabled={loading} onClick={() => setRefresh(value => value + 1)}>Refresh jobs</button>
    {loading ? <p role="status">Loading jobs…</p> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
    {!loading && !error && !page?.jobs.length ? <p>No recorded jobs yet. Older worker reviews remain available below.</p> : null}
    {page?.jobs.length ? <div className="history-scroll"><table className="history-table">
      <thead><tr><th>Job ID</th><th>Submitted</th><th>Path</th><th>Execution</th><th>Analysis coverage</th><th /></tr></thead>
      <tbody>{page.jobs.map(job => <tr key={job.job_id}>
        <td className="mono">{job.job_id}</td>
        <td>{new Date(job.created_at).toLocaleString()}</td>
        <td className="mono">{job.path}</td>
        <td>{statusLabel(job.execution_status)}</td><td>Unknown</td>
        <td><button type="button" className="btn btn-ghost" disabled={loading} onClick={() => setSelected(job.job_id)}>Open job {job.job_id}</button></td>
      </tr>)}</tbody>
    </table></div> : null}
    {page?.next_before_sequence != null ?
      <button type="button" className="btn btn-ghost" disabled={loading} onClick={() => void loadMore()}>Load more jobs</button> : null}
  </>;
}
