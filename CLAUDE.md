# A11y Remediation Agent

AI-powered WCAG 2.1 AA accessibility remediation for university course materials (.docx, .pdf, .pptx).

**Live site:** https://remediate.jenkleiman.com/

## Session State

**Always read `NOW.md` at the start of a session** to understand current work in progress. **Update `NOW.md` at the end of a session** (or when significant progress is made) so the next session picks up where this one left off.

## Project Context

DOJ Title II ADA rule (April 2024) requires public universities to meet WCAG 2.1 Level AA for all digital content. The original compliance date was April 24, 2026; DOJ extended this to **April 26, 2027** for entities with population ≥50,000 (and to April 26, 2028 for smaller entities) via an Interim Final Rule published 2026-04-20. The underlying WCAG 2.1 AA standard and ongoing ADA obligation are unchanged. Manual remediation costs $3-4/page. Existing automated tools apply generic fixes without understanding document context and don't get the job done well. This tool automates 70-80% of the work using an agentic AI approach where the model *understands* document intent before remediating, rather than running a fixed checklist. The core distinction is this agentic layer — the system makes context-dependent judgments that traditional deterministic pipelines cannot.

**End user experience:** Faculty log in to a web interface, upload a document (with optional course context), and receive the remediated file + compliance report. No CLI or technical knowledge required. Course context matters: knowing a document is a calculus syllabus vs. an art history lecture changes how elements are interpreted.

**Deployment:** Oracle Cloud free tier instance (ARM, Ubuntu). Future: university Mac Mini for on-premises.

**API costs:** Estimated ~$15–40 per semester of documents per class.

**Free tier:** 3 documents per user, 20MB each. Per-user accounts with usage tracking.

**Testing:** Need 5–10 representative sample documents from real courses for development and validation.

## Canonical Data Schema

**`docs/data_schema.md` is the single source of truth for all data models.** Consult it before building any new feature, tool, or agent component. Update it whenever models change. All pipeline data — from faculty submission through remediated output — flows through the models defined there.

## Architecture

This is NOT a deterministic pipeline. It's an agentic system with deterministic tools.

```
Document + Course Context In → Comprehend (Gemini + validators) → Strategize (Claude) → Execute (agent + tools) → Review (Claude + validators) → Output
```

- **Tools are deterministic Python functions** that parse, modify, validate
- **The agent decides** which tools to call, in what order, with what parameters
- **Context matters:** A bold "Example 3.2" in a math textbook is a sub-heading; same bold in an email is emphasis. The agent reasons about this.
- **Course context flows through the pipeline:** Faculty provide the course name/subject when submitting, and the comprehension and strategy phases use it to make better decisions.

### The Four Phases

1. **Comprehend** (Gemini + compliance utilities): Analyze the document holistically — identify type, structure, and purpose of every element, within the context of the course it belongs to. Also run the compliance-checking validator to flag existing issues.
2. **Strategize** (Claude): Generate a remediation strategy specific to that document, distinguishing a decorative image from a data-bearing graph, or bold emphasis from a missing heading.
3. **Execute** (deterministic tools): Apply fixes — alt text, heading structure, table headers, contrast, metadata, link text, etc. — to produce a remediated version.
4. **Review** (Claude + compliance utilities): Evaluate the result from a screen reader user's perspective, rerun the compliance-checking utilities, and flag uncertain or unremediated items for human review.

### Model Allocation

| Model | Role | Why |
|-------|------|-----|
| Gemini (2.5 Flash/Pro via google-genai SDK) | Multimodal document comprehension, PDF page analysis | Native PDF vision, 1M+ context, cost-effective for bulk visual work |
| Claude (Sonnet/Opus via anthropic SDK) | Strategy, execution reasoning, alt text refinement, self-review | Best reasoning and judgment |
| OpenAI GPT-4o | Fallback / specific subtasks | Available if needed |

## Tech Stack

- **Python 3.11+**
- **python-docx** — .docx parsing and modification. NOTE: Alt text and table headers require raw XML manipulation via lxml; python-docx API doesn't expose these.
- **PyMuPDF (pymupdf)** — PDF text/image extraction, page rendering. License: AGPL (acceptable for academic open-source use).
- **WeasyPrint ≥68.0** — HTML→PDF/UA-1 generation. `pdf_variant='pdf/ua-1'`. Output not guaranteed fully valid; always validate.
- **wcag-contrast-ratio** — WCAG contrast calculation (MIT).
- **google-genai** — Gemini API client.
- **anthropic** — Claude API client.
- **veraPDF** — PDF/UA validation. Java CLI, called via subprocess. Returns JSON.
- **Pillow** — Image processing for extraction/analysis.
- **lxml** — XML manipulation for docx internals.
- **FastAPI + uvicorn** — Web application and API server.
- **bcrypt** — Password hashing. NOTE: Do NOT use passlib — incompatible with bcrypt 5.x.
- **PyJWT** — JWT token creation/verification for session cookies.
- **authlib + httpx** — OAuth2 client for Google/Microsoft SSO.

## Project Structure

```
a11y-remediate/
├── CLAUDE.md
├── pyproject.toml
├── .env                     # API keys (GEMINI_API_KEY, ANTHROPIC_API_KEY, JWT_SECRET)
├── src/
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── document.py      # DocumentModel, ImageInfo, TableInfo, etc.
│   │   └── pipeline.py      # RemediationRequest, CourseContext, ComprehensionResult, etc.
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── docx_parser.py   # parse .docx → DocumentModel
│   │   ├── pdf_parser.py    # parse .pdf → DocumentModel + page images
│   │   ├── pptx_parser.py   # parse .pptx → DocumentModel
│   │   ├── image_extract.py # extract images with context
│   │   ├── alt_text.py      # read/write alt text (raw XML for docx)
│   │   ├── headings.py      # detect fake headings, set heading levels
│   │   ├── tables.py        # mark table headers (raw XML)
│   │   ├── lists.py         # convert fake lists to real lists
│   │   ├── metadata.py      # set title, language
│   │   ├── contrast.py      # check and fix color contrast
│   │   ├── links.py         # improve link text
│   │   ├── html_builder.py  # structured content → semantic HTML
│   │   ├── pdf_output.py    # WeasyPrint HTML → PDF/UA-1
│   │   ├── pdf_writer.py    # in-place PDF modification (metadata, alt text)
│   │   ├── itext_tagger.py  # iText Java CLI wrapper (structure tagging)
│   │   └── validator.py     # WCAG audit + veraPDF integration
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── comprehension.py # Gemini + validators: "what IS this document?"
│   │   ├── strategy.py      # Claude: "what does it NEED?"
│   │   ├── executor.py      # Claude agent loop with tool calls
│   │   ├── reviewer.py      # Claude + validators: screen reader perspective review
│   │   └── orchestrator.py  # comprehend → strategize → execute → review
│   ├── web/
│   │   ├── __init__.py
│   │   ├── app.py           # FastAPI application, API endpoints
│   │   ├── jobs.py          # Job tracking with SQLite
│   │   ├── users.py         # User accounts, usage limits
│   │   ├── auth.py          # Password hashing, JWT, cookies
│   │   ├── middleware.py    # FastAPI auth dependencies
│   │   ├── oauth.py         # Google/Microsoft OAuth2
│   │   └── static/
│   │       └── index.html   # Single-page frontend (vanilla JS)
│   ├── prompts/
│   │   ├── comprehension.md
│   │   ├── strategy.md
│   │   ├── execution.md
│   │   └── review.md
│   └── cli.py               # CLI entry point
├── java/
│   ├── itext-tagger/        # iText 9 structure tagging CLI
│   └── html-to-pdf/         # OpenHTMLtoPDF conversion CLI
├── tests/
│   ├── test_docs/           # sample .docx, .pdf, .pptx files
│   ├── test_parser.py
│   ├── test_tools.py
│   ├── test_users.py
│   ├── test_auth.py
│   ├── test_web_auth.py
│   └── test_agent.py
└── docs/
    ├── data_schema.md       # **canonical data model reference** — update when models change
    └── wcag_criteria.md     # reference: WCAG 2.1 AA criteria for documents
```

## WCAG 2.1 AA Criteria (Document-Relevant)

The tool targets these criteria. Every tool and agent decision maps back to one or more:

| Criterion | Requirement | Tool |
|-----------|-------------|------|
| 1.1.1 | Alt text for non-decorative images; empty alt for decorative | alt_text.py |
| 1.3.1 | Heading hierarchy, table structure, lists use semantic markup | headings.py, tables.py, lists.py |
| 1.4.1 | Color not sole conveyor of meaning | (agent judgment) |
| 1.4.3 | Contrast ≥4.5:1 normal text, ≥3:1 large text (≥18pt or ≥14pt bold) | contrast.py |
| 2.4.1 | Mechanism to bypass repeated content | (headings provide this in docs) |
| 2.4.2 | Document title in metadata | metadata.py |
| 2.4.4 | Link purpose determinable from text | links.py |
| 2.4.6 | Headings and labels describe topic/purpose | headings.py |
| 3.1.1 | Document language set | metadata.py |
| 3.1.2 | Language of parts identified | (flag for human review) |
| 4.1.2 | Accessible names and roles for UI components | (structural via other tools) |

For PDFs specifically: PDF/UA-1 (ISO 14289-1) compliance, validated via veraPDF.

## Critical Implementation Details

### Alt Text in .docx (Raw XML Required)

```python
# python-docx doesn't expose alt text. Access via lxml:
WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
for para in doc.paragraphs:
    for drawing in para._element.findall(f'.//{{{WP_NS}}}docPr'):
        existing_alt = drawing.get('descr', '')
        drawing.set('descr', new_alt_text)  # write alt text
```

### Table Headers in .docx (Raw XML Required)

```python
# Mark first row as header (repeats across pages):
from docx.oxml.ns import qn
for row in table.rows[:header_count]:
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    trPr.append(tblHeader)
```

### Fake Heading Detection Heuristic

A paragraph is likely a fake heading if:
- Style is Normal (not a Heading style)
- All runs are bold
- Font size > document body average by ≥2pt
- Short text (< ~10 words)
- Not inside a table cell
- Followed by non-bold body text

**The agent decides** whether these are actually headings based on document context.

### PDF Pipeline (Extract → Comprehend → Regenerate)

PDFs cannot be reliably remediated in-place. Instead:
1. Extract content (PyMuPDF text + images; Gemini visual page analysis)
2. Agent builds semantic model of document
3. Generate accessible HTML with all fixes applied
4. Render HTML → PDF/UA-1 via WeasyPrint
5. Validate with veraPDF

**Visual fidelity will differ from original.** Content and accessibility will be correct; aesthetics are best-effort.

### WeasyPrint PDF/UA Requirements

The HTML input must have:
- `<html lang="en">` (or appropriate language)
- `<title>` tag
- Proper heading hierarchy
- Alt text on all `<img>` tags
- `<table>` with `<th scope="col|row">`
- Logical source order = reading order

```python
from weasyprint import HTML
HTML(string=html_string).write_pdf(output_path, pdf_variant='pdf/ua-1')
```

### veraPDF Integration

```bash
# Java CLI — call via subprocess
verapdf -f ua1 --format json document.pdf
```

Returns JSON with pass/fail per rule. Parse and include in compliance report.

## Coding Standards

- **Type hints everywhere.** Use dataclasses or Pydantic for models.
- **Each tool is a pure function** where possible. Input → output, no hidden state.
- **Agent prompts live in `prompts/` as markdown files**, loaded at runtime.
- **Tests use real sample documents** from `tests/test_docs/`. Do not mock document parsing — test against actual .docx and .pdf files.
- **Error handling:** Tools should return structured results (success/failure + details), not raise exceptions that break the agent loop.
- **Logging:** Use `logging` module. Each tool logs what it changed. The changes log feeds into the compliance report.
- **Data schema:** `docs/data_schema.md` is the canonical reference for all models. When adding or modifying Pydantic models or tool result dataclasses, update the schema doc. All pipeline data flows through the models defined there.

## Common Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run only the e2e regression suite (~10 seconds, mocked LLM pipeline)
pytest tests/e2e/ -v
pytest -m e2e -v

# Run the web app (development)
uvicorn src.web.app:app --reload --port 8000

# Run on a single document (CLI)
python -m src.cli document.docx

# Run on a directory (CLI)
python -m src.cli ./course_materials/ --output-dir ./accessible/
```

## Environment Variables

```bash
# Required for remediation pipeline
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...

# Required for web app auth (generate with: python -c "import secrets; print(secrets.token_hex(32))")
JWT_SECRET=...

# Optional: OAuth (web app Google/Microsoft SSO)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
MICROSOFT_CLIENT_ID=...
MICROSOFT_CLIENT_SECRET=...

# Optional: Cost cap (system-wide spend kill switch — see "Cost cap" section below)
COST_CAP_DAILY_USD=         # daily $ ceiling on cumulative API spend; empty = unlimited
COST_CAP_WEEKLY_USD=        # 7-day rolling $ ceiling; empty = unlimited
COST_CAP_KILL_SWITCH=       # "1" / "true" to reject all new uploads immediately

# Optional: Concurrency
MAX_CONCURRENT_JOBS=        # max concurrent remediation jobs; empty = 1 (safe default)

# Optional: Per-user caps (admins exempt)
MAX_USER_CONCURRENT_JOBS=   # default 5; max in-flight per user
MAX_USER_JOBS_PER_HOUR=     # default 30; max submissions per hour per user

# Optional: Storage retention (background cleanup of old files)
RETENTION_ENABLED=          # "0"/"false" disables; default enabled
RETENTION_DAYS_UPLOADS=     # default 30; age threshold for data/uploads/
RETENTION_DAYS_OUTPUT=      # default 30; age threshold for data/output/
RETENTION_INTERVAL_HOURS=   # default 24; cleanup loop interval
```

## Observability (request IDs + health endpoint)

`src/web/observability.py` provides:

- **Request IDs.** `RequestIdMiddleware` assigns a UUID4 to every inbound request (or honors a sane upstream `X-Request-ID` from Caddy). The ID is stored in a `ContextVar`, threaded into every log record via `RequestIdFilter`, and echoed in the response header. Lets you grep one request across user → job → API calls in `journalctl`.
- **Logging format.** `configure_logging()` installs a single root handler with format `"%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"`. Idempotent.

`GET /api/health` returns liveness + readiness: `status`, `db`, queue depth (queued/processing), free disk, and version. Public endpoint suitable for uptime monitors. Sensitive operational details (cost spend, user counts, file paths) live on admin-only endpoints.

## E2E regression suite

`tests/e2e/` exercises the full HTTP stack through FastAPI's `TestClient` while mocking the orchestrator's `process()` to avoid real LLM API calls. Cheap to run (~10s for 44 tests) and meant as the safety net for upcoming refactors (Postgres migration, ARQ queue replacement).

Coverage:
- `test_auth_flow.py` — register, login, logout, /me, password reset
- `test_upload_flow.py` — full upload → process (mocked) → status poll → download
- `test_caps_and_limits.py` — cost cap (kill switch + daily ceiling), per-user concurrent cap, page balance, check ordering
- `test_admin_flow.py` — admin user list/get/update, cost-status endpoint, retention cleanup endpoint
- `test_observability_flow.py` — request-ID middleware, health endpoint, robots/sitemap

Mock pattern (`tests/e2e/conftest.py`): the `stub_process` fixture replaces `src.web.app.process` with a fast deterministic stub that writes a tiny output file and returns a `RemediationResult` with a known `cost_summary`. Tests can flip `stub_process["force_failure"] = True` to exercise the failure path.

Threading: uploads return immediately and a daemon thread runs the (mocked) pipeline. The `wait_for_job(client, job_id)` helper polls until the job reaches a terminal state. With the mocked pipeline, this happens in well under a second.

Mark: tests are tagged `pytest.mark.e2e` so they can be selected/excluded via `pytest -m e2e` or `pytest -m "not e2e"`.

## Storage retention

`src/web/retention.py` deletes files older than the configured window from `data/uploads/` and `data/output/`. Behavior:

- **What's deleted:** files in either directory whose mtime exceeds the threshold (default 30 days). Empty subdirectories also cleaned up.
- **What's preserved:** files referenced by jobs in `queued` or `processing` state — never deleted regardless of age.
- **Job records are NOT deleted.** SQLite rows are retained for cost analytics and audit. After cleanup, downloads of old jobs return 404 cleanly.
- **When it runs:** background daemon thread on app startup, every `RETENTION_INTERVAL_HOURS` (default 24).
- **Manual run:** `POST /api/admin/retention/cleanup` triggers a one-shot pass and returns the report (files scanned, deleted, bytes freed, errors). Admin-only.

This is the implementation half of the R4/Y4 retention policy. The companion policy doc (`docs/uga/retention-audit-policy.md`) records the policy itself for the FERPA / EITS conversation.

## Per-user job caps

`src/web/user_caps.py` enforces, at upload time, that no single user can monopolize the queue. Two limits, both env-configurable, both bypassed for admins:

- **Concurrent (queued + processing):** `MAX_USER_CONCURRENT_JOBS`, default 5. Blocks with HTTP 429 + `reason: "concurrent_cap"`.
- **Hourly (created in trailing 60 min):** `MAX_USER_JOBS_PER_HOUR`, default 30. Blocks with HTTP 429 + `reason: "hourly_cap"`.

These layer on top of (a) the per-IP `_upload_limit` rate limit, (b) per-user `pages_balance`, and (c) the system-wide cost cap. All four checks must pass for an upload to be accepted.

## Cost cap (system-wide kill switch)

`src/web/cost_cap.py` provides a system-wide spend ceiling that operates independently of per-user `pages_balance`. When the configured cap is hit (or the kill switch is set), `/api/upload` returns HTTP 503 with reason `daily_cap_exceeded`, `weekly_cap_exceeded`, or `kill_switch`.

- **Storage:** actual API cost per job is recorded in `jobs.estimated_cost_usd` after each pipeline run, sourced from `result.cost_summary.estimated_cost_usd`. Pre-existing jobs default to 0.
- **Caps:** read from env at check time, so changing `.env.production` and re-sourcing it (or restarting the service) takes effect without code changes. `0` / empty / invalid values mean unlimited.
- **Kill switch:** truthy values are `1`, `true`, `yes`, `on` (case-insensitive). Use during incidents or maintenance windows to pause spend without changing caps.
- **Visibility:** `GET /api/admin/cost-status` returns the current snapshot (today's spend, 7-day spend, configured caps, kill-switch state). Admin-only.
- **Window definitions:** "daily" = since UTC midnight today; "weekly" = trailing 7 days from now.

## Build Order

**Phase 1 — .docx (Sessions 1-4): DONE**
1. Data models + docx parser + image extraction
2. Remediation tools (alt text, headings, tables, lists, metadata, contrast, links, validator)
3. Agent integration (Gemini comprehension, Claude strategy/execution/review, orchestrator)
4. Test on real documents, tune prompts

**Phase 2 — PDF (Sessions 5-7): DONE**
5. PDF parser + page rendering + extraction
6. HTML builder + WeasyPrint PDF/UA output + veraPDF validation
7. Test PDF pipeline on real documents, tune

**Phase 3 — Additional Formats (Session 8+): DONE**
8. PowerPoint (.pptx) remediation support

**Phase 4 — Web Application: DONE**
9. FastAPI web app with upload, job tracking, compliance reports
10. User auth (registration, login, httpOnly JWT cookies, Google/Microsoft OAuth)
11. Per-user job isolation, free tier limits (3 docs, 20MB each)

**Phase 5 — Deployment (current):**
12. Deploy to Oracle Cloud free tier (ARM Ubuntu)
13. Production hardening (HTTPS/TLS, real JWT_SECRET, systemd service, reverse proxy)
14. End-to-end testing with real faculty documents, prompt tuning
15. Admin tooling (user management, tier upgrades)
16. Future: Mac Mini on-premises deployment for university

## Known Risks

- **WeasyPrint PDF/UA output may not fully validate.** v66+ improved significantly (NLnet-funded rewrite) but open issues remain (#2482). Expect some veraPDF failures that need workarounds.
- **PyMuPDF is AGPL.** Fine for open-source academic use. If this becomes a commercial product, need commercial license from Artifex.
- **Gemini 2.0 Flash/Flash-Lite retire March 31, 2026.** Use Gemini 2.5 Flash or later.
- **Complex tables** (merged cells, nested headers) will produce imperfect results. Flag for human review.
- **Scanned/image-only PDFs** require OCR preprocessing. Gemini can help but out of scope for v1.
- **Mathematical content** (LaTeX, MathType, equation images) needs special handling. Flag for review; MathML conversion is future work.
