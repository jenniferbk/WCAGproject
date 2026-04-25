"""Bench 1 — TikZ description vendor bake-off.

Compares Claude Haiku 4.5 (current production) vs Gemini 2.5 Flash vs
Gemini 3 Flash Preview on the TikZ-description task. Same prompt, same
samples, same temperature. Captures output text, input/output tokens,
estimated cost, latency.

Output: testdocs/strategy_experiment/tikz_bake_off.json + a printed
summary. Manual quality grading happens after by reading the descriptions.

Run: python3 scripts/bench_tikz_vendor.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from anthropic import Anthropic
from dotenv import load_dotenv
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bench_tikz")

PROMPT_PATH = Path(__file__).parent.parent / "src" / "prompts" / "tikz_description.md"
OUTPUT_PATH = Path(__file__).parent.parent / "testdocs" / "strategy_experiment" / "tikz_bake_off.json"

# Pricing per million tokens — current as of April 2026 verified from web search
PRICING = {
    "claude-haiku-4-5-20251001":   {"input": 1.00, "output": 5.00},
    "gemini-2.5-flash":            {"input": 0.15, "output": 0.60},
    "gemini-3-flash-preview":      {"input": 0.50, "output": 3.00},
}

def _t(*lines: str) -> str:
    """Helper: build a TikZ source string from raw lines."""
    return "\n".join(lines)


# 10 TikZ samples covering diverse math / CS pedagogy domains
SAMPLES = [
    {
        "id": "dfa",
        "kind": "state machine (DFA)",
        "source": _t(
            r"\begin{tikzpicture}[shorten >=1pt,node distance=2cm,on grid,auto]",
            r"  \node[state, accepting, initial] (q_0)   {$q_0$};",
            r"  \node[state] (q_1) [right=of q_0] {$q_1$};",
            r"  \node[state] (q_2) [right=of q_1] {$q_2$};",
            r"  \node[state] (q_3) [right=of q_2] {$q_3$};",
            r"  \node[state] (q_4) [right=of q_3] {$q_4$};",
            r"  \path[->]",
            r"    (q_0) edge [loop above] node {0} (q_0)",
            r"          edge node {1} (q_1)",
            r"    (q_1) edge node {0} (q_2)",
            r"          edge [bend right=-30] node {1} (q_3)",
            r"    (q_2) edge [bend left] node {1} (q_0)",
            r"          edge [bend right=-30] node {0} (q_4)",
            r"    (q_3) edge node {1} (q_2)",
            r"          edge [bend left] node {0} (q_1)",
            r"    (q_4) edge node {0} (q_3)",
            r"          edge [loop below] node {1} (q_4);",
            r"\end{tikzpicture}",
        ),
        "caption": "DFA accepting binary strings divisible by 5",
    },
    {
        "id": "tree",
        "kind": "binary search tree",
        "source": _t(
            r"\begin{tikzpicture}[level distance=1.2cm,",
            r"  level 1/.style={sibling distance=4cm},",
            r"  level 2/.style={sibling distance=2cm},",
            r"  every node/.style={circle, draw, minimum size=8mm}]",
            r"  \node {8}",
            r"    child {node {3}",
            r"      child {node {1}}",
            r"      child {node {6}",
            r"        child {node {4}}",
            r"        child {node {7}}",
            r"      }",
            r"    }",
            r"    child {node {10}",
            r"      child[missing] {}",
            r"      child {node {14}",
            r"        child {node {13}}",
            r"        child[missing] {}",
            r"      }",
            r"    };",
            r"\end{tikzpicture}",
        ),
        "caption": "Binary search tree with values 1, 3, 4, 6, 7, 8, 10, 13, 14",
    },
    {
        "id": "comm_diagram",
        "kind": "commutative diagram (category theory)",
        "source": _t(
            r"\begin{tikzpicture}[node distance=2cm, every node/.style={font=\large}]",
            r"  \node (A) at (0,2) {$A$};",
            r"  \node (B) at (3,2) {$B$};",
            r"  \node (C) at (0,0) {$C$};",
            r"  \node (D) at (3,0) {$D$};",
            r"  \draw[->] (A) -- (B) node[midway, above] {$f$};",
            r"  \draw[->] (A) -- (C) node[midway, left] {$g$};",
            r"  \draw[->] (B) -- (D) node[midway, right] {$h$};",
            r"  \draw[->] (C) -- (D) node[midway, below] {$k$};",
            r"\end{tikzpicture}",
        ),
        "caption": "Commutative square with morphisms f, g, h, k",
    },
    {
        "id": "weighted_graph",
        "kind": "weighted directed graph (Dijkstra)",
        "source": _t(
            r"\begin{tikzpicture}[->, >=stealth, node distance=2.5cm,",
            r"  every node/.style={circle, draw, minimum size=8mm, font=\small}]",
            r"  \node (s) {s};",
            r"  \node (a) [right of=s, above of=s] {a};",
            r"  \node (b) [right of=s, below of=s] {b};",
            r"  \node (c) [right of=a] {c};",
            r"  \node (t) [right of=b, above of=b] {t};",
            r"  \draw (s) edge node[above left] {7} (a);",
            r"  \draw (s) edge node[below left] {2} (b);",
            r"  \draw (a) edge node[above] {3} (c);",
            r"  \draw (b) edge node[above] {4} (a);",
            r"  \draw (b) edge node[below right] {5} (t);",
            r"  \draw (c) edge node[right] {1} (t);",
            r"\end{tikzpicture}",
        ),
        "caption": "Weighted DAG for shortest-path example, source s, sink t",
    },
    {
        "id": "circuit",
        "kind": "RC circuit",
        "source": _t(
            r"\begin{tikzpicture}[circuit ee IEC, every info/.style={font=\footnotesize}]",
            r"  \draw (0,0) to[battery1, l=$V_s$] (0,3)",
            r"            to[short] (3,3)",
            r"            to[R, l=$R$] (3,1.5)",
            r"            to[C, l=$C$] (3,0)",
            r"            to[short] (0,0);",
            r"\end{tikzpicture}",
        ),
        "caption": "Series RC circuit with voltage source V_s, resistor R, capacitor C",
    },
    {
        "id": "function_plot",
        "kind": "calculus function plot",
        "source": _t(
            r"\begin{tikzpicture}[scale=1.2]",
            r"  \draw[->] (-1,0) -- (4,0) node[right] {$x$};",
            r"  \draw[->] (0,-1) -- (0,4) node[above] {$y$};",
            r"  \draw[domain=0:3.2, smooth, thick, blue] plot (\x, {(\x)^2 - 2*(\x) + 1});",
            r"  \node[blue] at (3.4, 3.6) {$f(x) = (x-1)^2$};",
            r"  \fill[red] (1,0) circle (2pt) node[below right] {minimum};",
            r"  \draw[dashed] (1,0) -- (1,1);",
            r"\end{tikzpicture}",
        ),
        "caption": "Plot of f(x) = (x-1)^2 with minimum marked at x=1",
    },
    {
        "id": "triangle_proof",
        "kind": "geometric figure (triangle with congruence marks)",
        "source": _t(
            r"\begin{tikzpicture}[scale=1.5]",
            r"  \coordinate[label=above:$A$] (A) at (1.5, 2.6);",
            r"  \coordinate[label=below left:$B$] (B) at (0, 0);",
            r"  \coordinate[label=below right:$C$] (C) at (3, 0);",
            r"  \draw (A) -- (B) -- (C) -- cycle;",
            r"  \draw (B) -- (1.5, 0);",
            r"  \node at (1.5, -0.25) {$M$};",
            r"  \draw[double] (A) -- (1.5, 1.3);",
            r"  \draw[->] (1.5, 0.3) arc (90:120:0.4);",
            r"  \node at (1.7, 0.55) {$\theta$};",
            r"  \node at (0.75, -0.15) {$|BM|$};",
            r"  \node at (2.25, -0.15) {$|MC|$};",
            r"\end{tikzpicture}",
        ),
        "caption": "Triangle ABC with median AM; M is midpoint of BC",
    },
    {
        "id": "hasse",
        "kind": "Hasse diagram (lattice)",
        "source": _t(
            r"\begin{tikzpicture}[node distance=1.5cm,",
            r"  every node/.style={circle, draw, fill=white, inner sep=2pt, font=\small}]",
            r"  \node (1) at (0, 0) {1};",
            r"  \node (2) at (-2, 1.5) {2};",
            r"  \node (3) at (0, 1.5) {3};",
            r"  \node (5) at (2, 1.5) {5};",
            r"  \node (6) at (-1, 3) {6};",
            r"  \node (10) at (1, 3) {10};",
            r"  \node (15) at (3, 3) {15};",
            r"  \node (30) at (1, 4.5) {30};",
            r"  \draw (1) -- (2);",
            r"  \draw (1) -- (3);",
            r"  \draw (1) -- (5);",
            r"  \draw (2) -- (6);",
            r"  \draw (2) -- (10);",
            r"  \draw (3) -- (6);",
            r"  \draw (3) -- (15);",
            r"  \draw (5) -- (10);",
            r"  \draw (5) -- (15);",
            r"  \draw (6) -- (30);",
            r"  \draw (10) -- (30);",
            r"  \draw (15) -- (30);",
            r"\end{tikzpicture}",
        ),
        "caption": "Hasse diagram of divisors of 30 ordered by divisibility",
    },
    {
        "id": "neural_net",
        "kind": "neural network architecture",
        "source": _t(
            r"\begin{tikzpicture}[",
            r"  neuron/.style={circle, draw, minimum size=8mm, font=\small},",
            r"  >=stealth]",
            r"  \foreach \i/\name in {1/x_1, 2/x_2, 3/x_3} {",
            r"    \node[neuron] (i\i) at (0, -\i) {$\name$};",
            r"  }",
            r"  \foreach \i in {1,2,3,4} {",
            r"    \node[neuron, fill=blue!10] (h\i) at (3, -\i + 0.5) {};",
            r"  }",
            r"  \foreach \i/\name in {1/y_1, 2/y_2} {",
            r"    \node[neuron, fill=red!10] (o\i) at (6, -\i - 0.5) {$\name$};",
            r"  }",
            r"  \foreach \i in {1,2,3} \foreach \j in {1,2,3,4} \draw[->] (i\i) -- (h\j);",
            r"  \foreach \i in {1,2,3,4} \foreach \j in {1,2} \draw[->] (h\i) -- (o\j);",
            r"  \node at (0, -4) {input};",
            r"  \node at (3, -4) {hidden};",
            r"  \node at (6, -4) {output};",
            r"\end{tikzpicture}",
        ),
        "caption": "Feedforward neural network: 3 inputs → 4 hidden → 2 outputs",
    },
    {
        "id": "venn",
        "kind": "Venn diagram (three sets)",
        "source": _t(
            r"\begin{tikzpicture}",
            r"  \draw (0,0) circle (1.5cm) node at (-1.7, 0.7) {$A$};",
            r"  \draw (1.5,0) circle (1.5cm) node at (3.2, 0.7) {$B$};",
            r"  \draw (0.75,-1.3) circle (1.5cm) node at (0.75, -3) {$C$};",
            r"  \node at (-0.5, 0.3) {$1$};",
            r"  \node at (2, 0.3) {$2$};",
            r"  \node at (0.75, -2) {$3$};",
            r"  \node at (0.75, 0.4) {$4$};",
            r"  \node at (-0.1, -0.7) {$5$};",
            r"  \node at (1.6, -0.7) {$6$};",
            r"  \node at (0.75, -0.3) {$7$};",
            r"\end{tikzpicture}",
        ),
        "caption": "Three-set Venn diagram with regions 1-7",
    },
]


def build_prompt(tikz_source: str) -> str:
    template = PROMPT_PATH.read_text()
    return template.replace("{tikz_source}", tikz_source)


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    return (in_tokens * rates["input"] + out_tokens * rates["output"]) / 1_000_000


def run_claude(prompt: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    client = Anthropic()
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.time() - t0
    text = resp.content[0].text.strip()
    return {
        "model": model,
        "vendor": "anthropic",
        "text": text,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "latency_s": round(elapsed, 2),
        "cost_usd": round(estimate_cost(model, resp.usage.input_tokens, resp.usage.output_tokens), 5),
    }


def run_gemini(prompt: str, model: str, max_retries: int = 4) -> dict:
    """Call Gemini with exponential backoff retry on transient failures.

    Retries on 503 UNAVAILABLE (preview-model overload) and 429 RESOURCE_EXHAUSTED
    (rate limit). Backoff: 5s, 15s, 45s, 90s. Raises after max_retries.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    backoffs = [5, 15, 45, 90]
    attempt = 0
    last_exc: Exception | None = None
    while attempt <= max_retries:
        try:
            t0 = time.time()
            resp = client.models.generate_content(
                model=model,
                contents=[prompt],
                config=types.GenerateContentConfig(temperature=0.2),
            )
            elapsed = time.time() - t0
            text = (resp.text or "").strip()
            usage = resp.usage_metadata
            in_tok = usage.prompt_token_count if usage else 0
            out_tok = usage.candidates_token_count if usage else 0
            return {
                "model": model,
                "vendor": "google",
                "text": text,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "latency_s": round(elapsed, 2),
                "cost_usd": round(estimate_cost(model, in_tok, out_tok), 5),
                "retries": attempt,
            }
        except Exception as e:
            last_exc = e
            err_str = str(e)
            transient = "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            if not transient or attempt >= max_retries:
                raise
            wait = backoffs[min(attempt, len(backoffs) - 1)]
            logger.warning("Gemini %s attempt %d/%d transient error, waiting %ds: %s",
                           model, attempt + 1, max_retries + 1, wait, err_str[:120])
            time.sleep(wait)
            attempt += 1
    raise last_exc if last_exc else RuntimeError("retry loop ended without result")


def main():
    load_dotenv()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for sample in SAMPLES:
        logger.info("=== Sample: %s (%s) ===", sample["id"], sample["kind"])
        prompt = build_prompt(sample["source"])

        runs = []
        for runner, args in [
            (run_claude, ("claude-haiku-4-5-20251001",)),
            (run_gemini, ("gemini-2.5-flash",)),
            (run_gemini, ("gemini-3-flash-preview",)),
        ]:
            try:
                logger.info("→ %s", args[0])
                r = runner(prompt, *args)
                logger.info("   %d tokens in / %d out, %.1fs, $%.5f",
                            r["input_tokens"], r["output_tokens"], r["latency_s"], r["cost_usd"])
                runs.append(r)
            except Exception as e:
                logger.exception("Run failed for %s on %s", args[0], sample["id"])
                runs.append({"model": args[0], "error": str(e)})

        results.append({
            "sample_id": sample["id"],
            "kind": sample["kind"],
            "caption": sample["caption"],
            "tikz_source": sample["source"],
            "runs": runs,
        })

    OUTPUT_PATH.write_text(json.dumps(results, indent=2))
    logger.info("Wrote %s", OUTPUT_PATH)

    # Print summary table
    print("\n=== SUMMARY ===")
    print(f"{'Sample':<15} {'Model':<32} {'In':>6} {'Out':>6} {'Lat':>6} {'Cost':>9}")
    print("-" * 80)
    for sample in results:
        for r in sample["runs"]:
            if "error" in r:
                print(f"{sample['sample_id']:<15} {r['model']:<32} ERROR: {r['error'][:40]}")
                continue
            print(f"{sample['sample_id']:<15} {r['model']:<32} {r['input_tokens']:>6} {r['output_tokens']:>6} {r['latency_s']:>5.1f}s ${r['cost_usd']:>7.5f}")
    print()
    total_cost_by_vendor = {}
    for sample in results:
        for r in sample["runs"]:
            if "error" in r:
                continue
            v = r["model"]
            total_cost_by_vendor[v] = total_cost_by_vendor.get(v, 0) + r["cost_usd"]
    print("Total cost (3 samples) per model:")
    for m, c in total_cost_by_vendor.items():
        print(f"  {m:<32} ${c:.5f}")


if __name__ == "__main__":
    main()
