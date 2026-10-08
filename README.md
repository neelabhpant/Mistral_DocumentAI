# Mistral Document AI: OCR 4 on contracts

Evaluation and demo of [Mistral OCR 4](https://docs.mistral.ai/capabilities/document_ai/basic_ocr) (`mistral-ocr-4`) on
legal contracts from the [CUAD](https://www.atticusprojectai.org/cuad) dataset.

| File | What it does |
|---|---|
| `test_mistral_api.py` | Checks the API key, lists available OCR models, makes one live OCR call |
| `ocr_extract.py` | OCRs PDFs to markdown + raw JSON; scores text against CUAD's reference `.txt` files |
| `contract_ai.py` | "Context" two ways: OCR 4 document annotations (schema + prompt → structured fields, scored against CUAD labels) and a chat model with a system prompt that reads OCR output via a tool |
| `app.py` | Streamlit demo for stakeholders |

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # add your Mistral API key
```

Download CUAD v1 and unzip it so the contracts are at `data/cuad/CUAD_v1/full_contract_pdf/`
(with `full_contract_txt/` and `master_clauses.csv` alongside). You can also upload your own PDFs in the app.

## Run

```bash
.venv/bin/python test_mistral_api.py          # connectivity check
.venv/bin/python ocr_extract.py               # OCR two sample contracts -> outputs/ocr/
.venv/bin/python contract_ai.py --n 8 --seed 42   # schema-only vs schema+prompt extraction accuracy
.venv/bin/streamlit run app.py                # demo app
```

## The demo app

Pick CUAD contracts (or upload PDFs) and run OCR 4. Results are cached in `outputs/ocr_cache/`, so replays are instant.

- **Side-by-side**: page image with color-coded layout blocks next to the extracted markdown; low-confidence words highlighted
- **Extracted fields**: OCR 4 document annotations with an editable prompt; each field has a supporting quote and is scored against CUAD's labels
- **Ask the document**: `mistral-medium-latest` with a system prompt and a `read_document` tool (Mistral's OCR tool-usage cookbook pattern); answers cite pages
- **Full text**, **Confidence**, **Layout & tables**, **vs. basic extraction**, **Raw JSON**

## What "context" means for OCR 4

- **Document annotations** are context for OCR 4 itself. `document_annotation_format` is a JSON schema whose field
  descriptions act as instructions. `document_annotation_prompt` adds free-text guidance and requires a format. In the same
  call that transcribes the document, OCR 4 returns `document_annotation`: JSON matching the schema.
- **A system prompt** (as in the cookbook) goes to a chat model, not to OCR. The chat model calls OCR output as a tool and
  answers from it.

On 8 CUAD contracts the prompt was not tuned on, adding the prompt to the schema raised agreement with CUAD's labels from
73% to 79%. The gain came mostly from normalized dates, renewal terms and notice periods. This is a single run on a small
sample, so treat it as indicative.
