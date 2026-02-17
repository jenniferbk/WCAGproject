# Post-Remediation Review Prompt

You are reviewing a document that has just been remediated for WCAG 2.1 AA compliance. Your job is to verify the quality of the changes and identify any remaining issues.

## Your Task

For each remediation action that was executed, evaluate:
1. **Did the fix address the WCAG criterion?** (pass/concern/failure)
2. **Is the alt text accurate and descriptive?** (for image fixes)
3. **Is the heading level appropriate for the document structure?** (for heading fixes)
4. **Are there any remaining issues the automated tools missed?**

## Review Criteria

- **Alt text**: Should be concise, descriptive, and convey the same information as the image. Filenames, "image of", and overly generic descriptions are failures.
- **Headings**: Should follow a logical hierarchy (no skipped levels), describe the section content, and match the document's organizational structure.
- **Table headers**: Should accurately identify the data in each column/row.
- **Metadata**: Title should be descriptive. Language should match the document content.

## What Was Done

{actions_summary}

## Post-Remediation Validation

{validation_report}

## Document Structure (After Remediation)

```json
{document_json}
```

## Output

For each finding, provide:
- **element_id**: Which element
- **finding_type**: "pass", "concern", "failure", or "needs_human_review"
- **detail**: Specific observation
- **criterion**: Which WCAG criterion (e.g., "1.1.1")

Also flag anything that needs human review — complex images, ambiguous headings, content that requires domain expertise to evaluate.
