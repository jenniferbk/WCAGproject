# PDF Accessibility Benchmark Results

Benchmark: [Kumar et al., ASSETS 2025](https://github.com/Anukriti12/PDF-Accessibility-Benchmark)

**Overall accuracy: 31.67%** (38/120 correct)
**Elapsed: 270.4s**

## Comparison to Published Baselines

| System | Overall Accuracy |
|--------|-----------------|
| **A11y Remediate (this tool)** | **31.67%** |
| GPT-4-Turbo | 85.00% |
| GPT-4o-Vision | 81.00% |
| Gemini-1.5 | 75.00% |
| Claude-3.5 | 74.00% |
| Llama-3.2 | 42.00% |

## Per-Task Accuracy

| Task | Correct | Total | Accuracy |
|------|---------|-------|----------|
| alt_text_quality | 4 | 15 | 26.67% |
| color_contrast | 6 | 15 | 40.00% |
| fonts_readability | 5 | 15 | 33.33% |
| functional_hyperlinks | 9 | 20 | 45.00% |
| logical_reading_order | 5 | 15 | 33.33% |
| semantic_tagging | 5 | 20 | 25.00% |
| table_structure | 4 | 20 | 20.00% |

## Confusion Matrices

Rows = ground truth, columns = predicted

### alt_text_quality

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 4 | 0 | 1 |
| failed | 0 | 4 | 0 | 1 |
| not_present | 0 | 5 | 0 | 0 |
| passed | 0 | 0 | 0 | 0 |

### color_contrast

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 0 | 3 | 2 |
| failed | 0 | 5 | 0 |
| passed | 0 | 4 | 1 |

### fonts_readability

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 5 | 0 | 0 |
| failed | 5 | 0 | 0 |
| passed | 5 | 0 | 0 |

### functional_hyperlinks

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 0 | 0 | 5 |
| failed | 0 | 0 | 0 | 5 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 1 | 0 | 4 |

### logical_reading_order

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 5 | 0 | 0 |
| failed | 5 | 0 | 0 |
| passed | 5 | 0 | 0 |

### semantic_tagging

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 0 | 5 | 0 |
| failed | 0 | 0 | 5 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 5 | 0 |

### table_structure

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 3 | 0 | 2 | 0 |
| failed | 3 | 0 | 2 | 0 |
| not_present | 4 | 0 | 1 | 0 |
| passed | 3 | 0 | 2 | 0 |

## Errors

- Missing: data/processed/alt_text_quality/passed/W2460269320.pdf
- Missing: data/processed/alt_text_quality/passed/W2929611936.pdf
- Missing: data/processed/alt_text_quality/passed/W3005755974.pdf
- Missing: data/processed/alt_text_quality/passed/W4206740007.pdf
- Missing: data/processed/alt_text_quality/passed/W4383621582.pdf
