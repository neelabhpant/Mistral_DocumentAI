"""Create the Iceberg tables, load the enterprise data, and land the batch invoice PDFs in S3.

Usage: python -m ap.setup_lake [--reset]
--reset drops and recreates every table (the S3 landing folder is overwritten either way).
"""

import argparse
import csv
import hashlib
from datetime import date, datetime, timezone

from ap import lake
from ap.config import DATA, DB

ICEBERG = "STORED AS ICEBERG TBLPROPERTIES ('format-version'='2')"

TABLES = {
    # Enterprise data that already lives in Cloudera (ERP extracts)
    "vendors": ("Vendor master (ERP): bank account on file and payment terms", """
        vendor_id STRING, name STRING, address STRING, tax_id STRING, bank_name STRING, routing_number STRING,
        bank_account STRING, payment_terms STRING, status STRING"""),
    "purchase_orders": ("Purchase order headers (ERP)", """
        po_number STRING, vendor_id STRING, po_date DATE, buyer STRING, status STRING"""),
    "po_lines": ("Purchase order lines (ERP): agreed quantity and unit price", """
        po_number STRING, line_no INT, sku STRING, description STRING, qty INT, unit_price DECIMAL(12,2)"""),
    "goods_receipts": ("Goods received against PO lines (warehouse)", """
        receipt_id STRING, po_number STRING, line_no INT, sku STRING, qty_received INT, received_date DATE"""),
    "payments": ("Invoices already paid (AP ledger)", """
        payment_id STRING, vendor_id STRING, invoice_number STRING, invoice_date DATE, amount DECIMAL(12,2),
        paid_date DATE, payment_method STRING"""),
    # Document AI
    "documents": ("Invoice PDFs landed in S3: one row per file", """
        doc_id STRING, file_name STRING, s3_uri STRING, sha256 STRING, size_bytes BIGINT, source STRING,
        ingested_at TIMESTAMP, status STRING, processed_at TIMESTAMP"""),
    "extractions": ("Fields extracted by Mistral OCR 4, with page, bounding box and confidence", """
        doc_id STRING, field_name STRING, line_no INT, field_value STRING, confidence DOUBLE, page INT, bbox STRING,
        quote STRING, model STRING, extracted_at TIMESTAMP"""),
    "exceptions": ("Action queue: extracted fields joined against enterprise data", """
        doc_id STRING, vendor_id STRING, invoice_number STRING, rule_code STRING, detail STRING,
        amount_at_risk DECIMAL(12,2), priority INT, action STRING, evidence_field STRING, created_at TIMESTAMP"""),
}
DATE_COLS = {"po_date", "received_date", "invoice_date", "paid_date"}
INT_COLS = {"line_no", "qty", "qty_received"}
DEC_COLS = {"unit_price", "amount"}


def typed(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k in DATE_COLS:
            out[k] = date.fromisoformat(v)
        elif k in INT_COLS:
            out[k] = int(v)
        elif k in DEC_COLS:
            out[k] = float(v)
        else:
            out[k] = v
    return out


def create_tables(reset: bool) -> None:
    lake.execute(f"CREATE DATABASE IF NOT EXISTS {DB} COMMENT 'Cloudera + Mistral Document AI: accounts payable demo'")
    for name, (comment, cols) in TABLES.items():
        if reset:
            lake.execute(f"DROP TABLE IF EXISTS {DB}.{name} PURGE")
        lake.execute(f"CREATE TABLE IF NOT EXISTS {DB}.{name} ({cols}) COMMENT '{comment}' {ICEBERG}")


def load_enterprise() -> None:
    for name in ("vendors", "purchase_orders", "po_lines", "goods_receipts", "payments"):
        with open(DATA / "tables" / f"{name}.csv") as f:
            rows = [typed(r) for r in csv.DictReader(f)]
        lake.execute(f"TRUNCATE TABLE {DB}.{name}")
        lake.insert(name, rows)
        print(f"  {name}: {len(rows)} rows")


def land_batch() -> None:
    files = sorted((DATA / "pdfs").glob("*.pdf"))
    lake.put_files(files)
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    rows = [{
        "doc_id": f.name.split("_")[0], "file_name": f.name, "s3_uri": f"{lake.LANDING}/{f.name}",
        "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "size_bytes": f.stat().st_size,
        "source": "batch", "ingested_at": now, "status": "pending", "processed_at": None,
    } for f in files]
    lake.execute(f"TRUNCATE TABLE {DB}.documents")
    lake.insert("documents", rows)
    print(f"  documents: {len(rows)} PDFs landed in {lake.LANDING}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reset", action="store_true", help="drop and recreate all tables")
    args = ap.parse_args()
    print(f"Creating Iceberg tables in {DB}")
    create_tables(args.reset)
    print("Loading enterprise data")
    load_enterprise()
    print("Landing batch invoices")
    land_batch()
    for name in TABLES:
        n = lake.query(f"SELECT COUNT(*) AS n FROM {DB}.{name}")[0]["n"]
        print(f"  {DB}.{name}: {n}")


if __name__ == "__main__":
    main()
