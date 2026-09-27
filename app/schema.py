"""Shared Pydantic schemas for Phase 3 review findings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


DetectionMethod = Literal["static_rule", "llm_reasoning"]


class Finding(BaseModel):
    """A single schema-validated review finding (security or architecture)."""

    finding_type: str
    evidence: str
    confidence: int = Field(ge=0, le=100)
    suggested_fix: str
    detection_method: DetectionMethod

    @field_validator("evidence")
    @classmethod
    def evidence_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("evidence must be non-empty")
        return value


class ReviewResult(BaseModel):
    """Aggregated output from a review worker for one file."""

    findings: list[Finding]
    file_path: str
    timestamp: datetime
    worker_name: str
    # Optional coverage signal, distinct from gate needs_review:
    # inconclusive = a required review stage did not complete; ok = checked.
    # None is a legacy result with no explicit coverage signal.
    coverage_status: Literal["ok", "inconclusive"] | None = None
    # LLM claimed issue(s) that every post-filter dropped.
    # Distinct from clean (nothing claimed) and from inconclusive (incomplete).
    claim_status: Literal["unverified"] | None = None
