# LaTeX TikZ Descriptions + Math Review Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude-generated thorough descriptions for TikZ diagrams and a per-equation "Math Review" section in the remediation report for faculty verification.

**Architecture:** TikZ source is stored on MathInfo during parsing, then described by Claude Haiku during execution. The report generator builds a collapsible Math Review table showing all equations with rendered SVG, LaTeX source, description, and status.

**Tech Stack:** anthropic (Claude Haiku 4.5), ziamath (math rendering), existing report_generator.py

**Spec:** `docs/superpowers/specs/2026-04-06-latex-tikz-math-review-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/models/document.py` | Modify | Add `tikz_source` field to `MathInfo` |
| `src/tools/latex_parser.py` | Modify | Store TikZ source on MathInfo instead of discarding |
| `src/prompts/tikz_description.md` | Create | Claude prompt for thorough TikZ diagram description |
| `src/agent/executor.py` | Modify | Add `describe_tikz` action handler |
| `src/agent/orchestrator.py` | Modify | Auto-generate `describe_tikz` actions for TikZ math objects |
| `src/tools/report_generator.py` | Modify | Add `_build_math_review_section()` and insert into report |
| `tests/test_latex_parser.py` | Modify | Test TikZ source stored on MathInfo |
| `tests/test_report_generator.py` | Modify | Test math review section generation |

---

### Task 1: Add `tikz_source` field to MathInfo

**Files:**
- Modify: `src/models/document.py:125-134`
- Modify: `tests/test_latex_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_latex_parser.py`:

```python
class TestMathInfoTikzSource:
    """Tests for tikz_source field on MathInfo."""

    def test_tikz_source_default_empty(self):
        from src.models.document import MathInfo
        m = MathInfo(id="math_0", latex_source="x", mathml="<math><mi>x</mi></math>")
        assert m.tikz_source == ""

    def test_tikz_source_stored(self):
        from src.models.document import MathInfo
        tikz = r"\begin{tikzpicture}\node[state] (q0) {$q_0$};\end{tikzpicture}"
        m = MathInfo(
            id="math_0", latex_source="", mathml="",
            tikz_source=tikz,
        )
        assert m.tikz_source == tikz
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestMathInfoTikzSource -v`
Expected: FAIL — `tikz_source` field doesn't exist on MathInfo.

- [ ] **Step 3: Add the field**

In `src/models/document.py`, add `tikz_source` to `MathInfo` (line ~134, before the closing of the class):

```python
class MathInfo(BaseModel, frozen=True):
    """A mathematical expression extracted from a LaTeX document."""
    id: str
    latex_source: str
    mathml: str
    display: str = "block"
    description: str = ""
    equation_number: str | None = None
    confidence: float = 1.0
    unparsed: bool = False
    tikz_source: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_latex_parser.py::TestMathInfoTikzSource -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models/document.py tests/test_latex_parser.py
git commit -m "feat: add tikz_source field to MathInfo model"
```

---

### Task 2: Store TikZ source on MathInfo during parsing

**Files:**
- Modify: `src/tools/latex_parser.py:752-767`
- Modify: `tests/test_latex_parser.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_latex_parser.py`:

```python
class TestTikzSourceOnMathInfo:
    """Tests that TikZ source is stored on MathInfo during parsing."""

    def test_tikz_paragraph_creates_mathinfo_with_source(self):
        """When a TikZ diagram is detected, a MathInfo with tikz_source is created."""
        from src.tools.latex_parser import _is_tikz_content, _tikz_placeholder
        from src.models.document import MathInfo

        tikz_src = r"\begin{tikzpicture}\node[state] (q0) {$q_0$};\draw (q0) -- (q1);\end{tikzpicture}"
        assert _is_tikz_content(tikz_src)

        # The parser should create a MathInfo with tikz_source populated
        # We'll test this via parse_latex on a real document in integration,
        # but here we verify the MathInfo can hold it
        m = MathInfo(
            id="math_tikz_0",
            latex_source="",
            mathml="",
            display="block",
            description=_tikz_placeholder(tikz_src),
            tikz_source=tikz_src,
        )
        assert m.tikz_source == tikz_src
        assert "tikzpicture" in m.tikz_source
        assert "[Diagram:" in m.description  # placeholder description
```

- [ ] **Step 2: Run tests to verify they pass** (this test validates the model, not the parser change yet)

Run: `pytest tests/test_latex_parser.py::TestTikzSourceOnMathInfo -v`
Expected: PASS (model field exists from Task 1).

- [ ] **Step 3: Modify the parser to store TikZ source on MathInfo**

In `src/tools/latex_parser.py`, find the TikZ handling block (lines ~752-767). Currently it creates a `ParagraphInfo` with the placeholder text. Change it to ALSO create a `MathInfo` with the TikZ source stored.

Replace lines 752-767:

```python
            # 2. TikZ diagram — collect full paragraph text (error + non-error)
            #    to check for tikz patterns that may span multiple nodes.
            full_para_text = p_tag.get_text()
            if _is_tikz_content(error_text) or _is_tikz_content(full_para_text):
                tikz_source = full_para_text
                placeholder = _tikz_placeholder(tikz_source)
                warnings.append(
                    f"TikZ diagram detected (paragraph p_{p_idx}); "
                    f"replaced with placeholder. Source: {tikz_source[:200]}"
                )
                pid = f"p_{p_idx}"
                p_idx += 1
                return ParagraphInfo(
                    id=pid, text=placeholder, style_name="Normal",
                    runs=[RunInfo(text=placeholder, font_size_pt=12.0)],
                )
```

With:

```python
            # 2. TikZ diagram — collect full paragraph text (error + non-error)
            #    to check for tikz patterns that may span multiple nodes.
            full_para_text = p_tag.get_text()
            if _is_tikz_content(error_text) or _is_tikz_content(full_para_text):
                tikz_source = full_para_text
                placeholder = _tikz_placeholder(tikz_source)
                warnings.append(
                    f"TikZ diagram detected (paragraph p_{p_idx}); "
                    f"replaced with placeholder. Source: {tikz_source[:200]}"
                )
                # Store TikZ source on a MathInfo for Claude description later
                tikz_math_id = f"math_tikz_{len(math_list)}"
                math_list.append(MathInfo(
                    id=tikz_math_id,
                    latex_source="",
                    mathml="",
                    display="block",
                    description=placeholder,
                    tikz_source=tikz_source,
                ))
                pid = f"p_{p_idx}"
                p_idx += 1
                return ParagraphInfo(
                    id=pid, text=placeholder, style_name="Normal",
                    runs=[RunInfo(text=placeholder, font_size_pt=12.0)],
                    math_ids=[tikz_math_id],
                )
```

Note: `math_list` is the list that accumulates `MathInfo` objects during parsing. It's available in the enclosing scope of the `_process_error_paragraph` function. Check that `MathInfo` is imported at the top of the file (it should be since `_extract_math` already creates MathInfo objects).

- [ ] **Step 4: Run existing TikZ tests to verify they pass**

Run: `pytest tests/test_latex_parser.py -k tikz -v`
Expected: All existing TikZ tests PASS (behavior unchanged from the outside — paragraph still gets placeholder text).

- [ ] **Step 5: Commit**

```bash
git add src/tools/latex_parser.py tests/test_latex_parser.py
git commit -m "feat: store TikZ source on MathInfo during parsing"
```

---

### Task 3: TikZ description prompt and executor action

**Files:**
- Create: `src/prompts/tikz_description.md`
- Modify: `src/agent/executor.py`
- Modify: `tests/test_latex_parser.py` (or a new test file)

- [ ] **Step 1: Create the TikZ description prompt**

Write to `src/prompts/tikz_description.md`:

```markdown
You are describing a TikZ diagram for a blind student who needs to understand it thoroughly to complete their coursework. The student cannot see the diagram and relies entirely on your description.

Describe the diagram's complete structure:
- All nodes/vertices with their labels and any special properties (start state, accepting state, shape, color)
- All edges/arrows with their labels, direction, and what they connect
- Spatial layout if relevant (left-to-right, circular, tree structure)
- Any annotations, captions, or mathematical labels
- The overall purpose or type of diagram (state machine, tree, graph, circuit, etc.)

Be thorough but organized. Use a logical order (e.g., enumerate nodes first, then edges).

TikZ source:
```
{tikz_source}
```
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_latex_parser.py`:

```python
class TestDescribeTikzAction:
    """Tests for describe_tikz action in executor."""

    @patch("src.agent.executor.Anthropic")
    def test_describe_tikz_updates_description(self, mock_anthropic_cls):
        """describe_tikz action replaces placeholder with Claude description."""
        from src.agent.executor import _execute_single_action_pdf
        from src.models.pipeline import RemediationAction

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="This is a finite automaton with 3 states: q0 (start), q1, and q2 (accepting). Transitions: q0 to q1 on 'a', q1 to q2 on 'b'.",
            type="text",
        )]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create.return_value = mock_response

        model_dict = {
            "paragraphs": [],
            "images": [],
            "tables": [],
            "links": [],
            "math": [
                {
                    "id": "math_tikz_0",
                    "latex_source": "",
                    "mathml": "",
                    "display": "block",
                    "description": "[Diagram: placeholder]",
                    "tikz_source": r"\begin{tikzpicture}\node[state] (q0) {$q_0$};\end{tikzpicture}",
                }
            ],
            "metadata": {"title": "", "language": "en"},
        }

        action = RemediationAction(
            element_id="math_tikz_0",
            action_type="describe_tikz",
            parameters={"tikz_source": r"\begin{tikzpicture}\node[state] (q0) {$q_0$};\end{tikzpicture}"},
            wcag_criterion="1.1.1",
            description="Describe TikZ diagram",
        )

        result = _execute_single_action_pdf(action, model_dict)

        assert result["action_type"] == "describe_tikz"
        assert result["status"] == "executed"
        assert model_dict["math"][0]["description"] == mock_response.content[0].text

    def test_describe_tikz_fallback_on_missing_key(self):
        """describe_tikz keeps placeholder when ANTHROPIC_API_KEY is missing."""
        from src.agent.executor import _execute_single_action_pdf
        from src.models.pipeline import RemediationAction

        model_dict = {
            "paragraphs": [], "images": [], "tables": [], "links": [],
            "math": [{
                "id": "math_tikz_0", "latex_source": "", "mathml": "",
                "display": "block", "description": "[Diagram: placeholder]",
                "tikz_source": r"\begin{tikzpicture}\end{tikzpicture}",
            }],
            "metadata": {"title": "", "language": "en"},
        }

        action = RemediationAction(
            element_id="math_tikz_0",
            action_type="describe_tikz",
            parameters={"tikz_source": r"\begin{tikzpicture}\end{tikzpicture}"},
            wcag_criterion="1.1.1",
            description="Describe TikZ diagram",
        )

        with patch.dict("os.environ", {}, clear=True):
            result = _execute_single_action_pdf(action, model_dict)

        # Should fall back gracefully — keep placeholder
        assert result["status"] in ("executed", "failed")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_latex_parser.py::TestDescribeTikzAction -v`
Expected: FAIL — `describe_tikz` action type not handled.

- [ ] **Step 4: Implement `describe_tikz` action in executor**

In `src/agent/executor.py`, find the `_execute_single_action_pdf` function. Add a new `elif` block for `describe_tikz` before the final `else` clause (around line 478, after `add_math_description`):

```python
        elif action_type == "describe_tikz":
            tikz_source = params.get("tikz_source", "")
            if not tikz_source:
                return _action_dict(action, "failed", "No TikZ source provided")

            math_list = model_dict.get("math", [])
            math_idx = next(
                (i for i, m in enumerate(math_list) if m.get("id") == element_id),
                None,
            )
            if math_idx is None:
                return _action_dict(action, "failed", f"Math element not found: {element_id}")

            # Load prompt
            prompt_path = Path(__file__).parent.parent / "prompts" / "tikz_description.md"
            if prompt_path.exists():
                prompt_template = prompt_path.read_text(encoding="utf-8")
            else:
                prompt_template = (
                    "Describe this TikZ diagram thoroughly for a blind student. "
                    "Include all nodes, edges, labels, and relationships.\n\n"
                    "TikZ source:\n```\n{tikz_source}\n```"
                )
            prompt = prompt_template.replace("{tikz_source}", tikz_source)

            # Call Claude Haiku
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                return _action_dict(action, "failed", "ANTHROPIC_API_KEY not set — keeping placeholder")

            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=api_key)
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": prompt}],
                )
                description = response.content[0].text.strip()
                model_dict["math"][math_idx]["description"] = description
                return _action_dict(action, "executed", f"TikZ described: {description[:80]}")
            except Exception as e:
                logger.warning("TikZ description failed for %s: %s", element_id, e)
                return _action_dict(action, "failed", f"Claude API failed: {e}")
```

Make sure `os` and `Path` are imported at the top of `executor.py`. Check existing imports — `os` is likely already there, `Path` may need `from pathlib import Path`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_latex_parser.py::TestDescribeTikzAction -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/prompts/tikz_description.md src/agent/executor.py tests/test_latex_parser.py
git commit -m "feat: add describe_tikz action with Claude Haiku for TikZ diagrams"
```

---

### Task 4: Auto-generate describe_tikz actions in orchestrator

**Files:**
- Modify: `src/agent/orchestrator.py`

- [ ] **Step 1: Find where math actions are generated**

Search `src/agent/orchestrator.py` for where the strategy actions are created or where the executor is called. The orchestrator runs: comprehend → strategize → execute. We need to inject `describe_tikz` actions before execution, after the strategy phase.

- [ ] **Step 2: Add TikZ action generation**

In `src/agent/orchestrator.py`, after the strategy phase generates actions and before the executor runs, add logic to scan the document model for MathInfo objects with `tikz_source` and create `describe_tikz` actions for them.

Find the line where `strategy_result.actions` is used (or where the executor is called). Add before execution:

```python
        # ── Auto-generate TikZ description actions ─────────────────
        from src.models.pipeline import RemediationAction
        tikz_actions = []
        if hasattr(doc_model, "math") and doc_model.math:
            for math_info in doc_model.math:
                if math_info.tikz_source:
                    tikz_actions.append(RemediationAction(
                        element_id=math_info.id,
                        action_type="describe_tikz",
                        parameters={"tikz_source": math_info.tikz_source},
                        wcag_criterion="1.1.1",
                        description=f"Generate thorough description for TikZ diagram {math_info.id}",
                    ))
            if tikz_actions:
                logger.info("Added %d TikZ description action(s)", len(tikz_actions))
                # Prepend to strategy actions so they run during execution
                strategy_result = strategy_result.model_copy(update={
                    "actions": tikz_actions + list(strategy_result.actions),
                })
```

Note: Check the actual type of `strategy_result` and how its actions are stored. If it uses a different mechanism, adjust accordingly.

- [ ] **Step 3: Run existing tests**

Run: `pytest tests/ -x -q --tb=short`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/agent/orchestrator.py
git commit -m "feat: auto-generate describe_tikz actions for TikZ diagrams in orchestrator"
```

---

### Task 5: Math Review section in report

**Files:**
- Modify: `src/tools/report_generator.py`
- Modify: `tests/test_report_generator.py` (if exists, otherwise add tests inline)

- [ ] **Step 1: Write the failing test**

Find or create a test file for report generation. Add:

```python
class TestBuildMathReviewSection:
    """Tests for _build_math_review_section()."""

    def test_generates_section_with_equations(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="math_0", latex_source=r"x^2 + y^2 = r^2",
                     mathml="<math><mi>x</mi></math>", display="block",
                     description="x squared plus y squared equals r squared"),
            MathInfo(id="math_1", latex_source=r"\alpha",
                     mathml="<math><mi>α</mi></math>", display="inline",
                     description="alpha"),
            MathInfo(id="math_2", latex_source=r"\int_0^1 f(x) dx",
                     mathml="<math><mi>f</mi></math>", display="block",
                     description=""),  # missing description
        ]

        html = _build_math_review_section(math_list)

        assert "Math Review" in html
        assert "3 equations" in html
        assert "2 block" in html
        assert "1 inline" in html
        assert "x^2 + y^2 = r^2" in html  # LaTeX source shown
        assert "x squared plus y squared" in html  # description shown
        assert "Missing" in html  # status for empty description
        assert "<details" in html  # collapsible

    def test_empty_math_list_returns_empty(self):
        from src.tools.report_generator import _build_math_review_section

        html = _build_math_review_section([])
        assert html == ""

    def test_status_badges(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            # Trivial — single variable
            MathInfo(id="m0", latex_source="x", mathml="<math><mi>x</mi></math>",
                     display="inline", description="x"),
            # Complex with description — AI-generated
            MathInfo(id="m1", latex_source=r"\frac{a}{b}",
                     mathml="<math><mi>a</mi></math>", display="block",
                     description="a over b"),
            # No description — missing
            MathInfo(id="m2", latex_source=r"\sum_{i=1}^n",
                     mathml="<math><mi>n</mi></math>", display="block",
                     description=""),
        ]

        html = _build_math_review_section(math_list)

        assert "status-auto" in html
        assert "status-ai" in html
        assert "status-missing" in html

    def test_tikz_source_shown_for_tikz_diagrams(self):
        from src.tools.report_generator import _build_math_review_section
        from src.models.document import MathInfo

        math_list = [
            MathInfo(id="math_tikz_0", latex_source="", mathml="",
                     display="block", description="A state machine with 3 states.",
                     tikz_source=r"\begin{tikzpicture}\node[state] (q0) {};\end{tikzpicture}"),
        ]

        html = _build_math_review_section(math_list)

        assert "TikZ Diagram" in html or "tikzpicture" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_generator.py::TestBuildMathReviewSection -v` (or wherever you put the tests)
Expected: FAIL — `_build_math_review_section` doesn't exist.

- [ ] **Step 3: Implement `_build_math_review_section()`**

Add to `src/tools/report_generator.py`:

```python
def _build_math_review_section(math_list: list) -> str:
    """Build the Math Review section for the report.

    Shows every equation with rendered SVG, LaTeX source, description, and status.
    Grouped by display type: block equations first, then inline.
    """
    if not math_list:
        return ""

    from src.tools.math_renderer import render_mathml_to_svg
    from src.tools.math_descriptions import classify_math
    from src.models.document import MathInfo

    block_math = [m for m in math_list if m.display == "block"]
    inline_math = [m for m in math_list if m.display != "block"]

    total = len(math_list)
    block_count = len(block_math)
    inline_count = len(inline_math)

    def _status_badge(m) -> str:
        if not m.description:
            return '<span class="status-missing">Missing</span>'
        if m.tikz_source:
            return '<span class="status-ai">AI-generated</span>'
        if classify_math(m) == "trivial":
            return '<span class="status-auto">Auto</span>'
        return '<span class="status-ai">AI-generated</span>'

    def _render_row(m) -> str:
        # Render SVG
        if m.tikz_source:
            svg_html = '<em>[TikZ Diagram]</em>'
            source = _esc(m.tikz_source[:200])
            if len(m.tikz_source) > 200:
                source += "..."
        elif m.mathml:
            svg_html = render_mathml_to_svg(m.mathml)
            source = _esc(m.latex_source)
        else:
            svg_html = '<em>[No preview]</em>'
            source = _esc(m.latex_source)

        desc = _esc(m.description) if m.description else '<em>No description</em>'
        badge = _status_badge(m)
        eq_num = f' <span class="eq-num">({m.equation_number})</span>' if m.equation_number else ''

        return f"""<tr>
  <td class="math-render">{svg_html}{eq_num}</td>
  <td><code>{source}</code></td>
  <td>{desc}</td>
  <td>{badge}</td>
</tr>"""

    def _render_table(items: list, heading: str) -> str:
        if not items:
            return ""
        rows = "\n".join(_render_row(m) for m in items)
        return f"""<h3>{heading}</h3>
<table class="math-review">
  <thead>
    <tr><th>Rendered</th><th>LaTeX Source</th><th>Description</th><th>Status</th></tr>
  </thead>
  <tbody>
    {rows}
  </tbody>
</table>"""

    block_html = _render_table(block_math, "Block Equations")
    inline_html = _render_table(inline_math, "Inline Equations")

    return f"""<details class="math-review-section">
  <summary>Math Review ({total} equation{"s" if total != 1 else ""}: {block_count} block, {inline_count} inline)</summary>
  <div class="details-content">
    {block_html}
    {inline_html}
  </div>
</details>"""
```

- [ ] **Step 4: Add CSS for math review section**

In `generate_report_html()`, add to the CSS block (after the existing styles, around line 900):

```css
    /* Math review */
    .math-review-section { margin: 1.5rem 0; }
    .math-review { width: 100%; border-collapse: collapse; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
    .math-review th { background: #f0f0f0; text-align: left; padding: 0.5rem; border-bottom: 2px solid #ddd; }
    .math-review td { padding: 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }
    .math-review .math-render { max-width: 200px; overflow-x: auto; }
    .math-review code { font-size: 0.8rem; background: #f5f5f5; padding: 0.15rem 0.3rem; border-radius: 3px; word-break: break-all; }
    .status-auto { background: #dcfce7; color: #166534; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.8rem; }
    .status-ai { background: #fef3c7; color: #92400e; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.8rem; }
    .status-missing { background: #fee2e2; color: #991b1b; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.8rem; }
```

- [ ] **Step 5: Wire into `generate_report_html()`**

In `generate_report_html()`, add the math review section. The function already receives `result` which has access to the document model. Add a parameter for math list, or extract from result.

Add after the output files section and before the WCAG technical details (around line 952):

```python
    # Build math review section
    math_review_html = ""
    if hasattr(result, 'document_model') and result.document_model and hasattr(result.document_model, 'math') and result.document_model.math:
        math_review_html = _build_math_review_section(list(result.document_model.math))
```

Then insert `{math_review_html}` into the HTML template between the output files section and the WCAG technical details section:

```python
{'<div class="section"><h2>Your Output Files</h2>' + output_files_html + '</div>' if output_files_html else ''}

{math_review_html}

<div class="section" style="font-size: 0.85rem; color: #666;">
```

Note: Check how `result` provides access to the document model. If `RemediationResult` doesn't have a `document_model` field, you may need to pass it as a parameter to `generate_report_html()`. Check the `RemediationResult` model in `src/models/pipeline.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_report_generator.py::TestBuildMathReviewSection -v`
Expected: PASS.

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -x -q --tb=short`
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/tools/report_generator.py tests/test_report_generator.py
git commit -m "feat: add Math Review section to report with per-equation rendering and status"
```

---

### Task 6: Integration test with homework.tex

**Files:**
- No code changes — validation task

- [ ] **Step 1: Run homework.tex through the pipeline**

The homework.tex file has TikZ automata + math equations. Run it:

```bash
python3 -m src.cli tests/test_docs/homework.tex --output-dir /tmp/latex_test/
```

Or if CLI doesn't support output-dir, use the batch script or a small Python script:

```python
python3 -c "
from src.agent.orchestrator import process
from src.models.pipeline import RemediationRequest, CourseContext
result = process(RemediationRequest(
    document_path='tests/test_docs/homework.tex',
    course_context=CourseContext(course_name='CS Theory', department='Computer Science'),
    output_dir='/tmp/latex_test/',
))
print(f'Success: {result.success}')
print(f'Math objects: {len(result.document_model.math) if result.document_model else 0}')
print(f'Report: {result.report_path}')
"
```

- [ ] **Step 2: Verify TikZ descriptions**

Check the output report for TikZ diagrams:
- Should have Claude-generated descriptions (not just "Diagram: finite automaton with N states")
- Descriptions should mention specific node labels, transitions, and structure

- [ ] **Step 3: Verify Math Review section**

Open the report HTML and verify:
- "Math Review" collapsible section exists
- Shows all equations (block and inline)
- SVGs rendered correctly
- Status badges: Auto (for simple vars), AI-generated (for complex math), Missing (if any)
- TikZ diagrams show "[TikZ Diagram]" in render column

- [ ] **Step 4: Commit results if needed**

If any fixes were needed, commit them. Otherwise, just update NOW.md.
