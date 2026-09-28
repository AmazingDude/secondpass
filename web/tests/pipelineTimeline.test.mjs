import assert from "node:assert/strict";
import test from "node:test";

import { derivePipelineFromAudit } from "../src/pipelineTimeline.ts";

test("incomplete architecture review with findings remains a warning", () => {
  const events = [
    {
      stage: "agent_event",
      kind: "agent_event",
      worker_name: "architecture_worker",
      detail: {
        agent: "architecture_worker",
        message: "architecture_worker: inconclusive with issues",
      },
    },
    {
      stage: "schema_validation",
      kind: "stage",
      worker_name: "architecture",
      detail: { finding_count: 2 },
    },
  ];

  const timeline = derivePipelineFromAudit(events, "completed");
  assert.equal(timeline.states.architecture, "warn");
});

test("incomplete security review with findings remains a warning", () => {
  const events = [
    {
      stage: "agent_event",
      kind: "agent_event",
      worker_name: "security",
      detail: { agent: "supervisor", message: "logic-review: inconclusive with issues" },
    },
    { stage: "schema_validation", kind: "stage", worker_name: "security", detail: { finding_count: 2 } },
    { stage: "confidence_gate", kind: "stage", worker_name: "security", detail: { accepted_count: 2 } },
  ];

  const timeline = derivePipelineFromAudit(events, "completed");
  assert.equal(timeline.states.logic, "warn");
  assert.equal(timeline.states.security, "warn");
});
