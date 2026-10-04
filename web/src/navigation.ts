import { useSyncExternalStore } from "react";

type DashboardRoute =
  | { name: "submit" }
  | { name: "findings" | "memory"; reviewId?: number }
  | { name: "history"; view: "jobs" | "workers"; jobId?: string }
  | { name: "invalid" };

function subscribe(listener: () => void) {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}

function currentHash() {
  return window.location.hash;
}

export function jobLink(jobId: string) {
  return `#/history/jobs/${encodeURIComponent(jobId)}`;
}

export function reviewLink(reviewId: number) {
  return `#/reviews/${reviewId}`;
}

export function memoryLink(reviewId: number) {
  return `#/memory/reviews/${reviewId}`;
}

export function navigate(hash: string) {
  window.location.hash = hash;
}

export function useDashboardRoute(): DashboardRoute {
  const hash = useSyncExternalStore(subscribe, currentHash);
  if (hash === "" || hash === "#/submit") return { name: "submit" };
  if (hash === "#/findings") return { name: "findings" };
  const reviewMatch = /^#\/(reviews|memory\/reviews)\/([1-9]\d*)$/.exec(hash);
  if (reviewMatch && Number.isSafeInteger(Number(reviewMatch[2]))) {
    return { name: reviewMatch[1] === "reviews" ? "findings" : "memory", reviewId: Number(reviewMatch[2]) };
  }
  if (hash === "#/memory") return { name: "memory" };
  if (hash === "#/history") return { name: "history", view: "jobs" };
  if (hash === "#/history/workers") return { name: "history", view: "workers" };
  const match = /^#\/history\/jobs\/([^/]+)$/.exec(hash);
  if (match) {
    try {
      const jobId = decodeURIComponent(match[1]);
      if (jobId.trim()) return { name: "history", view: "jobs", jobId };
    } catch {
      // Invalid percent-encoding is a bad link, not a different saved job.
    }
  }
  return { name: "invalid" };
}
