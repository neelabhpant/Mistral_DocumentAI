"""The Document AI pipeline: PDF in S3 -> Mistral OCR 4 (schema mode) -> extractions with evidence -> rules.

Batch:  python -m ap.pipeline --pending      (what the Cloudera AI Job runs)
        python -m ap.pipeline --all          (reprocess everything)
Live:   ingest(pdf_bytes, file_name) from the app: lands the file, processes it, reruns the rules.
"""

import argparse
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ap import lake
from ap.config import AS_OF, DATA, DB, MIN_CONFIDENCE
from ap.evidence import locate
from ap.schema import ANNOTATION_FORMAT, HEADER_FIELDS, INVOICE_PROMPT, LINE_FIELDS, MONEY_FIELDS
from ocr_extract import ocr_bytes
from test_mistral_api import get_client

CACHE = DATA / "cache"
RULES = Path(__file__).with_name("rules.sql")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def ocr_uri(doc_id: str) -> str:
    return f"{lake.LANDING.rsplit('/', 1)[0]}/ocr/{doc_id}.json"


def pdf_bytes(doc: dict) -> bytes:
    """PDF from the lake, cached locally by content hash."""
    path = CACHE / f"{doc['sha256']}.pdf"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(lake.get_bytes(doc["s3_uri"]))
    return path.read_bytes()


def ocr_json(doc_id: str) -> dict:
    """Raw OCR 4 response for a document (local cache, else the lake)."""
    path = CACHE / f"{doc_id}.ocr.json"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(lake.get_bytes(ocr_uri(doc_id)))
    return json.loads(path.read_text())


def _fmt(field: str, v) -> str | None:
    if v is None or v == "":
        return None
    if field in MONEY_FIELDS:
        return f"{float(v):.2f}"
    if field == "quantity":
        return str(int(v)) if float(v) == int(v) else str(v)
    return str(v).strip()


def extract(doc: dict, client) -> tuple[dict, list[dict]]:
    """OCR 4 call with the invoice schema; returns the raw response and one row per extracted field."""
    start = time.perf_counter()
    resp = ocr_bytes(client, pdf_bytes(doc), confidence="word", annotation_format=ANNOTATION_FORMAT,
                     annotation_prompt=INVOICE_PROMPT)
    raw = resp.model_dump()
    raw["_seconds"] = round(time.perf_counter() - start, 1)
    fields = json.loads(resp.document_annotation or "{}")
    now, model = _now(), resp.model
    rows = []

    def add(field, value, line_no=None, row_key=None):
        ev = locate(raw, field, value, row_key)
        rows.append({"doc_id": doc["doc_id"], "field_name": field, "line_no": line_no, "field_value": _fmt(field, value),
                     "confidence": ev["confidence"], "page": ev["page"],
                     "bbox": json.dumps(ev["bbox"]) if ev["bbox"] else None, "quote": None, "model": model,
                     "extracted_at": now})

    for f in HEADER_FIELDS:
        add(f, fields.get(f))
    for n, item in enumerate(fields.get("line_items") or [], 1):
        key = item.get("sku") or item.get("description")
        for f in LINE_FIELDS:
            add(f, item.get(f), n, key)
    return raw, rows


def process(docs: list[dict], workers: int = 4) -> list[dict]:
    """OCR + extract a set of documents and write the results to the lake (one insert per table)."""
    if not docs:
        return []
    client = get_client()
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda d: (d, *extract(d, client)), docs))
    rows, ocr_files = [], []
    for doc, raw, doc_rows in results:
        path = CACHE / f"{doc['doc_id']}.ocr.json"
        path.write_text(json.dumps(raw, default=str))
        ocr_files.append(path)
        rows += doc_rows
        print(f"  {doc['doc_id']}: {len(doc_rows)} fields in {raw['_seconds']}s")
    # Raw OCR output is kept in the lake next to the PDFs (evidence and audit trail).
    staging = CACHE / "upload"
    staging.mkdir(exist_ok=True)
    for p in ocr_files:
        (staging / p.name.replace(".ocr.json", ".json")).write_bytes(p.read_bytes())
    lake.put_files(sorted(staging.glob("*.json")), prefix=ocr_uri("x").rsplit("/", 1)[0])
    for p in staging.glob("*.json"):
        p.unlink()

    ids = ", ".join(lake.lit(d["doc_id"]) for d in docs)
    lake.execute(f"DELETE FROM {DB}.extractions WHERE doc_id IN ({ids})")
    lake.insert("extractions", rows)
    lake.execute(f"UPDATE {DB}.documents SET status = 'processed', processed_at = now() WHERE doc_id IN ({ids})")
    return rows


def run_rules() -> int:
    sql = RULES.read_text().format(db=DB, as_of=AS_OF.isoformat(), min_confidence=MIN_CONFIDENCE)
    sql = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    for stmt in filter(str.strip, re.split(r";\s*$", sql, flags=re.M)):
        lake.execute(stmt)
    return lake.query(f"SELECT COUNT(*) AS n FROM {DB}.exceptions")[0]["n"]


def documents(where: str = "TRUE") -> list[dict]:
    return lake.query(f"SELECT doc_id, file_name, s3_uri, sha256 FROM {DB}.documents WHERE {where} ORDER BY doc_id")


def land(data: bytes, file_name: str) -> str:
    """Land an uploaded PDF in S3 and register it (idempotent by content hash). Returns its doc_id."""
    sha = hashlib.sha256(data).hexdigest()
    existing = lake.query(f"SELECT doc_id FROM {DB}.documents WHERE sha256 = {lake.lit(sha)}")
    if existing:
        return existing[0]["doc_id"]
    doc_id = f"U{_now():%m%d%H%M%S}"
    uri = f"{lake.LANDING}/{doc_id}_{file_name}"
    lake.put_bytes(data, uri)
    lake.insert("documents", [{"doc_id": doc_id, "file_name": file_name, "s3_uri": uri, "sha256": sha,
                               "size_bytes": len(data), "source": "upload", "ingested_at": _now(),
                               "status": "pending", "processed_at": None}])
    return doc_id


def ingest(data: bytes, file_name: str) -> str:
    """Live path: land the PDF, process it, rerun the rules."""
    doc_id = land(data, file_name)
    process(documents(f"doc_id = {lake.lit(doc_id)}"))
    run_rules()
    return doc_id


def reset_uploads() -> int:
    """Remove live uploads (documents, extractions, files) so the demo can be run again from the batch state."""
    docs = lake.query(f"SELECT doc_id, s3_uri FROM {DB}.documents WHERE source = 'upload'")
    if docs:
        ids = ", ".join(lake.lit(d["doc_id"]) for d in docs)
        lake.execute(f"DELETE FROM {DB}.extractions WHERE doc_id IN ({ids})")
        lake.execute(f"DELETE FROM {DB}.documents WHERE doc_id IN ({ids})")
        lake.hdfs("-rm", "-f", "-skipTrash", *[d["s3_uri"] for d in docs], *[ocr_uri(d["doc_id"]) for d in docs])
    run_rules()
    return len(docs)


def confirm_field(doc_id: str, field: str, value: str, reviewer: str) -> None:
    """Human review: record the confirmed value (confidence 1.0, model = reviewer) and rerun the rules."""
    lake.execute(f"UPDATE {DB}.extractions SET field_value = {lake.lit(value)}, confidence = 1.0, "
                 f"model = {lake.lit('reviewed by ' + reviewer)}, extracted_at = now() "
                 f"WHERE doc_id = {lake.lit(doc_id)} AND field_name = {lake.lit(field)} AND line_no IS NULL")
    run_rules()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pending", action="store_true", help="process documents not yet processed")
    g.add_argument("--all", action="store_true", help="reprocess every document")
    g.add_argument("--doc", nargs="+", help="process these doc_ids")
    g.add_argument("--rules-only", action="store_true", help="only rerun the rules")
    args = ap.parse_args()
    if not args.rules_only:
        where = ("status = 'pending'" if args.pending else "TRUE" if args.all
                 else f"doc_id IN ({', '.join(map(lake.lit, args.doc))})")
        docs = documents(where)
        print(f"Processing {len(docs)} documents with Mistral OCR 4")
        process(docs)
    print(f"Rules: {run_rules()} exceptions in {DB}.exceptions")


if __name__ == "__main__":
    main()
