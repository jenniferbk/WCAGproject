# Table Extraction — Accessibility Remediation

You are extracting a specific table from a scanned academic document page to make it accessible to blind students using screen readers.

## Table to Extract

Caption: {caption}

## Instructions

Look at this page image and find the table with the caption above. Extract its complete structure:

1. **Headers**: The column headers (usually the first row, often bold or visually distinct).
2. **Rows**: Every data row, preserving cell boundaries.

Return JSON with exactly this structure:
```json
{
  "headers": ["Column 1 Header", "Column 2 Header", ...],
  "rows": [
    ["Row 1 Cell 1", "Row 1 Cell 2", ...],
    ["Row 2 Cell 1", "Row 2 Cell 2", ...],
    ...
  ]
}
```

## Rules

- Include ALL rows and ALL columns. Do not truncate.
- For multi-line cell content, join the text with spaces into a single string.
- For empty cells, use an empty string "".
- If the table has no clear header row, set "headers" to an empty array and put all rows in "rows".
- If you cannot find or parse the table, return `{"headers": [], "rows": []}`.
