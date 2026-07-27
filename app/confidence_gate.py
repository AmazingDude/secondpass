"""Confidence gate: split review findings by threshold."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schema import Finding, ReviewResult

DEFAULT_THRESHOLD = 80


class GateResult(BaseModel):
    """Outcome of applying the confidence gate to a ReviewResult."""

    accepted: list[Finding]
    needs_review: list[Finding]
    threshold: int = Field(ge=0, le=100)


def apply_confidence_gate(
    result: ReviewResult,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> GateResult:
    """Split findings into accepted (>= threshold) and needs_review (< threshold).

    Order within each list matches the original findings order.
    """
    accepted: list[Finding] = []
    needs_review: list[Finding] = []

    for finding in result.findings:
        if finding.confidence >= threshold:
            accepted.append(finding)
        else:
            needs_review.append(finding)

    return GateResult(
        accepted=accepted,
        needs_review=needs_review,
        threshold=threshold,
    )
