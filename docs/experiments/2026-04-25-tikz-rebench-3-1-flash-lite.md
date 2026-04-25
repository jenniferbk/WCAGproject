# TikZ Bake-Off Rebench: Claude Haiku 4.5 vs Gemini 3.1 Flash Lite Preview

**Date:** 2026-04-25
**Source:** `testdocs/strategy_experiment/tikz_bake_off.json` (10 samples)
**Models compared:** `claude-haiku-4-5-20251001` (Anthropic) vs `gemini-3.1-flash-lite-preview` (Google)

## 1. Grading Table

Scores are 1–5 (5 = best). SA = Structural Accuracy, DC = Domain Context, PU = Pedagogical Usefulness.

| # | Sample | Claude SA | Claude DC | Claude PU | Gemini SA | Gemini DC | Gemini PU |
|---|---|---|---|---|---|---|---|
| 1 | DFA (binary mod 5) | 4 | 4 | 4 | 4 | 5 | 4 |
| 2 | Binary search tree | 2 | 4 | 3 | 5 | 5 | 5 |
| 3 | Commutative diagram | 5 | 5 | 5 | 5 | 5 | 5 |
| 4 | Weighted graph (Dijkstra) | 5 | 4 | 4 | 5 | 4 | 5 |
| 5 | RC circuit | 5 | 5 | 5 | 5 | 5 | 5 |
| 6 | Function plot $(x-1)^2$ | 4 | 4 | 4 | 5 | 5 | 5 |
| 7 | Triangle with median | 2 | 2 | 3 | 3 | 3 | 3 |
| 8 | Hasse diagram (div 30) | 3 | 5 | 4 | 5 | 5 | 5 |
| 9 | Neural net (3–4–2) | 5 | 5 | 5 | 5 | 5 | 5 |
| 10 | Venn diagram (3 sets) | 3 | 4 | 3 | 5 | 5 | 5 |
| **Avg** | | **3.8** | **4.2** | **4.0** | **4.7** | **4.7** | **4.7** |

## 2. Per-Sample Notes

**1. DFA.** Both models correctly identify the DFA, five states, initial+accepting marker on $q_0$, and all ten transitions with labels. Claude does not state the caption-level fact ("divisible by 5"); Gemini calls it a "DFA" by name (Claude says "FSA / FSM"). Both stumble slightly on bend direction descriptions for the leftward `bend left` arrows (TikZ's bend semantics are genuinely ambiguous in prose), but neither makes a factual edge error.

**2. Binary search tree.** **Claude says "8 nodes in total" — wrong.** The tree has 9 nodes (8, 3, 10, 1, 6, 14, 4, 7, 13). Claude's per-level enumeration actually lists all 9, so the summary contradicts its own breakdown. Gemini says "9 nodes in total" and gets it right. Gemini also correctly invokes the BST invariant ("left subtree contains smaller values"); Claude only says "binary search tree or general binary tree structure," hedging away from the BST property. Material loss for Claude on a basic counting task.

**3. Commutative square.** Both correctly identify the diagram, the four objects, four morphisms, and — crucially — get the composition direction right: $h \circ f = k \circ g$ (function-composition order, applied right-to-left). No errors either side. Effectively tied; Gemini's prose is slightly more readable for a blind student tracing two paths.

**4. Weighted graph.** Both list all 6 edges and weights correctly. Claude calls out the cycle implication and "two paths to t" explicitly, which is helpful for shortest-path framing. Gemini's spatial layout description ("$t$ directly below $c$") is more concrete. Tie on accuracy; Gemini slightly more navigable.

**5. RC circuit.** Both correct on the rectangular loop, $V_s$ on the left, $R$ then $C$ in series on the right. Claude adds "first-order RC circuit / transient response / time constants" — useful pedagogical hook. Gemini's clockwise traversal is cleaner for a screen-reader user. Tie.

**6. Function plot.** Gemini gives concrete sample points: "starts at (0,1)... touches the x-axis at (1,0)... passes through (2,1)" — these are correct evaluations of $(x-1)^2$ and let a blind student picture the curve. Claude's description is accurate but more abstract ("standard upward-opening parabola"). Gemini wins on pedagogical usefulness here.

**7. Triangle with median.** Both struggle. The caption is explicit: "**median** AM; M is midpoint of BC." **Claude calls AM an "altitude" four times and never says "median"** — domain misclassification. It also invents a "perpendicular indicator" reading of the angle arc, which is not what the arc shows. Gemini hedges with "median/altitude indicator" and at least mentions median. Both miss that the `\draw[double]` partial segment is a **congruence tick mark** (standard geometry convention for marking equal lengths or a specific segment), reading it instead as a styled line. Gemini wins on caption fidelity but neither description is great.

**8. Hasse diagram.** **Claude says "7 nodes"; the diagram has 8** (1, 2, 3, 5, 6, 10, 15, 30). Claude's enumeration lists all 8, contradicting its own count — same self-contradiction pattern as the BST. Both correctly identify it as a divisibility lattice / Hasse diagram of divisors of 30 and explain the prime-factor structure well. Gemini gets the count right.

**9. Neural network.** Both correct: 3-4-2 fully-connected feedforward, 12 + 8 = 20 edges, color coding noted, layer labels noted. Tie.

**10. Venn diagram.** Both correctly map regions 1–7 to the seven subsets (A only, B only, C only, A∩B, A∩C, B∩C, A∩B∩C). **Claude's region table is malformed** — the markdown header has three columns ("Region | Label | Location") but the rows have only two cells, so a screen reader would announce columns shifted by one. Gemini renders the regions as a clean numbered list with coordinates, plus a closing geometric summary ("triangular intersection surrounded by three lens-shaped intersections surrounded by three crescents") that is genuinely useful spatial language for a non-sighted reader.

## 3. Summary

**Structural accuracy:** Gemini wins (4.7 vs 3.8). Claude made two outright counting errors (BST: 8 vs actual 9; Hasse: 7 vs actual 8) where its own enumeration contradicted its own summary. Gemini hit zero counting errors across all ten samples. Both models had similar small ambiguities on TikZ bend-direction prose.

**Domain context:** Gemini wins (4.7 vs 4.2). Gemini named the DFA correctly (vs Claude's looser "FSA/FSM"), invoked the BST invariant explicitly, and mentioned "median" on the triangle. Claude's "altitude"-only reading of the triangle is the worst single domain miss in the run.

**Pedagogical usefulness:** Gemini wins (4.7 vs 4.0). Concrete sample points on the parabola, region geometry summary on the Venn diagram, and clockwise traversal on the circuit all matter more for a blind student than Claude's slightly more elaborate prose.

**Recommendation:** **Switch the TikZ description path to Gemini 3.1 Flash Lite Preview.** It is materially better on structural accuracy in this run — no fabricated counts — and matches or beats Claude on context and pedagogy. The cost difference is dramatic: $0.00297 vs $0.03384 total across 10 samples, a **11.4× cost reduction**. Gemini is also faster (~3.1 s vs ~5.5 s avg latency). Both models hit 100% reliability with zero retries.

The main caveat is the triangle case, where neither model recognized the `\draw[double]` congruence tick mark — that is a TikZ-convention gap, not a Gemini-specific weakness, and would need a prompt-side fix (or a short "common TikZ conventions" reference appended to the system prompt) regardless of vendor.

## 4. Cost / Latency (from this bench run)

| Metric | Claude Haiku 4.5 | Gemini 3.1 Flash Lite Preview |
|---|---|---|
| Total cost (10 samples) | $0.03384 | $0.00297 |
| Cost ratio | 1.0× | 0.088× (11.4× cheaper) |
| Avg latency / sample | ~5.5 s | ~3.1 s |
| Reliability | 100% (0 retries) | 100% (0 retries) |
