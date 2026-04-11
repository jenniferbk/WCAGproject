# PDF Accessibility Benchmark Results

Benchmark: [Kumar et al., ASSETS 2025](https://github.com/Anukriti12/PDF-Accessibility-Benchmark)

**Overall accuracy: 96.80%** (121/125 correct)
**Elapsed: 1081.6s**

## Comparison to Published Baselines

| System | Overall Accuracy |
|--------|-----------------|
| **A11y Remediate (this tool)** | **96.80%** |
| GPT-4-Turbo | 85.00% |
| GPT-4o-Vision | 81.00% |
| Gemini-1.5 | 75.00% |
| Claude-3.5 | 74.00% |
| Llama-3.2 | 42.00% |

## Per-Task Accuracy

| Task | Correct | Total | Accuracy |
|------|---------|-------|----------|
| alt_text_quality | 19 | 20 | 95.00% |
| color_contrast | 15 | 15 | 100.00% |
| fonts_readability | 15 | 15 | 100.00% |
| functional_hyperlinks | 20 | 20 | 100.00% |
| logical_reading_order | 12 | 15 | 80.00% |
| semantic_tagging | 20 | 20 | 100.00% |
| table_structure | 20 | 20 | 100.00% |

## Confusion Matrices

Rows = ground truth, columns = predicted

### alt_text_quality

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 5 | 0 | 0 | 0 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 4 | 1 |
| passed | 0 | 0 | 0 | 5 |

### color_contrast

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 5 | 0 | 0 |
| failed | 0 | 5 | 0 |
| passed | 0 | 0 | 5 |

### fonts_readability

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 5 | 0 | 0 |
| failed | 0 | 5 | 0 |
| passed | 0 | 0 | 5 |

### functional_hyperlinks

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 5 | 0 | 0 | 0 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 0 | 5 |

### logical_reading_order

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 5 | 0 | 0 |
| failed | 0 | 4 | 1 |
| passed | 2 | 0 | 3 |

### semantic_tagging

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 5 | 0 | 0 | 0 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 0 | 5 |

### table_structure

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 5 | 0 | 0 | 0 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 0 | 5 |
