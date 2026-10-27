/* "Under the hood": the reference architecture, what OCR 4.1 returned for one document, where the data lives (with
   the demo reset), and how well the pipeline scores against the generator's ground truth. */

import {cachedJSON, getJSON, pageImage, postJSON, runJob} from "./api.js";
import {
  button, callout, caption, codeBlock, details, errorBox, field, h, icon, int, loading, metric, pageHeader, pct,
  progressBar, segmented, spinner, table, tabs,
} from "./dom.js";
import {confidenceChart} from "./chart.js";
import {chatPanel} from "./chat.js";
import {forgetIngest} from "./ingest.js";
import {jsonTree} from "./json.js";
import {renderMarkdown} from "./markdown.js";
import {replaceRoute} from "./router.js";
import {pageViewer} from "./viewer.js";

const TABS = [
  {id: "architecture", label: "Architecture", draw: architecture},
  {id: "document", label: "One document", draw: oneDocument},
  {id: "lake", label: "Data lake & rules", draw: lake},
  {id: "accuracy", label: "Accuracy", draw: accuracy},
];
const DOC_TABS = [
  {id: "page", label: "Page & layout"},
  {id: "fields", label: "Extracted fields"},
  {id: "ask", label: "Ask the invoice"},
  {id: "confidence", label: "Confidence"},
  {id: "json", label: "Raw JSON"},
];
let lastScore = null;  // kept while the page is open

export function hoodView(root, config, parts) {
  root.append(pageHeader("Under the hood", "What OCR 4.1 returned, where the data lives, and how well it scores."));
  const current = TABS.find(t => t.id === parts[0]) || TABS[0];
  if (current.id !== parts[0]) replaceRoute("hood", current.id);
  const {el, panel} = tabs(TABS, current.id, (id, p) => {
    replaceRoute("hood", id);
    TABS.find(t => t.id === id).draw(p, config, []);
  }, {label: "Under the hood"});
  root.append(el);
  current.draw(panel, config, parts.slice(1));
}

// ---------- architecture ----------

function architecture(panel, config) {
  const src = "api/architecture.svg";
  panel.append(
    h("a", {class: "arch", href: src, target: "_blank", rel: "noopener", title: "Open full size"},
      h("img", {src, alt: "Reference architecture: Document AI on Cloudera with Mistral"})),
    h("a", {class: "btn btn-tertiary", href: src, download: "document-ai-reference-architecture.svg"},
      icon("download"), "Download SVG"),
    h("ol", {class: "arch-steps"}, config.steps.map(s => h("li", {}, s))),
    caption("Product names and logos are trademarks of their respective owners."));
}

// ---------- one document ----------

async function oneDocument(panel, config, [docId, tab]) {
  panel.append(loading("Loading documents…"));
  let docs;
  try {
    docs = (await cachedJSON("api/docs")).docs.filter(d => d.status === "processed");
  } catch (error) {
    panel.replaceChildren(errorBox(error));
    return;
  }
  if (!docs.length) {
    panel.replaceChildren(callout("info", "No processed documents yet."));
    return;
  }
  const doc = docs.find(d => d.doc_id === docId) || docs[0];
  const select = h("select", {id: "doc-select", class: "input"}, docs.map(d =>
    h("option", {value: d.doc_id, selected: d.doc_id === doc.doc_id},
      `${d.doc_id} · ${d.vendor_name ?? ""} · ${d.invoice_number ?? ""}`)));
  const body = h("div", {});
  select.addEventListener("change", () => showDocument(body, config, select.value, tab));
  panel.replaceChildren(field("Document", select.id, select), body);
  showDocument(body, config, doc.doc_id, tab);
}

let docRequest = 0;

async function showDocument(body, config, docId, tab) {
  const request = ++docRequest;
  const current = DOC_TABS.find(t => t.id === tab) || DOC_TABS[0];
  replaceRoute("hood", "document", docId, current.id);
  body.replaceChildren(loading("Loading what OCR 4.1 returned…"));
  let d;
  try {
    d = await cachedJSON(`api/docs/${encodeURIComponent(docId)}`);
  } catch (error) {
    if (request === docRequest) body.replaceChildren(errorBox(error));
    return;
  }
  if (request !== docRequest) return;
  let page = 1;
  const metrics = h("div", {class: "metrics"},
    metric("Pages", int(d.page_count)),
    metric("OCR + extraction", d.seconds === null ? "—" : `${Number(d.seconds).toFixed(1)}s`),
    metric("Avg confidence", pct(d.avg_confidence)),
    metric("Layout blocks", int(d.block_count)),
    metric("Est. cost", `$${Number(d.cost).toFixed(3)}`));
  let selectedTab = current.id;
  const {el, panel} = tabs(DOC_TABS, selectedTab, (id, p) => {
    selectedTab = id;
    replaceRoute("hood", "document", docId, id);
    drawDocTab(p, d, id, page, config);
  }, {label: "Document views", className: "subtabs"});
  const pager = d.page_count > 1 ? segmented("Page", d.pages.map(p => ({value: p.number, label: String(p.number)})), 1,
    n => {
      page = n;
      panel.replaceChildren();
      drawDocTab(panel, d, selectedTab, page, config);
    }) : "";
  body.replaceChildren(metrics, pager, el);
  drawDocTab(panel, d, selectedTab, page, config);
}

function drawDocTab(panel, d, id, page, config) {
  const p = d.pages[page - 1];
  if (id === "page") {
    const viewer = pageViewer({src: pageImage(d.document, p.number), alt: `${d.document.file_name}, page ${p.number}`,
      blocks: p.blocks, ratio: p.width && p.height ? p.width / p.height : undefined});
    const text = h("div", {class: "ocr-text md", tabindex: "0", "aria-label": "OCR text"});
    text.innerHTML = renderMarkdown(p.markdown, {marks: p.marks});  // escapes everything; marks become <mark>
    panel.append(h("div", {class: "two-cols"}, viewer, text));
  } else if (id === "fields") {
    panel.append(
      h("p", {}, h("strong", {}, "Context sent to OCR 4.1:"), " a JSON schema (", h("code", {}, "document_annotation_format"),
        ", each field description is an instruction) and a prompt (", h("code", {}, "document_annotation_prompt"),
        "). Page, box and confidence come from OCR 4.1's layout blocks and word scores."),
      table([
        {label: "Field", cell: f => f.field_name},
        {label: "Line", num: true, cell: f => f.line_no ?? ""},
        {label: "Value", cell: f => f.field_value ?? ""},
        {label: "Confidence", cell: f => progressBar(f.confidence, {low: f.confidence < config.min_confidence})},
        {label: "Page", num: true, cell: f => f.page ?? ""},
        {label: "Extracted by", cell: f => f.model},
      ], d.fields, {className: "scroll-520", label: "Extracted fields"}),
      h("div", {class: "two-cols"},
        details("Schema (document_annotation_format)", () => jsonTree(d.schema)),
        details("Prompt (document_annotation_prompt)", () => codeBlock(d.prompt, {wrap: true}))));
  } else if (id === "ask") {
    panel.append(chatPanel(d, config));
  } else if (id === "confidence") {
    panel.append(confidenceChart(d.pages),
      h("p", {}, h("strong", {}, "Least certain words"), ": where a reviewer should look first"),
      table([
        {label: "Page", num: true, cell: w => w.page},
        {label: "Word", cell: w => w.word},
        {label: "Confidence", cell: w => progressBar(w.confidence, {low: w.confidence < config.min_confidence})},
        {label: "Context", className: "wide", cell: w => w.context},
      ], d.least_certain, {label: "Least certain words"}));
  } else {
    rawJson(panel, d.document);
  }
}

async function rawJson(panel, doc) {
  const url = `api/docs/${encodeURIComponent(doc.doc_id)}/ocr.json`;
  panel.append(loading("Loading the raw OCR response…"));
  try {
    const raw = await cachedJSON(url);
    panel.replaceChildren(
      h("a", {class: "btn btn-tertiary", href: url, download: `${doc.doc_id}.ocr.json`}, icon("download"), "Download JSON"),
      jsonTree(raw));
  } catch (error) {
    panel.replaceChildren(errorBox(error));
  }
}

// ---------- data lake & rules ----------

async function lake(panel, config) {
  panel.append(loading("Counting the tables…"));
  let l;
  try {
    l = await getJSON("api/lake");
  } catch (error) {
    panel.replaceChildren(errorBox(error));
    return;
  }
  const reset = button("Remove live uploads");
  const status = h("div", {});
  reset.addEventListener("click", async () => {
    reset.disabled = true;
    status.replaceChildren(spinner("Cleaning up…"));
    try {
      const done = await runJob(postJSON("api/reset"));
      forgetIngest();
      status.replaceChildren(callout("opportunity", `Removed ${done.removed} uploaded document(s).`));
    } catch (error) {
      status.replaceChildren(errorBox(error));
    } finally {
      reset.disabled = false;
    }
  });
  panel.replaceChildren(
    h("p", {}, "PDFs and raw OCR output live in S3 under ", h("code", {}, l.s3_root),
      "; everything else is an Iceberg table in ", h("code", {}, l.db),
      ", queried through Impala and governed by SDX (Ranger policies, Atlas lineage)."),
    table([
      {label: "Table", cell: t => h("code", {}, t.table)},
      {label: "Kind", cell: t => t.kind},
      {label: "Rows", num: true, cell: t => int(t.rows)},
      {label: "What it holds", className: "wide", cell: t => t.comment},
    ], l.tables, {label: "Tables"}),
    details("Rules: extracted fields joined against enterprise data (ap/rules.sql)", () => codeBlock(l.rules_sql)),
    h("h3", {class: "section-title"}, "Reset the demo"),
    caption("Removes live uploads (files, documents, extractions) and reruns the rules, back to the batch state."),
    h("div", {class: "form-actions"}, reset), status);
}

// ---------- accuracy ----------

function accuracy(panel) {
  const go = button("Score against ground truth", {kind: "primary"});
  const out = h("div", {});
  go.addEventListener("click", async () => {
    go.disabled = true;
    out.replaceChildren(spinner("Scoring…"));
    try {
      lastScore = await runJob(postJSON("api/evaluate"));
      out.replaceChildren(...score(lastScore));
    } catch (error) {
      out.replaceChildren(errorBox(error));
    } finally {
      go.disabled = false;
    }
  });
  panel.append(
    h("p", {}, "The generator knows the true value of every field and every planted issue, so the pipeline can be " +
      "scored end to end."),
    h("div", {class: "form-actions"}, go), out);
  if (lastScore) out.replaceChildren(...score(lastScore));
}

function score(r) {
  const metrics = h("div", {class: "metrics metrics-3"},
    metric("Field accuracy", pct(r.field_accuracy), `${int(r.fields)} fields in ${r.docs} documents`),
    metric("Planted issues found", `${r.planted_ok} / ${r.planted_total}`),
    metric("Routed to review", int(r.review.length)));
  if (!r.misses.length) return [metrics];
  return [metrics, table([
    {label: "Doc", cell: m => m.doc_id},
    {label: "Field", cell: m => m.field},
    {label: "Expected", cell: m => m.expected ?? ""},
    {label: "Extracted", cell: m => m.extracted ?? ""},
    {label: "Caught by review", cell: m => (m.caught ? "yes" : "no")},
  ], r.misses, {label: "Misses"})];
}
