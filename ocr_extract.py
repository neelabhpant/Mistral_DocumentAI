"""Extract text from PDFs with Mistral OCR 4.

Usage:
  .venv/bin/python ocr_extract.py                 # runs the two default CUAD contracts
  .venv/bin/python ocr_extract.py a.pdf b.pdf     # any PDFs

Outputs go to outputs/ocr/<stem>/<stem>.md (page-joined markdown) and <stem>.json (raw response).
If a CUAD reference .txt with the same name exists, a rough text-similarity score is printed.
"""

import base64
import difflib
import json
import re
import sys
import time
from pathlib import Path

from test_mistral_api import OCR_MODEL, ROOT, get_client

CUAD = ROOT / "data/cuad/CUAD_v1"
PDF_DIR = CUAD / "full_contract_pdf"
TXT_DIR = CUAD / "full_contract_txt"
OUT_DIR = ROOT / "outputs/ocr"

DEFAULT_PDFS = [
    PDF_DIR / "Part_I/Distributor/FuseMedicalInc_20190321_10-K_EX-10.43_11575454_EX-10.43_Distributor Agreement.pdf",
    PDF_DIR / "Part_III/Hosting/GALACTICOMMTECHNOLOGIESINC_11_07_1997-EX-10.46-WEB HOSTING AGREEMENT.PDF",
]


def ocr_bytes(
    client,
    data: bytes,
    table_format="markdown",
    confidence="page",
    include_images=False,
    annotation_format=None,
    annotation_prompt=None,
):
    """OCR a PDF. With annotation_format (a JSON-schema response format), OCR 4 also returns
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
        table_format=table_format,
        confidence_scores_granularity=confidence,
        include_image_base64=include_images,
        **extra,
    )


def ocr_pdf(client, pdf: Path):
    return ocr_bytes(client, pdf.read_bytes())


def page_markdown(page, inline_images=False) -> str:
    """Page markdown with table placeholders like [tbl-0.md](tbl-0.md) replaced by the table content.

    With inline_images, image placeholders ![img-0.jpeg](img-0.jpeg) point at their base64 data URIs
    (requires the response to have been requested with include_images=True).
    """
    md = page.markdown
    for tbl in page.tables or []:
        md = md.replace(f"[{tbl.id}]({tbl.id})", tbl.content)
    if inline_images:
        for img in page.images or []:
            if img.image_base64:
                md = md.replace(f"]({img.id})", f"]({img.image_base64})")
    return md


def to_plain(text: str) -> str:
    """Strip markdown syntax and collapse whitespace for a rough comparison."""
    text = re.sub(r"<!--.*?-->|!\[.*?\]\(.*?\)", " ", text)
    text = re.sub(r"[#*_|`>]|^-{3,}$|:?-{3,}:?", " ", text, flags=re.M)
    return " ".join(text.split()).lower()


def similarity(ocr_text: str, ref_path: Path) -> float:
    ref = ref_path.read_text(encoding="utf-8", errors="ignore")
    # Word-level: about 100x faster than character-level on multi-page contracts.
    return difflib.SequenceMatcher(None, to_plain(ocr_text).split(), to_plain(ref).split(), autojunk=False).ratio()


def main() -> None:
    pdfs = [Path(p) for p in sys.argv[1:]] or DEFAULT_PDFS
    client = get_client()
    ref_txts = {p.stem: p for p in TXT_DIR.rglob("*.txt")}

    for pdf in pdfs:
        print(f"\n=== {pdf.name}")
        start = time.perf_counter()
        resp = ocr_pdf(client, pdf)
        elapsed = time.perf_counter() - start

        markdown = "\n\n".join(f"<!-- page {p.index + 1} -->\n{page_markdown(p)}" for p in resp.pages)
        out = OUT_DIR / pdf.stem
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{pdf.stem}.md").write_text(markdown, encoding="utf-8")
        (out / f"{pdf.stem}.json").write_text(json.dumps(resp.model_dump(mode="json"), indent=2), encoding="utf-8")

        confs = [p.confidence_scores.average_page_confidence_score for p in resp.pages if p.confidence_scores]
        min_confs = [p.confidence_scores.minimum_page_confidence_score for p in resp.pages if p.confidence_scores]
        n_tables = sum(len(p.tables or []) for p in resp.pages)
        print(f"model:            {resp.model}")
        print(f"pages:            {resp.usage_info.pages_processed}")
        print(f"chars extracted:  {len(markdown):,}")
        print(f"tables found:     {n_tables}")
        if confs:
            print(f"avg page conf:    {sum(confs) / len(confs):.3f}  (worst page min: {min(min_confs):.3f})")
        print(f"time:             {elapsed:.1f}s")
        if pdf.stem in ref_txts:
            print(f"similarity vs CUAD txt: {similarity(markdown, ref_txts[pdf.stem]):.3f}")
        print(f"saved to:         {out.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
