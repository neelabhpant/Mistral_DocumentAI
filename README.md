# Document AI on Cloudera: accounts payable with Mistral OCR 4

Supplier invoices land as PDFs in the governed data lake. [Mistral OCR 4](https://docs.mistral.ai/capabilities/document_ai/basic_ocr)
reads them inside the environment and returns the fields as JSON, using a schema, plus layout blocks and word confidence.
Impala joins every field against enterprise data in Iceberg (vendor master, purchase orders, goods receipts, payments).
The result is a prioritized **action queue**. Each action links back to the exact spot on the page and to the record that
triggered it.

The point of the demo: extraction alone is another silo, and enterprise data alone can't read a PDF. Together they produce
an action with an audit trail.

| OCR 4 capability | What it does in the demo |
|---|---|
| Schema mode (`document_annotation_format` + prompt) | Extracts the invoice fields and line items |
| Layout blocks with bounding boxes | Click-to-evidence: each flag highlights its spot on the page |
| Word confidence scores | Low-confidence key fields go to human review instead of payment |
| Self-hostable in one container | The documents never leave the environment |

## Layout

| Path | What it does |
|---|---|
| `ap/generate.py` | Synthetic AP data from one seed: vendors, POs, receipts, payment history, invoice PDFs (5 layouts, scans, a 2-page invoice) with planted issues, plus `ground_truth.json` |
| `ap/setup_lake.py` | Creates the Iceberg tables in `docai_ap`, loads the enterprise data, lands the batch PDFs in S3 |
| `ap/pipeline.py` | PDF in S3 → OCR 4 (schema mode) → `extractions` with page/bbox/confidence → rules. Batch CLI and the live `ingest()` path |
| `ap/rules.sql` | The joins that produce `exceptions`: bank mismatch, duplicate, unknown vendor, no PO, price over PO, qty over receipt, low confidence, discount window |
| `ap/evidence.py` | Ties each extracted value to its OCR 4 block, line or table row, bounding box and word confidence |
| `ap/evaluate.py` | Scores extraction and the action queue against the ground truth |
| `app.py` | Streamlit app: action queue with evidence, live ingest, and "under the hood" views (OCR output, chat, tables, rules, accuracy) |
| `deploy_app.py` | Creates or updates the Cloudera AI Application (`docai-ap`) and the "AP pipeline" Job |

Settings are in `ap/config.py`, overridable by environment variable: S3 prefix, database, Impala connection,
business date (`AP_AS_OF`) and review threshold (`AP_MIN_CONFIDENCE`).

## Run on Cloudera AI

Set `MISTRAL_API_KEY` as a project environment variable (Project Settings > Advanced) and restart your session.

```bash
python -m ap.generate --vendors 15 --seed 7   # data/ap/: PDFs, CSVs, ground truth (data/ is gitignored)
python -m ap.setup_lake --reset               # Iceberg tables + PDFs in S3
python -m ap.pipeline --pending               # OCR 4 + rules (or run the "AP pipeline" Job)
python -m ap.evaluate                         # accuracy vs ground truth
python deploy_app.py                          # Application + Job
```

The app and the Job run on a Python 3.12 runtime and install `requirements.txt` on first start (`ensure_deps.py`).

## Demo flow

1. **Action queue**: 40 invoices processed, 9 planted issues found, 2 sent to review. Open the bank-change invoice (B021): the remit-to
   account is highlighted on the page, next to the vendor master record that disagrees.
2. **Human review**: B001 is a smudged fax. OCR 4 misread the invoice number (A05319 instead of A05519) but with low confidence,
   so it went to a person instead of to payment. Confirm the value and the rules rerun.
3. **Ingest an invoice**: pick a held-back sample (`data/ap/live/`), for example L043. It lands in S3, OCR 4 reads it, and the rules flag it
   within seconds, using the same code as the batch Job.
4. **Under the hood**: the OCR output, the schema and prompt, chat with the invoice, the Iceberg tables, the SQL rules, and the accuracy score.
   **Remove live uploads** resets the demo.
