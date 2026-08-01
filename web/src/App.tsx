import { useCallback, useState } from "react";
import { getReview } from "./api";
import type { JobPayload, ReviewPayload } from "./api";
import { FindingsView } from "./views/FindingsView";
import { HistoryView } from "./views/HistoryView";
import { MemoryView } from "./views/MemoryView";
import { SubmitReview } from "./views/SubmitReview";

type Tab = "submit" | "findings" | "history" | "memory";

type Screen =
  | { name: "submit" }
  | { name: "findings"; reviews: ReviewPayload[]; jobPath?: string }
  | { name: "history" }
  | { name: "memory"; initialReviewId?: number | null };

const DEMO_PATH = "benchmark/fixtures/notes_idor.py";

const NAV: { id: Tab; label: string }[] = [
  { id: "submit", label: "Submit" },
  { id: "findings", label: "Findings" },
  { id: "history", label: "History" },
  { id: "memory", label: "Memory" },
];

export default function App() {
  const [screen, setScreen] = useState<Screen>({ name: "submit" });
  const [lastFindings, setLastFindings] = useState<{
    reviews: ReviewPayload[];
    jobPath?: string;
  } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const openFindings = useCallback(
    (reviews: ReviewPayload[], jobPath?: string) => {
      const next = { reviews, jobPath };
      setLastFindings(next);
      setScreen({ name: "findings", ...next });
    },
    [],
  );

  const handleCompleted = useCallback(
    async (job: JobPayload) => {
      setLoadError(null);
      const ids = job.persisted_review_ids || {};
      const reviewIds = [ids.security, ids.architecture].filter(
        (id): id is number => typeof id === "number",
      );
      try {
        const reviews = await Promise.all(reviewIds.map((id) => getReview(id)));
        openFindings(reviews, job.path);
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    },
    [openFindings],
  );

  function goTab(tab: Tab) {
    setLoadError(null);
    if (tab === "submit") {
      setScreen({ name: "submit" });
      return;
    }
    if (tab === "history") {
      setScreen({ name: "history" });
      return;
    }
    if (tab === "memory") {
      setScreen({
        name: "memory",
        initialReviewId: lastFindings?.reviews[0]?.id ?? null,
      });
      return;
    }
    if (lastFindings) {
      setScreen({ name: "findings", ...lastFindings });
    } else {
      setLoadError("No findings loaded yet — submit a review or open one from History.");
      setScreen({ name: "history" });
    }
  }

  const activeTab: Tab =
    screen.name === "findings"
      ? "findings"
      : screen.name === "history"
        ? "history"
        : screen.name === "memory"
          ? "memory"
          : "submit";

  return (
    <div className="app-shell">
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

      {loadError ? <p className="error-text">{loadError}</p> : null}

      <div className="view-panel" key={screen.name}>
        {screen.name === "submit" ? (
          <SubmitReview
            initialPath={DEMO_PATH}
            onCompleted={handleCompleted}
          />
        ) : null}

        {screen.name === "findings" ? (
          <FindingsView
            reviews={screen.reviews}
            jobPath={screen.jobPath}
            onBack={() => goTab("history")}
            backLabel="History"
          />
        ) : null}

        {screen.name === "history" ? (
          <HistoryView
            onOpenReview={(review) => {
              openFindings([review], review.file_path);
            }}
          />
        ) : null}

        {screen.name === "memory" ? (
          <MemoryView initialReviewId={screen.initialReviewId} />
        ) : null}
      </div>
    </div>
  );
}
