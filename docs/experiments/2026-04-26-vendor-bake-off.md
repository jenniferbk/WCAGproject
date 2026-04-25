# TikZ Description Bake-off: Claude Haiku 4.5 vs. Gemini 3 Flash Preview

**Date:** 2026-04-26
**Source data:** `testdocs/strategy_experiment/tikz_bake_off.json` (10 TikZ samples)
**Compared:** `claude-haiku-4-5-20251001` (Anthropic) vs. `gemini-3-flash-preview` (Google).
`gemini-2.5-flash` is excluded — 8/10 calls returned 429 free-tier quota errors.

## Bench economics

| Metric | Claude Haiku 4.5 | Gemini 3 Flash Preview |
|---|---|---|
| Total cost (10 samples) | $0.0330 | $0.0220 |
| Per sample | $0.0033 | $0.0022 (33% cheaper) |
| Avg latency | 5.1 s | 14.0 s |
| Reliability | 10/10 | 10/10 (1 transient retry on `tree`) |

For reference: gemini-2.5-flash hit free-tier daily-request quota and only completed 2/10 calls, so it is not graded.

## Grading table (1-5, higher is better)

| Sample | Vendor | Structural | Domain | Pedagogical |
|---|---|---|---|---|
| dfa | Claude | 5 | 5 | 5 |
| dfa | Gemini-3 | 5 | 5 | 5 |
| tree (BST) | Claude | 3 | 4 | 4 |
| tree (BST) | Gemini-3 | 5 | 5 | 5 |
| comm_diagram | Claude | 4 | 3 | 3 |
| comm_diagram | Gemini-3 | 5 | 4 | 4 |
| weighted_graph | Claude | 5 | 5 | 4 |
| weighted_graph | Gemini-3 | 5 | 5 | 5 |
| circuit (RC) | Claude | 5 | 5 | 4 |
| circuit (RC) | Gemini-3 | 5 | 5 | 5 |
| function_plot | Claude | 5 | 5 | 4 |
| function_plot | Gemini-3 | 5 | 5 | 5 |
| triangle_proof | Claude | 2 | 2 | 3 |
| triangle_proof | Gemini-3 | 4 | 4 | 4 |
| hasse | Claude | 5 | 5 | 5 |
| hasse | Gemini-3 | 5 | 5 | 5 |
| neural_net | Claude | 5 | 5 | 4 |
| neural_net | Gemini-3 | 5 | 5 | 5 |
| venn | Claude | 5 | 5 | 4 |
| venn | Gemini-3 | 5 | 5 | 5 |
| **Mean** | **Claude** | **4.4** | **4.4** | **4.1** |
| **Mean** | **Gemini-3** | **4.9** | **4.8** | **4.8** |

## Per-sample notes

**dfa.** Both correctly classify as a DFA, identify q_0 as the unique initial+accepting state, and trace all 10 transitions including the two self-loops. Gemini-3 adds a clean transition table at the end (`q_0 \xrightarrow{0} q_0`, etc.) that is directly usable as a screen-reader-friendly summary. Tie.

**tree (BST).** Claude says the tree has "**8 nodes**" — the source clearly defines 9 (8, 3, 1, 6, 4, 7, 10, 14, 13). Factual error. Claude also calls it "a binary search tree or general binary tree," hedging. Gemini-3 confidently identifies it as a BST and *verifies* the BST property by listing left/right subtrees of the root. Gemini-3 is materially better here.

**comm_diagram.** Claude attempts to state the commutativity equation: "**f ∘ h = g ∘ k**." This is wrong twice over: the standard convention is that the diagram commutes iff `h ∘ f = k ∘ g` (apply the first arrow, then the second), and Claude's `f ∘ h` is ill-typed (`h: B→D`, `f: A→B`, so `f ∘ h` cannot be formed). Gemini-3 doesn't claim a formula — it just says both paths terminate at D, which is correct but pedagogically thinner. Gemini-3 wins on accuracy; both miss the chance to state the actual commutativity condition.

**weighted_graph.** Both trace all 6 edges and weights correctly and identify s as source, t as sink. Claude correctly notes it's a DAG. Gemini-3 adds a "summary of connections" paragraph ("**s** branches out to **a** and **b**...") that reads like a trace, useful for sequential narration. Slight edge to Gemini-3 on usability.

**circuit (RC).** Both correctly identify as a series RC circuit using IEC symbols and trace the loop. Gemini-3 walks the loop clockwise from (0,0) which is easier to follow narratively. Both note the V_s, R, C labels with correct positions. Tie on accuracy; small edge to Gemini-3 on flow.

**function_plot.** Both correct. Gemini-3 goes further and computes specific waypoints on the curve: starts at (0, 1), touches x-axis at (1, 0), passes through (2, 1), reaches ~(3.2, 4.84). For a blind student trying to mentally picture the parabola, those concrete points are genuinely useful — Claude only describes the shape qualitatively. Gemini-3 is materially better on pedagogy.

**triangle_proof.** This is Claude's worst sample. Two factual errors:
1. Claude describes `\draw (B) -- (1.5, 0)` as "**Vertical line from B (0, 0) to M (1.5, 0)**." That segment is horizontal, not vertical (both endpoints have y=0).
2. Claude calls the AM segment "the altitude" and asserts the figure shows isosceles-triangle altitude properties. The source draws a *double-lined* segment only from A to (1.5, 1.3) — i.e., the segment stops halfway. Gemini-3 correctly says it "extends halfway down toward the base, ending at the center of the triangle." The caption labels it a *median*, not an altitude; Claude over-interprets. Gemini-3 is more cautious ("median or an altitude") and more faithful to what was actually drawn.

**hasse.** Both correctly identify the divisor lattice of 30, list all 8 nodes by level, and enumerate all 12 covering relations. Both note the prime-factorization meaning. Tie.

**neural_net.** Both correctly identify a 3-4-2 fully-connected feedforward MLP, count 12 input→hidden and 8 hidden→output edges, and note the blue/red color fills. Tie on accuracy; Gemini-3's explicit "Input Dimension: 3 / Hidden Dimension: 4 / Output Dimension: 2" closing summary is slightly cleaner.

**venn.** Both correctly map all 7 regions to the standard three-set partition. Gemini-3 adds the formal set-builder notation for each region (`A ∩ B^c ∩ C^c`, etc.), which is the form a student in a discrete-math course actually needs. Small edge to Gemini-3.

## Summary

- **Structural accuracy:** Gemini-3 wins. Claude made two clear factual errors (9 nodes called 8 in the BST; a horizontal segment called vertical in the triangle), plus a malformed composition equation in the commutative diagram. Gemini-3 had no comparable factual errors across the 10 samples.
- **Domain context:** Gemini-3 wins narrowly. Both classify diagram types correctly, but Claude over-interprets the triangle as an isosceles-altitude figure when the caption says median, and Claude's category-theory composition is wrong. Gemini-3 stays cautious where caution is warranted.
- **Pedagogical usefulness:** Gemini-3 wins. The transition table for the DFA, the concrete waypoints on the parabola, the BST-property check on the tree, and the set-builder notation on the Venn diagram are all things a blind student can directly use. Claude's descriptions are competent but stay closer to "what is drawn" without translating into the form a math/CS student needs.

**Recommendation:** For TikZ description in the comprehension/alt-text path, prefer **Gemini 3 Flash Preview**. It is 33% cheaper per call, equally reliable, and produces materially more accurate and more useful descriptions on this 10-sample bench. The 14 s vs. 5 s latency gap is real but acceptable for a per-figure call that is not on the user's interactive path. The one caveat: Gemini-3 outputs are longer (avg ~660 vs. ~570 output tokens), so if downstream prompts have tight context budgets, that's worth accounting for. The triangle_proof and BST results in particular suggest Claude Haiku 4.5 is willing to confidently assert details it hasn't actually verified from the source — a known failure mode for short-output models on dense visual specs.

Gemini 2.5 Flash is not a viable comparison here: at the free tier it hit daily quota after 2 successful calls. A paid-tier rerun would be needed before any judgment on it.
