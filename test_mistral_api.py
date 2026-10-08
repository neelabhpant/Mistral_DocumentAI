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
SAMPLE_PDF = (
    ROOT
    / "data/cuad/CUAD_v1/full_contract_pdf/Part_I/Affiliate_Agreements"
    / "SteelVaultCorp_20081224_10-K_EX-10.16_3074935_EX-10.16_Affiliate Agreement.pdf"
)
OCR_MODEL = "mistral-ocr-4"


def get_client() -> Mistral:
    load_dotenv(ROOT / ".env")
    api_key = (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip().strip("\"'")
    if not api_key:
        sys.exit("No API key found: set MISTRAL_API (or MISTRAL_API_KEY) in .env")
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

    print(f"\n2) Live OCR call ({OCR_MODEL}, first page of {SAMPLE_PDF.name})...")
    data_uri = "data:application/pdf;base64," + base64.b64encode(SAMPLE_PDF.read_bytes()).decode()
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
