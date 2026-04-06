# LaTeX Enhancements: TikZ AI Descriptions + Per-Equation Math Review

## Problem

Two gaps in the current LaTeX accessibility pipeline:

1. **TikZ diagrams** get generic placeholder descriptions ("Finite automaton diagram with 5 states and 3 transitions") instead of meaningful alt text. A blind student can't understand the diagram structure from this.

2. **No math verification for faculty.** When a LaTeX document with 90+ equations is remediated, faculty have no way to review whether the generated descriptions are correct without reading the full accessible HTML output.

## Feature 1: TikZ AI Descriptions

### Current Behavior

`latex_parser.py` detects TikZ blocks via `_is_tikz_content()` and generates a placeholder via `_tikz_placeholder()` that counts nodes and edges. The TikZ source is discarded.

### New Behavior

1. Parser detects TikZ block (existing logic unchanged)
2. Raw TikZ source stored on `MathInfo` as new field `tikz_source: str = ""`
3. During execution phase, executor sends TikZ source to Claude Haiku 4.5 for a thorough structural description
4. Claude prompt: describe the diagram thoroughly for a blind student — all nodes, edges, labels, positions, and relationships. Enough detail to understand the diagram and answer homework questions about it.
5. Description stored on `MathInfo.description`
6. HTML builder renders the existing placeholder text with the Claude-generated description as the accessible alt text
7. If Claude fails, fall back to `_tikz_placeholder()` output

### Model Choice

Claude Haiku 4.5 — the task is descriptive (translate visual structure to text), not reasoning-heavy. Cost: ~$0.001 per TikZ block. Most documents have 0-3 TikZ diagrams.

### Changes Required

| File | Change |
|------|--------|
| `src/models/document.py` | Add `tikz_source: str = ""` to `MathInfo` |
| `src/tools/latex_parser.py` | Store TikZ source on `MathInfo` instead of discarding it |
| `src/agent/executor.py` | New action type `describe_tikz` — sends TikZ source to Claude Haiku |
| `src/agent/strategy.py` | Generate `describe_tikz` actions for MathInfo objects with `tikz_source` |
| `src/prompts/tikz_description.md` | New prompt for Claude TikZ description |

### Prompt Design

```
You are describing a TikZ diagram for a blind student who needs to understand
it thoroughly to complete their coursework. The student cannot see the diagram
and relies entirely on your description.

Describe the diagram's complete structure:
- All nodes/vertices with their labels and any special properties (start state, accepting state, shape, color)
- All edges/arrows with their labels, direction, and what they connect
- Spatial layout if relevant (left-to-right, circular, tree structure)
- Any annotations, captions, or mathematical labels
- The overall purpose or type of diagram (state machine, tree, graph, circuit, etc.)

Be thorough but organized. Use a logical order (e.g., enumerate nodes first, then edges).

TikZ source:
{tikz_source}
```

### Fallback

If Claude API is unavailable or fails, use the existing `_tikz_placeholder()` output. The placeholder is better than nothing.

## Feature 2: Per-Equation Math Review Section

### Purpose

A dedicated section in the remediation report where faculty can review every equation's rendering, LaTeX source, and generated description. This is the quality gate — professors verify that descriptions are accurate before distributing to students.

### Location in Report

New collapsible section in `report_generator.py`, positioned between the human-readable summary ("What We Did" / "What Needs Attention") and the WCAG technical details. Title: **"Math Review"**.

Collapsed by default. Header shows total count: "Math Review (96 equations)".

### Content

Every `MathInfo` object in the document is shown. No filtering, no cap.

Grouped by display type:
1. **Block equations** first (display math — numbered, standalone, most important)
2. **Inline equations** second

Each equation shown as a row:

| Column | Content |
|--------|---------|
| Rendered | SVG from `render_mathml_to_svg()` (same rendering as accessible HTML) |
| LaTeX Source | Raw LaTeX in `<code>` block for faculty reference |
| Description | The generated accessibility description |
| Status | "Auto" (trivial/deterministic), "AI-generated" (Claude), "Missing" (no description) |

### Count Header

"Math Review (96 equations: 42 block, 54 inline)"

### Status Logic

- `MathInfo.description` is empty → **"Missing"** (red)
- `classify_math(math)` returns `"trivial"` → **"Auto"** (green)
- Otherwise → **"AI-generated"** (yellow) — faculty should verify these

### Changes Required

| File | Change |
|------|--------|
| `src/tools/report_generator.py` | New `_build_math_review_section()` function |
| `src/tools/math_renderer.py` | No changes — reuse existing `render_mathml_to_svg()` |
| `src/tools/math_descriptions.py` | No changes — reuse existing `classify_math()` |

### HTML Structure

```html
<details>
  <summary>Math Review (96 equations: 42 block, 54 inline)</summary>

  <h3>Block Equations</h3>
  <table class="math-review">
    <thead>
      <tr>
        <th>Rendered</th>
        <th>LaTeX Source</th>
        <th>Description</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>[SVG]</td>
        <td><code>x^2 + y^2 = r^2</code></td>
        <td>x squared plus y squared equals r squared</td>
        <td><span class="status-auto">Auto</span></td>
      </tr>
      ...
    </tbody>
  </table>

  <h3>Inline Equations</h3>
  <table class="math-review">
    ...
  </table>
</details>
```

### CSS

Minimal additions to report CSS:
- `.math-review td` — vertical-align top, padding
- `.status-auto` — green badge
- `.status-ai` — yellow badge
- `.status-missing` — red badge
- SVG column fixed width to prevent layout blowout

## Testing

### TikZ Descriptions
- Unit test: `_tikz_placeholder()` fallback when Claude unavailable
- Unit test: `describe_tikz` action creates description from TikZ source
- Integration test: homework.tex (has TikZ automaton) → description contains node/edge details

### Math Review Section
- Unit test: `_build_math_review_section()` with mixed trivial/complex/missing equations
- Unit test: correct grouping (block before inline)
- Unit test: status badges correct (auto/AI-generated/missing)
- Unit test: empty math list produces no section
- Integration test: homework.tex report contains "Math Review" section

## Cost Impact

- TikZ descriptions: ~$0.001/diagram (Haiku), typically 0-3 per document = negligible
- Math review section: zero API cost (reuses already-generated descriptions and SVGs)
