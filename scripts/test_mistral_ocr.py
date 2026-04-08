#!/usr/bin/env python3
"""
Mistral OCR 3 evaluation script.

Tests Mistral's document OCR against specific pages of the Mayer paper
to compare quality with our hybrid OCR pipeline (Tesseract + Gemini + Haiku).

Usage:
    python scripts/test_mistral_ocr.py [--pages 1 6 10] [--all-pages]

Requires MISTRAL_API_KEY in .env or environment.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

MAYER_PDF = Path(__file__).parent.parent / (
    "testdocs/7) Mayer Learners as information processors-Legacies "
    "and limitations of ed psych's 2nd metaphor (Mayer, 1996)-optional.pdf"
)

DEFAULT_PAGES = [1, 6, 10]  # Page 1: title+abstract, 6: Table 3, 10: two-column text


def run_ocr(pages: list[int], include_images: bool = False):
    """Run Mistral OCR on specified pages of the Mayer PDF."""
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("ERROR: MISTRAL_API_KEY not found in .env or environment.")
        print("Get one at https://console.mistral.ai/")
        sys.exit(1)

    if not MAYER_PDF.exists():
        print(f"ERROR: PDF not found at {MAYER_PDF}")
        sys.exit(1)

    from mistralai.client import Mistral

    client = Mistral(api_key=api_key)

    # Upload the PDF file
    print(f"Uploading {MAYER_PDF.name}...")
    t0 = time.time()
    uploaded = client.files.upload(
        file={
            "file_name": MAYER_PDF.name,
            "content": MAYER_PDF.read_bytes(),
        },
        purpose="ocr",
    )
    upload_time = time.time() - t0
    print(f"  Uploaded in {upload_time:.1f}s, file_id={uploaded.id}")

    # Run OCR
    # Use 0-indexed pages for the API (Mistral uses 0-indexed)
    api_pages = [p - 1 for p in pages]
    print(f"\nRunning OCR on pages {pages} (0-indexed: {api_pages})...")
    t0 = time.time()
    from mistralai.client.models.ocrrequest import FileChunk

    result = client.ocr.process(
        model="mistral-ocr-latest",
        document=FileChunk(file_id=uploaded.id, type="file"),
        pages=api_pages,
        include_image_base64=include_images,
        table_format="markdown",
    )
    ocr_time = time.time() - t0
    print(f"  OCR completed in {ocr_time:.1f}s")
    print(f"  Pages processed: {result.usage_info.pages_processed}")
    print(f"  Doc size: {result.usage_info.doc_size_bytes:,} bytes")

    # Output results per page
    output_dir = Path(__file__).parent.parent / "testdocs" / "output" / "mistral_ocr_eval"
    output_dir.mkdir(parents=True, exist_ok=True)

    for page_obj in result.pages:
        page_num = page_obj.index + 1  # Convert back to 1-indexed for display
        print(f"\n{'='*80}")
        print(f"PAGE {page_num}")
        print(f"{'='*80}")

        # Dimensions
        if page_obj.dimensions:
            print(f"Dimensions: {page_obj.dimensions.width}x{page_obj.dimensions.height} @ {page_obj.dimensions.dpi}dpi")

        # Markdown content
        print(f"\n--- Markdown ({len(page_obj.markdown)} chars) ---")
        print(page_obj.markdown)

        # Tables
        if page_obj.tables:
            print(f"\n--- Tables ({len(page_obj.tables)}) ---")
            for i, table in enumerate(page_obj.tables):
                print(f"  Table {i+1}: {table}")

        # Images
        if page_obj.images:
            print(f"\n--- Images ({len(page_obj.images)}) ---")
            for i, img in enumerate(page_obj.images):
                # Don't print base64 data
                img_info = {k: v for k, v in img.__dict__.items() if k != "image_base64"}
                print(f"  Image {i+1}: {img_info}")

        # Hyperlinks
        if page_obj.hyperlinks:
            print(f"\n--- Hyperlinks ({len(page_obj.hyperlinks)}) ---")
            for link in page_obj.hyperlinks:
                print(f"  {link}")

        # Header/footer
        if page_obj.header:
            print(f"Header: {page_obj.header}")
        if page_obj.footer:
            print(f"Footer: {page_obj.footer}")

        # Save markdown to file
        out_file = output_dir / f"page_{page_num}.md"
        out_file.write_text(page_obj.markdown)
        print(f"\n  Saved to {out_file}")

    # Save full response as JSON
    response_file = output_dir / "full_response.json"
    response_data = result.model_dump()
    # Strip base64 image data for readability
    for page in response_data.get("pages", []):
        for img in page.get("images", []):
            if img.get("image_base64"):
                img["image_base64"] = f"<{len(img['image_base64'])} chars>"
    response_file.write_text(json.dumps(response_data, indent=2, default=str))
    print(f"\nFull response saved to {response_file}")

    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Upload time: {upload_time:.1f}s")
    print(f"OCR time:    {ocr_time:.1f}s")
    print(f"Total time:  {upload_time + ocr_time:.1f}s")
    print(f"Pages:       {result.usage_info.pages_processed}")
    cost = result.usage_info.pages_processed / 1000  # $1/1K pages
    print(f"Est. cost:   ${cost:.4f} (at $1/1K pages batch rate)")
    total_chars = sum(len(p.markdown) for p in result.pages)
    print(f"Total chars: {total_chars:,}")
    total_tables = sum(len(p.tables) for p in result.pages if p.tables)
    print(f"Tables:      {total_tables}")
    total_images = sum(len(p.images) for p in result.pages if p.images)
    print(f"Images:      {total_images}")

    # Cleanup uploaded file
    try:
        client.files.delete(file_id=uploaded.id)
        print(f"\nCleaned up uploaded file {uploaded.id}")
    except Exception as e:
        print(f"\nWarning: could not delete uploaded file: {e}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate Mistral OCR 3 on the Mayer paper")
    parser.add_argument(
        "--pages", nargs="+", type=int, default=DEFAULT_PAGES,
        help=f"Page numbers to process (1-indexed, default: {DEFAULT_PAGES})"
    )
    parser.add_argument(
        "--all-pages", action="store_true",
        help="Process all 11 pages"
    )
    parser.add_argument(
        "--include-images", action="store_true",
        help="Include base64 image data in output"
    )
    args = parser.parse_args()

    if args.all_pages:
        args.pages = list(range(1, 12))

    print(f"Mistral OCR 3 Evaluation")
    print(f"Document: {MAYER_PDF.name}")
    print(f"Pages: {args.pages}")
    print()

    run_ocr(args.pages, args.include_images)


if __name__ == "__main__":
    main()
