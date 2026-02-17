# Remediation Strategy Prompt

You are planning WCAG 2.1 AA accessibility remediation for a university course document. Based on the comprehension analysis, create a specific remediation plan.

## Your Task

Create an ordered list of remediation actions. Each action must specify:
- **element_id**: Which element to fix (e.g., "img_0", "p_3", "tbl_0")
- **action_type**: What to do (see available actions below)
- **parameters**: Specific values for the action
- **rationale**: Why this fix is needed (cite the WCAG criterion)

## Available Actions

| action_type | parameters | WCAG |
|-------------|-----------|------|
| `set_alt_text` | `{"paragraph_index": int, "drawing_index": int, "alt_text": "..."}` | 1.1.1 |
| `set_decorative` | `{"paragraph_index": int, "drawing_index": int}` | 1.1.1 |
| `set_heading_level` | `{"paragraph_index": int, "level": int}` | 1.3.1, 2.4.6 |
| `mark_header_rows` | `{"table_index": int, "header_count": int}` | 1.3.1 |
| `set_title` | `{"title": "..."}` | 2.4.2 |
| `set_language` | `{"language": "..."}` | 3.1.1 |

## Ordering Rules

1. **Metadata first** (title, language) — these are quick wins
2. **Structure next** (headings, table headers) — these affect navigation
3. **Content last** (alt text) — these are the most work
4. If unsure about an element, add it to `items_for_human_review`

## Comprehension Analysis

```json
{comprehension_json}
```

## Document Structure

```json
{document_json}
```

## Pre-Remediation Validation

{validation_report}
