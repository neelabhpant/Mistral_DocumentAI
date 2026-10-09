"""Connectivity check for the Mistral API and OCR models.

Usage: .venv/bin/python test_mistral_api.py
"""

import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral

ROOT = Path(__file__).parent
SAMPLE_PDFS = ROOT / "data/ap/pdfs"  # generated invoices (python -m ap.generate)
OCR_MODEL = "mistral-ocr-4"


def get_client() -> Mistral:
    load_dotenv(ROOT / ".env")
    api_key = (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip().strip("\"'")
    if not api_key:
        sys.exit("No API key found: set MISTRAL_API_KEY as a Cloudera AI project environment variable "
                 "(Project Settings > Advanced; restart the app or session afterwards), or in .env when running locally")
    return Mistral(api_key=api_key)


def main() -> None:
    client = get_client()

    print("1) Listing models...")
    try:
        models = client.models.list().data or []
    except Exception as e:
        sys.exit(f"   FAILED to list models (check API key): {e}")
    print(f"   {len(models)} models visible to this key")
    ocr_ids = sorted(m.id for m in models if "ocr" in m.id.lower())
    print("   OCR models:", ", ".join(ocr_ids) or "(none)")
    for wanted in (OCR_MODEL, "mistral-ocr-latest"):
        print(f"   {wanted}: {'available' if wanted in ocr_ids else 'NOT listed'}")

    sample = next(iter(sorted(SAMPLE_PDFS.glob("*.pdf"))), None)
    if sample is None:
        sys.exit(f"   No sample PDF in {SAMPLE_PDFS}; run python -m ap.generate first")
    print(f"\n2) Live OCR call ({OCR_MODEL}, first page of {sample.name})...")
    data_uri = "data:application/pdf;base64," + base64.b64encode(sample.read_bytes()).decode()
    try:
        resp = client.ocr.process(
            model=OCR_MODEL,
            document={"type": "document_url", "document_url": data_uri},
            pages=[0],
            include_blocks=False,
        )
    except Exception as e:
        sys.exit(f"   FAILED OCR call: {e}")
    print(f"   served by model: {resp.model}")
    print(f"   usage: {resp.usage_info}")
    print("   first 300 chars:\n" + "-" * 60)
    print(resp.pages[0].markdown[:300])
    print("-" * 60 + "\nOK: API key works and OCR model is accessible.")


if __name__ == "__main__":
    main()
