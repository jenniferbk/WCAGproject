"""Pipeline-level models for the remediation workflow.

These models represent the flow of data through the four-phase pipeline:
RemediationRequest → ComprehensionResult → RemediationStrategy → RemediationResult

DocumentModel (in document.py) represents what's IN the document.
These models represent the submission, decisions, and outcomes wrapping around it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── API pricing (per million tokens) ────────────────────────────
# Update these when model pricing changes. Single source of truth.
_PRICING = {
    "gemini": {"input": 0.15, "output": 0.60},    # Gemini 2.5 Flash
    "claude": {"input": 3.0, "output": 15.0},      # Claude Sonnet 4.5
}


def estimate_usage_cost(usage: ApiUsage) -> float:
    """Estimate USD cost for a single API usage record."""
    model_lower = usage.model.lower()
    for key, rates in _PRICING.items():
        if key in model_lower:
            return (
                usage.input_tokens * rates["input"] / 1_000_000
                + usage.output_tokens * rates["output"] / 1_000_000
            )
    return 0.0


class ApiUsage(BaseModel, frozen=True):
    """Token usage from a single API call."""
    phase: str = ""              # "comprehension", "strategy", "review"
    model: str = ""              # e.g. "gemini-2.5-flash", "claude-sonnet-4-5-20250929"
    input_tokens: int = 0
    output_tokens: int = 0


class CostSummary(BaseModel, frozen=True):
    """Aggregated API cost data for a pipeline run."""
    usage_records: list[ApiUsage] = Field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.usage_records)

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.usage_records)

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate total cost based on published API pricing."""
        return round(sum(estimate_usage_cost(u) for u in self.usage_records), 4)


class CourseContext(BaseModel, frozen=True):
    """Context about the course a document belongs to.

    Faculty provide this when submitting. It shapes how the comprehension
    and strategy phases interpret document elements — e.g., bold "Example 3.2"
    in a math course is likely a sub-heading, not emphasis.
    """
    course_name: str = ""        # e.g. "MATH 201: Calculus II"
    department: str = ""         # e.g. "Mathematics"
    description: str = ""        # any additional context the faculty provides


class DocumentType(str, Enum):
    """High-level document type as identified by comprehension phase."""
    SYLLABUS = "syllabus"
    LECTURE_NOTES = "lecture_notes"
    ASSIGNMENT = "assignment"
    EXAM = "exam"
    HANDOUT = "handout"
    LAB_MANUAL = "lab_manual"
    READING = "reading"
    SLIDES = "slides"
    OTHER = "other"


class RemediationRequest(BaseModel, frozen=True):
    """Input to the remediation pipeline.

    Represents a faculty submission: a document plus context about
    where it's used. Created from email parsing or CLI input.
    """
    document_path: str
    course_context: CourseContext = Field(default_factory=CourseContext)
    submitter_email: str = ""
    submitted_at: str = ""       # ISO 8601
    output_dir: str = ""         # where to write results
    output_format: str = "same"  # "same", "pdf", "both"


class ElementPurpose(BaseModel, frozen=True):
    """The comprehension phase's judgment about a single element's purpose."""
    element_id: str              # p_0, img_0, tbl_0, etc.
    purpose: str                 # free-text description of what this element does
    is_decorative: bool = False  # for images: decorative vs. content-bearing
    suggested_action: str = ""   # e.g. "add_alt_text", "convert_to_heading", "flag_for_review"
    confidence: float = 1.0      # 0-1, how sure the model is


class ComprehensionResult(BaseModel, frozen=True):
    """Output of the comprehension phase.

    Combines Gemini's holistic document understanding with the
    validator's compliance check results.
    """
    document_type: DocumentType = DocumentType.OTHER
    document_summary: str = ""   # 1-3 sentence summary of the document
    audience: str = ""           # e.g. "undergraduate students"
    suggested_title: str = ""    # Gemini's suggested document title
    suggested_language: str = "" # Gemini's detected document language (e.g. "en")
    element_purposes: list[ElementPurpose] = Field(default_factory=list)
    image_descriptions: dict[str, str] = Field(default_factory=dict)  # img_id -> detailed description from Gemini vision
    validation_summary: str = "" # summary of pre-remediation validation
    validation_issues_count: int = 0
    raw_validation_report: str = ""  # full validator output for reference
    scanned_page_numbers: list[int] = Field(default_factory=list)  # PDF pages that are scanned (image-only)
    api_usage: list[ApiUsage] = Field(default_factory=list)


class RemediationAction(BaseModel, frozen=True):
    """A single planned or executed remediation action."""
    element_id: str              # what element this acts on
    action_type: str             # e.g. "set_alt_text", "set_heading_level", "fix_contrast"
    parameters: dict = Field(default_factory=dict)  # tool-specific params
    rationale: str = ""          # why this action was chosen
    status: str = "planned"      # "planned", "executed", "failed", "skipped"
    result_detail: str = ""      # what happened when executed


class RemediationStrategy(BaseModel, frozen=True):
    """Output of the strategy phase.

    Claude's plan for how to remediate this specific document,
    informed by comprehension results and course context.
    """
    actions: list[RemediationAction] = Field(default_factory=list)
    items_for_human_review: list[str] = Field(default_factory=list)
    strategy_summary: str = ""   # high-level description of the approach
    api_usage: list[ApiUsage] = Field(default_factory=list)


class ReviewFinding(BaseModel, frozen=True):
    """A single finding from the review phase."""
    element_id: str = ""
    finding_type: str = ""       # "pass", "concern", "failure", "needs_human_review"
    detail: str = ""
    criterion: str = ""          # WCAG criterion, e.g. "1.1.1"


class RemediationResult(BaseModel, frozen=True):
    """Final output of the entire pipeline.

    Contains the paths to output files, the compliance report,
    and anything flagged for human review.
    """
    success: bool = False
    input_path: str = ""
    output_path: str = ""        # path to remediated document
    companion_output_path: str = ""  # path to companion HTML (PDF only)
    report_path: str = ""        # path to compliance report

    # Pipeline artifacts
    comprehension: ComprehensionResult = Field(default_factory=ComprehensionResult)
    strategy: RemediationStrategy = Field(default_factory=RemediationStrategy)
    review_findings: list[ReviewFinding] = Field(default_factory=list)

    # Before/after comparison
    pre_validation_summary: str = ""
    post_validation_summary: str = ""
    issues_before: int = 0
    issues_after: int = 0
    issues_fixed: int = 0

    # Human review items
    items_for_human_review: list[str] = Field(default_factory=list)

    error: str = ""
    processing_time_seconds: float = 0.0
    cost_summary: CostSummary = Field(default_factory=CostSummary)
