"""Phase 3: Execute remediation actions on the document.

Takes the remediation strategy and applies each action to the document
using the deterministic tools. Tracks success/failure of each action.

This is NOT an LLM agent loop — it's a deterministic executor that
runs the planned actions in order. The intelligence is in the strategy
phase; execution just applies the plan.
"""

from __future__ import annotations

import copy
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document

from src.models.pipeline import RemediationAction, RemediationStrategy
from src.tools.alt_text import set_alt_text, set_decorative
from src.tools.headings import set_heading_level
from src.tools.metadata import set_language, set_title
from src.tools.tables import mark_header_rows

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of executing the remediation strategy."""
    success: bool
    output_path: str = ""
    actions_executed: int = 0
    actions_failed: int = 0
    actions_skipped: int = 0
    updated_actions: list[dict] = field(default_factory=list)
    error: str = ""


def execute(
    strategy: RemediationStrategy,
    input_path: str,
    output_dir: str = "",
) -> ExecutionResult:
    """Execute all planned remediation actions on a document.

    Args:
        strategy: The remediation strategy with planned actions.
        input_path: Path to the input .docx file.
        output_dir: Directory for output. Defaults to same directory as input.

    Returns:
        ExecutionResult with success/failure details.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        return ExecutionResult(
            success=False,
            error=f"Input file not found: {input_path}",
        )

    # Determine output path
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = input_file.parent

    output_path = out_dir / f"{input_file.stem}_remediated{input_file.suffix}"

    # Copy input to output location
    shutil.copy2(input_path, output_path)

    # Open the output copy for modification
    try:
        doc = Document(str(output_path))
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to open document: {e}",
        )

    executed = 0
    failed = 0
    skipped = 0
    updated_actions = []

    for action in strategy.actions:
        if action.status == "skipped":
            skipped += 1
            updated_actions.append(_action_dict(action, "skipped", "Pre-skipped"))
            continue

        result = _execute_action(doc, action)
        updated_actions.append(result)

        if result["status"] == "executed":
            executed += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    # Save the modified document
    try:
        doc.save(str(output_path))
        logger.info(
            "Document saved: %s (executed=%d, failed=%d, skipped=%d)",
            output_path, executed, failed, skipped,
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"Failed to save document: {e}",
            actions_executed=executed,
            actions_failed=failed,
            actions_skipped=skipped,
            updated_actions=updated_actions,
        )

    return ExecutionResult(
        success=True,
        output_path=str(output_path),
        actions_executed=executed,
        actions_failed=failed,
        actions_skipped=skipped,
        updated_actions=updated_actions,
    )


def _execute_action(doc: Document, action: RemediationAction) -> dict:
    """Execute a single remediation action."""
    try:
        action_type = action.action_type
        params = action.parameters

        if action_type == "set_alt_text":
            para_idx = params.get("paragraph_index")
            draw_idx = params.get("drawing_index", 0)
            alt = params.get("alt_text", "")

            if para_idx is None:
                return _action_dict(action, "failed", "Missing paragraph_index")
            if not alt:
                return _action_dict(action, "failed", "Empty alt text")

            result = set_alt_text(doc, para_idx, draw_idx, alt)
            if result.success:
                return _action_dict(action, "executed", f"Set alt text: {alt[:80]}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_decorative":
            para_idx = params.get("paragraph_index")
            draw_idx = params.get("drawing_index", 0)

            if para_idx is None:
                return _action_dict(action, "failed", "Missing paragraph_index")

            result = set_decorative(doc, para_idx, draw_idx)
            if result.success:
                return _action_dict(action, "executed", "Marked as decorative")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_heading_level":
            para_idx = params.get("paragraph_index")
            level = params.get("level")

            if para_idx is None:
                return _action_dict(action, "failed", "Missing paragraph_index")
            if level is None:
                return _action_dict(action, "failed", "Missing level")

            result = set_heading_level(doc, para_idx, level)
            if result.success:
                return _action_dict(action, "executed", f"Set to Heading {level}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "mark_header_rows":
            tbl_idx = params.get("table_index")
            count = params.get("header_count", 1)

            if tbl_idx is None:
                return _action_dict(action, "failed", "Missing table_index")

            result = mark_header_rows(doc, tbl_idx, count)
            if result.success:
                return _action_dict(action, "executed", f"Marked {count} header row(s)")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_title":
            title = params.get("title", "")
            if not title:
                return _action_dict(action, "failed", "Empty title")

            result = set_title(doc, title)
            if result.success:
                return _action_dict(action, "executed", f"Set title: {title}")
            return _action_dict(action, "failed", result.error)

        elif action_type == "set_language":
            lang = params.get("language", "")
            if not lang:
                return _action_dict(action, "failed", "Empty language")

            result = set_language(doc, lang)
            if result.success:
                return _action_dict(action, "executed", f"Set language: {lang}")
            return _action_dict(action, "failed", result.error)

        else:
            return _action_dict(action, "skipped", f"Unknown action type: {action_type}")

    except Exception as e:
        logger.exception("Action execution failed: %s", action.action_type)
        return _action_dict(action, "failed", f"Exception: {e}")


def _action_dict(action: RemediationAction, status: str, detail: str) -> dict:
    """Build a result dict for an action."""
    return {
        "element_id": action.element_id,
        "action_type": action.action_type,
        "parameters": action.parameters,
        "rationale": action.rationale,
        "status": status,
        "result_detail": detail,
    }
