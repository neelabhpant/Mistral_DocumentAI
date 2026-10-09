"""Extract text from PDFs with Mistral OCR 4.

Usage: python ocr_extract.py a.pdf [b.pdf ...]

Outputs go to outputs/ocr/<stem>/<stem>.md (page-joined markdown) and <stem>.json (raw response).
"""

import base64
import json
import sys
import time
from pathlib import Path

from test_mistral_api import OCR_MODEL, ROOT, get_client

OUT_DIR = ROOT / "outputs/ocr"


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


def main() -> None:
    pdfs = [Path(p) for p in sys.argv[1:]]
    if not pdfs:
        sys.exit(__doc__)
    client = get_client()

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
        print(f"saved to:         {out.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
