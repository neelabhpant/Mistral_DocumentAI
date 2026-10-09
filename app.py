"""Document AI on Cloudera: accounts payable invoices read by Mistral OCR 4, checked against governed
enterprise data in Iceberg, and turned into a prioritized action queue with click-to-evidence.

Run: streamlit run app.py   (on Cloudera AI: launch_app.py)
"""

import json
import os
import re
import time
from pathlib import Path

import altair as alt
import pandas as pd
import pymupdf
import streamlit as st
from mistralai.client.models import OCRResponse
from PIL import Image, ImageDraw, ImageFont

from ap import architecture, brand, explain, lake, pipeline
from ap.assistant import CHAT_MODEL, SUGGESTIONS, SYSTEM_PROMPT, ask, doc_for_chat, tool_spec
from ap.config import AS_OF, DATA, DB, MIN_CONFIDENCE, S3_ROOT
from ap.schema import ANNOTATION_FORMAT, INVOICE_PROMPT
from ocr_extract import page_markdown
from test_mistral_api import OCR_MODEL, get_client

PRICE_PER_PAGE = 4 / 1000  # USD, standard (non-batch) API pricing
REVIEWER = os.getenv("HADOOP_USER_NAME") or os.getenv("USER") or "reviewer"
PRIORITY = {1: "1 · Critical", 2: "2 · High", 3: "3 · Medium", 4: "Opportunity", 5: "Review"}
TONE, GRAY, BLOCK_COLORS = brand.TONE, brand.GRAY, brand.BLOCK_COLORS
MD_PLACEHOLDER = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
WORD_OK = re.compile(r"^[\w$%.,;:'\"()/&-]+$")

st.set_page_config(page_title="Document AI · Cloudera + Mistral AI", page_icon=brand.MISTRAL_ICON, layout="wide")
st.markdown(brand.CSS, unsafe_allow_html=True)


# ---------- data access (Iceberg via Impala, PDFs and OCR output from S3) ----------


@st.cache_data(ttl=600, show_spinner=False)
def q(sql: str) -> pd.DataFrame:
    return pd.DataFrame(lake.query(sql))


def refresh() -> None:
    q.clear()


@st.cache_resource(show_spinner=False)
def client():
    try:
        return get_client()
    except SystemExit as e:
        st.error(f"Mistral API key problem: {e}")
        st.stop()


@st.cache_data(show_spinner=False)
def pdf(sha256: str, s3_uri: str) -> bytes:
    return pipeline.pdf_bytes({"sha256": sha256, "s3_uri": s3_uri})


@st.cache_data(show_spinner=False)
def ocr(doc_id: str, processed_at: str) -> OCRResponse:
    raw = {k: v for k, v in pipeline.ocr_json(doc_id).items() if not k.startswith("_")}
    return OCRResponse.model_validate(raw)


def documents() -> pd.DataFrame:
    return q(f"""SELECT d.doc_id, d.file_name, d.s3_uri, d.sha256, d.source, d.status,
                        CAST(d.ingested_at AS STRING) ingested_at, CAST(d.processed_at AS STRING) processed_at,
                        h.vendor_name, h.invoice_number, h.total
                 FROM {DB}.documents d LEFT JOIN {DB}.invoice_header h ON h.doc_id = d.doc_id ORDER BY d.doc_id""")


def extractions(doc_id: str) -> pd.DataFrame:
    return q(f"SELECT * FROM {DB}.extractions WHERE doc_id = {lake.lit(doc_id)} ORDER BY line_no NULLS FIRST, field_name")


@st.cache_data(show_spinner=False)
def render_page(sha: str, _pdf: bytes, index: int, dpi: int) -> Image.Image:
    with pymupdf.open(stream=_pdf, filetype="pdf") as doc:
        pix = doc[index].get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ---------- drawing ----------


def blocks(page) -> list:
    return [b for b in (page.blocks or []) if getattr(b, "top_left_x", None) is not None]


def words(page) -> list:
    cs = page.confidence_scores
    return (cs.word_confidence_scores or []) if cs else []


def draw_blocks(img: Image.Image, page) -> Image.Image:
    dims = page.dimensions
    scale = img.width / dims.width if dims else 1.0
    out = img.copy()
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=max(11, int(11 * scale)))
    for b in blocks(page):
        color = BLOCK_COLORS.get(b.type, GRAY)
        rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
        box = [b.top_left_x * scale, b.top_left_y * scale, b.bottom_right_x * scale, b.bottom_right_y * scale]
        draw.rectangle(box, fill=rgb + (28,), outline=rgb + (255,), width=2)
        tw = draw.textlength(b.type, font=font)
        label = [box[0], box[1] - font.size - 4, box[0] + tw + 8, box[1]]
        if label[1] < 0:
            label = [box[0], box[1], box[0] + tw + 8, box[1] + font.size + 4]
        draw.rectangle(label, fill=rgb + (235,))
        draw.text((label[0] + 4, label[1] + 1), b.type, fill=(255, 255, 255, 255), font=font)
    return Image.alpha_composite(out.convert("RGBA"), overlay).convert("RGB")


def draw_evidence(img: Image.Image, marks: list[tuple[list[float], str, str, bool]]) -> Image.Image:
    """marks: (bbox as page fractions, label, hex color, primary)."""
    out = img.convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=max(12, img.width // 70))
    for bbox, label, color, primary in sorted(marks, key=lambda m: m[3]):
        rgb = tuple(int(color[i : i + 2], 16) for i in (1, 3, 5))
        pad = 4 if primary else 2
        box = [bbox[0] * img.width - pad, bbox[1] * img.height - pad, bbox[2] * img.width + pad, bbox[3] * img.height + pad]
        draw.rectangle(box, fill=rgb + ((40 if primary else 18),), outline=rgb + (255,), width=4 if primary else 2)
        if primary:
            tw = draw.textlength(label, font=font)
            tag = [box[0], box[1] - font.size - 8, box[0] + tw + 12, box[1]]
            draw.rectangle(tag, fill=rgb + (240,))
            draw.text((tag[0] + 6, tag[1] + 3), label, fill=(255, 255, 255, 255), font=font)
    return Image.alpha_composite(out, overlay).convert("RGB")


def legend(types) -> None:
    chips = "".join(
        f'<span class="chip"><span class="sw" style="background:{BLOCK_COLORS.get(t, GRAY)}"></span>{t}</span>'
        for t in sorted(types)
    )
    st.markdown(chips, unsafe_allow_html=True)


def highlighted_markdown(page, threshold: float) -> str:
    """Page markdown with words below `threshold` confidence wrapped in <mark>, then tables/images inlined."""
    md = page.markdown
    protected = [m.span() for m in MD_PLACEHOLDER.finditer(md)]
    spans = []
    for w in words(page):
        core = w.text.strip()
        if w.confidence >= threshold or not WORD_OK.match(core) or not re.search(r"\w", core):
            continue
        start = w.start_index + (len(w.text) - len(w.text.lstrip()))
        end = start + len(core)
        if md[start:end] != core or any(s < end and start < e for s, e in protected):
            continue
        spans.append((start, end, w.confidence))
    for start, end, conf in sorted(spans, reverse=True):
        md = f'{md[:start]}<mark class="lowconf" title="confidence {conf:.2f}">{md[start:end]}</mark>{md[end:]}'
    return page_markdown(page.model_copy(update={"markdown": md}), inline_images=True)


def show_md(md: str) -> None:
    # Escape $ so dollar amounts aren't rendered as LaTeX.
    st.markdown(md.replace("$", r"\$"), unsafe_allow_html=True)


def low_conf_words(resp, n=15) -> pd.DataFrame:
    rows = []
    for p in resp.pages:
        for w in words(p):
            core = w.text.strip()
            if not re.search(r"[A-Za-z0-9]", core) or not WORD_OK.match(core):
                continue
            s = w.start_index
            ctx = p.markdown[max(0, s - 50) : s + len(w.text) + 50].replace("\n", " ")
            rows.append({"Page": p.index + 1, "Word": core, "Confidence": w.confidence, "Context": f"…{ctx}…"})
    rows.sort(key=lambda r: r["Confidence"])
    return pd.DataFrame(rows[:n])


def has(v) -> bool:
    """True for a real value (pandas turns missing numbers into NaN, which is truthy)."""
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and v != ""


def money(x) -> str:
    return f"${float(x):,.2f}" if has(x) else ""


# ---------- sidebar ----------

VIEWS = ["Action queue", "Ingest an invoice", "Under the hood"]
with st.sidebar:
    st.markdown(brand.sidebar(), unsafe_allow_html=True)
    view = st.radio("View", VIEWS, key="view", label_visibility="collapsed")
    st.divider()
    st.caption(f"OCR: `{OCR_MODEL}` (schema mode)  \nData: Iceberg `{DB}` via Impala  \nFiles: `{S3_ROOT}`  \n"
               f"Business date: {AS_OF:%b %d, %Y}")
    if st.button("Refresh data", width="stretch"):
        refresh()
        st.rerun()
    st.markdown("<div class='legal'>Cloudera and Mistral AI logos are trademarks of their owners. "
                "Vendors and invoices are fictitious.</div>", unsafe_allow_html=True)


# ---------- 1. Action queue ----------


def evidence_image(doc: pd.Series, exc: dict, fields: dict, tone: str):
    keys = explain.evidence_keys(exc)
    primary = fields.get(keys[0])
    resp = ocr(doc["doc_id"], doc["processed_at"])
    n_pages = len(resp.pages)
    page_no = int(primary["page"]) if primary is not None and has(primary.get("page")) else 1
    if n_pages > 1:
        page_no = st.segmented_control("Page", list(range(1, n_pages + 1)), default=page_no, key=f"pg_{doc['doc_id']}_{exc['rule_code']}") or page_no
    data = pdf(doc["sha256"], doc["s3_uri"])
    img = render_page(doc["sha256"], data, page_no - 1, 130)
    marks = []
    for i, key in enumerate(keys):
        f = fields.get(key)
        if f is None or not has(f.get("bbox")) or not has(f.get("page")) or int(f["page"]) != page_no:
            continue
        conf = f.get("confidence")
        label = f"{key[0].replace('_', ' ')}" + (f" · {conf:.2f}" if has(conf) else "")
        marks.append((json.loads(f["bbox"]), label, TONE[tone] if i == 0 else brand.EVIDENCE, i == 0))
    st.image(draw_evidence(img, marks), width="stretch")
    if primary is not None and has(primary.get("bbox")):
        conf = primary.get("confidence")
        st.caption(f"Evidence: page {int(primary['page'])}"
                   + (f" · OCR confidence {conf:.2f}" if has(conf) else "")
                   + f" · {primary['model']} · {primary['extracted_at']}  \nSource file: `{doc['s3_uri']}`")


def action_queue() -> None:
    docs = documents()
    exc = q(f"""SELECT e.*, v.name vendor_name, d.source, d.file_name
                FROM {DB}.exceptions e
                LEFT JOIN {DB}.vendors v ON v.vendor_id = e.vendor_id
                LEFT JOIN {DB}.documents d ON d.doc_id = e.doc_id
                ORDER BY e.priority, e.amount_at_risk DESC""")
    n_fields = int(q(f"SELECT COUNT(*) n FROM {DB}.extractions")["n"].iloc[0])
    processed = int((docs["status"] == "processed").sum()) if not docs.empty else 0

    st.markdown(brand.header("Invoices that need action",
                             "Supplier invoices read by Mistral OCR 4, checked against governed data in Cloudera."),
                unsafe_allow_html=True)
    risk = exc[exc["priority"] <= 3] if not exc.empty else exc
    savings = exc[exc["rule_code"] == "DISCOUNT_WINDOW"] if not exc.empty else exc
    review = exc[exc["rule_code"] == "LOW_CONFIDENCE"] if not exc.empty else exc
    s1, s2, s3 = st.columns(3)
    s1.markdown(brand.step(1, "Read", "Mistral OCR 4", f"Extracted {n_fields:,} fields from {processed} invoice PDFs "
                           "in S3, inside this environment."), unsafe_allow_html=True)
    s2.markdown(brand.step(2, "Join", "Cloudera", "Every field checked against the vendor master, POs, goods receipts "
                           "and payments in Iceberg (Impala)."), unsafe_allow_html=True)
    s3.markdown(brand.step(3, "Act", "Together", f"{len(exc)} actions, each linked to the exact spot on the page and "
                           "to the record that triggered it."), unsafe_allow_html=True)

    def open_architecture():
        st.session_state["view"] = "Under the hood"
        st.session_state["uth_tab"] = "Architecture"

    st.button("See the reference architecture →", type="tertiary", on_click=open_architecture)

    k = st.columns(5)
    k[0].metric("Invoices processed", processed)
    k[1].metric("Need action", risk["doc_id"].nunique() if not risk.empty else 0)
    at_risk = risk["amount_at_risk"].astype(float).sum() if not risk.empty else 0
    to_save = savings["amount_at_risk"].astype(float).sum() if not savings.empty else 0
    k[2].metric("Dollars at risk", f"${at_risk:,.0f}", help=money(at_risk))
    k[3].metric("Discounts", f"${to_save:,.0f}", help=money(to_save))
    k[4].metric("Human review", review["doc_id"].nunique() if not review.empty else 0,
                help=f"A key field read with OCR confidence below {MIN_CONFIDENCE:.2f}")

    if exc.empty:
        st.info("No exceptions. Run the pipeline (`python -m ap.pipeline --pending`) or ingest an invoice.")
        return

    table = pd.DataFrame({
        "Priority": exc["priority"].map(PRIORITY),
        "Issue": exc["rule_code"].map(lambda r: explain.RULES.get(r, (r,))[0]),
        "Vendor": exc["vendor_name"].fillna("(unknown)"),
        "Invoice": exc["invoice_number"],
        "Amount": exc["amount_at_risk"].astype(float),
        "Recommended action": exc["action"],
        "Doc": exc["doc_id"] + exc["source"].map(lambda s: " (live)" if s == "upload" else ""),
    })
    # The selection resets whenever the queue's contents change (key includes them), so a row index never
    # points at a different invoice after the rules rerun. Without a selection, the last focused invoice stays open.
    version = abs(hash(tuple(exc["doc_id"] + exc["rule_code"])))
    sel = st.dataframe(
        table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key=f"queue_{version}",
        column_config={"Amount": st.column_config.NumberColumn(format="$%,.2f"),
                       "Recommended action": st.column_config.TextColumn(width="large")},
    )
    rows = sel.selection.rows if sel and sel.selection else []
    focus = st.session_state.get("focus")  # (doc_id, rule_code)
    if rows:
        i = rows[0]
    elif focus and ((exc["doc_id"] == focus[0]) & (exc["rule_code"] == focus[1])).any():
        i = int(((exc["doc_id"] == focus[0]) & (exc["rule_code"] == focus[1])).to_numpy().argmax())
    elif focus and (exc["doc_id"] == focus[0]).any():
        i = int((exc["doc_id"] == focus[0]).to_numpy().argmax())
    else:
        i = 0
    st.session_state["focus"] = (exc["doc_id"].iloc[i], exc["rule_code"].iloc[i])
    e = exc.iloc[i].to_dict()
    label, how, tone = explain.RULES.get(e["rule_code"], (e["rule_code"], "", "risk"))
    doc = docs[docs["doc_id"] == e["doc_id"]].iloc[0]
    fx = extractions(e["doc_id"])
    fields = {(r["field_name"], None if pd.isna(r["line_no"]) else int(r["line_no"])): r for r in fx.to_dict("records")}

    st.divider()
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        evidence_image(doc, e, fields, tone)
    with right:
        st.markdown(f"{brand.tag(label, tone)} "
                    f"&nbsp; **{e['vendor_name'] or '(unknown vendor)'}** · invoice {e['invoice_number']}",
                    unsafe_allow_html=True)
        amount = e["amount_at_risk"]
        if has(amount):
            st.markdown(f"### {money(amount)} {'savings' if tone == 'opportunity' else 'at risk'}")
        st.write(e["detail"])
        {"risk": st.error, "review": st.warning, "opportunity": st.success}[tone](f"**Action:** {e['action']}")

        st.markdown(f"**The document vs. the record** ({how})")
        comp = explain.comparison(e, fields)
        if comp:
            st.dataframe(
                pd.DataFrame({
                    "": [c["what"] for c in comp],
                    "On the invoice (OCR 4)": [c["invoice"] for c in comp],
                    "In Cloudera": [c["record"] for c in comp],
                    "Table": [f"{DB}.{c['source']}" if c["source"] else "" for c in comp],
                    "Match": ["yes" if c["ok"] else "NO" for c in comp],
                }),
                hide_index=True, width="stretch",
            )
        if e["rule_code"] == "LOW_CONFIDENCE":
            f = fields.get((e["evidence_field"], None)) or {}
            with st.form(f"review_{e['doc_id']}_{e['evidence_field']}"):
                st.markdown(f"**Review:** compare with the highlighted spot on the page and confirm the "
                            f"{e['evidence_field'].replace('_', ' ')}.")
                value = st.text_input("Confirmed value", f.get("field_value") or "")
                if st.form_submit_button("Confirm and re-check", type="primary"):
                    with st.spinner("Saving the reviewed value and rerunning the rules…"):
                        pipeline.confirm_field(e["doc_id"], e["evidence_field"], value.strip(), REVIEWER)
                    refresh()
                    st.session_state["focus"] = (e["doc_id"], None)
                    st.rerun()
        others = exc[(exc["doc_id"] == e["doc_id"]) & (exc.index != exc.index[i])]
        if not others.empty:
            st.caption("Also on this invoice: " + ", ".join(explain.RULES.get(r, (r,))[0] for r in others["rule_code"]))
        with st.expander("How Cloudera found this (SQL over Iceberg)"):
            st.code(explain.rule_sql(e["rule_code"]), language="sql")
        with st.expander("Audit trail"):
            st.markdown(
                f"- File landed: `{doc['s3_uri']}` at {doc['ingested_at']} (sha256 `{doc['sha256'][:16]}…`)\n"
                f"- Read by `{OCR_MODEL}` at {doc['processed_at']}; raw output `{pipeline.ocr_uri(doc['doc_id'])}`\n"
                f"- Fields in `{DB}.extractions` (with page, bounding box and confidence)\n"
                f"- Exception written to `{DB}.exceptions` at {e['created_at']}"
            )


# ---------- 2. Ingest ----------


def ingest_view() -> None:
    st.markdown(brand.header("Ingest an invoice",
                             "A new invoice lands in governed S3, Mistral OCR 4 reads it inside the environment, and the "
                             "same rules check it against enterprise data. The same code runs in the batch Job."),
                unsafe_allow_html=True)
    samples = sorted((DATA / "live").glob("*.pdf"))
    src = st.radio("Source", ["Held-back sample invoices", "Upload a PDF"], horizontal=True)
    data = name = None
    if src.startswith("Held"):
        if samples:
            pick = st.selectbox("Invoice", samples, format_func=lambda p: p.name)
            data, name = pick.read_bytes(), pick.name
        else:
            st.info("No held-back samples. Run `python -m ap.generate` first.")
    else:
        f = st.file_uploader("Invoice PDF", type="pdf")
        if f:
            data, name = f.getvalue(), f.name
    if data and st.button("Ingest and check", type="primary"):
        times = {}
        with st.status("Processing…", expanded=True) as status:
            t = time.perf_counter()
            st.write(f"Landing the PDF in `{pipeline.lake.LANDING}` (access governed by SDX)…")
            doc_id = pipeline.land(data, name)
            times["Land in S3"] = time.perf_counter() - t
            t = time.perf_counter()
            st.write(f"Mistral OCR 4 is reading `{doc_id}` with the invoice schema…")
            pipeline.process(pipeline.documents(f"doc_id = {lake.lit(doc_id)}"))
            times["OCR 4 + evidence"] = time.perf_counter() - t
            t = time.perf_counter()
            st.write("Joining every field against vendors, POs, receipts and payments in Iceberg…")
            pipeline.run_rules()
            times["Rules in Impala"] = time.perf_counter() - t
            status.update(label=f"Done: {doc_id}", state="complete")
        refresh()
        st.session_state["ingested"] = (doc_id, times)
    if "ingested" in st.session_state:
        doc_id, times = st.session_state["ingested"]
        st.caption(" · ".join(f"{k} {v:.1f}s" for k, v in times.items()))
        found = q(f"SELECT rule_code, detail, amount_at_risk, action FROM {DB}.exceptions WHERE doc_id = {lake.lit(doc_id)}")
        if found.empty:
            st.success(f"`{doc_id}` passed every check: ready to pay on its due date.")
        else:
            for r in found.to_dict("records"):
                label, _, tone = explain.RULES.get(r["rule_code"], (r["rule_code"], "", "risk"))
                {"risk": st.error, "review": st.warning, "opportunity": st.success}[tone](
                    f"**{label}** {money(r['amount_at_risk'])}: {r['detail']}  \n{r['action']}")

        def open_in_queue():
            st.session_state["view"] = "Action queue"
            st.session_state["focus"] = (doc_id, None)

        st.button("Open in the action queue", on_click=open_in_queue, disabled=found.empty)


# ---------- 3. Under the hood ----------


@st.cache_data
def architecture_svg() -> str:
    return architecture.render_svg()


def under_the_hood() -> None:
    st.markdown(brand.header("Under the hood", "What OCR 4 returned, where the data lives, and how well it scores."),
                unsafe_allow_html=True)
    t_arch, t_doc, t_lake, t_eval = st.tabs(["Architecture", "One document", "Data lake & rules", "Accuracy"], key="uth_tab")
    with t_arch:
        st.image(architecture_svg(), width="stretch")  # an image gets Streamlit's full-screen button on hover
        st.download_button("Download SVG", architecture_svg(), "document-ai-reference-architecture.svg", "image/svg+xml",
                           type="tertiary")
        st.markdown("\n".join(f"{i}. {step}" for i, step in enumerate(architecture.STEPS, 1)))
        st.caption("Product names and logos are trademarks of their respective owners.")
    with t_doc:
        docs = documents()
        docs = docs[docs["status"] == "processed"]
        if docs.empty:
            st.info("No processed documents yet.")
        else:
            document_detail(docs)
    with t_lake:
        lake_view()
    with t_eval:
        st.write("The generator knows the true value of every field and every planted issue, so the pipeline can be "
                 "scored end to end.")
        if st.button("Score against ground truth"):
            from ap.evaluate import evaluate
            with st.spinner("Scoring…"):
                st.session_state["eval"] = evaluate()
        r = st.session_state.get("eval")
        if r:
            ok = sum(x["status"] == "ok" for x in r["queue"])
            e1, e2, e3 = st.columns(3)
            e1.metric("Field accuracy", f"{r['field_accuracy']:.1%}", help=f"{r['fields']:,} fields in {r['docs']} documents")
            e2.metric("Planted issues found", f"{ok} / {len(r['queue'])}")
            e3.metric("Routed to review", len(r["review"]))
            if r["misses"]:
                st.dataframe(pd.DataFrame(r["misses"], columns=["Doc", "Field", "Expected", "Extracted"]).assign(
                    **{"Caught by review": lambda d: d["Doc"].isin(r["review"])}), hide_index=True, width="stretch")


def document_detail(docs: pd.DataFrame) -> None:
    labels = {r["doc_id"]: f"{r['doc_id']} · {r['vendor_name']} · {r['invoice_number']}" for r in docs.to_dict("records")}
    doc_id = st.selectbox("Document", list(labels), format_func=labels.get)
    doc = docs[docs["doc_id"] == doc_id].iloc[0]
    resp = ocr(doc_id, doc["processed_at"])
    pages = resp.pages
    data = pdf(doc["sha256"], doc["s3_uri"])
    avg_conf = sum(p.confidence_scores.average_page_confidence_score for p in pages) / len(pages)
    raw = pipeline.ocr_json(doc_id)
    k = st.columns(5)
    k[0].metric("Pages", len(pages))
    k[1].metric("OCR + extraction", f"{raw.get('_seconds', 0):.1f}s")
    k[2].metric("Avg confidence", f"{avg_conf:.1%}")
    k[3].metric("Layout blocks", sum(len(blocks(p)) for p in pages))
    k[4].metric("Est. cost", f"${len(pages) * PRICE_PER_PAGE:.3f}")
    page_no = st.segmented_control("Page", list(range(1, len(pages) + 1)), default=1, key=f"utp_{doc_id}") if len(pages) > 1 else 1
    page = pages[(page_no or 1) - 1]

    t_side, t_fields, t_ask, t_conf, t_json = st.tabs(["Page & layout", "Extracted fields", "Ask the invoice", "Confidence", "Raw JSON"])
    with t_side:
        left, right = st.columns(2, gap="large")
        with left:
            dpi = page.dimensions.dpi if page.dimensions else 100
            st.image(draw_blocks(render_page(doc["sha256"], data, page.index, dpi * 2), page), width="stretch")
            legend({b.type for b in blocks(page)})
        with right, st.container(height=900, border=True):
            show_md(highlighted_markdown(page, MIN_CONFIDENCE))
    with t_fields:
        fx = extractions(doc_id)
        st.markdown("**Context sent to OCR 4:** a JSON schema (`document_annotation_format`, each field description is an "
                    "instruction) and a prompt (`document_annotation_prompt`). Page, box and confidence come from OCR 4's "
                    "layout blocks and word scores.")
        st.dataframe(
            fx[["field_name", "line_no", "field_value", "confidence", "page", "model"]].rename(columns={
                "field_name": "Field", "line_no": "Line", "field_value": "Value", "confidence": "Confidence",
                "page": "Page", "model": "Extracted by"}),
            hide_index=True, width="stretch", height=520,
            column_config={"Confidence": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1)},
        )
        c1, c2 = st.columns(2)
        with c1.expander("Schema (document_annotation_format)"):
            st.json(ANNOTATION_FORMAT, expanded=False)
        with c2.expander("Prompt (document_annotation_prompt)"):
            st.code(INVOICE_PROMPT, language=None, wrap_lines=True)
    with t_ask:
        chat(doc_id, resp)
    with t_conf:
        conf_df = pd.DataFrame([
            {"Page": p.index + 1, "Measure": label, "Confidence": getattr(p.confidence_scores, attr)}
            for p in pages for label, attr in (("Average", "average_page_confidence_score"), ("Lowest word", "minimum_page_confidence_score"))
        ])
        base = alt.Chart(conf_df).encode(
            x=alt.X("Page:O", axis=alt.Axis(labelAngle=0, grid=False)),
            y=alt.Y("Confidence:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", gridColor="#F1EBDD")),
            color=alt.Color("Measure:N", scale=alt.Scale(domain=["Average", "Lowest word"], range=[brand.EVIDENCE, brand.MISTRAL_ORANGE]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Page", "Measure", alt.Tooltip("Confidence:Q", format=".1%")],
        )
        st.altair_chart((base.mark_line(strokeWidth=2) + base.mark_point(size=80, filled=True)).properties(height=240), width="stretch")
        st.markdown("**Least certain words**: where a reviewer should look first")
        st.dataframe(low_conf_words(resp), hide_index=True, width="stretch",
                     column_config={"Confidence": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1)})
    with t_json:
        st.json(raw, expanded=False)


def chat(doc_id: str, resp) -> None:
    docs = {doc_id: doc_for_chat(resp, page_markdown)}
    system = SYSTEM_PROMPT.format(documents=", ".join(docs))
    if st.session_state.get("chat_id") != doc_id:
        st.session_state.update(chat_id=doc_id, chat_messages=[{"role": "system", "content": system}], chat_log=[])
    st.markdown(f"`{CHAT_MODEL}` with a system prompt and a `read_document` tool that returns OCR 4's text for the "
                "invoice (Mistral's OCR tool-usage cookbook pattern).")
    with st.expander("What the assistant was told (system prompt + tool)"):
        st.code(system, language="markdown", wrap_lines=True)
        st.json(tool_spec(list(docs)), expanded=False)
    pending = None
    for col, s in zip(st.columns(len(SUGGESTIONS)), SUGGESTIONS):
        if col.button(s, width="stretch", key=f"sugg_{s}"):
            pending = s
    with st.container(height=420, border=True):
        if not st.session_state["chat_log"]:
            st.caption("Ask a question about this invoice, or pick a suggestion above.")
        for entry in st.session_state["chat_log"]:
            with st.chat_message(entry["role"]):
                show_md(entry["content"])
                if entry.get("calls"):
                    with st.expander(f"Tool calls ({len(entry['calls'])})"):
                        for c in entry["calls"]:
                            st.code(f'{c["tool"]}(name="{c["name"]}")  → {c["chars"]:,} characters of OCR text', language=None)
    question = st.chat_input("Ask about this invoice") or pending
    if question:
        st.session_state["chat_log"].append({"role": "user", "content": question})
        st.session_state["chat_messages"].append({"role": "user", "content": question})
        try:
            with st.spinner(f"{CHAT_MODEL} is reading…"):
                answer, calls = ask(client(), st.session_state["chat_messages"], docs)
            st.session_state["chat_log"].append({"role": "assistant", "content": answer, "calls": calls})
        except Exception as e:
            st.session_state["chat_log"].append({"role": "assistant", "content": f"Error: {e}"})
        st.rerun()


def lake_view() -> None:
    st.markdown(f"PDFs and raw OCR output live in S3 under `{S3_ROOT}`; everything else is an Iceberg table in `{DB}`, "
                "queried through Impala and governed by SDX (Ranger policies, Atlas lineage).")
    from ap.setup_lake import TABLES
    counts = q(" UNION ALL ".join(f"SELECT '{t}' AS t, COUNT(*) AS n FROM {DB}.{t}" for t in TABLES))
    n = dict(zip(counts["t"], counts["n"]))
    st.dataframe(pd.DataFrame([
        {"Table": f"{DB}.{t}", "Kind": "Enterprise data" if i < 5 else "Document AI", "Rows": n.get(t), "What it holds": c}
        for i, (t, (c, _)) in enumerate(TABLES.items())
    ]), hide_index=True, width="stretch")
    with st.expander("Rules: extracted fields joined against enterprise data (ap/rules.sql)"):
        st.code(Path("ap/rules.sql").read_text(), language="sql")
    st.markdown("**Reset the demo**")
    st.caption("Removes live uploads (files, documents, extractions) and reruns the rules, back to the batch state.")
    if st.button("Remove live uploads"):
        with st.spinner("Cleaning up…"):
            removed = pipeline.reset_uploads()
        refresh()
        st.session_state.pop("ingested", None)
        st.success(f"Removed {removed} uploaded document(s).")


{"Action queue": action_queue, "Ingest an invoice": ingest_view, "Under the hood": under_the_hood}[view]()
