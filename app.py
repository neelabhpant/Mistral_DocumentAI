"""Streamlit demo of Mistral OCR 4 on contract PDFs.

Run: .venv/bin/streamlit run app.py
"""

import hashlib
import html
import io
import json
import re
import time
from pathlib import Path

import altair as alt
import pandas as pd
import pymupdf
import streamlit as st
from mistralai.client.models import OCRResponse
from PIL import Image, ImageDraw, ImageFont

from contract_ai import (
    ANNOTATION_FORMAT,
    CHAT_MODEL,
    DEFAULT_PROMPT,
    SYSTEM_PROMPT,
    accuracy,
    annotate,
    ask,
    cuad_labels,
    doc_for_chat,
    parse_fields,
    score,
    tool_spec,
)
from ocr_extract import DEFAULT_PDFS, PDF_DIR, TXT_DIR, ROOT, ocr_bytes, page_markdown, similarity
from test_mistral_api import OCR_MODEL, get_client

CACHE_DIR = ROOT / "outputs/ocr_cache"
PRICE_PER_PAGE = 4 / 1000  # USD, standard (non-batch) API pricing
LOW_CONF = 0.8

# Categorical slots in fixed order; structural/peripheral types share a neutral gray and are told apart by their box label.
GRAY = "#8a8984"
BLOCK_COLORS = {
    "text": "#2a78d6",
    "title": "#eb6834",
    "table": "#1baf7a",
    "list": "#eda100",
    "signature": "#e87ba4",
    "image": "#008300",
    "caption": "#4a3aa7",
}
MD_PLACEHOLDER = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
WORD_OK = re.compile(r"^[\w$%.,;:'\"()/&-]+$")

st.set_page_config(page_title="Mistral OCR 4 · Document AI demo", layout="wide")
st.markdown(
    """<style>
    .chip {display:inline-flex;align-items:center;gap:6px;margin:0 14px 4px 0;font-size:0.85rem;color:#52514e}
    .chip span.sw {width:12px;height:12px;border-radius:3px;display:inline-block}
    mark.lowconf {background:#fde3c8;border-bottom:2px solid #ec835a;padding:0 1px;border-radius:2px}
    </style>""",
    unsafe_allow_html=True,
)


# ---------- data helpers ----------

@st.cache_data(show_spinner=False)
def cuad_library() -> dict[str, str]:
    """Map pdf path -> display label for every CUAD contract."""
    lib = {}
    for p in sorted(PDF_DIR.rglob("*")):
        if p.suffix.lower() != ".pdf":
            continue
        company = p.stem.split("_")[0]
        doc_type = re.split(r"[_-]", p.stem)[-1].strip().title()
        lib[str(p)] = f"{p.parent.name.replace('_', ' ')} · {company} · {doc_type}"
    return lib


@st.cache_data(show_spinner=False)
def reference_texts() -> dict[str, str]:
    return {p.stem: str(p) for p in TXT_DIR.rglob("*.txt")}


@st.cache_resource(show_spinner=False)
def client():
    try:
        return get_client()
    except SystemExit as e:
        st.error(f"Mistral API key problem: {e}")
        st.stop()


def cache_path(sha: str, table_format: str) -> Path:
    return CACHE_DIR / f"{sha}_{table_format}.json"


def run_ocr(name: str, data: bytes, table_format: str, use_cache: bool) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    path = cache_path(sha, table_format)
    if use_cache and path.exists():
        saved = json.loads(path.read_text())
        resp, seconds, cached = OCRResponse.model_validate(saved["response"]), saved["seconds"], True
    else:
        start = time.perf_counter()
        resp = ocr_bytes(client(), data, table_format=table_format, confidence="word", include_images=True)
        seconds, cached = time.perf_counter() - start, False
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name, "seconds": seconds, "response": resp.model_dump(mode="json")}))

    ref = reference_texts().get(Path(name).stem)
    full_md = "\n\n".join(page_markdown(p) for p in resp.pages)
    return {
        "name": name,
        "sha": sha,
        "pdf": data,
        "resp": resp,
        "seconds": seconds,
        "cached": cached,
        "similarity": similarity(full_md, Path(ref)) if ref else None,
    }


def run_annotation(doc: dict, prompt: str | None, use_cache: bool, cache_only: bool = False) -> dict | None:
    """OCR 4 call with document annotations (schema + optional prompt), cached per document, schema and prompt."""
    key = hashlib.sha1((json.dumps(ANNOTATION_FORMAT, sort_keys=True) + (prompt or "")).encode()).hexdigest()[:12]
    path = CACHE_DIR / f"{doc['sha']}_annot_{key}.json"
    if use_cache and path.exists():
        saved, cached = json.loads(path.read_text()), True
    elif cache_only:
        return None
    else:
        start = time.perf_counter()
        resp = annotate(client(), doc["pdf"], prompt)
        saved, cached = {"seconds": time.perf_counter() - start, "prompt": prompt, "annotation": resp.document_annotation}, False
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved))
    return {"fields": parse_fields(saved["annotation"]), "seconds": saved["seconds"], "cached": cached}


@st.cache_data(show_spinner=False)
def render_page(sha: str, _pdf: bytes, index: int, dpi: int) -> Image.Image:
    with pymupdf.open(stream=_pdf, filetype="pdf") as doc:
        pix = doc[index].get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


@st.cache_data(show_spinner=False)
def text_layer(sha: str, _pdf: bytes) -> list[str]:
    with pymupdf.open(stream=_pdf, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


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


def strip_images(dump: dict) -> dict:
    for p in dump.get("pages", []):
        for img in p.get("images", []):
            if img.get("image_base64"):
                img["image_base64"] = "<base64 omitted>"
    return dump


# ---------- sidebar ----------

with st.sidebar:
    st.title("Document AI")
    st.caption(f"Model: `{OCR_MODEL}` · ${PRICE_PER_PAGE * 1000:.0f} per 1,000 pages")
    source = st.radio("Documents", ["Sample contracts (CUAD)", "Upload PDFs"], horizontal=False)
    selected: list[tuple[str, bytes | Path]] = []
    if source.startswith("Sample"):
        lib = cuad_library()
        picks = st.multiselect(
            f"Choose from {len(lib)} contracts",
            options=list(lib),
            default=[str(p) for p in DEFAULT_PDFS if str(p) in lib],
            format_func=lib.get,
            placeholder="Type to search by company or type",
        )
        selected = [(Path(p).name, Path(p)) for p in picks]
    else:
        files = st.file_uploader("PDF files", type="pdf", accept_multiple_files=True)
        selected = [(f.name, f.getvalue()) for f in files or []]

    with st.expander("Options"):
        table_format = st.radio("Table format", ["markdown", "html"], horizontal=True)
        use_cache = st.toggle("Reuse cached results", value=True, help="Instant replay of documents already processed.")

    run = st.button("Run OCR 4", type="primary", disabled=not selected, width="stretch")

if run:
    results = []
    progress = st.progress(0.0, text="Starting…")
    for i, (name, src) in enumerate(selected):
        progress.progress(i / len(selected), text=f"OCR 4 is reading {name} ({i + 1}/{len(selected)})")
        try:
            data = src.read_bytes() if isinstance(src, Path) else src
            results.append(run_ocr(name, data, table_format, use_cache))
        except Exception as e:
            st.error(f"**{name}**: OCR failed: {e}")
    progress.empty()
    st.session_state["results"] = results

results = st.session_state.get("results")

# ---------- landing ----------

if not results:
    st.title("Mistral OCR 4 for document understanding")
    st.write(
        "Pick contracts in the sidebar (or upload your own PDFs) and press **Run OCR 4**. "
        "The model turns each page into structured markdown, and this demo shows what it got out."
    )
    cards = [
        ("Keeps structure", "Headings, numbered clauses, lists and tables come out as clean markdown, not a wall of text."),
        ("Sees the layout", "Every block is classified (title, text, table, signature, header…) with its position on the page."),
        ("Knows when it's unsure", "Confidence per page and per word; unreadable handwriting is marked [ILLEGIBLE], not guessed."),
        ("Takes context", "Give it a schema and instructions and it returns the contract terms you need, with supporting quotes."),
        ("Fast and cheap", "Roughly a second per few pages, at $4 per 1,000 pages."),
    ]
    for col, (title, body) in zip(st.columns(len(cards)), cards):
        with col.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(body)
    st.stop()

# ---------- batch summary ----------

st.title("OCR 4 results")
if len(results) > 1:
    summary = pd.DataFrame(
        [
            {
                "Document": r["name"],
                "Pages": r["resp"].usage_info.pages_processed,
                "Seconds": round(r["seconds"], 1),
                "Avg confidence": sum(p.confidence_scores.average_page_confidence_score for p in r["resp"].pages)
                / len(r["resp"].pages),
                "Tables": sum(len(p.tables or []) for p in r["resp"].pages),
                "Similarity to reference": r["similarity"],
            }
            for r in results
        ]
    )
    st.dataframe(
        summary,
        hide_index=True,
        width="stretch",
        column_config={
            "Avg confidence": st.column_config.NumberColumn(format="%.3f"),
            "Similarity to reference": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    doc = results[st.selectbox("Document", range(len(results)), format_func=lambda i: results[i]["name"])]
else:
    doc = results[0]
    st.subheader(doc["name"])

resp, sha = doc["resp"], doc["sha"]
pages = resp.pages
n_pages = resp.usage_info.pages_processed
avg_conf = sum(p.confidence_scores.average_page_confidence_score for p in pages) / len(pages)
n_blocks = sum(len(blocks(p)) for p in pages)
n_tables = sum(len(p.tables or []) for p in pages)

k = st.columns(6)
k[0].metric("Pages", n_pages)
k[1].metric("Processing time", f"{doc['seconds']:.1f}s", help="Cached replay: time of the original run." if doc["cached"] else None)
k[2].metric("Throughput", f"{n_pages / doc['seconds']:.1f} pages/s")
k[3].metric("Avg confidence", f"{avg_conf:.1%}")
k[4].metric("Layout blocks", n_blocks, help=f"{n_tables} tables")
k[5].metric("Est. cost", f"${n_pages * PRICE_PER_PAGE:.3f}")
if doc["cached"]:
    st.caption("Loaded from cache: no API call was made for this run.")

page_no = st.slider("Page", 1, n_pages, 1, key=f"page_{sha}") if n_pages > 1 else 1
page = pages[page_no - 1]

t_side, t_fields, t_ask, t_full, t_conf, t_layout, t_basic, t_json = st.tabs(
    ["Side-by-side", "Extracted fields", "Ask the document", "Full text", "Confidence", "Layout & tables", "vs. basic extraction", "Raw JSON"]
)

# 1. Side-by-side
with t_side:
    c1, c2, c3 = st.columns([1, 1, 2])
    show_boxes = c1.toggle("Show layout boxes", value=True)
    highlight = c2.toggle("Highlight uncertain words", value=True, help=f"Words below {LOW_CONF:.0%} confidence")
    raw = c3.toggle("Show raw markdown")
    left, right = st.columns(2, gap="large")
    with left:
        dpi = page.dimensions.dpi if page.dimensions else 100
        img = render_page(sha, doc["pdf"], page.index, dpi * 2)
        st.image(draw_blocks(img, page) if show_boxes else img, width="stretch")
        if show_boxes:
            legend({b.type for b in blocks(page)})
    with right, st.container(height=900, border=True):
        if raw:
            st.code(page_markdown(page), language="markdown", wrap_lines=True)
        else:
            show_md(highlighted_markdown(page, LOW_CONF) if highlight else page_markdown(page, inline_images=True))

# 2. Extracted fields: context given to OCR 4 itself (document annotations)
RESULT_LABELS = {
    "match": "✓ match",
    "both empty": "✓ correctly empty",
    "partial": "≈ partial",
    "mismatch": "✗ mismatch",
    "missed": "✗ missed",
    "extra": "+ not in CUAD labels",
}
with t_fields:
    st.markdown(
        "**Context for OCR 4 itself.** Alongside the PDF, OCR 4 accepts a JSON schema of the fields you want (each "
        "field's description is an instruction) and an optional free-text prompt. In the same call that transcribes the "
        "document, it returns those fields as JSON, each with a supporting quote."
    )
    f1, f2 = st.columns([3, 1])
    with f1:
        prompt_text = st.text_area("Prompt (`document_annotation_prompt`)", DEFAULT_PROMPT, height=230, key="annot_prompt")
    with f2:
        use_prompt = st.toggle("Send the prompt", value=True, help="Turn off to compare against the schema alone.")
        extract = st.button("Extract fields", type="primary", width="stretch")
        st.caption("Edit the prompt and re-run to see how the context changes the output.")
    prompt = prompt_text if use_prompt else None

    try:
        annot = run_annotation(doc, prompt, use_cache, cache_only=not extract)
    except Exception as e:
        annot = None
        st.error(f"Extraction failed: {e}")

    if annot:
        labels = cuad_labels(doc["name"])
        rows = score(annot["fields"], labels or {})
        a1, a2, a3 = st.columns(3)
        if labels:
            a1.metric("Agreement with CUAD labels", f"{accuracy(rows):.0%}", help="Match or correctly empty = 1, partial = 0.5.")
        a2.metric("Fields extracted", f"{sum(bool(r['value']) for r in rows)} / {len(rows)}")
        a3.metric("Extraction time", f"{annot['seconds']:.1f}s", help="OCR + annotation in one call." + (" Cached." if annot["cached"] else ""))
        st.dataframe(
            pd.DataFrame(
                {
                    "Field": [r["field"].replace("_", " ").capitalize() for r in rows],
                    "Extracted": [r["value"] for r in rows],
                    "Supporting quote": [r["quote"] for r in rows],
                    **({"CUAD label": [r["label"] for r in rows], "Result": [RESULT_LABELS[r["result"]] for r in rows]} if labels else {}),
                }
            ),
            hide_index=True,
            width="stretch",
            row_height=60,
            column_config={"Supporting quote": st.column_config.TextColumn(width="large")},
        )
        if labels:
            st.caption(
                "CUAD labels are human annotations of this contract. \"Not in CUAD labels\" means the model found a value "
                "the annotators left blank, so check the quote."
            )
        else:
            st.caption("No CUAD labels for this document, so there is no score. Check the values against the quotes.")
    elif not extract:
        st.info("Press **Extract fields** to run OCR 4 with this schema and prompt.")

    with st.expander("The context sent to OCR 4 (schema + prompt)"):
        st.markdown("`document_annotation_format`: JSON schema; field descriptions act as instructions")
        st.json(ANNOTATION_FORMAT, expanded=False)
        st.markdown("`document_annotation_prompt`")
        st.code(prompt or "(not sent)", language=None, wrap_lines=True)

# 3. Ask the document: system prompt for a chat model that uses OCR 4 output as a tool
with t_ask:
    docs = {r["name"]: doc_for_chat(r["resp"], page_markdown) for r in results}
    system = SYSTEM_PROMPT.format(documents=", ".join(docs))
    chat_id = tuple(r["sha"] for r in results)
    if st.session_state.get("chat_id") != chat_id:
        st.session_state.update(chat_id=chat_id, chat_messages=[{"role": "system", "content": system}], chat_log=[])

    st.markdown(
        f"**Context for a chat model.** A system prompt tells `{CHAT_MODEL}` its role, its rules, and the "
        "`read_document` tool it can call. The tool returns OCR 4's text for a contract, so the model decides when to "
        "read which document, then answers with page citations. This is the pattern from Mistral's OCR tool-usage cookbook."
    )
    with st.expander("What the assistant was told (system prompt + tool)"):
        st.code(system, language="markdown", wrap_lines=True)
        st.json(tool_spec(list(docs)), expanded=False)

    suggestions = [
        "Who are the parties to this agreement?",
        "When does the agreement expire, and how does it renew?",
        "Is there a non-compete or exclusivity clause? Quote it.",
    ]
    pending = None
    for col, q in zip(st.columns(len(suggestions)), suggestions):
        if col.button(q, width="stretch"):
            pending = q

    with st.container(height=520, border=True):
        if not st.session_state["chat_log"]:
            st.caption("Ask a question about the loaded contracts, or pick a suggestion above.")
        for entry in st.session_state["chat_log"]:
            with st.chat_message(entry["role"]):
                show_md(entry["content"])
                if entry.get("calls"):
                    with st.expander(f"Tool calls ({len(entry['calls'])})"):
                        for c in entry["calls"]:
                            st.code(f'{c["tool"]}(name="{c["name"]}")  → {c["chars"]:,} characters of OCR text', language=None)

    question = st.chat_input("Ask about the loaded contracts") or pending
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

# 4. Full text
with t_full:
    plain_md = "\n\n".join(f"<!-- page {p.index + 1} -->\n{page_markdown(p)}" for p in pages)
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button("Download .md", plain_md, f"{Path(doc['name']).stem}.md", "text/markdown")
    d2.download_button("Download .json", json.dumps(resp.model_dump(mode="json")), f"{Path(doc['name']).stem}.json", "application/json")
    with st.container(height=900, border=True):
        for p in pages:
            st.caption(f"Page {p.index + 1}")
            show_md(page_markdown(p, inline_images=True))
            st.divider()

# 5. Confidence
with t_conf:
    illegible = [(p.index + 1, m) for p in pages for m in re.finditer(r"\[ILLEGIBLE\]", page_markdown(p))]
    m1, m2, m3 = st.columns(3)
    m1.metric("Average confidence", f"{avg_conf:.1%}")
    m2.metric("Lowest page minimum", f"{min(p.confidence_scores.minimum_page_confidence_score for p in pages):.1%}")
    m3.metric("[ILLEGIBLE] markers", len(illegible))

    conf_df = pd.DataFrame(
        [
            {"Page": p.index + 1, "Measure": label, "Confidence": getattr(p.confidence_scores, attr)}
            for p in pages
            for label, attr in (("Average", "average_page_confidence_score"), ("Lowest word", "minimum_page_confidence_score"))
        ]
    )
    color = alt.Color(
        "Measure:N",
        scale=alt.Scale(domain=["Average", "Lowest word"], range=["#2a78d6", "#eb6834"]),
        legend=alt.Legend(orient="top", title=None),
    )
    base = alt.Chart(conf_df).encode(
        x=alt.X("Page:O", axis=alt.Axis(labelAngle=0, grid=False)),
        y=alt.Y("Confidence:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%", gridColor="#e8e7e3")),
        color=color,
        tooltip=["Page", "Measure", alt.Tooltip("Confidence:Q", format=".1%")],
    )
    chart = base.mark_line(strokeWidth=2) + base.mark_point(size=80, filled=True, stroke="white", strokeWidth=2)
    st.markdown("**Confidence by page**")
    st.altair_chart(chart.properties(height=280), width="stretch")

    st.markdown("**Least certain words**: where a human reviewer should look first")
    st.dataframe(
        low_conf_words(resp),
        hide_index=True,
        width="stretch",
        column_config={"Confidence": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1)},
    )
    if illegible:
        st.markdown("**Illegible content flagged by the model**")
        for pg, m in illegible:
            md = page_markdown(pages[pg - 1])
            st.caption(f"Page {pg}: …{md[max(0, m.start() - 60) : m.end() + 60]}…")

# 6. Layout & tables
with t_layout:
    block_rows = [
        {"Page": p.index + 1, "Type": b.type, "Content": (b.content or "")[:160]} for p in pages for b in blocks(p)
    ]
    bdf = pd.DataFrame(block_rows)
    if bdf.empty:
        st.info("No layout blocks returned.")
    else:
        l1, l2 = st.columns([1, 3])
        with l1:
            st.markdown("**Block types**")
            st.dataframe(bdf["Type"].value_counts().rename_axis("Type").reset_index(name="Count"), hide_index=True)
        with l2:
            types = st.multiselect("Filter by type", sorted(bdf["Type"].unique()))
            st.dataframe(bdf[bdf["Type"].isin(types)] if types else bdf, hide_index=True, width="stretch", height=380)
    st.markdown(f"**Extracted tables ({n_tables})**")
    for p in pages:
        for t in p.tables or []:
            with st.container(border=True):
                st.caption(f"Page {p.index + 1} · {t.id}")
                show_md(t.content)
    if not n_tables:
        st.caption("No tables detected in this document.")

# 7. vs basic extraction
with t_basic:
    layer = text_layer(sha, doc["pdf"])
    chars = sum(len(t.strip()) for t in layer)
    if chars < 20 * len(layer):
        st.warning("This PDF has **no usable text layer** (scanned image). Basic extraction finds nothing; OCR 4 reads the pixels.")
    else:
        st.caption(
            "This PDF has an embedded text layer. Basic extraction returns flat text with hard line breaks and no "
            "headings, tables or reading structure; OCR 4 returns structured markdown."
        )
    if doc["similarity"] is not None:
        st.metric("OCR 4 text vs CUAD reference text", f"{doc['similarity']:.1%}", help="Word-sequence similarity after removing markdown and normalizing whitespace.")
    b1, b2 = st.columns(2, gap="large")
    with b1:
        st.markdown(f"**Basic PDF text layer**, page {page_no}")
        st.code(layer[page.index] or "(empty)", language=None, height=800, wrap_lines=True)
    with b2:
        st.markdown(f"**OCR 4**, page {page_no}")
        with st.container(height=800, border=True):
            show_md(page_markdown(page, inline_images=True))

# 8. Raw JSON
with t_json:
    st.caption("Full API response (embedded images omitted here; included in the downloaded JSON).")
    st.json(strip_images(resp.model_dump(mode="json")), expanded=False)
