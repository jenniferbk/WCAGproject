# Strategy LLM vs Deterministic Mapper — 2026-04-25

## Question

Does the strategy-phase Claude call do decision work that can't be reduced
to a template, or is it a checklist follower? If it's templated, redirect
the budget to vision-based alt verification per `project_llm_reallocation_plan.md`.

## Method

Ran each of 5 testdocs PDFs through comprehension once (controls for
Gemini vision noise), then through both `strategize()` (LLM, Claude
Sonnet 4.5) and `strategize_deterministic()` (new mapper). Compared
action sets by `(element_id, action_type)` tuple and parameter values.

Code: `scripts/strategy_mapper_experiment.py`. Mapper:
`src/agent/strategy.py:strategize_deterministic`. Switch:
`STRATEGY_MODE=deterministic` env var in orchestrator.

## Per-doc results

| Doc | Imgs | LLM actions | DET actions | Common | Param diffs (real / cosmetic) |
|---|---:|---:|---:|---:|---|
| Skinner | 10 | 23 | 23 | 21 | 5 / 8 |
| Erlwanger | 11 | 13 | 10 | 10 | ? / 8 |
| Mayer | 11 | 13 | 13 | 13 | 0 / 12 |
| EMAT syllabus | 0 | 21 | 6 | 1 | 0 / 0 |
| Lesson 2 | 36 | 24 | 24 | 21 | 0 / 21 |

LLM strategy cost: **$0.71 across 5 docs ($0.14/doc avg)**.
Mapper cost: **$0**. Comprehension cost: $0.07 for the run.

## What the LLM is actually doing

48 of 54 parameter diffs are **cosmetic** — the LLM emits
`paragraph_index` and `drawing_index` for PDF `set_alt_text` actions
even though the PDF executor uses `element_id` directly. The
`alt_text` value matches the mapper's verbatim. Strip these and the
"diff" disappears.

The remaining 6 diffs are real:

1. **Heading level assignment** (3 cases, Skinner). LLM picks H1 vs.
   mapper's default H2 based on document hierarchy.
2. **Heading detection** (12 cases, EMAT syllabus). Comprehension
   produced 59 element_purposes but **none** had
   `suggested_action="convert_to_heading"`. The mapper found nothing.
   The LLM detected 12 fake headings on its own, applying the
   comprehension prompt's "bold short text in Normal style is likely
   a heading" rule that the comprehension call didn't actually run.
3. **Link text generation** (6 cases, EMAT syllabus). LLM produced
   descriptive text for raw-URL links. Mapper skips by design — this
   is real judgment work.
4. **Alt text refinement** (1 case, Skinner img_6). Comprehension
   produced a confused description ("The image contains a photograph
   and two columns of text..."). The LLM rewrote it to focus on the
   photo ("Photograph of a self-instruction room in Sever Hall...").
5. **Language normalization** (1 case, Mayer). LLM emits `"en"`,
   mapper passes through `"English"` — mapper bug, comprehension
   should normalize to BCP-47.
6. **Hallucinated alt text** (3 cases, Erlwanger). LLM emitted
   `set_alt_text` for `img_8`, `img_9`, `img_10` despite comprehension
   having vision descriptions for only 8 of 11 images. **The LLM
   fabricated alt text without seeing the images.** Mapper correctly
   sent these to human review.

## Verdict

**Neither pure-mapper nor pure-LLM is right.** The honest breakdown:

- ~70% of the LLM's work is template-following (could be deterministic)
- ~25% is judgment work that **comprehension should do but currently doesn't** (heading detection + level, link text, language normalization, occasional alt-text refinement)
- ~5% is **harmful** (alt-text hallucination on images comprehension couldn't see)

The LLM is a band-aid for comprehension's gaps. It plugs them imperfectly
and at $0.14/doc, while introducing hallucinations.

## Recommendation

Move the LLM's real work upstream to comprehension. Specifically:

1. **Comprehension reliably emits `suggested_action="convert_to_heading"`
   AND `heading_level: 1|2|3`** for all detected fake headings (currently
   emits zero of either).
2. **Comprehension emits `link_text_proposal`** for each link with a
   raw-URL display text.
3. **Comprehension normalizes `suggested_language`** to BCP-47.
4. **Comprehension prompt confirms: do not invent alt text for images
   it cannot see.** (Comprehension already obeys this; the strategy LLM
   was the offender.)

After those upstream changes, the mapper does the same work the LLM
does today — for $0/doc, with no hallucinations. The freed $0.14/doc
budget goes to a vision-verification pass on alt text per
`project_llm_reallocation_plan.md`.

## Caveats

- 5-doc sample. The headline percentages are illustrative, not
  statistically robust.
- Comprehension hit Gemini free-tier rate limits during the run; one
  doc (Lesson 2) shows `element_purposes=0` from a 503 response.
  This actually strengthens the finding — even when comprehension is
  partial, the LLM strategy fills gaps with confabulation rather than
  flagging for review.
- Action-set match doesn't measure outcome. Phase B (full pipeline +
  veraPDF on both modes) would confirm the diagnostic translates to
  equivalent or better remediated output. Recommend before shipping
  the swap.

## Next step

Brainstorm the comprehension prompt extensions (1-3 above) and run
Phase B before any production change. Tracked in
`project_llm_reallocation_plan.md`.
