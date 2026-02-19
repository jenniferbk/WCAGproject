"""Lenient JSON parser for LLM output.

Handles common issues: trailing commas, markdown fences, single quotes,
control characters, and embedded JSON blocks.
"""

import json
import re


def parse_json_lenient(text: str) -> dict:
    """Parse JSON with fallback repairs for common LLM output issues.

    Attempts strict parse first, then progressively repairs:
    1. Strip markdown code fences
    2. Remove trailing commas before } or ]
    3. Replace single-quoted strings with double-quoted
    4. Remove control characters
    5. Extract first { ... } block as last resort

    Raises json.JSONDecodeError if all repair attempts fail.
    """
    # Try strict parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:])
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    # Remove trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)

    # Replace single-quoted strings with double-quoted
    # (only when they look like JSON keys/values, not contractions)
    cleaned = re.sub(r"(?<=[\[{,:])\s*'([^']*?)'\s*(?=[,}\]:])", r' "\1"', cleaned)
    cleaned = re.sub(r"^\s*'([^']*?)'\s*(?=:)", r'"\1"', cleaned, flags=re.MULTILINE)

    # Remove control characters that break JSON
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Last resort: find the first { ... } block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        block = match.group(0)
        # Re-apply trailing comma fix
        block = re.sub(r",\s*([}\]])", r"\1", block)
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(
        "Could not parse response as JSON after repair attempts",
        text, 0,
    )
