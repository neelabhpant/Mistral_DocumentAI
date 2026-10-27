/* "Action queue": what OCR 4.1 read and Cloudera checked, as a ranked table of actions; the selected action's page
   with its evidence boxed, the invoice next to the record that disagrees, human review, the SQL and the audit trail. */

import {cachedJSON, getJSON, pageImage, postJSON, runJob} from "./api.js";
import {
  button, callout, caption, codeBlock, details, errorBox, field, h, int, loading, metric, money, money0,
  pageHeader, segmented, spinner, table, tag,
} from "./dom.js";
import {navigate, replaceRoute, rerender} from "./router.js";
import {pageViewer} from "./viewer.js";

let detailRequest = 0;  // ignores answers for a row the user has already left

export async function queueView(root, config, parts) {
  root.append(pageHeader("Invoices that need action",
    "Supplier invoices read by Mistral OCR 4.1, checked against governed data in Cloudera."));
  const body = h("div", {}, loading("Loading the action queue…"));
  root.append(body);
  let queue;
  try {
    queue = await cachedJSON("api/queue");
  } catch (error) {
    body.replaceChildren(errorBox(error));
    return;
  }
  body.replaceChildren(...summary(queue.kpis, config));
  if (!queue.rows.length) {
    body.append(callout("info", "No exceptions. Run the pipeline (", h("code", {}, "python -m ap.pipeline --pending"),
      ") or ingest an invoice."));
    return;
  }
  const detail = h("section", {class: "detail", "aria-live": "polite"});
  const list = queueTable(queue.rows, config, row => {
    replaceRoute("queue", row.doc_id, row.rule_code, row.evidence_field);
    showDetail(detail, row, config);
  });
  body.append(list.el, detail);
  list.select(pick(queue.rows, parts));
}

function summary(kpis, config) {
  const steps = h("div", {class: "steps"},
    step(1, "Read", "Mistral OCR 4.1",
      `Extracted ${int(kpis.fields)} fields from ${kpis.processed} invoice PDFs in S3, inside this environment.`),
    step(2, "Join", "Cloudera",
      "Every field checked against the vendor master, POs, goods receipts and payments in Iceberg (Impala)."),
    step(3, "Act", "Together",
      `${kpis.actions} actions, each linked to the exact spot on the page and to the record that triggered it.`));
  const arch = button("See the reference architecture →", {kind: "tertiary",
    onclick: () => navigate("hood", "architecture")});
  const metrics = h("div", {class: "metrics"},
    metric("Invoices processed", int(kpis.processed)),
    metric("Need action", int(kpis.need_action)),
    metric("Dollars at risk", money0(kpis.at_risk), money(kpis.at_risk)),
    metric("Discounts", money0(kpis.discounts), money(kpis.discounts)),
    metric("Human review", int(kpis.review),
      `A key field read with OCR confidence below ${Number(config.min_confidence).toFixed(2)}`));
  return [steps, h("div", {class: "arch-link"}, arch), metrics];
}

function step(n, title, by, text) {
  return h("div", {class: "step"},
    h("div", {class: "step-head"}, h("span", {class: "step-num"}, String(n)), h("b", {}, title),
      h("span", {class: "step-by"}, by)),
    h("p", {}, text));
}

/** The row in the URL if it's still there, else that doc's first row, else the first row (the old app's focus). */
function pick(rows, [doc, rule, evidenceField]) {
  return rows.find(r => r.doc_id === doc && r.rule_code === rule && r.evidence_field === evidenceField)
    || rows.find(r => r.doc_id === doc) || rows[0];
}

function queueTable(rows, config, onSelect) {
  const label = row => config.rules[row.rule_code]?.label || row.rule_code;
  const columns = [
    {label: "Priority", cell: r => config.priorities[String(r.priority)] || r.priority},
    {label: "Issue", cell: label},
    {label: "Vendor", cell: r => r.vendor_name || "(unknown)"},
    {label: "Invoice", cell: r => r.invoice_number},
    {label: "Amount", num: true, cell: r => money(r.amount_at_risk)},
    {label: "Recommended action", className: "wide", cell: r => r.action},
    {label: "Doc", cell: r => `${r.doc_id}${r.source === "upload" ? " (live)" : ""}`},
  ];
  let current = null;
  const el = table(columns, rows, {
    className: "queue-table", label: "Action queue",
    rowAttrs: (row, i) => ({
      tabindex: "0", class: "selectable", "aria-selected": "false", dataset: {i: String(i)},
      onclick: () => select(row),
      onkeydown: event => onKey(event, i),
    }),
  });
  const trs = [...el.querySelectorAll("tbody tr")];

  function select(row) {
    if (row === current) return;
    current = row;
    trs.forEach((tr, i) => tr.setAttribute("aria-selected", String(rows[i] === row)));
    showRow(trs[rows.indexOf(row)]);
    onSelect(row);
  }

  function onKey(event, i) {
    const to = {ArrowDown: i + 1, ArrowUp: i - 1}[event.key];
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(rows[i]);
    } else if (to !== undefined && rows[to]) {
      event.preventDefault();
      trs[to].focus();
      select(rows[to]);
    }
  }

  /** Scrolls the table (not the page) so the row is visible: a row picked from the URL may be far down. */
  function showRow(tr) {
    const top = tr.offsetTop - el.querySelector("thead").offsetHeight;
    if (top < el.scrollTop || tr.offsetTop + tr.offsetHeight > el.scrollTop + el.clientHeight) el.scrollTop = top;
  }

  return {el, select};
}

async function showDetail(detail, row, config) {
  const request = ++detailRequest;
  detail.replaceChildren(loading("Loading the evidence…"));
  const q = new URLSearchParams({doc_id: row.doc_id, rule_code: row.rule_code, evidence_field: row.evidence_field});
  let x;
  try {
    x = await getJSON(`api/exception?${q}`);
  } catch (error) {
    if (request === detailRequest) detail.replaceChildren(errorBox(error));
    return;
  }
  if (request !== detailRequest) return;
  detail.replaceChildren(h("div", {class: "detail-cols"}, evidencePane(x), factsPane(x, config)));
}

function evidencePane(x) {
  const pane = h("div", {class: "evidence"});
  const stage = h("div", {});
  const show = page => {
    const marks = x.evidence.marks.filter(m => m.page === page).map(m => ({...m, tone: x.tone}));
    stage.replaceChildren(pageViewer({src: pageImage(x.document, page), alt: `${x.document.file_name}, page ${page}`,
      marks}));
  };
  if (x.page_count > 1) {
    const pages = Array.from({length: x.page_count}, (_, i) => ({value: i + 1, label: String(i + 1)}));
    pane.append(segmented("Page", pages, x.evidence.page, show));
  }
  show(x.evidence.page);
  pane.append(stage);
  const c = x.evidence.caption;
  if (c) {
    const confidence = c.confidence === null ? "" : ` · OCR confidence ${Number(c.confidence).toFixed(2)}`;
    pane.append(caption(`Evidence: page ${c.page}${confidence} · ${c.model} · ${c.extracted_at}`, h("br"),
      "Source file: ", h("code", {}, x.document.s3_uri)));
  }
  return pane;
}

function factsPane(x, config) {
  const r = x.row;
  const pane = h("div", {class: "facts"},
    h("div", {class: "facts-title"}, tag(x.label, x.tone), " ",
      h("strong", {}, r.vendor_name || "(unknown vendor)"), ` · invoice ${r.invoice_number ?? ""}`));
  if (r.amount_at_risk !== null) {
    pane.append(h("h2", {class: "amount"}, `${money(r.amount_at_risk)} ${x.tone === "opportunity" ? "savings" : "at risk"}`));
  }
  pane.append(h("p", {}, r.detail),
    callout(x.tone, h("strong", {}, "Action:"), " ", r.action),
    h("p", {class: "compare-title"}, h("strong", {}, "The document vs. the record"), ` (${x.how})`),
    comparison(x.comparison));
  if (x.review) pane.append(reviewForm(x));
  if (x.others.length) pane.append(caption(`Also on this invoice: ${x.others.join(", ")}`));
  pane.append(
    details("How Cloudera found this (SQL over Iceberg)", () => codeBlock(x.rule_sql)),
    details("Audit trail", () => auditTrail(x)));
  return pane;
}

function comparison(rows) {
  if (!rows.length) return "";
  return table([
    {label: "", cell: c => c.what},
    {label: "On the invoice (OCR 4.1)", cell: c => c.invoice ?? ""},
    {label: "In Cloudera", cell: c => [c.record ?? "", c.table && h("span", {class: "source-table"}, c.table)]},
    {label: "Match", cell: c => (c.ok ? "yes" : h("strong", {class: "mismatch"}, "NO"))},
  ], rows, {className: "compare-table", label: "The document vs. the record"});
}

function reviewForm(x) {
  const id = "review-value";
  const fieldName = x.review.field.replaceAll("_", " ");
  const input = h("input", {id, class: "input", type: "text", value: x.review.value, autocomplete: "off"});
  const submit = button("Confirm and re-check", {kind: "primary", type: "submit"});
  const problem = h("p", {class: "field-error", role: "alert", hidden: true});
  const status = h("div", {});
  const form = h("form", {class: "review"},
    h("p", {}, h("strong", {}, "Review:"), ` compare with the highlighted spot on the page and confirm the ${fieldName}.`),
    field("Confirmed value", id, input, problem), h("div", {class: "form-actions"}, submit), status);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    problem.hidden = true;
    input.disabled = submit.disabled = true;
    try {
      const started = postJSON(`api/docs/${encodeURIComponent(x.document.doc_id)}/confirm`,
        {field: x.review.field, value: input.value});
      await started;  // a 400 (a value that can't be stored) is shown under the field
      status.replaceChildren(spinner("Saving the reviewed value and rerunning the rules…"));
      await runJob(started);
      replaceRoute("queue", x.document.doc_id);
      rerender();
    } catch (error) {
      input.disabled = submit.disabled = false;
      status.replaceChildren();
      if (error.status === 400) {
        problem.textContent = error.message;
        problem.hidden = false;
      } else {
        status.replaceChildren(errorBox(error));
      }
    }
  });
  return form;
}

function auditTrail(x) {
  const a = x.audit;
  return h("ul", {class: "audit"},
    h("li", {}, "File landed: ", h("code", {}, a.s3_uri), ` at ${a.ingested_at} (sha256 `,
      h("code", {}, `${(a.sha256 || "").slice(0, 16)}…`), ")"),
    h("li", {}, "Read by ", h("code", {}, a.ocr_model), ` at ${a.processed_at}; raw output `, h("code", {}, a.ocr_uri)),
    h("li", {}, "Fields in ", h("code", {}, a.extractions_table), " (with page, bounding box and confidence)"),
    h("li", {}, "Exception written to ", h("code", {}, a.exceptions_table), ` at ${a.created_at}`));
}

