import assert from "node:assert/strict";
import test from "node:test";

import { derivePipelineFromAudit } from "../src/pipelineTimeline.ts";

test("interrupted work warns instead of leaving an agent active", () => {
  const events = [{
    stage: "agent_event", kind: "agent_event", worker_name: "security",
    detail: { agent: "supervisor", message: "supervisor -> security_worker" },
  }];
  const timeline = derivePipelineFromAudit(events, "interrupted");
  assert.equal(timeline.states.security, "warn");
  assert.notEqual(timeline.states.supervisor, "active");
});

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

test("unverified architecture claim does not become done after schema validation", () => {
  for (const message of [
    "architecture_worker: claim_unverified",
    "architecture_worker: inconclusive with unverified claim",
  ]) {
    const events = [
      {
        stage: "agent_event",
        kind: "agent_event",
        worker_name: "architecture_worker",
        detail: { agent: "architecture_worker", message },
      },
      { stage: "schema_validation", kind: "stage", worker_name: "architecture", detail: { finding_count: 0 } },
    ];

    const timeline = derivePipelineFromAudit(events, "completed");
    assert.equal(timeline.states.architecture, "warn", message);
  }
});

test("unverified security claim does not become done after validation", () => {
  const events = [
    {
      stage: "agent_event",
      kind: "agent_event",
      worker_name: "security",
      detail: { agent: "supervisor", message: "logic-review: unverified" },
    },
    { stage: "schema_validation", kind: "stage", worker_name: "security", detail: { finding_count: 0 } },
    { stage: "confidence_gate", kind: "stage", worker_name: "security", detail: { accepted_count: 0 } },
  ];

  const timeline = derivePipelineFromAudit(events, "completed");
  assert.equal(timeline.states.logic, "warn");
  assert.equal(timeline.states.security, "warn");
});
