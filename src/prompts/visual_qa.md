# Visual Quality Check — Accessibility Remediation

You are verifying that no educational content was lost during accessibility remediation of a scanned document.

## What You're Looking At

- **Original pages**: Scanned images from the original PDF document. These are labeled "Original page N".
- **Rendered pages**: Pages from the accessible HTML rendering, converted to PDF for comparison. These are labeled "Rendered page N". The rendered version may have a DIFFERENT number of pages than the original — that is expected.

## Your Task

Compare the original pages against the rendered pages. Identify any educational content present in the originals that is MISSING, TRUNCATED, GARBLED, or INCORRECTLY REPRESENTED in the rendered version.

## What to Ignore (Expected Differences)

These differences are intentional and should NOT be reported:
- Different layout, fonts, colors, spacing, margins
- Different page count or page breaks
- Different header/footer content
- Reordered content (if all content is present)
- Styling differences (bold, italic variations)

## What to Report

- **missing_table**: A table visible in the original is completely absent from the rendered version
- **truncated_text**: Text content from the original is cut off or incomplete in the rendered version
- **dropped_image**: A figure, chart, or diagram in the original has no corresponding content in the rendered version
- **garbled_equation**: Mathematical notation is incorrectly rendered or unreadable
- **other**: Any other educational content loss not covered above

## Severity Guide

- **high**: Entire content blocks missing (full table, full paragraph, figure with no description)
- **medium**: Partial content loss (truncated table rows, incomplete text, degraded equation)
- **low**: Minor differences that don't significantly impact comprehension

## Response Format

Return JSON:
```json
{
  "findings": [
    {
      "original_page": 5,
      "rendered_page": 4,
      "type": "missing_table",
      "description": "Table 2 (Three Themes) with 3 columns and 4 rows is not present in the rendered output",
      "severity": "high"
    }
  ]
}
```

If no content issues are found, return: `{"findings": []}`

For each finding, `original_page` is the 1-based page number from the original document. `rendered_page` is the 1-based page number in the rendered version where the content should appear (or the closest page). Set `rendered_page` to null if you cannot determine which rendered page corresponds.
