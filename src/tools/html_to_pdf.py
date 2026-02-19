"""Python wrapper for the OpenHTMLtoPDF Java CLI.

Converts semantic HTML to tagged PDF/UA using OpenHTMLtoPDF.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default JAR path relative to project root
_DEFAULT_JAR = Path(__file__).parent.parent.parent / "java" / "html-to-pdf" / "build" / "libs" / "html-to-pdf-all.jar"

# JAVA_HOME for Homebrew OpenJDK 17
_JAVA_HOME = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"


@dataclass
class ConversionResult:
    """Result from the HTML-to-PDF converter."""
    success: bool
    output_path: str = ""
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def html_to_tagged_pdf(
    html_path: str,
    output_path: str,
    jar_path: str | None = None,
) -> ConversionResult:
    """Convert an HTML file to tagged PDF/UA using OpenHTMLtoPDF.

    Args:
        html_path: Path to the input HTML file.
        output_path: Path for the output PDF.
        jar_path: Path to the fat JAR. Defaults to built location.

    Returns:
        ConversionResult with success/failure and details.
    """
    jar = Path(jar_path) if jar_path else _DEFAULT_JAR
    if not jar.exists():
        return ConversionResult(
            success=False,
            errors=[f"html-to-pdf JAR not found: {jar}. Run 'gradle fatJar' in java/html-to-pdf/"],
        )

    if not Path(html_path).exists():
        return ConversionResult(
            success=False,
            errors=[f"HTML file not found: {html_path}"],
        )

    java_bin = _find_java()
    if not java_bin:
        return ConversionResult(
            success=False,
            errors=["Java not found. Install Java 17+: brew install openjdk@17"],
        )

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [java_bin, "-jar", str(jar), html_path, output_path]
    logger.info("Running html-to-pdf: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        stdout = result.stdout.strip()
        if not stdout:
            stderr = result.stderr.strip()
            return ConversionResult(
                success=False,
                errors=[f"No output from html-to-pdf. stderr: {stderr[:500]}"],
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            return ConversionResult(
                success=False,
                errors=[f"Invalid JSON from converter: {e}. stdout: {stdout[:500]}"],
            )

        return ConversionResult(
            success=data.get("success", False),
            output_path=data.get("output_path", ""),
            changes=data.get("changes", []),
            warnings=data.get("warnings", []),
            errors=data.get("errors", []),
        )

    except subprocess.TimeoutExpired:
        return ConversionResult(success=False, errors=["html-to-pdf timed out (120s)"])
    except Exception as e:
        return ConversionResult(success=False, errors=[f"Failed to run html-to-pdf: {e}"])


def _find_java() -> str | None:
    """Find the Java executable."""
    java_path = Path(_JAVA_HOME) / "bin" / "java"
    if java_path.exists():
        return str(java_path)

    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        java_path = Path(java_home) / "bin" / "java"
        if java_path.exists():
            return str(java_path)

    try:
        result = subprocess.run(
            ["which", "java"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return None
