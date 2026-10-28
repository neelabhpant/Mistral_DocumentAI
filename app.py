"""Document AI on Cloudera: the web app. A JSON API over the ap/ package (action queue with click-to-evidence, human
review, live ingest, chat with an invoice, tables and rules, accuracy) that also serves the front end in static/ (plain
HTML, CSS and JavaScript modules, no build step).

Long operations (ingest, review, reset, scoring) run as background jobs that the front end polls, so no request stays
open behind the Cloudera AI proxy and the UI can show each step. Jobs, caches and the Impala lock live in this one
process, so it runs as a single worker.

Run: uvicorn app:app --port 8090   (on Cloudera AI: launch_app.py)
"""

import json
import math
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from functools import cache, lru_cache
from pathlib import Path

import pymupdf
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mistralai.client import Mistral
from mistralai.client.models import OCRResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from ap import architecture, brand, explain, lake, pipeline
from ap.assistant import CHAT_MODEL, SUGGESTIONS, SYSTEM_PROMPT, ask, doc_for_chat, tool_spec
from ap.config import AS_OF, DATA, DB, MIN_CONFIDENCE, ROOT, S3_ROOT
from ap.evaluate import evaluate
from ap.ocr import OCR_MODEL, api_key, get_client, page_markdown
from ap.schema import ANNOTATION_FORMAT, HEADER_FIELDS, INVOICE_PROMPT
from ap.setup_lake import TABLES

PRICE_PER_PAGE = 4 / 1000  # USD, standard (non-batch) API pricing
REVIEWER = os.getenv("HADOOP_USER_NAME") or os.getenv("USER") or "reviewer"
PRIORITY = {1: "1 · Critical", 2: "2 · High", 3: "3 · Medium", 4: "Opportunity", 5: "Review"}
MD_PLACEHOLDER = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
WORD_OK = re.compile(r"^[\w$%.,;:'\"()/&-]+$")

DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # becomes part of local cache file names and S3 paths
EVIDENCE_FIELD = re.compile(r"[A-Za-z0-9_:]{1,64}")
HEX_ID = re.compile(r"[0-9a-f]{1,32}")  # job and chat ids
API_KEY = re.compile(r"[\x21-\x7e]{16,256}")  # printable ASCII, no spaces
MAX_UPLOAD = 25 * 1024 * 1024
NO_KEY = ("No Mistral API key: paste one under Mistral API key in the sidebar, or set MISTRAL_API_KEY in Project "
          "Settings > Advanced.")
STATIC = ROOT / "static"
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}

# A Windows registry can map .js to text/plain, which browsers refuse for modules; older type maps lack woff2.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")


# ---------- lake access: one lock, a short-lived cache ----------

# cml.data_v1 hands out one shared Impala connection, and a HiveServer2 connection can't be used from several threads
# at once (FastAPI runs these endpoints in a thread pool). Every operation that reaches the lake holds this lock.
LAKE = threading.RLock()
CACHE_SECONDS = 600  # as the old st.cache_data(ttl=600)
_cache: dict[str, tuple[float, list[dict]]] = {}
_epoch = 0  # bumped by clear_cache, so a read that overlapped a job's writes isn't cached after the job


def clean(v):
    """A value leaving the server: Decimal -> float, datetime -> "YYYY-MM-DD HH:MM:SS", date -> ISO, NaN -> None."""
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return f"{v:%Y-%m-%d %H:%M:%S}"
    if isinstance(v, date):
        return v.isoformat()
    return v


def fetch(sql: str) -> list[dict]:
    with LAKE:
        rows = lake.query(sql)
    return [{k: clean(v) for k, v in r.items()} for r in rows]


def query(sql: str) -> list[dict]:
    """fetch, cached by SQL text for CACHE_SECONDS. The rows are shared: don't modify them."""
    hit = _cache.get(sql)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    epoch, rows = _epoch, fetch(sql)
    if epoch == _epoch:
        _cache[sql] = (time.monotonic(), rows)
    return rows


def clear_cache() -> None:
    global _epoch
    _epoch += 1
    _cache.clear()


def documents() -> list[dict]:
    return query(f"""SELECT d.doc_id, d.file_name, d.s3_uri, d.sha256, d.source, d.status,
                            CAST(d.ingested_at AS STRING) ingested_at, CAST(d.processed_at AS STRING) processed_at,
                            h.vendor_name, h.invoice_number, h.total
                     FROM {DB}.documents d LEFT JOIN {DB}.invoice_header h ON h.doc_id = d.doc_id ORDER BY d.doc_id""")


def check(ok, message: str) -> None:
    """400 with message unless ok."""
    if not ok:
        raise HTTPException(400, message)


def document(doc_id: str, processed: bool = False) -> dict:
    """A document's row: 400 for a malformed id, 404 for an unknown one (or, with processed, an unprocessed one)."""
    check(DOC_ID.fullmatch(doc_id), "Invalid doc_id.")
    doc = next((d for d in documents() if d["doc_id"] == doc_id), None)
    if doc is None or (processed and doc["status"] != "processed"):
        raise HTTPException(404, f"No {'processed ' if processed else ''}document {doc_id}.")
    return doc


def extractions(doc_id: str) -> list[dict]:
    return query(f"SELECT * FROM {DB}.extractions WHERE doc_id = {lake.lit(doc_id)} "
                 "ORDER BY line_no NULLS FIRST, field_name")


def queue_rows() -> list[dict]:
    return query(f"""SELECT e.*, v.name vendor_name, d.source, d.file_name
                     FROM {DB}.exceptions e
                     LEFT JOIN {DB}.vendors v ON v.vendor_id = e.vendor_id
                     LEFT JOIN {DB}.documents d ON d.doc_id = e.doc_id
                     ORDER BY e.priority, e.amount_at_risk DESC""")


@lru_cache(maxsize=64)
def _ocr(doc_id: str, processed_at: str) -> tuple[dict, OCRResponse]:
    raw = pipeline.ocr_json(doc_id)
    return raw, OCRResponse.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})


def ocr(doc: dict) -> tuple[dict, OCRResponse]:
    """A document's raw OCR 4.1 output and the parsed response, kept per (doc_id, processed_at)."""
    with LAKE:  # ocr_json may read the lake, and it writes the local cache
        return _ocr(doc["doc_id"], doc["processed_at"])


PDF = threading.Lock()  # PyMuPDF isn't thread-safe


@lru_cache(maxsize=64)
def page_png(sha256: str, s3_uri: str, n: int, dpi: int) -> bytes | None:
    """Page n (1-based) as PNG, or None past the last page."""
    with LAKE:
        data = pipeline.pdf_bytes({"sha256": sha256, "s3_uri": s3_uri})
    with PDF, pymupdf.open(stream=data, filetype="pdf") as pdf:
        return pdf[n - 1].get_pixmap(dpi=dpi).tobytes("png") if 1 <= n <= pdf.page_count else None


# ---------- background jobs ----------

JOBS: OrderedDict[str, dict] = OrderedDict()  # the 50 most recent, oldest first
JOBS_LOCK = threading.Lock()


def start_job(kind: str, fn, key: str | None = None) -> str:
    """Run fn(step) in a thread and return the job's id. fn returns the result dict, and wraps each stage in
    `with step(name, label):` so the UI can show progress. key (a tab's Mistral key) is scrubbed from the error."""
    job = {"id": secrets.token_hex(8), "kind": kind, "state": "running", "steps": [], "result": None, "error": None}
    with JOBS_LOCK:
        JOBS[job["id"]] = job
        while len(JOBS) > 50:
            JOBS.popitem(last=False)

    @contextmanager
    def step(name: str, label: str):
        s = {"name": name, "label": label, "state": "running", "seconds": None}
        with JOBS_LOCK:
            job["steps"].append(s)
        start, state = time.perf_counter(), "error"
        try:
            yield s
            state = "done"
        finally:
            with JOBS_LOCK:
                s.update(state=state, seconds=round(time.perf_counter() - start, 1))

    def run() -> None:
        result = error = None
        try:
            result = fn(step)
        except BaseException as e:  # SystemExit too (get_client exits without a key): a job must never stay running
            log_exception(key)
            error = mistral_error(e, key) if getattr(e, "status_code", None) else scrub(str(e) or type(e).__name__, key)
        clear_cache()  # before the job reads as finished, so the UI's reload sees the new data
        with JOBS_LOCK:
            job.update(state="error" if error else "done", result=result, error=error[:500] if error else None)

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return job["id"]


# ---------- Mistral client: the tab's key, or the project's ----------


class MissingKey(Exception):
    """No Mistral API key in the request, and none set for the project."""


def tab_key(x_mistral_key: str | None = Header(None)) -> str | None:
    """The key a browser tab sent in X-Mistral-Key, if any. It lives only in this request and its job."""
    if x_mistral_key is not None and not API_KEY.fullmatch(x_mistral_key):
        raise HTTPException(400, "That doesn't look like a Mistral API key.")
    return x_mistral_key


@cache
def project_client():
    """The client for the project's MISTRAL_API_KEY, kept for the life of the process (changing a project environment
    variable restarts the Application). Nothing is cached while there's no key."""
    try:
        return get_client()
    except SystemExit:  # get_client exits with instructions when no key is set
        raise MissingKey(NO_KEY) from None


@contextmanager
def mistral(key: str | None):
    """A client for the tab's key, closed afterwards; else the project's client."""
    if key:
        with Mistral(api_key=key) as client:
            yield client
    else:
        yield project_client()


def env_key() -> bool:
    """Whether the project has a Mistral key: the lookup get_client does (never the value)."""
    return bool(api_key())


def scrub(text: str, key: str | None) -> str:
    return text.replace(key, "[key]") if key else text


def mistral_error(e: BaseException, key: str | None) -> str:
    """A short message for a failed Mistral call, without the tab's key. SDK HTTP errors carry .status_code."""
    if getattr(e, "status_code", None) in (401, 403):
        return "Mistral rejected this key."
    return scrub(f"Mistral API call failed: {(str(e) or type(e).__name__).splitlines()[0][:200]}", key)


def log_exception(key: str | None = None) -> None:
    print(scrub(traceback.format_exc(), key), file=sys.stderr, flush=True)


# ---------- OCR helpers ----------


def blocks(page) -> list:
    """Layout blocks with coordinates (UnknownBlock entries have none)."""
    return [b for b in (page.blocks or []) if getattr(b, "top_left_x", None) is not None]


def words(page) -> list:
    cs = page.confidence_scores
    return (cs.word_confidence_scores or []) if cs else []


def marked_markdown(page) -> tuple[str, list[float]]:
    """Page markdown, tables inlined, with each word below MIN_CONFIDENCE between sentinels: "\\ue000{n}\\ue001" before
    word n and "\\ue002" after it. Returns it and each marked word's confidence. The front end escapes the text and
    turns the sentinels into <mark>, so no HTML is built from OCR output here."""
    md = page.markdown
    protected = [m.span() for m in MD_PLACEHOLDER.finditer(md)]
    spans = []
    for w in sorted(words(page), key=lambda w: w.start_index):
        core = w.text.strip()
        if w.confidence >= MIN_CONFIDENCE or not WORD_OK.match(core) or not re.search(r"\w", core):
            continue
        start = w.start_index + (len(w.text) - len(w.text.lstrip()))
        end = start + len(core)
        if md[start:end] != core or any(s < end and start < e for s, e in protected):
            continue
        if spans and start < spans[-1][1]:  # overlaps the previous marked word
            continue
        spans.append((start, end, round(w.confidence, 4)))
    parts, pos = [], 0
    for n, (start, end, _) in enumerate(spans):
        parts += [md[pos:start], f"\ue000{n}\ue001", md[start:end], "\ue002"]
        pos = end
    marked = "".join(parts) + md[pos:]
    return page_markdown(page.model_copy(update={"markdown": marked})), [conf for *_, conf in spans]


def least_certain(resp, n: int = 15) -> list[dict]:
    """The n lowest-confidence words with ±50 characters of context: where a reviewer should look first."""
    rows = []
    for p in resp.pages:
        for w in words(p):
            core = w.text.strip()
            if not re.search(r"[A-Za-z0-9]", core) or not WORD_OK.match(core):
                continue
            s = w.start_index
            context = p.markdown[max(0, s - 50) : s + len(w.text) + 50].replace("\n", " ")
            rows.append({"page": p.index + 1, "word": core, "confidence": w.confidence, "context": f"…{context}…"})
    return sorted(rows, key=lambda r: r["confidence"])[:n]


def page_json(page) -> dict:
    dims, cs = page.dimensions, page.confidence_scores
    markdown, marks = marked_markdown(page)
    boxes = []
    if dims:  # block coordinates are pixels of the page image OCR 4.1 saw; the UI gets page fractions
        boxes = [{"type": b.type, "bbox": [round(b.top_left_x / dims.width, 4), round(b.top_left_y / dims.height, 4),
                                           round(b.bottom_right_x / dims.width, 4),
                                           round(b.bottom_right_y / dims.height, 4)]} for b in blocks(page)]
    return {"number": page.index + 1, "width": dims and dims.width, "height": dims and dims.height,
            "dpi": dims and dims.dpi, "avg_confidence": cs.average_page_confidence_score if cs else None,
            "min_confidence": cs.minimum_page_confidence_score if cs else None, "blocks": boxes,
            "markdown": markdown, "marks": marks}


def evidence(row: dict, fields: dict) -> dict:
    """The boxes to draw for an exception (bbox as page fractions): the rule's field first (the primary), then the
    fields that support it, on whichever pages they were found; the page to open, and the primary's caption."""
    keys = explain.evidence_keys(row)
    marks = []
    for i, key in enumerate(keys):
        f = fields.get(key)
        if f and f["bbox"] and f["page"]:
            conf = f["confidence"]
            marks.append({"page": f["page"], "bbox": json.loads(f["bbox"]), "primary": i == 0,
                          "label": key[0].replace("_", " ") + (f" · {conf:.2f}" if conf is not None else "")})
    primary = fields.get(keys[0])
    located = bool(primary and primary["bbox"] and primary["page"])
    return {"page": primary["page"] if primary and primary["page"] else 1, "marks": marks,
            "caption": {k: primary[k] for k in ("page", "confidence", "model", "extracted_at")} if located else None}


def as_text(v) -> str | None:
    return None if v is None else str(v)


# ---------- API ----------

app = FastAPI(title="Document AI on Cloudera", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Security and cache headers on every response. And every POST must carry X-DocAI: 1: a browser can't add a custom
    header to a cross-site request without a CORS preflight, and this server never approves one, so that stops
    cross-site request forgery."""
    if request.method not in ("GET", "HEAD") and request.headers.get("x-docai") != "1":
        response = JSONResponse({"error": "Missing X-DocAI header"}, 403)
    elif int(request.headers.get("content-length") or 0) > MAX_UPLOAD + 1024 * 1024:  # before it's spooled to disk
        response = JSONResponse({"error": "The PDF is larger than 25 MB."}, 413)
    else:
        response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Content-Security-Policy"] = CSP
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif request.url.path.endswith((".js", ".css")):  # a restarted Application must never serve stale modules
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, e: StarletteHTTPException):
    return JSONResponse({"error": str(e.detail)}, e.status_code, headers=e.headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, e: RequestValidationError):
    first = (e.errors() or [{}])[0]
    where = ".".join(str(x) for x in first.get("loc", ())[1:]) or "request"
    return JSONResponse({"error": f"Invalid {where}: {first.get('msg', 'bad value')}"}, 400)


@app.exception_handler(MissingKey)
async def missing_key(request: Request, e: MissingKey):
    return JSONResponse({"error": str(e)}, 400)


@app.exception_handler(Exception)
async def crashed(request: Request, e: Exception):
    # This response skips the middleware, so it brings its own headers. Starlette then re-raises, and uvicorn logs it.
    message = scrub(f"{type(e).__name__}: {e}", request.headers.get("x-mistral-key"))[:500]
    return JSONResponse({"error": message}, 500, headers={**SECURITY_HEADERS, "Cache-Control": "no-store"})


def samples() -> list[str]:
    return sorted(p.name for p in (DATA / "live").glob("*.pdf"))


class Review(BaseModel):
    field: str
    value: str


class Question(BaseModel):
    question: str
    chat_id: str | None = None


@app.get("/api/config")
def config():
    """Settings for the UI. No lake access, so it works while Impala is down."""
    rules = {code: {"label": label, "how": how, "tone": tone} for code, (label, how, tone) in explain.RULES.items()}
    return {"ocr_model": OCR_MODEL, "chat_model": CHAT_MODEL, "db": DB, "s3_root": S3_ROOT, "landing": lake.LANDING,
            "as_of": AS_OF.isoformat(), "min_confidence": MIN_CONFIDENCE, "price_per_page": PRICE_PER_PAGE,
            "reviewer": REVIEWER, "env_key": env_key(), "rules": rules, "priorities": PRIORITY,
            "steps": architecture.STEPS, "samples": samples()}


@app.get("/api/queue")
def queue():
    rows = queue_rows()
    risk = [r for r in rows if r["priority"] <= 3]
    discounts = [r for r in rows if r["rule_code"] == "DISCOUNT_WINDOW"]
    return {"kpis": {"processed": sum(d["status"] == "processed" for d in documents()),
                     "fields": query(f"SELECT COUNT(*) n FROM {DB}.extractions")[0]["n"],
                     "actions": len(rows),
                     "need_action": len({r["doc_id"] for r in risk}),
                     "at_risk": round(sum(r["amount_at_risk"] or 0 for r in risk), 2),
                     "discounts": round(sum(r["amount_at_risk"] or 0 for r in discounts), 2),
                     "review": len({r["doc_id"] for r in rows if r["rule_code"] == "LOW_CONFIDENCE"})},
            "rows": rows}


@app.get("/api/exception")
def exception_detail(doc_id: str, rule_code: str, evidence_field: str):
    """The detail pane for the first queue row with this doc, rule and evidence field."""
    check(DOC_ID.fullmatch(doc_id), "Invalid doc_id.")
    check(rule_code in explain.RULES, "Unknown rule_code.")
    check(EVIDENCE_FIELD.fullmatch(evidence_field), "Invalid evidence_field.")
    rows = queue_rows()
    wanted = (doc_id, rule_code, evidence_field)
    row = next((r for r in rows if (r["doc_id"], r["rule_code"], r["evidence_field"]) == wanted), None)
    if row is None:
        raise HTTPException(404, "That exception is not in the queue.")
    doc = document(doc_id)
    fields = {(f["field_name"], f["line_no"]): f for f in extractions(doc_id)}
    with LAKE:
        comparison = explain.comparison(row, fields)
    _, resp = ocr(doc)
    label, how, tone = explain.RULES[rule_code]
    reviewed = fields.get((evidence_field, None)) or {}
    return {
        "row": row, "label": label, "how": how, "tone": tone, "document": doc, "page_count": len(resp.pages),
        "evidence": evidence(row, fields),
        "comparison": [{"what": c["what"], "invoice": as_text(c["invoice"]), "record": as_text(c["record"]),
                        "table": f"{DB}.{c['source']}" if c["source"] else "", "ok": bool(c["ok"])}
                       for c in comparison],
        "review": ({"field": evidence_field, "value": reviewed.get("field_value") or ""}
                   if rule_code == "LOW_CONFIDENCE" else None),
        "others": [explain.RULES[r["rule_code"]][0] for r in rows if r["doc_id"] == doc_id and r is not row],
        "rule_sql": explain.rule_sql(rule_code),
        "audit": {"s3_uri": doc["s3_uri"], "ingested_at": doc["ingested_at"], "sha256": doc["sha256"],
                  "ocr_model": OCR_MODEL, "processed_at": doc["processed_at"], "ocr_uri": pipeline.ocr_uri(doc_id),
                  "extractions_table": f"{DB}.extractions", "exceptions_table": f"{DB}.exceptions",
                  "created_at": row["created_at"]},
    }


@app.get("/api/docs")
def docs():
    return {"docs": documents()}


@app.get("/api/docs/{doc_id}")
def doc_detail(doc_id: str):
    """The "One document" view: what OCR 4.1 returned for a processed document, and the fields extracted from it."""
    doc = document(doc_id, processed=True)
    raw, resp = ocr(doc)
    pages = [page_json(p) for p in resp.pages]
    scores = [p.confidence_scores.average_page_confidence_score for p in resp.pages if p.confidence_scores]
    keep = ("field_name", "line_no", "field_value", "confidence", "page", "model")
    return {
        "document": doc, "page_count": len(pages), "seconds": raw.get("_seconds"),
        "avg_confidence": sum(scores) / len(scores) if scores else None,
        "block_count": sum(len(p["blocks"]) for p in pages), "cost": len(pages) * PRICE_PER_PAGE,
        "pages": pages,
        "fields": [{k: f[k] for k in keep} for f in extractions(doc_id)],
        "least_certain": least_certain(resp),
        "schema": ANNOTATION_FORMAT, "prompt": INVOICE_PROMPT,
        "chat": {"model": CHAT_MODEL, "system_prompt": SYSTEM_PROMPT.format(documents=doc_id),
                 "tools": tool_spec([doc_id]), "suggestions": SUGGESTIONS},
    }


@app.get("/api/docs/{doc_id}/ocr.json")
def doc_ocr(doc_id: str):
    raw, _ = ocr(document(doc_id, processed=True))
    return JSONResponse(raw)


@app.get("/api/docs/{doc_id}/pages/{n}.png")
def doc_page(doc_id: str, n: int, dpi: int = 160):
    """Page n as PNG. The front end adds v=<sha256 prefix> to the URL, so the browser may keep it."""
    doc = document(doc_id)
    png = page_png(doc["sha256"], doc["s3_uri"], n, max(72, min(220, dpi)))
    if png is None:
        raise HTTPException(404, f"{doc_id} has no page {n}.")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/docs/{doc_id}/confirm", status_code=202)
def confirm(doc_id: str, body: Review):
    """Human review (a job): store the confirmed value and rerun the rules."""
    document(doc_id)
    check(body.field in HEADER_FIELDS, "Unknown field.")
    check(len(body.value) <= 200, "The value is too long (200 characters at most).")
    try:
        value = pipeline.check_value(body.field, body.value)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    def run(step):
        with step("Save and re-check", "Saving the reviewed value and rerunning the rules…"), LAKE:
            pipeline.confirm_field(doc_id, body.field, value, REVIEWER)
        return {"doc_id": doc_id, "field": body.field, "value": value}

    return {"job_id": start_job("confirm", run)}


@app.post("/api/ingest", status_code=202)
def ingest(sample: str | None = Form(None), file: UploadFile | None = File(None), key: str | None = Depends(tab_key)):
    """Live path (a job): land a held-back sample or an uploaded PDF in S3, read it with OCR 4.1, rerun the rules."""
    check((sample is None) != (file is None), "Send either a sample name or a PDF file.")
    if sample is not None:
        check(sample in samples(), "Unknown sample.")
        data, name = (DATA / "live" / sample).read_bytes(), sample
    else:
        data = file.file.read(MAX_UPLOAD + 1)
        check(len(data) <= MAX_UPLOAD, "The PDF is larger than 25 MB.")
        check(data.startswith(b"%PDF-"), "That file isn't a PDF.")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(file.filename or "").name)[:100]
        name = name if name.lower().endswith(".pdf") else f"{name}.pdf"

    def run(step):
        with mistral(key) as client, LAKE:  # without a key the job fails here, before anything lands
            with step("Land in S3", f"Landing the PDF in `{lake.LANDING}` (access governed by SDX)…") as land:
                doc_id = pipeline.land(data, name)
            with step("OCR 4.1 + evidence", f"Mistral OCR 4.1 is reading `{doc_id}` with the invoice schema…") as read:
                pipeline.process(pipeline.documents(f"doc_id = {lake.lit(doc_id)}"), client=client)
            with step("Rules in Impala",
                      "Joining every field against vendors, POs, receipts and payments in Iceberg…") as rules:
                pipeline.run_rules()
            found = fetch(f"SELECT rule_code, detail, amount_at_risk, action, evidence_field FROM {DB}.exceptions "
                          f"WHERE doc_id = {lake.lit(doc_id)} ORDER BY priority, amount_at_risk DESC")
        labels = {code: {"label": label, "tone": tone} for code, (label, _, tone) in explain.RULES.items()}
        return {"doc_id": doc_id, "times": {s["name"]: s["seconds"] for s in (land, read, rules)},
                "exceptions": [{**r, **labels[r["rule_code"]]} for r in found]}

    return {"job_id": start_job("ingest", run, key)}


@app.post("/api/reset", status_code=202)
def reset():
    def run(step):
        with step("Remove live uploads", "Cleaning up…"), LAKE:
            return {"removed": pipeline.reset_uploads()}

    return {"job_id": start_job("reset", run)}


@app.post("/api/evaluate", status_code=202)
def score():
    def run(step):
        with step("Score", "Scoring…"), LAKE:
            r = evaluate()
        misses = [{"doc_id": d, "field": f, "expected": as_text(e), "extracted": as_text(x), "caught": d in r["review"]}
                  for d, f, e, x in r["misses"]]
        return {"docs": r["docs"], "fields": r["fields"], "field_accuracy": r["field_accuracy"], "review": r["review"],
                "misses": misses, "queue": r["queue"], "planted_ok": sum(q["status"] == "ok" for q in r["queue"]),
                "planted_total": len(r["queue"])}

    return {"job_id": start_job("evaluate", run)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    check(HEX_ID.fullmatch(job_id), "Invalid job id.")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job.")
        return {**job, "steps": [dict(s) for s in job["steps"]]}


CHATS: OrderedDict[str, dict] = OrderedDict()  # chat_id -> {"doc_id", "messages"}, the 100 most recent
CHATS_LOCK = threading.Lock()


@app.post("/api/docs/{doc_id}/chat")
def chat(doc_id: str, body: Question, key: str | None = Depends(tab_key)):
    """Ask the invoice: the chat model with a read_document tool over the document's OCR text."""
    question = body.question.strip()
    check(1 <= len(question) <= 2000, "Ask a question of 1 to 2000 characters.")
    check(body.chat_id is None or HEX_ID.fullmatch(body.chat_id), "Invalid chat_id.")
    _, resp = ocr(document(doc_id, processed=True))
    with CHATS_LOCK:
        chat_id = body.chat_id
        if chat_id not in CHATS or CHATS[chat_id]["doc_id"] != doc_id:  # new, forgotten, or another document's
            chat_id = secrets.token_hex(8)
            system = {"role": "system", "content": SYSTEM_PROMPT.format(documents=doc_id)}
            CHATS[chat_id] = {"doc_id": doc_id, "messages": [system]}
            while len(CHATS) > 100:
                CHATS.popitem(last=False)
        CHATS.move_to_end(chat_id)
        conv = CHATS[chat_id]
    # ask() adds the turns to the list it gets: give it a copy, kept only once the model has answered, so a failed
    # turn is never sent again.
    messages = [*conv["messages"], {"role": "user", "content": question}]
    with mistral(key) as client:  # a missing key is a 400 before anything is sent
        try:
            answer, calls = ask(client, messages, {doc_id: doc_for_chat(resp, page_markdown)})
        except Exception as e:
            log_exception(key)
            return JSONResponse({"error": mistral_error(e, key)}, 502)
    conv["messages"] = messages
    return {"chat_id": chat_id, "answer": answer, "calls": calls}


@app.post("/api/mistral/check")
def check_key(key: str | None = Depends(tab_key)):
    """Whether a key works and can use OCR 4.1 (the sidebar's "Use in this tab")."""
    with mistral(key) as client:
        try:
            ids = {m.id for m in client.models.list().data or []}
        except Exception as e:
            log_exception(key)
            return JSONResponse({"error": mistral_error(e, key)}, 502)
    return {"ok": True, "ocr_model": OCR_MODEL, "ocr_available": OCR_MODEL in ids}


@app.post("/api/refresh")
def refresh():
    clear_cache()
    return {"ok": True}


@app.get("/api/lake")
def lake_tables():
    counts = query(" UNION ALL ".join(f"SELECT '{t}' AS t, COUNT(*) AS n FROM {DB}.{t}" for t in TABLES))
    n = {r["t"]: r["n"] for r in counts}
    return {"s3_root": S3_ROOT, "db": DB, "rules_sql": pipeline.RULES.read_text(),
            "tables": [{"table": f"{DB}.{t}", "kind": "Enterprise data" if i < 5 else "Document AI", "rows": n.get(t),
                        "comment": comment} for i, (t, (comment, _)) in enumerate(TABLES.items())]}


@cache
def architecture_svg() -> str:
    return architecture.render_svg()


@app.get("/api/architecture.svg")
def architecture_view():
    return Response(architecture_svg(), media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})


# ---------- front end ----------


@app.get("/brand.css")
def brand_css():
    return Response(brand.css(), media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    if not (STATIC / "index.html").exists():
        raise HTTPException(404, "static/index.html is missing.")
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
