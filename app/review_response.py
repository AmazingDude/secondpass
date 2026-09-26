"""Validate detector responses before they can establish successful coverage."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewIssue(BaseModel):
    """Shared field types, retaining legacy defaults for optional issue details."""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    finding_type: str = ""
    confidence: int = Field(default=70, ge=0, le=100)
    message: str = ""
    evidence: str = ""
    suggested_fix: str = ""
    location: str = ""
    line: int = Field(default=1, ge=1)
    severity: Literal["INFO", "WARNING", "ERROR"] = "WARNING"
    snippet: str = ""

    @model_validator(mode="after")
    def issue_has_description(self) -> ReviewIssue:
        if not self.message and not self.evidence:
            raise ValueError("Issue has no description")
        return self


class ReviewAssessment(BaseModel):
    """Required response envelope; issue evidence remains worker-specific."""

    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)

    has_issues: bool
    summary: str = Field(min_length=1)
    issues: list[ReviewIssue]

    @model_validator(mode="after")
    def clean_has_no_issues(self) -> ReviewAssessment:
        if not self.has_issues and self.issues:
            raise ValueError("Clean assessment contains issues")
        return self


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response field")
        result[key] = value
    return result


def parse_review_response(response: Any) -> ReviewAssessment:
    """Require a completed, non-refusal response containing one JSON object.

    A single Markdown fence is tolerated for compatible providers, but arbitrary
    surrounding prose or embedded example objects are not review results.
    Raises ValueError without exposing response content to logs or callers.
    """
    try:
        choice = response.choices[0]
        if getattr(choice, "finish_reason", "stop") != "stop":
            raise ValueError("Incomplete completion")
        message = choice.message
        if getattr(message, "refusal", None):
            raise ValueError("Refusal")
        content = message.content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", content, flags=re.DOTALL)
        if fenced:
            content = fenced.group(1)
        return ReviewAssessment.model_validate(
            json.loads(content, object_pairs_hook=_unique_object)
        )
    except (AttributeError, IndexError, TypeError, ValueError):
        raise ValueError("Invalid model response") from None
