# PDF Accessibility Benchmark Results

Benchmark: [Kumar et al., ASSETS 2025](https://github.com/Anukriti12/PDF-Accessibility-Benchmark)

**Overall accuracy: 65.60%** (82/125 correct)
**Elapsed: 181.1s**

## Comparison to Published Baselines

| System | Overall Accuracy |
|--------|-----------------|
| **A11y Remediate (this tool)** | **65.60%** |
| GPT-4-Turbo | 85.00% |
| GPT-4o-Vision | 81.00% |
| Gemini-1.5 | 75.00% |
| Claude-3.5 | 74.00% |
| Llama-3.2 | 42.00% |

## Per-Task Accuracy

| Task | Correct | Total | Accuracy |
|------|---------|-------|----------|
| alt_text_quality | 13 | 20 | 65.00% |
| color_contrast | 9 | 15 | 60.00% |
| fonts_readability | 9 | 15 | 60.00% |
| functional_hyperlinks | 15 | 20 | 75.00% |
| logical_reading_order | 8 | 15 | 53.33% |
| semantic_tagging | 13 | 20 | 65.00% |
| table_structure | 15 | 20 | 75.00% |

## Confusion Matrices

Rows = ground truth, columns = predicted

### alt_text_quality

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 0 | 0 | 5 |
| failed | 0 | 4 | 0 | 1 |
| not_present | 0 | 0 | 4 | 1 |
| passed | 0 | 0 | 0 | 5 |

### color_contrast

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 0 | 1 | 4 |
| failed | 0 | 4 | 1 |
| passed | 0 | 0 | 5 |

### fonts_readability

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 0 | 4 | 1 |
| failed | 0 | 5 | 0 |
| passed | 0 | 1 | 4 |

### functional_hyperlinks

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 5 | 0 | 0 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 0 | 5 |

### logical_reading_order

| gold ↓ / predicted → | cannot_tell | failed | passed |
|---|---|---|---|
| cannot_tell | 2 | 1 | 2 |
| failed | 0 | 4 | 1 |
| passed | 2 | 1 | 2 |

### semantic_tagging

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 4 | 0 | 1 |
| failed | 0 | 4 | 0 | 1 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 1 | 0 | 4 |

### table_structure

| gold ↓ / predicted → | cannot_tell | failed | not_present | passed |
|---|---|---|---|---|
| cannot_tell | 0 | 0 | 0 | 5 |
| failed | 0 | 5 | 0 | 0 |
| not_present | 0 | 0 | 5 | 0 |
| passed | 0 | 0 | 0 | 5 |
