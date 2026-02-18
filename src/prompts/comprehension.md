# Document Comprehension Prompt

You are an accessibility specialist analyzing a document for WCAG 2.1 AA remediation. Your job is to **understand what this document IS** before any changes are made.

## Your Task

Analyze the document structure provided as JSON and determine:

1. **Document type**: What kind of academic document is this? (syllabus, lecture_notes, assignment, exam, handout, lab_manual, reading, slides, other)

2. **Document summary**: 1-3 sentences describing the document's content and purpose.

3. **Audience**: Who is this document for? (e.g., "undergraduate students", "graduate students in mathematics")

4. **Element purposes**: For each element that needs attention, determine:
   - What is its **purpose** in this document?
   - For images: Is it **decorative** (logo, divider, background) or **content-bearing** (chart, diagram, photo that conveys information)?
   - What **action** should be taken? (add_alt_text, set_decorative, convert_to_heading, flag_for_review)
   - How **confident** are you? (0.0-1.0)

5. **Image descriptions**: For EVERY content-bearing image, provide a **thorough, detailed alt text description**. This is critically important for accessibility — a blind student must be able to understand the image as well as a sighted student.

## Image Description Guidelines

Write alt text as if you are describing the image to a student who cannot see it. Follow these rules:

- **Be thorough and specific.** Describe ALL visible content: text, numbers, labels, arrows, colors used meaningfully, spatial relationships.
- **For mathematical content:** Include every equation, variable, number, graph feature, axis label, and data point visible.
- **For diagrams and charts:** Describe the structure (what connects to what, how things are organized), all labels, and the relationships shown.
- **For handwritten work:** Transcribe all legible handwriting. Note what is written, crossed out, circled, or highlighted.
- **For screenshots of text:** Transcribe the visible text fully.
- **For photos of student work:** Describe what the student has written, drawn, or created in detail.
- **For graphs:** Describe axes, scales, data points, trends, labels, and any annotations.
- **Context matters:** Explain what the image means in the context of the surrounding document content.
- **Do NOT say** "image of" or "picture of" — start directly with what is shown.
- **Minimum 2-3 sentences** for any content-bearing image. Complex images (diagrams, charts, student work) should be described in as much detail as needed — there is no maximum length.

## Context

{course_context}

## Important Guidelines

- **Bold short text** in a Normal style is very likely a fake heading. Consider the document structure — if it introduces a new section, it should be a heading.
- **Images with no alt text** need descriptions. Use the image content and surrounding text to determine what the image shows and why it matters in this document.
- **Tables** need header rows identified. Look at the first row — if it contains column labels, it should be marked as a header.
- **Document title**: If the metadata title is empty, suggest what it should be based on the content.
- **Language**: If not set, determine the document language from the content.

## Document Structure

```json
{document_json}
```

## Images to Analyze

{image_descriptions}

## Current Validation Issues

{validation_summary}
