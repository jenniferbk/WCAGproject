"""Tests for pipeline-level data models."""

import pytest
from pydantic import ValidationError

from src.models.pipeline import (
    ComprehensionResult,
    CourseContext,
    DocumentType,
    ElementPurpose,
    RemediationAction,
    RemediationRequest,
    RemediationResult,
    RemediationStrategy,
    ReviewFinding,
)


class TestCourseContext:
    def test_create_empty(self):
        ctx = CourseContext()
        assert ctx.course_name == ""
        assert ctx.department == ""

    def test_create_full(self):
        ctx = CourseContext(
            course_name="MATH 201: Calculus II",
            department="Mathematics",
            description="Second semester calculus for STEM majors",
        )
        assert ctx.course_name == "MATH 201: Calculus II"

    def test_immutable(self):
        ctx = CourseContext(course_name="Test")
        with pytest.raises(ValidationError):
            ctx.course_name = "Changed"


class TestRemediationRequest:
    def test_create_minimal(self):
        req = RemediationRequest(document_path="/path/to/doc.docx")
        assert req.document_path == "/path/to/doc.docx"
        assert req.course_context.course_name == ""
        assert req.output_format == "same"

    def test_create_with_context(self):
        req = RemediationRequest(
            document_path="/path/to/syllabus.docx",
            course_context=CourseContext(
                course_name="BIO 101",
                department="Biology",
            ),
            submitter_email="prof@university.edu",
        )
        assert req.course_context.course_name == "BIO 101"
        assert req.submitter_email == "prof@university.edu"

    def test_serialization(self):
        req = RemediationRequest(
            document_path="/doc.docx",
            course_context=CourseContext(course_name="ART 100"),
        )
        json_str = req.model_dump_json()
        restored = RemediationRequest.model_validate_json(json_str)
        assert restored.course_context.course_name == "ART 100"


class TestComprehensionResult:
    def test_create(self):
        result = ComprehensionResult(
            document_type=DocumentType.SYLLABUS,
            document_summary="A course syllabus for introductory biology.",
            audience="undergraduate students",
            element_purposes=[
                ElementPurpose(
                    element_id="img_0",
                    purpose="Decorative university logo",
                    is_decorative=True,
                    suggested_action="set_decorative",
                    confidence=0.95,
                ),
            ],
            validation_issues_count=3,
        )
        assert result.document_type == DocumentType.SYLLABUS
        assert len(result.element_purposes) == 1
        assert result.element_purposes[0].is_decorative

    def test_document_types(self):
        for dt in DocumentType:
            result = ComprehensionResult(document_type=dt)
            assert result.document_type == dt


class TestRemediationStrategy:
    def test_create_with_actions(self):
        strategy = RemediationStrategy(
            actions=[
                RemediationAction(
                    element_id="img_0",
                    action_type="set_alt_text",
                    parameters={"alt_text": "Graph showing population growth"},
                    rationale="Image contains data that needs description",
                ),
                RemediationAction(
                    element_id="p_3",
                    action_type="set_heading_level",
                    parameters={"level": 2},
                    rationale="Bold 16pt text is a section header",
                ),
            ],
            items_for_human_review=["Complex table in tbl_2 has merged cells"],
            strategy_summary="Standard syllabus remediation with 2 fixes needed",
        )
        assert len(strategy.actions) == 2
        assert strategy.actions[0].status == "planned"


class TestRemediationResult:
    def test_create_success(self):
        result = RemediationResult(
            success=True,
            input_path="/in/doc.docx",
            output_path="/out/doc.docx",
            issues_before=5,
            issues_after=1,
            issues_fixed=4,
        )
        assert result.issues_fixed == 4

    def test_create_failure(self):
        result = RemediationResult(
            success=False,
            error="Gemini API timeout",
        )
        assert not result.success
        assert "timeout" in result.error

    def test_full_pipeline_roundtrip(self):
        result = RemediationResult(
            success=True,
            input_path="/doc.docx",
            output_path="/out/doc.docx",
            comprehension=ComprehensionResult(
                document_type=DocumentType.LECTURE_NOTES,
                document_summary="Lecture on photosynthesis",
            ),
            strategy=RemediationStrategy(
                actions=[
                    RemediationAction(
                        element_id="img_0",
                        action_type="set_alt_text",
                        parameters={"alt_text": "Diagram of chloroplast"},
                        status="executed",
                    ),
                ],
            ),
            review_findings=[
                ReviewFinding(
                    element_id="img_0",
                    finding_type="pass",
                    detail="Alt text adequately describes the diagram",
                    criterion="1.1.1",
                ),
            ],
            issues_before=3,
            issues_after=0,
            issues_fixed=3,
        )
        json_str = result.model_dump_json()
        restored = RemediationResult.model_validate_json(json_str)
        assert restored.comprehension.document_type == DocumentType.LECTURE_NOTES
        assert restored.strategy.actions[0].status == "executed"
        assert restored.review_findings[0].finding_type == "pass"
