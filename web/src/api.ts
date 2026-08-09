/** Typed client for the local FastAPI backend. */

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
  options?: ReviewOptions;
  persisted_review_ids?:
    | {
        security?: number | null;
        architecture?: number | null;
      }
    | number[];
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

function formatApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const row = item as { msg?: unknown; loc?: unknown };
          const msg = typeof row.msg === "string" ? row.msg : null;
          if (!msg) return null;
          const loc = Array.isArray(row.loc)
            ? row.loc
                .filter((part) => typeof part === "string" || typeof part === "number")
                .join(".")
            : "";
          return loc ? `${loc}: ${msg}` : msg;
        }
        return null;
      })
      .filter((part): part is string => Boolean(part));
    if (parts.length) return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    const row = detail as { msg?: unknown; message?: unknown };
    if (typeof row.msg === "string" && row.msg.trim()) return row.msg;
    if (typeof row.message === "string" && row.message.trim()) return row.message;
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = formatApiDetail(body.detail, detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export type ReviewOptions = {
  workers: number;
  max_files: number;
  include_init: boolean;
  include: string[];
  exclude: string[];
};

export type ReviewPreview = {
  path: string;
  kind: "file" | "directory";
  selected_count: number;
  eligible_count: number;
  discovered_count: number;
  capped: boolean;
  files: string[];
  skipped?: {
    init: number;
    trivial: number;
    include: number;
    exclude: number;
    junk_directories: number;
  };
};

export function submitReview(path: string, options?: ReviewOptions) {
  return request<{ job_id: string }>("/reviews", {
    method: "POST",
    body: JSON.stringify({ path, ...options }),
  });
}

export function previewReview(path: string, options: ReviewOptions) {
  const query = new URLSearchParams({
    path,
    max_files: String(options.max_files),
    include_init: String(options.include_init),
  });
  options.include.forEach((pattern) => query.append("include", pattern));
  options.exclude.forEach((pattern) => query.append("exclude", pattern));
  return request<ReviewPreview>(`/reviews/preview?${query.toString()}`);
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

export type MemoryPromotion = {
  status: "saved" | "skipped" | "error" | string;
  lesson_id?: string | null;
  matched_id?: string | null;
  reason?: string | null;
  distance?: number | null;
  error?: string | null;
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
  return request<OutcomePayload & { memory_promotion?: MemoryPromotion | null }>(
    "/outcomes",
    {
    method: "POST",
    body: JSON.stringify(body),
    },
  );
}

export { API_BASE };
