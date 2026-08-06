/** Derive agent-timeline node states from real audit / hook events. */

import type { AuditEvent } from "./api";

export type NodeId =
  | "supervisor"
  | "security"
  | "semgrep"
  | "logic"
  | "memory_worker"
  | "chroma"
  | "web_worker"
  | "tavily"
  | "architecture"
  | "gate";

export type AgentKey =
  | "supervisor"
  | "security"
  | "memory"
  | "web"
  | "architecture"
  | "tool";

export type NodeState = "idle" | "active" | "done" | "warn";

export type TimelineRow = {
  id: NodeId;
  label: string;
  sub: string;
  agent: AgentKey;
  children?: { id: NodeId; label: string; sub: string }[];
};

export const AGENT_COLOR: Record<AgentKey, string> = {
  supervisor: "#6366f1",
  security: "#2c6be0",
  memory: "#8b7fd6",
  web: "#0d9488",
  architecture: "#fb923c",
  tool: "#64748b",
};

/** Human-friendly ordering — parents with nested tool steps. */
export const TIMELINE_ROWS: TimelineRow[] = [
  {
    id: "supervisor",
    label: "Supervisor",
    sub: "accepts the job and decides who runs",
    agent: "supervisor",
  },
  {
    id: "security",
    label: "Security worker",
    sub: "static scan + logic review",
    agent: "security",
    children: [
      { id: "semgrep", label: "run_static_scan", sub: "semgrep" },
      { id: "logic", label: "logic_review", sub: "llm" },
    ],
  },
  {
    id: "memory_worker",
    label: "Memory worker",
    sub: "curated lessons from past reviews",
    agent: "memory",
    children: [{ id: "chroma", label: "search_memory", sub: "chroma" }],
  },
  {
    id: "web_worker",
    label: "Web worker",
    sub: "external remediation context",
    agent: "web",
    children: [{ id: "tavily", label: "search_web", sub: "tavily" }],
  },
  {
    id: "architecture",
    label: "Architecture worker",
    sub: "cross-file layering checks",
    agent: "architecture",
  },
  {
    id: "gate",
    label: "Confidence gate ≥80",
    sub: "accept, flag for review, persist",
    agent: "supervisor",
  },
];

export type NodeInfo = { message?: string; duration?: number };

const ALL_NODE_IDS: NodeId[] = TIMELINE_ROWS.flatMap((row) => [
  row.id,
  ...(row.children ?? []).map((child) => child.id),
]);

function idleStates(): Record<NodeId, NodeState> {
  return Object.fromEntries(ALL_NODE_IDS.map((id) => [id, "idle"])) as Record<
    NodeId,
    NodeState
  >;
}

function mark(
  states: Record<NodeId, NodeState>,
  id: NodeId,
  next: NodeState,
) {
  const prev = states[id];
  if (prev === "warn" && next === "done") return;
  if (prev === "done" && next === "active") return;
  states[id] = next;
}

function setInfo(
  info: Map<NodeId, NodeInfo>,
  id: NodeId,
  patch: NodeInfo,
) {
  const prev = info.get(id) ?? {};
  info.set(id, {
    message: patch.message ?? prev.message,
    duration: patch.duration ?? prev.duration,
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

function finishActive(
  states: Record<NodeId, NodeState>,
  except?: NodeId,
) {
  for (const id of ALL_NODE_IDS) {
    if (id === except) continue;
    if (states[id] === "active") mark(states, id, "done");
  }
}

/**
 * Map chronological audit events → node idle/active/done/warn + captions.
 * Job finished: untouched optional workers stay idle → UI shows "not routed".
 */
export function derivePipelineFromAudit(
  events: AuditEvent[],
  jobStatus: string | undefined,
): {
  states: Record<NodeId, NodeState>;
  info: Map<NodeId, NodeInfo>;
  doneCount: number;
  totalCount: number;
  percent: number;
} {
  const states = idleStates();
  const info = new Map<NodeId, NodeInfo>();

  for (const event of events) {
    const kind = eventKind(event);
    const detail = event.detail || {};
    const worker = (event.worker_name || "").toLowerCase();
    const agent = String(detail.agent || worker || "").toLowerCase();
    const message = String(detail.message || "").toLowerCase();
    const tool = String(detail.tool || "").toLowerCase();

    if (kind === "tool") {
      const duration =
        typeof detail.duration_ms === "number"
          ? Math.round(detail.duration_ms)
          : undefined;
      const failed = detail.ok === false || String(detail.status || "").startsWith("error");
      const toolState: NodeState = failed ? "warn" : "done";

      if (tool.includes("static_scan") || tool === "run_static_scan") {
        finishActive(states, "semgrep");
        mark(states, "security", "active");
        mark(states, "semgrep", toolState);
        setInfo(info, "semgrep", {
          message: failed ? "static scan failed" : "static scan finished",
          duration,
        });
        continue;
      }
      if (tool.includes("search_memory")) {
        finishActive(states, "chroma");
        mark(states, "memory_worker", "active");
        mark(states, "chroma", toolState);
        setInfo(info, "chroma", {
          message: failed ? "memory search failed" : "lesson retrieval finished",
          duration,
        });
        setInfo(info, "memory_worker", {
          message: failed ? "memory lookup failed" : "searching curated lessons",
        });
        continue;
      }
      if (tool.includes("search_web")) {
        finishActive(states, "tavily");
        mark(states, "web_worker", "active");
        mark(states, "tavily", toolState);
        setInfo(info, "tavily", {
          message: failed ? "web search failed" : "web context fetched",
          duration,
        });
        setInfo(info, "web_worker", {
          message: failed ? "web lookup failed" : "fetching remediation context",
        });
        continue;
      }
      continue;
    }

    if (kind === "agent_event") {
      if (
        message.includes("path review starting") ||
        message.includes("starting review") ||
        message.includes("supervisor starting")
      ) {
        mark(states, "supervisor", "active");
        setInfo(info, "supervisor", { message: String(detail.message || "") });
        continue;
      }
      if (message.includes("-> security")) {
        finishActive(states);
        mark(states, "supervisor", "done");
        mark(states, "security", "active");
        setInfo(info, "security", { message: "Semgrep + logic review" });
        continue;
      }
      if (
        message.includes("logic-review") ||
        message.includes("logic review") ||
        message.includes("honest logic")
      ) {
        finishActive(states, "logic");
        mark(states, "security", "active");
        if (message.includes("inconclusive") || message.includes("rate")) {
          mark(states, "logic", "warn");
          mark(states, "security", "warn");
          setInfo(info, "logic", { message: String(detail.message || "") });
        } else if (
          message.includes("concrete issue") ||
          message.includes("clean") ||
          message.includes("no issue")
        ) {
          mark(states, "logic", "done");
          setInfo(info, "logic", { message: String(detail.message || "") });
        } else {
          mark(states, "logic", "active");
          setInfo(info, "logic", { message: String(detail.message || "") });
        }
        continue;
      }
      if (message.includes("routing:")) {
        mark(states, "supervisor", "active");
        setInfo(info, "supervisor", { message: String(detail.message || "") });
        continue;
      }
      if (message.includes("-> memory")) {
        finishActive(states);
        mark(states, "memory_worker", "active");
        setInfo(info, "memory_worker", {
          message: "routed — searching personal lessons",
        });
        continue;
      }
      if (message.includes("memory_worker ->")) {
        mark(states, "memory_worker", "done");
        mark(states, "chroma", states.chroma === "idle" ? "done" : states.chroma);
        setInfo(info, "memory_worker", { message: String(detail.message || "") });
        continue;
      }
      if (message.includes("-> web")) {
        finishActive(states);
        mark(states, "web_worker", "active");
        setInfo(info, "web_worker", {
          message: "routed — fetching public guidance",
        });
        continue;
      }
      if (message.includes("web_worker ->")) {
        mark(states, "web_worker", "done");
        mark(states, "tavily", states.tavily === "idle" ? "done" : states.tavily);
        setInfo(info, "web_worker", { message: String(detail.message || "") });
        continue;
      }
      if (
        message.includes("skip save_finding") ||
        message.includes("left lesson memory")
      ) {
        mark(states, "supervisor", "active");
        setInfo(info, "supervisor", {
          message: "no unsupervised Chroma write",
        });
        continue;
      }
      if (message.includes("-> architecture")) {
        finishActive(states);
        mark(states, "architecture", "active");
        setInfo(info, "architecture", {
          message: "cross-file architecture review",
        });
        continue;
      }
      if (
        agent.includes("architecture") ||
        message.includes("architecture_worker")
      ) {
        if (
          message.includes("clean") ||
          message.includes("claimed") ||
          message.includes("filtered")
        ) {
          mark(states, "architecture", "done");
        } else {
          mark(states, "architecture", "active");
        }
        setInfo(info, "architecture", {
          message: String(detail.message || ""),
        });
        continue;
      }
      if (agent.includes("supervisor") || worker === "supervisor") {
        mark(states, "supervisor", states.supervisor === "done" ? "done" : "active");
        setInfo(info, "supervisor", { message: String(detail.message || "") });
      }
      continue;
    }

    // stage events
    switch (event.stage) {
      case "review_start":
        mark(states, "supervisor", "active");
        setInfo(info, "supervisor", {
          message: "starting Security + Architecture review",
        });
        break;
      case "prompt_io":
        if (worker.includes("security") || agent.includes("security")) {
          mark(states, "security", "active");
          if (states.logic === "idle") mark(states, "logic", "active");
          setInfo(info, "logic", { message: "model prompt / completion" });
        } else if (worker.includes("architecture")) {
          mark(states, "architecture", "active");
        } else {
          mark(states, "supervisor", "active");
        }
        break;
      case "schema_validation":
        if (worker.includes("architecture")) {
          mark(states, "architecture", "done");
        } else {
          mark(states, "security", "active");
          if (states.logic === "active") mark(states, "logic", "done");
        }
        break;
      case "confidence_gate": {
        finishActive(states, "gate");
        const accepted =
          typeof detail.accepted_count === "number" ? detail.accepted_count : "?";
        const needs =
          typeof detail.needs_review_count === "number"
            ? detail.needs_review_count
            : "?";
        mark(states, "gate", "active");
        setInfo(info, "gate", {
          message: `${accepted} accepted · ${needs} needs review`,
        });
        if (worker.includes("architecture")) {
          mark(states, "architecture", "done");
        } else {
          mark(states, "security", "done");
          if (states.semgrep === "active") mark(states, "semgrep", "done");
          if (states.logic === "active") mark(states, "logic", "done");
        }
        mark(states, "gate", "done");
        break;
      }
      case "chroma_save_skip":
        mark(states, "supervisor", "done");
        setInfo(info, "supervisor", {
          message: "verified outcomes require human accept/reject",
        });
        break;
      case "review_persisted":
      case "review_complete":
        finishActive(states);
        mark(states, "gate", "done");
        mark(states, "supervisor", "done");
        break;
      default:
        break;
    }
  }

  if (jobStatus === "completed" || jobStatus === "failed") {
    for (const id of ALL_NODE_IDS) {
      if (states[id] === "active") {
        mark(states, id, jobStatus === "failed" ? "warn" : "done");
      }
    }
  }

  const doneCount = ALL_NODE_IDS.filter(
    (id) => states[id] === "done" || states[id] === "warn",
  ).length;

  return {
    states,
    info,
    doneCount,
    totalCount: ALL_NODE_IDS.length,
    percent: Math.round((doneCount / ALL_NODE_IDS.length) * 100),
  };
}
