# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: Post-launch — OCR quality fixes complete, LaTeX feature next

## OCR Quality Fixes — COMPLETE (this session)
Three root causes identified and fixed for scanned PDF OCR:

### Fix 1: Two-column reading order (`_sort_regions_by_column`)
- Gemini's cross-column `reading_order` caused interleaving of left/right column text
- New function groups by column (0=full-width, 1=left, 2=right), sorts each group, concatenates left→right
- Full-width items positioned relative to column content boundaries

### Fix 2: Garbled text detection + retry (`_is_garbled_text`, `_find_garbled_pages`)
- Detects garbled OCR output (unusual consonant clusters, no-vowel words, non-ASCII artifacts)
- Garbled pages retried at 300 DPI (up from 200), with Tesseract fallback
- Threshold: 15% garbled words triggers retry

### Fix 3: Page header/footer filtering (`_is_leaked_header_footer`)
- Pattern-based post-filter for running headers/footers Gemini misclassified
- Catches "ALL CAPS TEXT 157" and "158 AUTHOR" patterns
- Applied to all region types (heading, paragraph, etc.), not just paragraphs
- Also filters in Tesseract fallback path

### Fix 4: Paragraph deduplication (`_deduplicate_ocr_paragraphs`)
- Gemini assigns full-width content to both columns → exact duplicates after column sort
- Exact match + normalized matching (collapse whitespace, fix hyphens, normalize dashes/quotes)
- Fuzzy prefix matching (first 60 normalized chars) for near-duplicates across batches

### Fix 5: Two-column CSS layout in HTML builder
- `column` field added to ParagraphInfo model
- HTML builder groups consecutive column items into `<div class="two-column">` with CSS columns
- Responsive: collapses to single column on narrow screens

### Fix 6: Improved OCR prompt
- Stronger formatting detection instructions (italic abstract, block quotes, key terms)
- Explicit anti-duplication: "Do NOT assign full-width content to both column 1 and column 2"
- Landscape page handling note

### Mayer PDF Results (before → after)
- Leaked headers/footers: 4 → 0
- Garbled text: multiple lines → 0
- Duplicate paragraphs: 22 → ~0 (with fuzzy matching)
- Italic tags: 54 → 61 (better formatting detection)
- Two-column layout: now rendered
- Cost: $0.20 → $0.15
- Time: 13.3 min → 8.9 min

### Test count: 735 (up from 704)

## Up Next: LaTeX / Math Accessibility
- Research complete (see memory: `project_latex_feature.md`)
- Pipeline: detect LaTeX → Pandoc to HTML+MathML → AI generates natural language alt text → output
- Key tools: Pandoc (subprocess), MathJax 4 + SRE, MathCAT Python bindings
- MathML satisfies WCAG 1.3.1; screen readers (NVDA, VoiceOver, JAWS) all support it now
- PDF output: Formula tags with alt text (PDF/UA-1), eventually MathML associated files (PDF/UA-2)

## Other Upcoming
- End-to-end testing with real faculty documents
- Production deployment of OCR fixes
- Admin tooling improvements
- Future: Mac Mini on-premises deployment for university
