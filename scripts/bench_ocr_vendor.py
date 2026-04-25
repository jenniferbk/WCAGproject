"""Bench 2 — scanned-page OCR correction vendor bake-off.

Compares Claude Haiku 4.5 (current production) vs Gemini 3 Flash Preview
on the OCR-correction task at `src/tools/scanned_page_ocr.py:_haiku_correct_text`.

Per Jennifer's direction (2026-04-25), we skip Gemini 2.5 Flash since it's
unverdictable today (free-tier daily quota throttle).

Inputs: a fully-scanned PDF page (Erlwanger from testdocs), the same
production prompt, and the same Tesseract block extraction. Outputs:
correction count, JSON validity, cost, latency. Side-by-side correction
comparison goes to JSON for manual quality grading.

Run: python3 scripts/bench_ocr_vendor.py
"""

from __future__ import annotations

import base64
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

import fitz  # PyMuPDF

from src.tools.scanned_page_ocr import (
    PAGE_DPI,
    _load_hybrid_correction_prompt,
    _tesseract_extract_blocks,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("bench_ocr")

PDF_PATH = Path(__file__).parent.parent / "testdocs" / "4) Erlwanger Benny's conception of rules and answers in IPI Mathematics Erlwanger (2004,1973).pdf"
OUTPUT_PATH = Path(__file__).parent.parent / "testdocs" / "strategy_experiment" / "ocr_bake_off.json"
PAGES_TO_TEST = [0, 5]  # First page and a middle page

PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "gemini-3-flash-preview":    {"input": 0.50, "output": 3.00},
}


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    rates = PRICING.get(model)
    if not rates:
        return 0.0
    return (in_tok * rates["input"] + out_tok * rates["output"]) / 1_000_000


def render_page(doc: fitz.Document, page_number: int, dpi: int = PAGE_DPI) -> bytes:
    page = doc[page_number]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def parse_corrections(raw: str) -> tuple[dict[int, str], bool, str]:
    """Parse a correction response. Returns (corrections, json_valid, error_msg)."""
    try:
        data = json.loads(raw)
        json_valid = True
        err = ""
    except json.JSONDecodeError as e:
        try:
            from src.utils.json_repair import parse_json_lenient
            data = parse_json_lenient(raw)
            json_valid = False
            err = f"strict json failed; lenient succeeded: {e}"
        except Exception as e2:
            return {}, False, f"both json parsers failed: {e2}"
    corrections: dict[int, str] = {}
    for item in data.get("corrections", []):
        cid = item.get("id")
        ct = item.get("corrected_text")
        if cid is not None and ct is not None:
            corrections[int(cid)] = ct
    return corrections, json_valid, err


def run_claude(prompt: str, image_b64: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    client = Anthropic()
    t0 = time.time()
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    elapsed = time.time() - t0
    content = resp.content[0]
    raw = content.text if content.type == "text" else ""
    corrections, json_valid, err = parse_corrections(raw)
    return {
        "model": model,
        "vendor": "anthropic",
        "raw": raw,
        "corrections": corrections,
        "correction_count": len(corrections),
        "json_valid": json_valid,
        "json_error": err,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "latency_s": round(elapsed, 2),
        "cost_usd": round(estimate_cost(model, resp.usage.input_tokens, resp.usage.output_tokens), 5),
    }


def run_gemini(prompt: str, png_bytes: bytes, model: str = "gemini-3-flash-preview", max_retries: int = 4) -> dict:
    """Gemini call with retry on 503/429."""
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    backoffs = [5, 15, 45, 90]
    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )
            elapsed = time.time() - t0
            raw = (resp.text or "").strip()
            corrections, json_valid, err = parse_corrections(raw)
            usage = resp.usage_metadata
            in_tok = usage.prompt_token_count if usage else 0
            out_tok = usage.candidates_token_count if usage else 0
            return {
                "model": model,
                "vendor": "google",
                "raw": raw,
                "corrections": corrections,
                "correction_count": len(corrections),
                "json_valid": json_valid,
                "json_error": err,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "latency_s": round(elapsed, 2),
                "cost_usd": round(estimate_cost(model, in_tok, out_tok), 5),
                "retries": attempt,
            }
        except Exception as e:
            err_str = str(e)
            transient = "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str
            if not transient or attempt >= max_retries:
                raise
            wait = backoffs[min(attempt, len(backoffs) - 1)]
            logger.warning("Gemini attempt %d/%d transient error, waiting %ds: %s",
                           attempt + 1, max_retries + 1, wait, err_str[:120])
            time.sleep(wait)
    raise RuntimeError("retry loop ended without result")


def main():
    load_dotenv()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not PDF_PATH.exists():
        logger.error("PDF not found: %s", PDF_PATH)
        sys.exit(1)

    doc = fitz.open(str(PDF_PATH))
    logger.info("Opened %s (%d pages)", PDF_PATH.name, doc.page_count)

    prompt_template = _load_hybrid_correction_prompt()
    results = []

    for page_num in PAGES_TO_TEST:
        logger.info("=== Page %d ===", page_num)

        # Render page
        png_bytes = render_page(doc, page_num)
        image_b64 = base64.b64encode(png_bytes).decode("utf-8")

        # Tesseract pass to get blocks
        try:
            blocks = _tesseract_extract_blocks(doc, page_num)
        except Exception as e:
            logger.error("Tesseract failed on page %d: %s", page_num, e)
            results.append({"page": page_num, "success": False, "error": f"tesseract: {e}"})
            continue

        if not blocks:
            logger.warning("Page %d: no Tesseract blocks", page_num)
            continue

        logger.info("Page %d: %d Tesseract blocks", page_num, len(blocks))

        # Build prompt
        blocks_json = json.dumps(blocks, ensure_ascii=False)
        prompt = prompt_template.replace("{blocks_json}", blocks_json)

        # Per-page run
        page_result = {
            "page": page_num,
            "block_count": len(blocks),
            "blocks": blocks,
            "runs": [],
        }

        for runner, args in [
            (run_claude, ()),
            (run_gemini, ()),
        ]:
            try:
                if runner is run_claude:
                    logger.info("→ Claude Haiku 4.5")
                    r = runner(prompt, image_b64, *args)
                else:
                    logger.info("→ Gemini 3 Flash Preview")
                    r = runner(prompt, png_bytes, *args)
                logger.info("   %d corrections, json_valid=%s, %d/%d tokens, %.1fs, $%.5f",
                            r["correction_count"], r["json_valid"],
                            r["input_tokens"], r["output_tokens"],
                            r["latency_s"], r["cost_usd"])
                page_result["runs"].append(r)
            except Exception as e:
                logger.exception("Run failed on page %d", page_num)
                page_result["runs"].append({"error": str(e)})

        results.append(page_result)

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Wrote %s", OUTPUT_PATH)

    print("\n=== SUMMARY ===")
    print(f"{'Page':<6} {'Vendor':<26} {'Corr':>5} {'JSON':>6} {'In':>6} {'Out':>6} {'Lat':>7} {'Cost':>9}")
    print("-" * 80)
    total_cost = {}
    for page_result in results:
        if not page_result.get("runs"):
            continue
        for r in page_result["runs"]:
            if "error" in r:
                print(f"{page_result['page']:<6} {'(failed)':<26} ERROR: {r['error'][:30]}")
                continue
            print(f"{page_result['page']:<6} {r['model']:<26} {r['correction_count']:>5d} "
                  f"{'OK' if r['json_valid'] else 'lenient':>6} "
                  f"{r['input_tokens']:>6} {r['output_tokens']:>6} {r['latency_s']:>6.1f}s ${r['cost_usd']:>7.5f}")
            total_cost[r["model"]] = total_cost.get(r["model"], 0) + r["cost_usd"]
    print()
    print("Total cost per model:")
    for m, c in total_cost.items():
        print(f"  {m:<32s} ${c:.5f}")


if __name__ == "__main__":
    main()
