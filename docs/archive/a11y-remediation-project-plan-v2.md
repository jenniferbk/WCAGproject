# WCAG 2.1 AA Document Accessibility Remediation Agent
## Project Plan — Phases 1 & 2 (Agentic Architecture)

**Project:** AI-driven course material accessibility remediation  
**Context:** DOJ Title II ADA compliance deadline April 24, 2026  
**Target standard:** WCAG 2.1 Level AA  
**Dev environment:** Claude Code  
**Deployment target:** Dedicated Mac Mini + OpenClaw (Phase 3, out of scope here)  
**End user experience:** Professor sends file → receives compliant file + report

---

## Design Philosophy

This is NOT a deterministic pipeline. A fixed checklist-runner produces mediocre results 
on real documents because accessibility remediation requires *understanding what the 
document is trying to communicate*.

A chemistry lab handout needs different remediation than a statistics syllabus. A diagram 
of a molecular structure needs different alt text than a photograph of a historical event. 
A bold "Example 3.2" in a math textbook is a sub-heading; the same bold text in an email 
is emphasis. Context matters.

**The agent approach:**
1. **Comprehend** the document holistically (what is it, who is it for, how is it structured)
2. **Strategize** a remediation plan specific to this document
3. **Execute** using tools, making contextual decisions at each step
4. **Self-review** the output as a screen reader user would experience it

The Python code provides **tools the agent calls**. The agent provides **judgment**.

---

## Architecture

```
DOCUMENT IN (docx or pdf, via message/file drop)
    ↓
┌─────────────────────────────────────────┐
│            COMPREHENSION LAYER           │
│                                         │
│  Gemini (multimodal, long context)      │
│  "Look at this document. What is it?    │
│   How is it structured? What's the      │
│   pedagogical intent?"                  │
│                                         │
│  Output: DocumentUnderstanding          │
│  (semantic model, not just parse tree)  │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│          STRATEGY LAYER                  │
│                                         │
│  Claude (reasoning, planning)           │
│  "Given this document and its intent,   │
│   what's the right remediation plan?    │
│   Which images are decorative vs        │
│   informative? What heading hierarchy   │
│   serves this content?"                 │
│                                         │
│  Output: RemediationPlan                │
│  (document-specific, not generic)       │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│          EXECUTION LAYER                 │
│                                         │
│  Agent loop (Claude) with tool calls    │
│  Calls tools as needed, adapts when     │
│  it encounters ambiguity, reasons       │
│  about edge cases in context            │
│                                         │
│  Tools available:                       │
│    parse_document()                     │
│    extract_images()                     │
│    set_alt_text(image_id, text)         │
│    set_heading_level(para_id, level)    │
│    convert_to_real_list(para_ids)       │
│    mark_table_headers(table_id, ...)    │
│    set_metadata(title, language)        │
│    check_contrast(element_id)           │
│    fix_contrast(element_id, strategy)   │
│    improve_link_text(link_id, text)     │
│    rebuild_pdf_as_html(structure)       │
│    render_pdf_ua(html)                  │
│    validate_wcag(document)              │
│    generate_report(findings)            │
└────────────────┬────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│          REVIEW LAYER                    │
│                                         │
│  Claude (self-review)                   │
│  "Read through this remediated document │
│   as if you were using a screen reader. │
│   Does it make sense? Is anything       │
│   confusing, missing, or wrong?"        │
│                                         │
│  Output: final fixes + confidence score │
└────────────────┬────────────────────────┘
                 ↓
OUTPUT: Remediated document + compliance report
        (with honest "needs human review" flags)
```

**Model allocation:**
- **Gemini 2.0 Flash/Pro:** Document comprehension (multimodal, long context, cost-effective for bulk visual analysis). First pass on PDF pages. Bulk image understanding.
- **Claude (Sonnet or Opus via API):** Strategy, execution reasoning, alt text refinement, self-review. The "brain" that makes judgment calls.
- **OpenAI GPT-4o:** Available as fallback or for specific subtasks where it outperforms.

---

## Tool Library (What Claude Code Builds)

These are the deterministic building blocks. Each does one thing reliably. 
The agent decides when, how, and in what order to use them.

### Core Document Tools

```python
# ── parse_document(filepath) ──
# Input: .docx or .pdf file path
# Output: raw DocumentModel (structural parse, no interpretation)
#   - paragraphs with text, style info, font info, position
#   - images with binary data, position, any existing alt text
#   - tables with cell contents, merge info
#   - links with text and URLs
#   - metadata (title, language, author — may be empty)
#   - for PDFs: page images for visual analysis
#
# Implementation: python-docx for .docx, PyMuPDF for .pdf
# This is pure extraction. No judgment. No fixing.


# ── extract_images(filepath) ──
# Input: document file path
# Output: list of {id, binary_data, format, position, 
#          surrounding_text, existing_alt_text}
#
# For .docx: pull from relationships + raw XML for alt text
# For .pdf: PyMuPDF image extraction + page crop for context


# ── get_page_images(filepath) ──  [PDF only]
# Input: PDF file path
# Output: list of page images (PNG bytes) for visual analysis
# Used by comprehension layer to "look at" the document
```

### Remediation Tools

```python
# ── set_alt_text(filepath, image_id, alt_text) ──
# Writes alt text into document XML
# For .docx: modifies <wp:docPr descr="...">
# Returns: success/failure


# ── set_heading_level(filepath, paragraph_id, level) ──  
# Changes a paragraph's style to Heading {level}
# Handles conversion from Normal/bold to real heading
# Returns: success/failure, old_style


# ── convert_to_real_list(filepath, paragraph_ids, list_type) ──
# Converts manual bullet paragraphs (• or - prefix) to proper list style
# list_type: "bullet" or "numbered"
# Returns: number of paragraphs converted


# ── mark_table_headers(filepath, table_id, header_rows, header_cols) ──
# Sets tblHeader property on specified rows
# Marks specified columns as row headers via scope
# Returns: success/failure


# ── set_metadata(filepath, title=None, language=None) ──
# Sets core properties on document
# Returns: what was changed


# ── check_contrast(text_color, background_color, font_size, is_bold) ──
# Pure function: calculates WCAG contrast ratio
# Returns: {ratio, passes_normal, passes_large, minimum_required}


# ── fix_contrast(filepath, element_id, strategy="darken") ──
# Adjusts text or background color to meet minimum ratio
# strategy: "darken" (darken text) or "lighten" (lighten background)
# Returns: old_color, new_color, new_ratio


# ── improve_link_text(filepath, link_id, new_text) ──
# Replaces link display text while preserving URL
# Returns: old_text, new_text
```

### PDF Reconstruction Tools

```python
# ── build_accessible_html(structured_content, options) ──
# Takes agent's structured remediation output
# Generates semantic HTML with:
#   - proper heading hierarchy
#   - alt text on images (base64 embedded or referenced)
#   - table headers with scope attributes
#   - lang attribute
#   - title element
#   - logical reading order
#   - accessible link text
# options: font_family, preserve_layout_level, etc.
# Returns: HTML string


# ── render_pdf_ua(html_string, output_path) ──
# WeasyPrint HTML → PDF/UA-1
# Returns: output file path


# ── convert_html_to_docx(html_string, output_path) ──
# Generates .docx from structured content (alternative output)
# Uses python-docx to build proper document with all a11y features
# Returns: output file path
```

### Validation Tools

```python
# ── validate_wcag(filepath) ──
# Runs full WCAG 2.1 AA audit on output document
# For .docx: custom checks on document model
# For .pdf: veraPDF + custom checks on source HTML
# Returns: {
#   passed: bool,
#   issues: [{criterion, severity, description, location, auto_fixable}],
#   score: float (0-100),
#   summary: str
# }


# ── validate_pdf_ua(filepath) ──
# Runs veraPDF PDF/UA-1 validation
# Returns: {valid: bool, errors: list, warnings: list}


# ── generate_report(audit_results, changes_made, confidence) ──
# Creates human-readable compliance report
# Sections:
#   - Executive summary (for the professor: "X issues found, Y fixed, Z need your review")
#   - Changes made (what the agent did and why)
#   - Remaining issues (what needs human attention, with specific guidance)
#   - WCAG criteria coverage
# Returns: markdown string (renderable as HTML or PDF)
```

---

## Agent Prompts (The Actual Intelligence)

### Comprehension Prompt (Gemini)

```
You are analyzing a university course document to prepare it for 
accessibility remediation under WCAG 2.1 Level AA.

[Document content — pages as images for PDF, or full text + image 
descriptions for .docx]

Provide a comprehensive understanding of this document:

1. DOCUMENT IDENTITY
   - What type of document is this? (syllabus, lab handout, lecture notes, 
     exam, problem set, reading guide, etc.)
   - What course/subject is it for?
   - Who is the intended audience?
   - What is the pedagogical purpose?

2. STRUCTURAL ANALYSIS
   - How is the document organized? (sections, sub-sections, flow)
   - What is the logical heading hierarchy? (not what the formatting says, 
     but what the *content structure* actually is)
   - Are there multi-column layouts, sidebars, callout boxes?
   - What is the intended reading order?

3. VISUAL ELEMENTS
   - For each image/figure/diagram:
     - What is it? (photograph, diagram, chart, graph, equation, logo, 
       decorative element)
     - What role does it play? (illustrates a concept, presents data, 
       provides an example, purely decorative)
     - What information does it convey that the surrounding text does NOT?
     - Suggested alt text approach: brief alt, extended description, 
       or mark as decorative
   
4. TABLES
   - For each table:
     - Is it a data table or a layout table?
     - What are the headers? (column headers, row headers, both, nested)
     - Is the table simple or complex?
     - Can it be simplified without losing meaning?

5. POTENTIAL ISSUES
   - What accessibility problems do you anticipate?
   - What will be straightforward to fix vs. requiring human judgment?
   - Are there any elements that are fundamentally inaccessible and 
     need redesign (not just remediation)?

Return as structured JSON.
```

### Strategy Prompt (Claude)

```
You are an accessibility remediation specialist planning the 
remediation of a university course document.

Here is the document understanding from initial analysis:
{comprehension_output}

Here is the raw structural parse:
{document_model}

Create a specific remediation plan for THIS document. Do not apply 
generic rules — reason about what this document needs.

For each issue you identify:
1. What is the problem?
2. Why is it a problem for accessibility? (cite WCAG 2.1 AA criterion)
3. What is your proposed fix?
4. How confident are you this fix is correct? (high/medium/low)
5. If low confidence, what should be flagged for human review?

Consider:
- A bold "Example 3.2" in a math textbook is a sub-heading.
  The same bold text in an email is emphasis. What is it HERE?
- An image of a graph in a statistics course needs alt text that 
  describes the data trend. The same image in a graphic design course 
  might need alt text describing the visual composition.
- A table used for layout should be linearized. A data table needs 
  proper headers. Which is this?
- Decorative images should have empty alt text, not a description.
  What counts as decorative in this educational context?

Your plan should be ordered by impact: fix the things that matter 
most for a screen reader user first.

Return as a structured remediation plan with tool calls.
```

### Self-Review Prompt (Claude)

```
You just remediated a university course document for WCAG 2.1 AA 
compliance. Now review your own work.

Original document understanding:
{comprehension_output}

Changes made:
{changes_log}

Remediated document content:
{output_content}

Review as if you are:
1. A blind student using a screen reader to study for an exam
2. A low-vision student using high contrast mode
3. A student with cognitive disabilities who relies on clear structure

For each persona, walk through the document and identify:
- Does the reading order make sense?
- Is any important information lost or garbled?
- Are alt texts actually useful, or are they generic garbage?
- Do the headings create a navigable structure?
- Would you understand the tables?
- Is anything confusing or missing?

Rate your overall confidence: 
- HIGH: ready to deliver, minor issues at most
- MEDIUM: functional but some elements need professor review
- LOW: significant issues remain, needs human intervention

List any final fixes to make before delivery.
```

---

## Phase 1: Word Document (.docx) Agent

### What to Build (Claude Code Sessions)

**Session 1: Tool library — document parsing and extraction**

Build the foundation tools. These are stateless, deterministic functions.

```
src/
├── tools/
│   ├── __init__.py
│   ├── docx_parser.py        # parse_document() for .docx
│   ├── image_extractor.py    # extract_images() — raw XML approach for alt text
│   ├── contrast_calc.py      # check_contrast() — pure WCAG math
│   └── utils.py              # shared helpers
├── models/
│   ├── __init__.py
│   └── document_model.py     # data classes for DocumentModel, Image, Table, etc.
├── tests/
│   ├── test_docs/            # professor's sample docs go here
│   └── test_parser.py
└── requirements.txt
```

Key implementation details:

- Alt text extraction via lxml (python-docx doesn't expose it):
  ```python
  # Navigate to wp:docPr in the XML tree
  WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
  for para in doc.paragraphs:
      for drawing in para._element.findall(f'.//{{{WP_NS}}}docPr'):
          alt = drawing.get('descr', '')
          name = drawing.get('name', '')
  ```

- Fake heading detection heuristic:
  ```python
  # A paragraph is a "fake heading" if:
  # - Style is Normal (not a Heading style)
  # - All runs are bold
  # - Font size > document average by 2+ points
  # - Paragraph is short (< ~10 words)
  # - Followed by non-bold paragraph
  # The agent decides whether these are actually headings
  ```

- Image binary extraction:
  ```python
  # Images are in docx part relationships
  for rel in doc.part.rels.values():
      if "image" in rel.reltype:
          img_blob = rel.target_part.blob
          img_content_type = rel.target_part.content_type
  ```

Test with professor's sample docs. Ensure every element is captured.

**Deliverable:** `parse_document("sample.docx")` returns a complete, accurate DocumentModel.


**Session 2: Tool library — remediation tools**

Build the tools that modify documents.

```
src/tools/
├── ... (from Session 1)
├── alt_text_writer.py       # set_alt_text() — write into XML
├── heading_modifier.py      # set_heading_level() — change paragraph style
├── list_converter.py        # convert_to_real_list()
├── table_header_marker.py   # mark_table_headers()
├── metadata_setter.py       # set_metadata()
├── contrast_fixer.py        # fix_contrast()
├── link_improver.py         # improve_link_text()
└── wcag_validator.py        # validate_wcag()
```

Each tool:
- Takes a file path (or in-memory document) + parameters
- Makes ONE specific change
- Returns what it changed (for the changes log)
- Doesn't make judgment calls — that's the agent's job

Test each tool in isolation. Break a document, fix it with the tool, verify.

**Deliverable:** Complete tool library. Each tool works independently.


**Session 3: Agent orchestration — comprehension + strategy**

This is where it gets interesting. Wire up the LLM calls.

```
src/
├── tools/           (from Sessions 1-2)
├── agent/
│   ├── __init__.py
│   ├── comprehension.py    # Gemini call for document understanding
│   ├── strategy.py         # Claude call for remediation planning
│   ├── executor.py         # Agent loop: execute plan using tools
│   ├── reviewer.py         # Claude self-review
│   ├── orchestrator.py     # Top-level: comprehend → strategize → execute → review
│   └── prompts/
│       ├── comprehension.txt
│       ├── strategy.txt
│       ├── execution.txt
│       └── review.txt
├── config/
│   ├── api_keys.env
│   └── settings.yaml       # model selection, temperature, etc.
```

The orchestrator:
```python
def remediate_document(filepath):
    # 1. Parse (deterministic)
    doc_model = parse_document(filepath)
    
    # 2. Comprehend (Gemini — what IS this document?)
    understanding = comprehend(doc_model)
    
    # 3. Strategize (Claude — what does it NEED?)
    plan = strategize(understanding, doc_model)
    
    # 4. Execute (Claude agent loop with tools)
    result, changes_log = execute(plan, filepath)
    
    # 5. Review (Claude — did we do a good job?)
    review = self_review(understanding, changes_log, result)
    
    # 6. Apply any final fixes from review
    if review.final_fixes:
        result = apply_fixes(review.final_fixes, result)
    
    # 7. Validate
    audit = validate_wcag(result)
    
    # 8. Generate report
    report = generate_report(audit, changes_log, review.confidence)
    
    return result, report
```

For the execution layer, the agent gets tool definitions and runs a loop:
```python
# The agent sees its plan and available tools
# It calls tools as needed, adapting when results are unexpected
# 
# Example agent reasoning (this happens in the LLM):
#
# "The strategy says paragraph 14 is a fake heading. Let me look at it...
#  it says 'Important Safety Notes' in bold 14pt. The surrounding content
#  is 12pt normal. This is clearly a section heading in a lab handout.
#  The current hierarchy has an H1 (lab title) and H2s for main sections.
#  This is a subsection under 'Procedure', so H3 is right.
#  → calling set_heading_level(para_id=14, level=3)
#  
#  Next image: it's a photo of lab equipment setup. The comprehension
#  layer identified it as informative. Surrounding text describes the
#  procedure but doesn't describe the physical setup.
#  → calling set_alt_text(image_id=3, 
#      text='Laboratory setup showing a ring stand with a Bunsen burner 
#      positioned beneath a flask secured with a clamp. A thermometer 
#      is inserted through the flask stopper.')
#  
#  This table... the comprehension layer wasn't sure if it's layout or
#  data. Looking at it: it has two columns, 'Reagent' and 'Amount'.
#  That's clearly a data table.
#  → calling mark_table_headers(table_id=2, header_rows=[0])"
```

**Deliverable:** End-to-end .docx remediation. Drop in professor's sample, get back fixed document + report.


**Session 4: Testing, tuning, edge cases**

Run every sample document through the agent. Evaluate:
- Are the alt texts good? (not generic, actually descriptive, contextually appropriate)
- Are the heading decisions correct? (not just structurally valid but semantically right)
- Does the self-review catch real issues?
- What does the agent get wrong? (tune prompts based on failure modes)
- What does it flag for human review that it should have auto-fixed? (tighten confidence)
- What does it auto-fix that it should have flagged? (loosen confidence)

Build a simple evaluation rubric. For each sample doc:
- Manually create a "gold standard" remediation
- Compare agent output to gold standard
- Score by category: alt text quality, heading accuracy, table headers, etc.

This is the session where the tool becomes *good* vs merely *functional*.

**Deliverable:** Tuned agent that produces quality output on real professor documents.


**Session 5: CLI + reporting polish**

```bash
# The interface (for now — OpenClaw wraps this later)
python remediate.py document.docx
python remediate.py document.docx --output-dir ./fixed/
python remediate.py ./course_materials/ --batch
```

Polish the compliance report:
- Professor-friendly executive summary at the top
  ("12 issues found. 10 fixed automatically. 2 need your review.")
- For "needs review" items: specific, actionable guidance
  ("Image on page 4 appears to be a complex chemical diagram. 
   We generated a basic description but recommend you verify it 
   captures the key reaction pathway your students need to understand.")
- Changes log for transparency
- WCAG criteria mapping for compliance documentation

**Deliverable:** Clean, usable CLI tool with professional reporting.

---

## Phase 2: PDF Pipeline (Extract → Comprehend → Regenerate)

### Same agent, different tools.

The agent architecture is identical. The comprehension, strategy, execution, 
and review layers don't change. What changes is the tools.

**Session 6: PDF extraction tools**

```
src/tools/
├── ... (all .docx tools from Phase 1)
├── pdf_parser.py           # parse_document() for .pdf via PyMuPDF
├── pdf_page_renderer.py    # get_page_images() — render pages as PNG for Gemini
├── pdf_image_extractor.py  # extract_images() from PDF
```

PyMuPDF (fitz) gives us:
- Page rendering as images (for Gemini visual analysis)
- Text extraction with font/size/position metadata
- Image extraction
- Table detection (basic)

The comprehension layer gets richer input for PDFs:
- Full page images (Gemini sees the layout visually)
- Extracted text with position info
- Extracted images separately

Gemini is critical here because it can look at a PDF page image and understand:
- Two-column layout
- Sidebar vs. main content
- Caption under a figure
- Header/footer vs. body content
- Reading order across complex layouts

This is exactly the kind of thing a deterministic parser struggles with.

**Deliverable:** `parse_document("sample.pdf")` returns DocumentModel + page images.


**Session 7: PDF reconstruction tools**

```
src/tools/
├── ... (existing tools)
├── html_builder.py         # build_accessible_html()
├── pdf_ua_renderer.py      # render_pdf_ua() via WeasyPrint
├── docx_builder.py         # convert_html_to_docx() — alternative output
```

The HTML builder takes the agent's structured output and generates 
semantic HTML. This is more template than tool — but the agent 
controls the structure that feeds into it.

```python
def build_accessible_html(structured_content):
    """
    Agent has already decided:
    - heading hierarchy
    - which images are decorative vs informative
    - alt text for each image
    - table header configuration
    - reading order
    - document language and title
    
    This function just renders those decisions as valid semantic HTML.
    """
```

WeasyPrint renders to PDF/UA-1:
```python
from weasyprint import HTML

HTML(string=html).write_pdf(output_path, pdf_variant="pdf/ua-1")
```

CSS for the HTML output should attempt to preserve the 
original document's visual character (fonts, spacing, general layout) 
while ensuring accessibility. The agent can make decisions about this too — 
"this was a single-column document, keep it simple" vs. 
"this had a sidebar layout, use CSS grid with proper source order."

**Deliverable:** Agent can take PDF, extract, remediate, and output compliant PDF/UA + report.


**Session 8: PDF validation + dual output**

- veraPDF integration for PDF/UA validation
- Option to output .docx as well as PDF (some professors may want editable format)
- Batch processing for PDF directories
- End-to-end testing with professor's sample PDFs

**Deliverable:** Complete PDF pipeline with validation.


**Session 9: Integration, batch processing, prep for OpenClaw**

- Unified CLI handles both .docx and .pdf
- Batch processing with summary report across all documents
- Structured output format that OpenClaw skill can wrap easily
- Configuration file for preferences (preferred output format, 
  level of automation vs. flagging for review, API model selection)

```bash
# Single file
python remediate.py syllabus.docx

# Batch
python remediate.py ./fall2026_materials/ --output-dir ./accessible/

# PDF with both outputs  
python remediate.py lecture_notes.pdf --format both

# Conservative mode (flags more for human review)
python remediate.py exam.docx --confidence-threshold high
```

**Deliverable:** Production-ready tool, packaged for Phase 3 OpenClaw integration.

---

## Cost Estimates Per Document

API costs scale with document complexity, not just page count.

| Scenario | Comprehend | Strategize | Execute | Review | Total est. |
|----------|-----------|-----------|---------|--------|------------|
| Simple 5-page .docx, 2 images | $0.01 | $0.02 | $0.05 | $0.02 | ~$0.10 |
| 15-page .docx, 10 images, tables | $0.03 | $0.04 | $0.20 | $0.04 | ~$0.31 |
| 10-page PDF, mixed content | $0.08 | $0.05 | $0.15 | $0.04 | ~$0.32 |
| 50-page PDF, image-heavy | $0.25 | $0.10 | $0.60 | $0.08 | ~$1.03 |

Full semester of materials (80 documents): estimated $15-40 total.  
vs. manual remediation at $3-4/page: easily $5,000-10,000+.

---

## What Success Looks Like

**For the professor:**  
"I sent my 80 course documents to the bot. The next morning I had 80 
compliant versions and a report telling me which 6 images needed me to 
double-check the descriptions. I spent 20 minutes on it. Done."

**For compliance:**  
Each document comes with a WCAG 2.1 AA compliance report documenting 
what was checked, what was fixed, and what was reviewed. Auditable 
paper trail.

**For your research (Phase 4):**  
An evaluation framework comparing AI remediation quality against human 
expert remediation. How good is the alt text? How accurate are the 
heading decisions? Where does the agent fail? What's the effective 
compliance rate? Publication-ready study at the intersection of AI and 
accessibility in education.

---

## Honest Caveats (unchanged — these are real regardless of architecture)

1. **Alt text quality requires human review.** The agentic approach produces 
   much better alt text than a deterministic pipeline (because it understands 
   the pedagogical context), but "better" ≠ "perfect." Complex discipline-specific 
   visuals need instructor verification.

2. **Complex tables remain hard.** Merged cells, nested headers, and 
   tables-as-layout will produce imperfect results.

3. **PDF visual fidelity.** Regenerated PDFs will not look identical to 
   originals. Content and accessibility will be correct; aesthetics are best-effort.

4. **Scanned/image-only PDFs.** Require OCR preprocessing. Gemini can handle 
   this to a degree. Out of scope for initial build.

5. **Mathematical content.** LaTeX, MathType, equation images need special 
   handling. Flag for review initially; MathML conversion is a future enhancement.

6. **This tool reduces remediation effort by ~80%, not 100%.** But 80% of an 
   $8,000 problem is significant.
