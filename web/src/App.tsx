import { useCallback, useRef, useState } from "react";
import { API_BASE, getReview } from "./api";
import type { JobPayload, ReviewPayload } from "./api";
import { FindingsView } from "./views/FindingsView";
import { HistoryView } from "./views/HistoryView";
import { MemoryView } from "./views/MemoryView";
import { SubmitReview } from "./views/SubmitReview";
import { navigate, useDashboardRoute } from "./navigation";

type Tab = "submit" | "findings" | "history" | "memory";

const DEMO_PATH = "benchmark/fixtures/notes_idor.py";

const NAV: { id: Tab; label: string }[] = [
  { id: "submit", label: "Submit" },
  { id: "findings", label: "Findings" },
  { id: "history", label: "History" },
  { id: "memory", label: "Memory" },
];

function apiHostLabel(base: string) {
  try {
    return new URL(base).host;
  } catch {
    return base.replace(/^https?:\/\//, "");
  }
}

export default function App() {
  const screen = useDashboardRoute();
  const findingsGeneration = useRef(0);
  const [lastFindings, setLastFindings] = useState<{
    reviews: ReviewPayload[];
    jobPath?: string;
    backTo: "submit" | "history";
    backLink: string;
  } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const openFindings = useCallback(
    (
      reviews: ReviewPayload[],
      jobPath?: string,
      backTo: "submit" | "history" = "history",
    ) => {
      findingsGeneration.current += 1;
      const backLink = backTo === "history" ? window.location.hash : "#/submit";
      const next = { reviews, jobPath, backTo, backLink };
      setLastFindings(next);
      navigate("#/findings");
    },
    [],
  );

  const handleCompleted = useCallback(async (job: JobPayload) => {
    const generation = ++findingsGeneration.current;
    setLoadError(null);
    const ids = job.persisted_review_ids || {};
    const reviewIds = (
      Array.isArray(ids) ? ids : [ids.security, ids.architecture]
    ).filter((id): id is number => typeof id === "number");
    try {
      const reviews = await Promise.all(reviewIds.map((id) => getReview(id)));
      if (generation !== findingsGeneration.current) return;
      // Stay on Submit so the live timeline/audit trail remain visible;
      // Findings opens only when the user chooses.
      setLastFindings({ reviews, jobPath: job.path, backTo: "submit", backLink: "#/submit" });
    } catch (err) {
      if (generation === findingsGeneration.current) {
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    }
  }, []);

  function goTab(tab: Tab) {
    setLoadError(null);
    navigate(`#/${tab}`);
  }

  const activeTab: Tab =
    screen.name === "findings"
      ? "findings"
      : screen.name === "history" || screen.name === "invalid"
        ? "history"
        : screen.name === "memory"
          ? "memory"
          : "submit";

  return (
    <div className="app-frame">
      <header className="app-topbar">
        <div className="app-topbar-inner">
          <div className="app-brand">
            <span className="app-brand-name">secondpass</span>
          </div>

          <nav className="app-nav" aria-label="Main">
            {NAV.map((item) => (
              <button
                key={item.id}
                type="button"
                className={[
                  "app-nav-btn",
                  activeTab === item.id ? "active" : "",
                ].join(" ")}
                onClick={() => goTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="app-topbar-status mono">
            api {apiHostLabel(API_BASE)} · connected
          </div>
        </div>
      </header>

      <div className="app-shell">
        {loadError &&
        (screen.name === "submit" || screen.name === "findings") ? (
          <p className="error-text">{loadError}</p>
        ) : null}

        <div className="view-panel" key={screen.name}>
          {screen.name === "submit" ? (
            <SubmitReview
              initialPath={DEMO_PATH}
              onCompleted={handleCompleted}
              findingsReady={Boolean(lastFindings)}
              onViewFindings={
                lastFindings
                  ? () =>
                      openFindings(
                        lastFindings.reviews,
                        lastFindings.jobPath,
                        "submit",
                      )
                  : undefined
              }
            />
          ) : null}

          {screen.name === "findings" ? (
            <FindingsView
              reviews={lastFindings?.reviews ?? []}
              jobPath={lastFindings?.jobPath}
              onBack={() => navigate(lastFindings?.backLink || "#/history")}
              backLabel={lastFindings?.backTo === "submit" ? "Submit" : "History"}
            />
          ) : null}

          {screen.name === "history" ? (
            <HistoryView
              view={screen.view}
              jobId={screen.jobId}
              onOpenReview={(review) => {
                openFindings([review], review.file_path, "history");
              }}
            />
          ) : null}

          {screen.name === "memory" ? (
            <MemoryView initialReviewId={lastFindings?.reviews[0]?.id ?? null} />
          ) : null}
          {screen.name === "invalid" ? <div className="card">
            <p className="error-text" role="alert">Invalid dashboard link. Open History to find a saved job.</p>
            <a className="btn btn-ghost" href="#/history">Open History</a>
          </div> : null}
        </div>
      </div>
    </div>
  );
}
