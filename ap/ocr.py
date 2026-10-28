"""Mistral OCR 4.1: the API client, the OCR call and each page's markdown."""

import base64
import os
import sys

from dotenv import load_dotenv
from mistralai.client import Mistral

from ap.config import ROOT

OCR_MODEL = "mistral-ocr-4-1"  # OCR 4.0 retired 2026-09-30; mistral-ocr-4 is now an alias of 4.1


def api_key() -> str:
    """The project's Mistral key: MISTRAL_API_KEY (or MISTRAL_API) from the environment, else from .env locally."""
    load_dotenv(ROOT / ".env")
    return (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip().strip("\"'")


def get_client() -> Mistral:
    key = api_key()
    if not key:
        sys.exit("No API key found: set MISTRAL_API_KEY as a Cloudera AI project environment variable "
                 "(Project Settings > Advanced; restart the app or session afterwards), or in .env when running locally")
    return Mistral(api_key=key)


def ocr_bytes(client, data: bytes, confidence="page", annotation_format=None, annotation_prompt=None):
    """OCR a PDF. With annotation_format (a JSON-schema response format), OCR 4.1 also returns
    response.document_annotation; annotation_prompt adds free-text guidance for that pass."""
    data_uri = "data:application/pdf;base64," + base64.b64encode(data).decode()
    extra = {}
    if annotation_format:
        extra["document_annotation_format"] = annotation_format
        if annotation_prompt:
            extra["document_annotation_prompt"] = annotation_prompt
    return client.ocr.process(
        model=OCR_MODEL,
        document={"type": "document_url", "document_url": data_uri},
        table_format="markdown",
        confidence_scores_granularity=confidence,
        include_image_base64=False,
        **extra,
    )


def page_markdown(page) -> str:
    """Page markdown with table placeholders like [tbl-0.md](tbl-0.md) replaced by the table content."""
    md = page.markdown
    for tbl in page.tables or []:
        md = md.replace(f"[{tbl.id}]({tbl.id})", tbl.content)
    return md
