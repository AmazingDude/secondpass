import { Check, Loader2, Minus, TriangleAlert } from "lucide-react";
import {
  AGENT_COLOR,
  TIMELINE_ROWS,
  type NodeId,
  type NodeInfo,
  type NodeState,
} from "../pipelineTimeline";

type Props = {
  states: Record<NodeId, NodeState>;
  info: Map<NodeId, NodeInfo>;
  running: boolean;
  doneCount: number;
  totalCount: number;
  percent: number;
};

export function AgentTimeline({
  states,
  info,
  running,
  doneCount,
  totalCount,
  percent,
}: Props) {
  return (
    <div className="agent-timeline">
      <div className="agent-timeline-progress">
        <div className="agent-timeline-progress-meta mono">
          <span>
            {doneCount}/{totalCount} steps complete
          </span>
          <span>{percent}%</span>
        </div>
        <div className="agent-timeline-bar" aria-hidden="true">
          <div
            className="agent-timeline-bar-fill"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      <ol className="agent-timeline-list">
        {TIMELINE_ROWS.map((row, index) => {
          const state = states[row.id];
          const color = AGENT_COLOR[row.agent];
          const skipped = !running && state === "idle" && percent > 0;
          const last = index === TIMELINE_ROWS.length - 1;
          const rowInfo = info.get(row.id);
          return (
            <li key={row.id} className="agent-timeline-item">
              {!last ? (
                <span className="agent-timeline-rail" aria-hidden="true" />
              ) : null}
              <span className="agent-timeline-dot-slot">
                <StepDot state={state} color={color} skipped={skipped} />
              </span>
              <div
                className={`agent-timeline-card agent-timeline-card--${state}`}
                style={
                  state === "active"
                    ? {
                        borderColor: `color-mix(in srgb, ${color} 45%, var(--surface-border))`,
                        background: `color-mix(in srgb, ${color} 8%, var(--surface))`,
                      }
                    : state === "warn"
                      ? undefined
                      : undefined
                }
              >
                <div className="agent-timeline-card-head">
                  <span
                    className="agent-timeline-title"
                    style={state !== "idle" ? { color } : undefined}
                  >
                    {row.label}
                  </span>
                  {rowInfo?.duration != null ? (
                    <span className="agent-timeline-meta mono">
                      {rowInfo.duration}ms
                    </span>
                  ) : null}
                  {skipped ? (
                    <span className="agent-timeline-meta mono">not routed</span>
                  ) : null}
                </div>
                <p className="agent-timeline-sub">
                  {rowInfo?.message ?? row.sub}
                </p>
                {row.children ? (
                  <ul className="agent-timeline-children">
                    {row.children.map((child) => {
                      const childState = states[child.id];
                      const childSkipped =
                        !running && childState === "idle" && percent > 0;
                      const childInfo = info.get(child.id);
                      return (
                        <li key={child.id} className="agent-timeline-child">
                          <StepDot
                            state={childState}
                            color={AGENT_COLOR.tool}
                            skipped={childSkipped}
                            small
                          />
                          <div className="agent-timeline-child-body">
                            <div className="agent-timeline-card-head">
                              <span
                                className={`agent-timeline-child-label mono ${
                                  childState === "idle" ? "is-idle" : ""
                                }`}
                              >
                                {child.label}
                              </span>
                              <span className="agent-timeline-meta mono">
                                {child.sub}
                              </span>
                              {childInfo?.duration != null ? (
                                <span className="agent-timeline-meta mono">
                                  {childInfo.duration}ms
                                </span>
                              ) : null}
                            </div>
                            <span className="agent-timeline-sub">
                              {childSkipped
                                ? "skipped"
                                : (childInfo?.message ?? "waiting")}
                            </span>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function StepDot({
  state,
  color,
  skipped,
  small,
}: {
  state: NodeState;
  color: string;
  skipped?: boolean;
  small?: boolean;
}) {
  const sizeClass = small ? "agent-dot agent-dot--sm" : "agent-dot";
  if (state === "active") {
    return (
      <span
        className={`${sizeClass} agent-dot--active`}
        style={{
          borderColor: color,
          background: `color-mix(in srgb, ${color} 12%, transparent)`,
        }}
      >
        <Loader2
          className={small ? "agent-dot-icon--sm" : "agent-dot-icon"}
          style={{ color }}
          aria-hidden
        />
      </span>
    );
  }
  if (state === "done" || state === "warn") {
    const tone = state === "warn" ? "var(--severity-medium)" : color;
    const Icon = state === "warn" ? TriangleAlert : Check;
    return (
      <span
        className={`${sizeClass} agent-dot--filled`}
        style={{ background: tone }}
      >
        <Icon
          className={small ? "agent-dot-icon--sm" : "agent-dot-icon"}
          strokeWidth={3}
          aria-hidden
        />
      </span>
    );
  }
  return (
    <span
      className={`${sizeClass} agent-dot--idle ${skipped ? "is-skipped" : ""}`}
    >
      {skipped ? (
        <Minus
          className={small ? "agent-dot-icon--sm" : "agent-dot-icon"}
          aria-hidden
        />
      ) : null}
    </span>
  );
}
