/** Typed client for existing FastAPI endpoints (no new routes). */

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ||
  "http://127.0.0.1:8000";

export type JobStatus = "queued" | "running" | "completed" | "failed";

export type Finding = {
  finding_type: string;
  evidence: string;
  confidence: number;
  suggested_fix: string;
  detection_method: string;
};

export type GateResult = {
  accepted: Finding[];
  needs_review: Finding[];
  threshold: number;
};

export type ReviewPayload = {
  id: number;
  file_path: string;
  worker_name: string;
  created_at: string;
  gate_threshold: number;
  accepted_count: number;
  needs_review_count: number;
  job_id: string | null;
  review_result: {
    findings: Finding[];
    file_path: string;
    timestamp: string;
    worker_name: string;
    /** Distinct from needs_review: coverage failed (e.g. rate limit), not a low-confidence finding. */
    coverage_status?: "ok" | "inconclusive" | null;
    /** Architecture: LLM claimed finding(s) that post-filters rejected — not genuine clean. */
    claim_status?: "unverified" | null;
  };
  gate_result: GateResult;
};

export type JobPayload = {
  job_id: string;
  path: string;
  status: JobStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  persisted_review_ids?: {
    security?: number | null;
    architecture?: number | null;
  };
  summary?: Record<string, unknown>;
  result?: Record<string, unknown>;
};

export type AuditEventKind = "stage" | "agent_event" | "tool";

export type AuditEvent = {
  id: number;
  /** stage | agent_event | tool — present on newer API responses */
  kind?: AuditEventKind;
  stage: string;
  worker_name: string | null;
  timestamp: string;
  detail: Record<string, unknown>;
};

export type JobAuditPayload = {
  job_id: string;
  event_count: number;
  events: AuditEvent[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export function submitReview(path: string) {
  return request<{ job_id: string }>("/reviews", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function getJob(jobId: string) {
  return request<JobPayload>(`/reviews/jobs/${jobId}`);
}

export function getJobAudit(jobId: string) {
  return request<JobAuditPayload>(`/reviews/jobs/${jobId}/audit`);
}

export function getReview(reviewId: number) {
  return request<ReviewPayload>(`/reviews/${reviewId}`);
}

export function listReviews(limit = 50) {
  return request<{ reviews: ReviewPayload[] }>(
    `/reviews?limit=${encodeURIComponent(String(limit))}`,
  );
}

export type OutcomePayload = {
  id: number;
  file_path: string;
  accepted: boolean;
  reason: string;
  linked_fix_commit: string | null;
  review_id: number | null;
  created_at: string;
  finding: Finding;
};

export function listOutcomes(filePath: string) {
  return request<{ file_path: string; outcomes: OutcomePayload[] }>(
    `/outcomes?file_path=${encodeURIComponent(filePath)}`,
  );
}

export function postOutcome(body: {
  review_id: number;
  index: number;
  accepted: boolean;
  reason: string;
  linked_fix_commit?: string | null;
}) {
  return request<OutcomePayload>("/outcomes", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export { API_BASE };
