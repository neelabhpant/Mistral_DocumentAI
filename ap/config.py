"""Shared settings for the AP demo. Override the lake locations with environment variables (an AMP launch sets
them from its form; a blank value means the default)."""

import os
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/ap"


def _default_s3_root() -> str:
    """<data lake bucket>/user/<user>/docai, with the bucket taken from the Hive warehouse location ("" if unknown)."""
    try:
        props = {p.findtext("name"): p.findtext("value") for p in ET.parse("/etc/hadoop/conf/hive-site.xml").getroot()}
    except (OSError, ET.ParseError):
        return ""
    warehouse = props.get("hive.metastore.warehouse.external.dir") or props.get("hive.metastore.warehouse.dir") or ""
    scheme, _, rest = warehouse.partition("://")
    user = os.getenv("HADOOP_USER_NAME") or os.getenv("USER")
    if not rest or not user:
        return ""
    return f"{scheme}://{rest.split('/')[0]}/user/{user}/docai"


# "Today" for the demo: due dates and discount windows are relative to it, so the demo looks the same
# whenever it is shown. Regenerate with a new --as-of to move it.
AS_OF = date.fromisoformat(os.getenv("AP_AS_OF") or "2026-10-08")

S3_ROOT = (os.getenv("AP_S3_ROOT") or _default_s3_root()).rstrip("/")
DB = os.getenv("AP_DB") or "docai_ap"
IMPALA_CONNECTION = os.getenv("AP_IMPALA_CONNECTION") or "default-impala-aws"

BUYER = {
    "name": "Halvorsen Fabrication Co.",
    "address": ["2200 Foundry Row", "Toledo, OH 43604"],
    "ap_email": "ap@halvorsenfab.example",
}
MIN_CONFIDENCE = float(os.getenv("AP_MIN_CONFIDENCE") or "0.85")  # below this, key fields go to human review
