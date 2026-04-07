# Remediation Benchmark Results

Ran the full remediation pipeline on 125 documents from the 
Kumar et al. PDF Accessibility Benchmark.

## Aggregate

- **Documents processed:** 125
- **Succeeded:** 125 (100.0%)
- **Failed:** 0
- **Total issues before remediation:** 607
- **Total issues after remediation:** 262
- **Total issues fixed:** 345
- **Fix rate:** 56.8%
- **Total API cost:** $16.2265
- **Median time per doc:** 112.2s
- **Avg cost per doc:** $0.1298
- **Wall time:** 19200s (320.0 min)

## Structure tree improvements (the honest PDF/UA metric)

Our visual validator reads the page content and so cannot see iText struct tree changes. These numbers measure what screen readers and PDF/UA validators actually care about:

- **Docs with no struct tree → tagged:** 20/125
- **Docs with 0 headings → at least 1 heading:** 93/125
- **Total headings added across all docs:** 534
- **Docs with new figure alt text:** 84/125
- **Average accessibility gain per doc:** 1.62 (out of 4)

## Per-task breakdown

| Task | N | Issues before | Issues after | Fixed | Fix rate | Avg cost | Median time |
|---|---|---|---|---|---|---|---|
| alt_text_quality | 20 | 82 | 23 | 59 | 72% | $0.0713 | 79s |
| color_contrast | 15 | 84 | 40 | 44 | 52% | $0.1112 | 116s |
| fonts_readability | 15 | 78 | 37 | 41 | 53% | $0.0735 | 68s |
| functional_hyperlinks | 20 | 89 | 37 | 52 | 58% | $0.1731 | 166s |
| logical_reading_order | 15 | 66 | 29 | 37 | 56% | $0.1280 | 99s |
| semantic_tagging | 20 | 109 | 46 | 63 | 58% | $0.1004 | 102s |
| table_structure | 20 | 99 | 50 | 49 | 49% | $0.2321 | 184s |

## Per-document results

| Task | Label | OpenAlex ID | Headings | Fig alt | Visual Δ | Time | Cost |
|---|---|---|---|---|---|---|---|
| alt_text_quality | cannot_tell | W2460269320 ✓ | 0 → 8 | 1/1 → 1/1 | 6 → 2 | 105s | $0.1157 |
| alt_text_quality | cannot_tell | W2929611936 ✓ | 1 → 1 | 3/3 → 5/5 | 3 → 1 | 122s | $0.0870 |
| alt_text_quality | cannot_tell | W3005755974 ✓ | 0 → 1 | 1/1 → 1/1 | 5 → 1 | 61s | $0.0551 |
| alt_text_quality | cannot_tell | W4206740007 ✓ | 0 → 1 | 1/1 → 1/1 | 3 → 1 | 92s | $0.0735 |
| alt_text_quality | cannot_tell | W4383621582 ✓ | 29 → 2 | 1/1 → 1/1 | 3 → 0 | 67s | $0.0504 |
| alt_text_quality | failed | W2460269320 ✓ | 0 → 8 | 1/1 → 1/1 | 6 → 2 | 102s | $0.1106 |
| alt_text_quality | failed | W2929611936 ✓ | 1 → 1 | 3/3 → 5/5 | 3 → 1 | 121s | $0.0849 |
| alt_text_quality | failed | W3005755974 ✓ | 0 → 1 | 1/1 → 1/1 | 5 → 1 | 66s | $0.0564 |
| alt_text_quality | failed | W4206740007 ✓ | 0 → 1 | 1/1 → 1/1 | 3 → 1 | 90s | $0.0725 |
| alt_text_quality | failed | W4383621582 ✓ | 29 → 2 | 1/1 → 1/1 | 3 → 0 | 60s | $0.0514 |
| alt_text_quality | not_present | W2460269320 ✓ | 0 → 8 | 0/1 → 1/1 | 6 → 2 | 116s | $0.1165 |
| alt_text_quality | not_present | W2929611936 ✓ | 1 → 1 | 0/3 → 5/5 | 4 → 1 | 106s | $0.0869 |
| alt_text_quality | not_present | W3005755974 ✓ | 0 → 1 | 0/1 → 1/1 | 5 → 1 | 63s | $0.0534 |
| alt_text_quality | not_present | W4206740007 ✓ | 0 → 1 | 1/1 → 1/1 | 3 → 1 | 67s | $0.0645 |
| alt_text_quality | not_present | W4383621582 ✓ | 29 → 1 | 0/1 → 1/1 | 4 → 2 | 55s | $0.0488 |
| alt_text_quality | passed | W2460269320 ✓ | 0 → 4 | 1/1 → 1/1 | 6 → 2 | 79s | $0.0703 |
| alt_text_quality | passed | W2929611936 ✓ | 1 → 1 | 3/3 → 1/1 | 3 → 1 | 72s | $0.0566 |
| alt_text_quality | passed | W3005755974 ✓ | 0 → 1 | 1/1 → 1/1 | 5 → 1 | 215s | $0.0539 |
| alt_text_quality | passed | W4206740007 ✓ | 0 → 1 | 1/1 → 1/1 | 3 → 1 | 70s | $0.0628 |
| alt_text_quality | passed | W4383621582 ✓ | 1 → 2 | 1/1 → 1/1 | 3 → 1 | 72s | $0.0546 |
| color_contrast | cannot_tell | W1989729767 ✓ | 0 → 6 | 0/0 → 0/0 | 4 → 1 | 70s | $0.0855 |
| color_contrast | cannot_tell | W1996876153 ✓ | 0 → 6 | 0/3 → 5/5 | 5 → 2 | 141s | $0.1275 |
| color_contrast | cannot_tell | W2016642098 ✓ | 0 → 6 | 0/2 → 1/2 | 6 → 3 | 120s | $0.1243 |
| color_contrast | cannot_tell | W2595658205 ✓ | 0 → 7 | 0/3 → 2/2 | 6 → 2 | 116s | $0.1175 |
| color_contrast | cannot_tell | W2642438850 ✓ | 0 → 4 | 0/3 → 2/3 | 6 → 3 | 132s | $0.1153 |
| color_contrast | failed | W1989729767 ✓ | 0 → 6 | 0/0 → 0/0 | 5 → 2 | 60s | $0.0868 |
| color_contrast | failed | W1996876153 ✓ | 0 → 6 | 0/3 → 4/5 | 6 → 4 | 145s | $0.1342 |
| color_contrast | failed | W2016642098 ✓ | 0 → 9 | 0/2 → 2/2 | 6 → 4 | 106s | $0.1184 |
| color_contrast | failed | W2595658205 ✓ | 0 → 6 | 0/3 → 2/2 | 6 → 2 | 109s | $0.1188 |
| color_contrast | failed | W2642438850 ✓ | 0 → 4 | 0/3 → 3/3 | 6 → 2 | 121s | $0.1557 |
| color_contrast | passed | W1989729767 ✓ | 0 → 6 | 0/0 → 0/0 | 4 → 1 | 69s | $0.0845 |
| color_contrast | passed | W1996876153 ✓ | 0 → 6 | 0/3 → 3/5 | 6 → 4 | 130s | $0.1169 |
| color_contrast | passed | W2016642098 ✓ | 0 → 7 | 0/2 → 2/2 | 6 → 3 | 99s | $0.1109 |
| color_contrast | passed | W2595658205 ✓ | 0 → 6 | 0/3 → 2/2 | 6 → 2 | 122s | $0.1221 |
| color_contrast | passed | W2642438850 ✓ | 0 → 11 | 0/3 → 0/0 | 6 → 5 | 94s | $0.0498 |
| fonts_readability | cannot_tell | W2772922866 ✓ | 0 → 3 | 0/1 → 1/1 | 6 → 3 | 72s | $0.0754 |
| fonts_readability | cannot_tell | W2805701040 ✓ | 0 → 6 | 0/2 → 1/1 | 6 → 3 | 106s | $0.1155 |
| fonts_readability | cannot_tell | W2896131989 ✓ | 0 → 2 | 0/1 → 1/1 | 6 → 3 | 77s | $0.0766 |
| fonts_readability | cannot_tell | W4235175634 ✓ | 0 → 3 | 0/0 → 0/0 | 5 → 3 | 63s | $0.0683 |
| fonts_readability | cannot_tell | W4283371348 ✓ | 0 → 1 | 0/0 → 0/0 | 3 → 1 | 43s | $0.0502 |
| fonts_readability | failed | W2772922866 ✓ | 0 → 2 | 0/1 → 1/1 | 6 → 3 | 68s | $0.0678 |
| fonts_readability | failed | W2805701040 ✓ | 0 → 5 | 0/2 → 1/1 | 6 → 2 | 117s | $0.1093 |
| fonts_readability | failed | W2896131989 ✓ | 0 → 4 | 0/1 → 1/1 | 6 → 3 | 89s | $0.0790 |
| fonts_readability | failed | W4235175634 ✓ | 0 → 2 | 0/0 → 0/0 | 5 → 2 | 57s | $0.0593 |
| fonts_readability | failed | W4283371348 ✓ | 0 → 1 | 0/0 → 0/0 | 3 → 1 | 44s | $0.0534 |
| fonts_readability | passed | W2772922866 ✓ | 0 → 2 | 0/1 → 1/1 | 6 → 3 | 62s | $0.0634 |
| fonts_readability | passed | W2805701040 ✓ | 0 → 6 | 0/2 → 1/1 | 6 → 3 | 127s | $0.1088 |
| fonts_readability | passed | W2896131989 ✓ | 0 → 3 | 0/1 → 1/1 | 6 → 3 | 76s | $0.0653 |
| fonts_readability | passed | W4235175634 ✓ | 0 → 2 | 0/0 → 0/0 | 5 → 3 | 56s | $0.0560 |
| fonts_readability | passed | W4283371348 ✓ | 0 → 1 | 0/0 → 0/0 | 3 → 1 | 44s | $0.0537 |
| functional_hyperlinks | cannot_tell | W2893185172 ✓ | 0 → 2 | 0/0 → 6/6 | 5 → 1 | 165s | $0.1876 |
| functional_hyperlinks | cannot_tell | W2991007371 ✓ | 0 → 2 | 0/0 → 0/0 | 5 → 4 | 954s | $0.1123 |
| functional_hyperlinks | cannot_tell | W3005911753 ✓ | 0 → 1 | 0/0 → 3/3 | 5 → 1 | 110s | $0.2037 |
| functional_hyperlinks | cannot_tell | W3018272089 ✓ | 0 → 4 | 0/0 → 0/0 | 4 → 1 | 243s | $0.0911 |
| functional_hyperlinks | cannot_tell | W3069372847 ✓ | 0 → 2 | 0/0 → 3/4 | 6 → 4 | 137s | $0.2188 |
| functional_hyperlinks | failed | W2893185172 ✓ | 0 → 2 | 0/0 → 6/6 | 5 → 1 | 166s | $0.1821 |
| functional_hyperlinks | failed | W2991007371 ✓ | 0 → 2 | 0/0 → 0/0 | 5 → 4 | 823s | $0.1176 |
| functional_hyperlinks | failed | W3005911753 ✓ | 0 → 1 | 0/0 → 3/3 | 5 → 1 | 140s | $0.2444 |
| functional_hyperlinks | failed | W3018272089 ✓ | 0 → 2 | 0/0 → 0/0 | 4 → 1 | 86s | $0.1694 |
| functional_hyperlinks | failed | W3069372847 ✓ | 0 → 2 | 0/0 → 4/4 | 6 → 3 | 194s | $0.2426 |
| functional_hyperlinks | not_present | W2893185172 ✓ | 0 → 1 | 0/3 → 6/6 | 3 → 1 | 147s | $0.1963 |
| functional_hyperlinks | not_present | W2991007371 ✓ | 0 → 4 | 0/7 → 0/0 | 3 → 2 | 1116s | $0.0648 |
| functional_hyperlinks | not_present | W3005911753 ✓ | 0 → 1 | 0/9 → 3/3 | 3 → 1 | 96s | $0.1595 |
| functional_hyperlinks | not_present | W3018272089 ✓ | 0 → 2 | 0/4 → 0/0 | 4 → 1 | 120s | $0.1536 |
| functional_hyperlinks | not_present | W3069372847 ✓ | 0 → 1 | 0/3 → 2/4 | 6 → 3 | 174s | $0.2378 |
| functional_hyperlinks | passed | W2893185172 ✓ | 0 → 2 | 0/3 → 6/6 | 4 → 1 | 194s | $0.2068 |
| functional_hyperlinks | passed | W2991007371 ✓ | 0 → 4 | 0/18 → 0/0 | 5 → 4 | 884s | $0.1197 |
| functional_hyperlinks | passed | W3005911753 ✓ | 0 → 1 | 0/9 → 3/3 | 3 → 1 | 113s | $0.1645 |
| functional_hyperlinks | passed | W3018272089 ✓ | 0 → 2 | 0/4 → 0/0 | 4 → 1 | 85s | $0.1390 |
| functional_hyperlinks | passed | W3069372847 ✓ | 0 → 1 | 0/8 → 3/3 | 4 → 1 | 166s | $0.2507 |
| logical_reading_order | cannot_tell | W2004322054 ✓ | 0 → 6 | 0/0 → 1/1 | 6 → 2 | 91s | $0.1040 |
| logical_reading_order | cannot_tell | W2039213038 ✓ | 0 → 6 | 1/1 → 1/2 | 5 → 2 | 105s | $0.1180 |
| logical_reading_order | cannot_tell | W2510625382 ✓ | 0 → 5 | 0/0 → 1/1 | 3 → 1 | 95s | $0.0846 |
| logical_reading_order | cannot_tell | W2953207266 ✓ | 0 → 10 | 0/0 → 6/16 | 5 → 4 | 263s | $0.2672 |
| logical_reading_order | cannot_tell | W4230438091 ✓ | 4 → 4 | 0/0 → 0/0 | 3 → 1 | 54s | $0.0676 |
| logical_reading_order | failed | W2004322054 ✓ | 0 → 4 | 0/0 → 0/1 | 6 → 2 | 109s | $0.1101 |
| logical_reading_order | failed | W2039213038 ✓ | 0 → 10 | 1/1 → 1/2 | 5 → 3 | 112s | $0.1234 |
| logical_reading_order | failed | W2510625382 ✓ | 0 → 6 | 0/0 → 1/1 | 3 → 1 | 85s | $0.0806 |
| logical_reading_order | failed | W2953207266 ✓ | 0 → 4 | 0/0 → 6/16 | 5 → 3 | 327s | $0.2661 |
| logical_reading_order | failed | W4230438091 ✓ | 4 → 5 | 0/0 → 0/0 | 3 → 1 | 46s | $0.0633 |
| logical_reading_order | passed | W2004322054 ✓ | 0 → 6 | 0/0 → 1/1 | 6 → 2 | 99s | $0.1076 |
| logical_reading_order | passed | W2039213038 ✓ | 0 → 6 | 1/1 → 1/2 | 5 → 2 | 109s | $0.1142 |
| logical_reading_order | passed | W2510625382 ✓ | 0 → 5 | 0/0 → 1/1 | 3 → 1 | 82s | $0.0845 |
| logical_reading_order | passed | W2953207266 ✓ | 2 → 4 | 0/1 → 12/16 | 5 → 3 | 287s | $0.2705 |
| logical_reading_order | passed | W4230438091 ✓ | 4 → 2 | 0/0 → 0/0 | 3 → 1 | 44s | $0.0579 |
| semantic_tagging | cannot_tell | W2067815167 ✓ | 0 → 4 | 0/2 → 2/2 | 6 → 2 | 99s | $0.0891 |
| semantic_tagging | cannot_tell | W2390023818 ✓ | 0 → 7 | 0/1 → 0/4 | 6 → 4 | 139s | $0.1449 |
| semantic_tagging | cannot_tell | W2895738059 ✓ | 0 → 7 | 0/5 → 5/6 | 6 → 3 | 159s | $0.1234 |
| semantic_tagging | cannot_tell | W2951147986 ✓ | 0 → 5 | 0/0 → 1/1 | 4 → 1 | 60s | $0.0617 |
| semantic_tagging | cannot_tell | W984760328 ✓ | 0 → 5 | 0/1 → 1/1 | 6 → 2 | 90s | $0.0857 |
| semantic_tagging | failed | W2067815167 ✓ | 0 → 4 | 0/2 → 2/2 | 6 → 3 | 84s | $0.0840 |
| semantic_tagging | failed | W2390023818 ✓ | 0 → 6 | 0/1 → 5/5 | 6 → 3 | 143s | $0.1401 |
| semantic_tagging | failed | W2895738059 ✓ | 0 → 7 | 0/5 → 6/6 | 6 → 2 | 131s | $0.1264 |
| semantic_tagging | failed | W2951147986 ✓ | 0 → 5 | 0/0 → 1/1 | 4 → 1 | 75s | $0.0642 |
| semantic_tagging | failed | W984760328 ✓ | 0 → 4 | 0/1 → 1/1 | 6 → 2 | 89s | $0.0882 |
| semantic_tagging | not_present | W2067815167 ✓ | 0 → 4 | 0/0 → 2/2 | 6 → 3 | 92s | $0.0871 |
| semantic_tagging | not_present | W2390023818 ✓ | 0 → 7 | 0/0 → 1/1 | 6 → 4 | 180s | $0.1479 |
| semantic_tagging | not_present | W2895738059 ✓ | 0 → 7 | 0/0 → 6/6 | 6 → 2 | 176s | $0.1248 |
| semantic_tagging | not_present | W2951147986 ✓ | 0 → 4 | 0/0 → 1/1 | 4 → 1 | 310s | $0.0538 |
| semantic_tagging | not_present | W984760328 ✓ | 0 → 4 | 0/0 → 1/1 | 6 → 2 | 83s | $0.0883 |
| semantic_tagging | passed | W2067815167 ✓ | 1 → 4 | 0/2 → 2/2 | 6 → 2 | 102s | $0.0893 |
| semantic_tagging | passed | W2390023818 ✓ | 4 → 7 | 0/1 → 1/5 | 6 → 3 | 157s | $0.1239 |
| semantic_tagging | passed | W2895738059 ✓ | 10 → 7 | 0/5 → 5/6 | 4 → 3 | 122s | $0.1168 |
| semantic_tagging | passed | W2951147986 ✓ | 4 → 10 | 0/0 → 1/1 | 5 → 1 | 81s | $0.0805 |
| semantic_tagging | passed | W984760328 ✓ | 3 → 4 | 0/2 → 1/1 | 4 → 2 | 93s | $0.0875 |
| table_structure | cannot_tell | W1974692547 ✓ | 11 → 6 | 0/1 → 27/27 | 5 → 2 | 324s | $0.3461 |
| table_structure | cannot_tell | W2296421107 ✓ | 7 → 8 | 0/7 → 1/1 | 5 → 3 | 161s | $0.2253 |
| table_structure | cannot_tell | W2404866520 ✓ | 26 → 32 | 0/3 → 1/1 | 5 → 3 | 185s | $0.2546 |
| table_structure | cannot_tell | W2810718311 ✓ | 9 → 22 | 0/2 → 3/4 | 5 → 3 | 184s | $0.2359 |
| table_structure | cannot_tell | W2922538610 ✓ | 5 → 10 | 0/3 → 2/2 | 4 → 1 | 162s | $0.1994 |
| table_structure | failed | W1974692547 ✓ | 11 → 17 | 0/1 → 15/27 | 5 → 3 | 337s | $0.2496 |
| table_structure | failed | W2296421107 ✓ | 7 → 11 | 0/7 → 1/1 | 5 → 3 | 178s | $0.2036 |
| table_structure | failed | W2404866520 ✓ | 26 → 27 | 0/3 → 1/1 | 5 → 3 | 169s | $0.2373 |
| table_structure | failed | W2810718311 ✓ | 9 → 14 | 0/5 → 3/4 | 5 → 3 | 169s | $0.2140 |
| table_structure | failed | W2922538610 ✓ | 5 → 10 | 0/3 → 2/2 | 4 → 1 | 195s | $0.2130 |
| table_structure | not_present | W1974692547 ✓ | 0 → 24 | 0/0 → 24/27 | 6 → 3 | 389s | $0.2810 |
| table_structure | not_present | W2296421107 ✓ | 0 → 9 | 0/0 → 1/1 | 6 → 3 | 158s | $0.1839 |
| table_structure | not_present | W2404866520 ✓ | 0 → 34 | 0/0 → 1/1 | 6 → 3 | 230s | $0.2426 |
| table_structure | not_present | W2810718311 ✓ | 0 → 13 | 0/0 → 3/4 | 5 → 3 | 163s | $0.2282 |
| table_structure | not_present | W2922538610 ✓ | 0 → 7 | 0/0 → 2/2 | 4 → 1 | 187s | $0.1623 |
| table_structure | passed | W1974692547 ✓ | 11 → 7 | 0/1 → 27/27 | 5 → 2 | 337s | $0.3395 |
| table_structure | passed | W2296421107 ✓ | 7 → 7 | 0/7 → 0/1 | 5 → 4 | 137s | $0.1979 |
| table_structure | passed | W2404866520 ✓ | 26 → 33 | 0/3 → 1/1 | 5 → 3 | 185s | $0.1847 |
| table_structure | passed | W2810718311 ✓ | 9 → 23 | 0/2 → 4/4 | 5 → 2 | 175s | $0.2525 |
| table_structure | passed | W2922538610 ✓ | 5 → 6 | 0/3 → 2/2 | 4 → 1 | 146s | $0.1896 |
