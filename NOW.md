# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: Post-launch — billing live, polish and testing

## Stripe Billing — COMPLETE
- Live mode active (real charges)
- Webhook configured: `checkout.session.completed` → `/api/billing/webhook`
- Packs: Starter 50/$5, Standard 200/$15, Bulk 500/$30
- Pricing hidden by default, revealed via "Buy more" link in account card or low-balance/limit-reached CTAs
- 28 tests in `tests/test_billing.py`
- `dotenv` loading added to `app.py` (was missing, caused env var issues)

## Scanned Page OCR — IN PROGRESS
- **Problem**: Scanned PDFs (e.g., Erlwanger) got alt-text summaries instead of actual text. UGA remediation specialist flagged this.
- **Solution**: Gemini vision OCR extracts real text with formatting + layout from scanned pages, replaces image placeholders with real ParagraphInfo/TableInfo/ImageInfo.
- **Code complete**: `src/tools/scanned_page_ocr.py` (new), modified `orchestrator.py` and `pdf_parser.py`. 37 new tests, 704 total passing.
- **What's done**:
  - `ScannedPageResult` dataclass + Gemini structured JSON schema for page regions
  - `process_scanned_pages()` — renders pages to PNG, sends to Gemini in batches, converts regions to model objects
  - `_regions_to_model_objects()` — heading/paragraph/table/figure/equation/caption/footnote conversion
  - `_merge_ocr_into_model()` in orchestrator — replaces ScannedPageAnchor placeholders with OCR content
  - Improved `_detect_scanned_pages()` with area-based detection
  - Prompt at `src/prompts/scanned_ocr.md`
- **Next**: Test with real Erlwanger PDF (`python scripts/test_batch.py --doc "Erlwanger"`) to validate OCR quality and tune the prompt

## Up Next
- End-to-end testing with real faculty documents
- Production hardening
- Admin tooling improvements
- Future: Mac Mini on-premises deployment for university
