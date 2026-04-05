# Math Equation Descriptions for Accessibility

You are generating natural language descriptions of mathematical equations for blind students using screen readers. These descriptions must be mathematically precise and complete — a student should be able to understand and work with the equation from your description alone.

## Context
- Course: {course_context}
- Document type: {document_type}

## Requirements

1. **Be mathematically precise.** "x equals negative b plus or minus the square root of b squared minus 4ac, all divided by 2a" — not "the quadratic formula" (that's a label, not a description).

2. **Describe structure.** For fractions, say "numerator ... divided by denominator ...". For integrals, say "the integral from [lower] to [upper] of [integrand] with respect to [variable]".

3. **Read nested expressions inside-out.** "the square root of the quantity b squared minus 4ac" — the "quantity" cue tells the listener where grouping begins.

4. **Include equation numbers.** If the equation is numbered, start with "Equation N:" so students can follow cross-references in the text.

5. **Use context.** If the surrounding text says "the Laplace transform is defined as", your description can reference that: "Equation 1, the definition of the Laplace transform: ..."

## Equations to describe

For each equation below, provide a JSON array of objects with "id" and "description" fields.

{equations}

Respond with ONLY the JSON array. Example:
[
  {"id": "math_5", "description": "Equation 1: the Laplace transform of f of t, defined as the integral from 0 to infinity of f of t times e to the negative s t, with respect to t"},
  {"id": "math_6", "description": "the convolution of f and g at time t, defined as the integral from 0 to t of f of tau times g of t minus tau, with respect to tau"}
]
