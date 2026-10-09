"""Shared settings for the AP demo. Override the lake locations with environment variables."""

import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/ap"

# "Today" for the demo: due dates and discount windows are relative to it, so the demo looks the same
# whenever it is shown. Regenerate with a new --as-of to move it.
AS_OF = date.fromisoformat(os.getenv("AP_AS_OF", "2026-10-08"))

S3_ROOT = os.getenv("AP_S3_ROOT", "s3a://go01-demo/user/npant/docai")
DB = os.getenv("AP_DB", "docai_ap")
IMPALA_CONNECTION = os.getenv("AP_IMPALA_CONNECTION", "default-impala-aws")

BUYER = {
    "name": "Halvorsen Fabrication Co.",
    "address": ["2200 Foundry Row", "Toledo, OH 43604"],
    "ap_email": "ap@halvorsenfab.example",
}
MIN_CONFIDENCE = float(os.getenv("AP_MIN_CONFIDENCE", "0.85"))  # below this, key fields go to human review
