# OCR Correction Bake-off: Claude Haiku 4.5 vs. Gemini 3 Flash Preview

**Date:** 2026-04-25 (run twice for confirmation)
**Source data:** `testdocs/strategy_experiment/ocr_bake_off.json`
**Compared:** Production scanned-page OCR-correction call site (`src/tools/scanned_page_ocr.py:_haiku_correct_text`) on Claude Haiku 4.5 (current) vs. Gemini 3 Flash Preview.

## TL;DR

**Keep Claude.** Gemini 3 Flash Preview reliably produces unparseable output on this task — wrong schema (bare array vs `{"corrections": [...]}`) AND mid-stream truncation at ~150 output tokens despite a 4096-token max set. Claude's outputs are imperfect (occasional unescaped nested quotes) but the production lenient parser handles ~75% of them and the rest degrade gracefully to no-op.

The 64% cost savings Gemini would have offered are moot — broken at any price.

## Data

Two pages of the Erlwanger PDF (fully scanned). Run twice. Results consistent across runs.

| Page | Vendor | Corrections found | JSON valid | Output tokens | Latency | Cost |
|---|---|---|---|---|---|---|
| 0 (10 Tesseract blocks) | Claude Haiku 4.5 | 0 first run, 3 second run | False (parser saved page-5 first run, dropped page-0) | 392 / 416 | 4.6s / 4.3s | $0.00506 / $0.00518 |
| 0 | Gemini 3 Flash Preview | 0 (truncated mid-JSON) | False | 150 / 149 | 20.6s / 18.9s | $0.00175 / $0.00175 |
| 5 (23 Tesseract blocks) | Claude Haiku 4.5 | 7 first run, 7 second run | False (lenient succeeds — ` ```json ` fence stripped) | 369 / 343 | 4.3s / 3.6s | $0.00556 / $0.00543 |
| 5 | Gemini 3 Flash Preview | 0 (truncated mid-JSON) | False | 153 / 151 | 26.5s / 23.7s | $0.00212 / $0.00212 |

## Why Gemini fails

Inspecting the raw outputs:

**Page 0** — single giant correction for id 1 (whole abstract paragraph as `corrected_text`), then output cut off mid-string with no closing `"`, no `]`, no `}`. 149 output tokens.

**Page 5** — produced as a **bare array** (`[{...}, {...}]`), not the `{"corrections": [...]}` schema the prompt requests. Cut off at the 4th item (`"id": 8, "corrected_text": "B:`). 151 output tokens.

Both runs hit ~150 output tokens despite `max_output_tokens=4096`. Likely cause: `response_mime_type="application/json"` interacting with Gemini 3 Flash Preview's thinking-budget consumption — the visible candidates_token_count is the small surfaced portion, while the model spends the rest of its budget on internal reasoning.

The schema deviation on page 5 is a separate issue: production code at `scanned_page_ocr.py:933` does `data.get("corrections", [])` which extracts 0 from a bare array. Even a complete bare-array response would parse to nothing.

## Why Claude is OK (mostly)

Claude wraps responses in `` ```json `` markdown fences. The lenient parser at `src/utils/json_repair.py:31-36` strips them, so production handles this. Page 5 (7 corrections) parses cleanly.

Page 0 first run had unescaped nested quotes inside `corrected_text` (e.g., `"Perspective on "Benny's Conception...""`), which neither the strict nor lenient parser repairs. Production handles this gracefully — `_haiku_correct_text` catches the exception and returns `{}`, processing continues with uncorrected Tesseract output.

So Claude's failure mode is "occasional silent no-op." Gemini's failure mode is "always silent no-op."

## Things to try when verification clears

Bench 2 was blocked partway through diagnosis by Gemini's identity-verification rate gating. When that clears, we should retry these to know whether to revisit:

1. Remove `response_mime_type="application/json"` — let Gemini emit free-form text, parse manually
2. Add `thinking_config=ThinkingConfig(thinking_budget=0)` — disable internal reasoning to free up the visible budget
3. Use `response_schema=` with an explicit Pydantic schema for forced shape compliance
4. Test against Gemini 2.5 Flash (non-preview) — preview-model quirks may not apply

If any of those produces complete, schema-compliant output, the cost case for Gemini comes back into play and we should re-grade quality.

## Production followup (independent of vendor)

The TWO Claude failure modes surfaced in Bench 2 are real production bugs:

1. **Markdown fence wrapping** is handled (parser strips it) ✓
2. **Unescaped nested quotes** — silently drops corrections with no visibility. Worth instrumenting:
   - Add a counter for parse-failure rate per call site (`scanned_page_ocr.py:_haiku_correct_text` and `_haiku_correct_table_cells`)
   - At ingest time, escape quotes in source text before showing it to the model? Risky — Claude may strip the escapes back out
   - Better: if first parse fails, retry once with explicit "use only single quotes inside string values" instruction

Both of these matter regardless of vendor and should ship before any vendor swap.

## Cost reference

If Gemini OCR correction worked, single-doc savings would be ~$0.007 per page (Gemini $0.0022 vs Claude $0.0055). At 100 pages/doc avg and 100 docs/week peak (UGA scale rough estimate), that's ~$70/week saved. Not nothing, but contingent on actually getting parseable output, which we don't have today.
