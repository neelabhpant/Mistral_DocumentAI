"""Stand-ins for Cloudera and Mistral, so the real app and ap/ code run end to end on one machine (the tests, and a
local preview of the UI).

- The lake: ap.lake keeps its code, but its three ways out are replaced. Impala becomes one SQLite database (with the
  few rewrites Impala SQL needs), the hdfs CLI a folder standing in for S3, and lit() quotes for SQLite.
- FakeMistral: ocr.process builds an OCR 4.1-shaped response from the PDF's text layer (blocks with boxes, the
  line-item table, word confidences) and annotates it from the generator's ground truth; chat.complete reads the
  invoice with one read_document call, then answers; models.list includes OCR 4.1.

install(tmp_dir) wires them in. Call it before anything imports ap.config, which reads the environment it sets.
"""

import base64
import csv
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pymupdf

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "ap"
S3_ROOT = "s3a://test-lake/user/test/docai"
PROJECT_KEY = "fake-project-key-0123456789"
NO_KEY = ("No API key found: set MISTRAL_API_KEY as a Cloudera AI project environment variable "  # test_mistral_api's
          "(Project Settings > Advanced; restart the app or session afterwards), or in .env when running locally")

OVERRIDES: dict[str, dict[str, tuple]] = {}  # doc_id -> {field: (value read, or None for the true one; confidence)}
DELAY = 0.0  # seconds the OCR call takes for a live ingest
TMP: Path | None = None


# ---------- lake: Impala as SQLite, S3 as a folder ----------

LITERAL = re.compile(r"('(?:[^']|'')*')")
ICEBERG = re.compile(r"\s+COMMENT\s+'(?:[^']|'')*'\s+STORED AS ICEBERG TBLPROPERTIES\s*\([^)]*\)\s*$")
REWRITES = [  # Impala -> SQLite, applied outside string literals
    (r"\bTRUNCATE TABLE\b", "DELETE FROM"),
    (r"\bAS (?:STRING|DATE|TIMESTAMP)\b", "AS TEXT"),  # CAST(...)
    (r"\bSTRING\b", "TEXT"),  # column type: as STRING, SQLite would store routing number 086910239 as 86910239
    (r"\bDATE\s+$", ""),  # DATE '2026-10-08' -> '2026-10-08' (the literal is the next part)
    (r"\bif\(", "iif("),
    (r"\s+PURGE\s*$", ""),
]


def sqlite_sql(sql: str, db: str) -> str | None:
    """Impala SQL as SQLite, or None for a statement SQLite has no use for."""
    if re.match(r"\s*CREATE DATABASE\b", sql):
        return None
    parts = LITERAL.split(ICEBERG.sub("", sql))
    for i in range(0, len(parts), 2):  # odd parts are string literals
        part = parts[i].replace(f"{db}.", "")
        for pattern, repl in REWRITES:
            part = re.sub(pattern, repl, part)
        parts[i] = part
    return "".join(parts)


def sqlite_lit(v) -> str:
    """lake.lit for SQLite, which escapes a quote by doubling it."""
    if v is None or v == "":
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return str(v) if isinstance(v, Decimal) else repr(v)
    if isinstance(v, datetime):
        return f"'{v:%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


class FakeImpala:
    """The project's Impala data connection: get_cursor() over one in-memory SQLite database."""

    def __init__(self, db: str):
        self.db, self.lock = db, threading.Lock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None,
                                    detect_types=sqlite3.PARSE_DECLTYPES)
        fn = self.conn.create_function
        fn("regexp_replace", 3, lambda s, pattern, repl: None if s is None else re.sub(pattern, repl, str(s)))
        fn("concat", -1, lambda *args: None if None in args else "".join(map(str, args)))  # NULL if any is, as Impala
        fn("date_add", 2, lambda d, n: None if d is None else str(date.fromisoformat(str(d)[:10]) + timedelta(days=n)))
        fn("now", 0, lambda: f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}")

    def get_cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, impala: FakeImpala):
        self.impala, self.description, self.rows = impala, None, []

    def execute(self, sql: str) -> None:
        sql = sqlite_sql(sql, self.impala.db)
        if sql:
            with self.impala.lock:  # one connection, one statement at a time
                cur = self.impala.conn.execute(sql)
                self.description, self.rows = cur.description, cur.fetchall()

    def fetchall(self) -> list:
        return self.rows


class FakeS3:
    """`hdfs dfs` on a folder: s3a://bucket/key is <root>/bucket/key."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, uri: str) -> Path:
        scheme, _, key = uri.partition("://")
        if scheme != "s3a":
            raise ValueError(f"Not an s3a:// URI: {uri}")
        return self.root / key

    def hdfs(self, *args: str, data: bytes | None = None) -> bytes:
        cmd, *rest = args
        paths = [a for a in rest if a == "-" or not a.startswith("-")]  # without flags such as -p, -f, -skipTrash
        if cmd == "-mkdir":
            self.path(paths[0]).mkdir(parents=True, exist_ok=True)
        elif cmd == "-put" and paths[0] == "-":  # -put -f - uri, with the bytes on stdin
            self.path(paths[1]).parent.mkdir(parents=True, exist_ok=True)
            self.path(paths[1]).write_bytes(data)
        elif cmd == "-put":  # -put -f files... prefix/
            *files, prefix = paths
            self.path(prefix).mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copyfile(f, self.path(prefix) / Path(f).name)
        elif cmd == "-cat":
            if not self.path(paths[0]).is_file():
                raise RuntimeError(f"hdfs dfs -cat {paths[0]} failed: No such file or directory")
            return self.path(paths[0]).read_bytes()
        elif cmd == "-rm":
            for p in paths:
                self.path(p).unlink(missing_ok=True)
        else:
            raise NotImplementedError(f"hdfs dfs {' '.join(args)}")
        return b""


# ---------- OCR 4.1 from the PDF's text layer ----------

DPI = 200  # OCR 4.1 gives boxes in pixels of a 200 dpi page image
TITLE_PT, ROW_PT, WRAP_PT = 13, 3, 280  # bigger type is a title; one row within 3 pt; a line this wide may wrap
SMUDGE = 0.41  # confidence of the field under an ink smudge
PLACEHOLDER = re.compile(r"\[(tbl-\d+\.md)\]\(\1\)")
PDF = threading.Lock()  # PyMuPDF isn't thread-safe, and pipeline.process runs OCR calls in a thread pool


@cache
def ground_truth() -> dict:
    return json.loads((DATA / "ground_truth.json").read_text())["invoices"]


@cache
def known_pdfs() -> dict[str, str]:
    """doc_id by the sha256 of each invoice PDF the generator wrote."""
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p.name.split("_")[0]
            for folder in ("pdfs", "live") for p in (DATA / folder).glob("*.pdf")}


@cache
def vendor_master() -> dict[str, dict]:
    with open(DATA / "tables" / "vendors.csv", newline="") as f:
        return {v["vendor_id"]: v for v in csv.DictReader(f)}


@cache
def generated() -> tuple[dict, dict]:
    """The generator's invoices and vendors by id (seed 7, as shipped)."""
    from ap.generate import build

    data = build(15, 7)
    return {i.doc_id: i for i in data["invoices"]}, {v["vendor_id"]: v for v in data["vendors"]}


def annotation(doc_id: str) -> dict:
    """What schema mode extracts from a generated invoice (the InvoiceFields shape): the ground truth, plus the
    address and tax ID it prints from the vendor master."""
    t = ground_truth()[doc_id]
    v = vendor_master()[t["vendor_id"]]
    return {"vendor_name": t["vendor_name"], "vendor_address": v["address"], "vendor_tax_id": v["tax_id"],
            "invoice_number": t["invoice_number"], "invoice_date": t["invoice_date"], "due_date": t["due_date"],
            "po_number": t["po_number"], "payment_terms": t["terms"], "currency": "USD", "bank_name": t["bank_name"],
            "routing_number": t["routing_number"], "bank_account": t["account_number"],
            "bank_change_notice": t["note"] or None,
            "line_items": [{"sku": line["sku"], "description": line["description"], "quantity": line["qty"],
                            "unit_price": line["unit_price"], "amount": line["amount"]} for line in t["lines"]],
            "subtotal": t["subtotal"], "tax": t["tax"], "total": t["total"]}


def unknown_annotation(text: str) -> dict:
    """A PDF the generator didn't write: its first line as the vendor (so no vendor master match) and an invoice
    number, if one is labelled."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    number = re.search(r"invoice\s*(?:#|no\.?|number)?\s*:?\s*([A-Z0-9][A-Z0-9-]{2,})", text, re.I)
    return {"vendor_name": lines[0] if lines else "Unknown vendor", "invoice_number": number[1] if number else None,
            "line_items": []}


def text_pdf(data: bytes, doc_id: str | None):
    """The PDF to take text positions from: the file itself, or for a scan (no text layer) the clean invoice
    regenerated from the seed, which has the same layout."""
    pdf = pymupdf.open(stream=data, filetype="pdf")
    if doc_id is None or any(page.get_text().strip() for page in pdf):
        return pdf
    pdf.close()
    path = TMP / "clean" / f"{doc_id}.pdf"
    if not path.exists():
        from ap.generate import render

        invoices, vendors = generated()
        invoice = replace(invoices[doc_id], scanned=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        render(invoice, vendors[invoice.vendor_id], path)
    return pymupdf.open(path)


def _union(rects) -> list[float]:
    x0, y0, x1, y1 = zip(*rects)
    return [min(x0), min(y0), max(x1), max(y1)]


def _rows(items) -> list[list]:
    """(rect, text) items in visual rows, top to bottom, each row left to right."""
    rows = []
    for item in sorted(items, key=lambda i: i[0][1] + i[0][3]):
        if rows and abs(rows[-1][0][0][1] + rows[-1][0][0][3] - item[0][1] - item[0][3]) < 2 * ROW_PT:
            rows[-1].append(item)
        else:
            rows.append([item])
    return [sorted(row, key=lambda i: i[0][0]) for row in rows]


def _unwrap(rows) -> list[list]:
    """Join a paragraph line that wrapped onto the next row: OCR returns a paragraph as one line."""
    out = []
    for row in rows:
        if out and len(out[-1]) == len(row) == 1:
            (r0, t0), (r1, t1) = out[-1][0], row[0]
            if r0[2] - r0[0] > WRAP_PT and abs(r0[0] - r1[0]) < 2:
                out[-1] = [(_union([r0, r1]), f"{t0} {t1}")]
                continue
        out.append(row)
    return out


def layout(page, fix) -> list[dict]:
    """The page's text as OCR-style blocks in reading order: {type, rect (points), rows of (rect, text)}. Bigger type
    is a title and the small print at the bottom the footer. The line-item table, from its first row of four or more
    columns to the row before Subtotal, is one block whose cells are the PDF's text spans."""
    parts = []
    for b in page.get_text("dict", flags=0)["blocks"]:  # flags=0: ligatures expanded, odd spaces as spaces
        lines = []
        for line in b.get("lines", []):
            spans = [(s["bbox"], fix(" ".join(s["text"].split()))) for s in line["spans"] if s["text"].strip()]
            if spans:
                lines.append({"rect": line["bbox"], "spans": spans,
                              "text": fix(" ".join("".join(s["text"] for s in line["spans"]).split())),
                              "big": max(s["size"] for s in line["spans"]) >= TITLE_PT})
        found = []
        for big in (True, False):
            group = [line for line in lines if line["big"] == big]
            if group:
                rect = _union(line["rect"] for line in group)
                found.append({"type": "title" if big else "footer" if rect[1] > 0.92 * page.rect.height else "text",
                              "rect": rect, "rows": _unwrap(_rows([(line["rect"], line["text"]) for line in group])),
                              "cells": _rows([span for line in group for span in line["spans"]])})
        parts += sorted(found, key=lambda p: p["rect"][1])
    blocks, table, closed = [], None, False
    for p in parts:
        first = p["rows"][0]
        columns = len(p["rows"]) == 1 and len(first) >= 4
        if p["type"] == "text" and not closed and (table or columns) and not first[0][1].startswith("Subtotal"):
            if table is None:
                table = {"type": "table", "rect": p["rect"], "rows": []}
                blocks.append(table)
            table["rect"] = _union([table["rect"], p["rect"]])
            table["rows"] += p["cells"]
        else:
            closed = closed or table is not None
            blocks.append(p)
    return blocks


def table_md(rows) -> str:
    """Rows of (rect, text) cells as a markdown table. Columns start where the first row's cells do; a row with other
    cells (a note under an item) puts each in the column under its center."""
    starts = [rect[0] for rect, _ in rows[0]]
    lines = []
    for row in rows:
        cells = [""] * len(starts)
        for i, (rect, cell) in enumerate(row):
            center = (rect[0] + rect[2]) / 2
            col = i if len(row) == len(starts) else max([0] + [j for j, x in enumerate(starts) if x <= center])
            cells[col] = f"{cells[col]} {cell}".strip()
        lines.append(f"| {' | '.join(cells)} |")
    return "\n".join([lines[0], "|" + "---|" * len(starts), *lines[1:]])


def scores(text: str, rng: random.Random, lo: float, hi: float, lows: list) -> list[dict]:
    """Word confidences, start_index into text: random in [lo, hi], or the confidence of a low needle it overlaps."""
    low = [(m.start(), m.end(), conf) for needle, conf in lows for m in re.finditer(re.escape(needle), text, re.I)]
    out = []
    for m in re.finditer(r"[^\s|]+", text):
        if not re.search(r"\w", m[0]) or PLACEHOLDER.fullmatch(m[0]):
            continue
        conf = round(rng.uniform(lo, hi), 4)
        conf = next((c for start, end, c in low if start < m.end() and m.start() < end), conf)
        out.append({"text": m[0], "confidence": conf, "start_index": m.start()})
    return out


def ocr_page(page, index: int, rng: random.Random, lo: float, hi: float, lows: list, swaps: list) -> dict:
    """One page of an OCR 4.1 response: markdown, blocks in pixels, the line-item table, word confidences."""

    def fix(s: str) -> str:  # a misread value, printed as OCR read it
        for old, new in swaps:
            s = s.replace(old, new)
        return s

    px = DPI / 72
    md, blocks, tables = [], [], []
    for b in layout(page, fix):
        if b["type"] == "table":
            table_id, content = f"tbl-{index}.md", table_md(b["rows"])
            tables.append({"id": table_id, "content": content, "format": "markdown",
                           "word_confidence_scores": scores(content, rng, lo, hi, lows)})
            md.append(f"[{table_id}]({table_id})")
        else:
            lines = [" ".join(cell for _, cell in row) for row in b["rows"]]
            content = f"# {' '.join(lines)}" if b["type"] == "title" else "\n".join(lines)
            md.append(content)
        x0, y0, x1, y1 = (round(v * px) for v in b["rect"])
        blocks.append({"type": b["type"], "content": content, "top_left_x": x0, "top_left_y": y0,
                       "bottom_right_x": x1, "bottom_right_y": y1})
    markdown = "\n\n".join(md)
    words = scores(markdown, rng, lo, hi, lows)
    confs = [w["confidence"] for w in words] + [w["confidence"] for t in tables for w in t["word_confidence_scores"]]
    return {"index": index, "markdown": markdown, "images": [], "tables": tables, "blocks": blocks,
            "dimensions": {"dpi": DPI, "width": round(page.rect.width * px), "height": round(page.rect.height * px)},
            "confidence_scores": {"average_page_confidence_score": round(sum(confs) / len(confs), 4),
                                  "minimum_page_confidence_score": min(confs),
                                  "word_confidence_scores": words} if confs else None}


def ocr_response(data: bytes, model: str):
    """What OCR 4.1 in schema mode returns for a PDF: the generator's invoices are annotated from the ground truth
    (B001-style smudges and the tests' OVERRIDES read with low confidence), anything else from its text."""
    from mistralai.client.models import OCRResponse

    from ap.evidence import candidates

    sha = hashlib.sha256(data).hexdigest()
    doc_id = known_pdfs().get(sha)
    truth = ground_truth().get(doc_id)
    if DELAY and (truth is None or truth["set"] == "live"):  # a live ingest's OCR call takes a moment
        time.sleep(DELAY)
    with PDF, text_pdf(data, doc_id) as pdf:
        fields = annotation(doc_id) if truth else unknown_annotation("".join(page.get_text() for page in pdf))
        smudged = {truth["smudge"]: (None, SMUDGE)} if truth and truth["smudge"] else {}
        lows, swaps = [], []  # (text as printed, confidence) and (true value, value read) as printed
        for field, (value, conf) in {**smudged, **OVERRIDES.get(doc_id, {})}.items():
            if value is not None:
                swaps += zip(candidates(field, fields.get(field)), candidates(field, value))
                fields[field] = value
            lows += [(needle, conf) for needle in candidates(field, fields.get(field))]
        lo, hi = (0.85, 0.99) if truth and truth["scanned"] else (0.93, 0.999)
        pages = [ocr_page(page, n, random.Random(f"{sha}:{n}"), lo, hi, lows, swaps) for n, page in enumerate(pdf)]
    return OCRResponse.model_validate({"pages": pages, "model": model, "document_annotation": json.dumps(fields),
                                       "usage_info": {"pages_processed": len(pages), "doc_size_bytes": len(data)}})


# ---------- Mistral ----------


class FakeMistral:
    """Stands in for mistralai.client.Mistral, also as a context manager: ocr.process, chat.complete, models.list."""

    keys: list[str] = []  # the api_key of every client made, oldest first
    rejected: set[str] = set()  # keys Mistral answers with a 401

    def __init__(self, api_key: str):
        FakeMistral.keys.append(api_key)
        self.api_key = api_key
        self.ocr = SimpleNamespace(process=self._ocr)
        self.chat = SimpleNamespace(complete=self._chat)
        self.models = SimpleNamespace(list=self._models)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def _authorize(self) -> None:
        if self.api_key in FakeMistral.rejected:
            import httpx2
            from mistralai.client.errors import SDKError

            raise SDKError("API error occurred", httpx2.Response(401, json={"message": "Unauthorized"}))

    def _ocr(self, model: str, document: dict, **options):
        self._authorize()
        return ocr_response(base64.b64decode(document["document_url"].split(",", 1)[1]), model)

    def _chat(self, model: str, messages: list, tools: list | None = None, temperature=None):
        """Read the loaded invoice with the tool first, then answer from what it returned."""
        self._authorize()
        if messages[-1]["role"] != "tool":
            name = tools[0]["function"]["parameters"]["properties"]["name"]["enum"][0]
            call = SimpleNamespace(id=f"call_{len(messages)}",
                                   function=SimpleNamespace(name="read_document", arguments=json.dumps({"name": name})))
            message = SimpleNamespace(content="", tool_calls=[call])
        else:
            lines = [line.lstrip("# ").strip() for line in messages[-1]["content"].splitlines()
                     if line.strip() and not line.startswith(("###", "|"))]
            answer = f"**{lines[0]}** (p. 1)\n\n" + "\n".join(f"- {line}" for line in lines[1:4]) if lines else ""
            message = SimpleNamespace(content=answer or "Not stated on the invoice.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _models(self):
        from test_mistral_api import OCR_MODEL

        self._authorize()
        ids = (OCR_MODEL, "mistral-ocr-latest", "mistral-medium-latest")
        return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in ids])


def get_client() -> FakeMistral:
    """test_mistral_api.get_client, with the key from the environment only (never a local .env)."""
    key = (os.getenv("MISTRAL_API") or os.getenv("MISTRAL_API_KEY") or "").strip()
    if not key:
        sys.exit(NO_KEY)
    return FakeMistral(api_key=key)


def set_project_key(on: bool) -> None:
    """Give the project a MISTRAL_API_KEY, or take it away. The app keeps its project client, so that goes too."""
    os.environ.pop("MISTRAL_API", None)
    if on:
        os.environ["MISTRAL_API_KEY"] = PROJECT_KEY
    else:
        os.environ.pop("MISTRAL_API_KEY", None)
    if "app" in sys.modules:
        sys.modules["app"].project_client.cache_clear()


# ---------- wiring ----------


def install(tmp_dir: Path, overrides: dict | None = None, project_key: bool = True, delay: float = 0.0):
    """Point the app and ap/ at the fakes, with lake files and caches under tmp_dir, and return the app module.

    overrides: {doc_id: {field: (value or None, confidence)}}, a field OCR misreads (or reads correctly when value is
    None) with that confidence. delay: seconds OCR takes for a live ingest."""
    global OVERRIDES, DELAY, TMP
    if "ap.config" in sys.modules:
        raise RuntimeError("tests.fakes.install() must run before ap.config is imported")
    OVERRIDES, DELAY, TMP = overrides or {}, delay, Path(tmp_dir)
    for name in ("AP_AS_OF", "AP_MIN_CONFIDENCE"):  # the defaults the data was generated for
        os.environ.pop(name, None)
    os.environ.update(AP_S3_ROOT=S3_ROOT, AP_DB="docai_ap", HADOOP_USER_NAME="test.reviewer")
    set_project_key(project_key)
    # Impala returns DECIMAL as Decimal (1023.70 prints as 1023.70), DATE as date and TIMESTAMP as datetime.
    sqlite3.register_converter("DECIMAL", lambda b: Decimal(b.decode()).quantize(Decimal("0.01")))
    sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()[:10]))
    sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))

    from ap import lake

    impala = FakeImpala("docai_ap")
    lake.hdfs, lake.lit, lake._connection = FakeS3(TMP / "s3").hdfs, sqlite_lit, lambda: impala

    import test_mistral_api
    from ap import pipeline

    import app

    pipeline.CACHE = TMP / "cache"
    test_mistral_api.get_client = pipeline.get_client = app.get_client = get_client
    app.Mistral = FakeMistral
    app.load_dotenv = lambda *args, **kwargs: False  # the project key is what set_project_key says
    app.PDF = PDF  # one PyMuPDF lock for the app's page images and the fake OCR
    return app


def bootstrap(doc_ids: list[str] | None = None) -> None:
    """Set up the lake as the AMP's jobs do: tables, enterprise data, the batch invoices (all, or these), OCR, rules."""
    from ap import pipeline, setup_lake

    setup_lake.create_tables(True)
    setup_lake.load_enterprise()
    if doc_ids is None:
        setup_lake.land_batch()
    else:  # land_batch lands every PDF in DATA/pdfs: give it a folder with just these
        subset = TMP / "subset"
        (subset / "pdfs").mkdir(parents=True, exist_ok=True)
        for doc_id in doc_ids:
            pdf = next((DATA / "pdfs").glob(f"{doc_id}_*.pdf"))
            shutil.copyfile(pdf, subset / "pdfs" / pdf.name)
        with mock.patch.object(setup_lake, "DATA", subset):
            setup_lake.land_batch()
    pipeline.process(pipeline.documents("TRUE"), client=FakeMistral(api_key=PROJECT_KEY))
    pipeline.run_rules()
