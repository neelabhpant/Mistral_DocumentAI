/* "Ingest an invoice": a held-back sample or an uploaded PDF goes through the same code as the batch Job (land in S3,
   OCR 4.1, rules), shown step by step. */

import {postForm, runJob} from "./api.js";
import {button, callout, caption, codeText, errorBox, field, h, icon, money, pageHeader} from "./dom.js";
import {noKeyCallout} from "./keypanel.js";
import {navigate} from "./router.js";

let last = null;  // the last successful ingest, kept while the page is open
let source = "sample";

/** Forgets the last result (after "Remove live uploads"). */
export function forgetIngest() {
  last = null;
}

export function ingestView(root, config) {
  root.append(pageHeader("Ingest an invoice",
    "A new invoice lands in governed S3, Mistral OCR 4.1 reads it inside the environment, and the same rules check it " +
    "against enterprise data. The same code runs in the batch Job."));
  const hint = noKeyCallout(config);
  const picker = h("div", {class: "ingest-source"});
  const go = button("Ingest and check", {kind: "primary"});
  const status = h("div", {class: "ingest-status"});
  const result = h("div", {class: "ingest-result"});
  let chosen = () => null;

  const radios = h("div", {class: "radio-row", role: "radiogroup", "aria-label": "Source"},
    radio("sample", "Held-back sample invoices"), radio("upload", "Upload a PDF"));

  function radio(value, label) {
    const input = h("input", {type: "radio", name: "ingest-source", value, checked: source === value});
    input.addEventListener("change", () => {
      source = value;
      showPicker();
    });
    return h("label", {class: "radio"}, input, label);
  }

  function showPicker() {
    if (source === "sample") {
      if (!config.samples.length) {
        picker.replaceChildren(callout("info", "No held-back samples. Run ", h("code", {}, "python -m ap.generate"),
          " first."));
        chosen = () => null;
      } else {
        const select = h("select", {id: "ingest-sample", class: "input"},
          config.samples.map(name => h("option", {value: name}, name)));
        picker.replaceChildren(field("Invoice", select.id, select));
        chosen = () => ({sample: select.value});
      }
    } else {
      const file = h("input", {id: "ingest-file", class: "input", type: "file", accept: ".pdf,application/pdf"});
      file.addEventListener("change", update);
      picker.replaceChildren(field("Invoice PDF", file.id, file));
      chosen = () => (file.files[0] ? {file: file.files[0]} : null);
    }
    update();
  }

  function update() {
    go.disabled = !chosen();
  }

  go.addEventListener("click", async () => {
    const pick = chosen();
    if (!pick) return;
    const form = new FormData();
    if (pick.sample) form.append("sample", pick.sample);
    else form.append("file", pick.file);
    go.disabled = true;
    result.replaceChildren();
    const steps = h("ol", {class: "job-steps"});
    const head = h("div", {class: "job-head"}, h("span", {class: "spinner-dot", "aria-hidden": "true"}), "Processing…");
    status.replaceChildren(h("div", {class: "job", role: "status"}, head, steps));
    try {
      const done = await runJob(postForm("api/ingest", form), job => drawSteps(steps, job.steps));
      head.replaceChildren(icon("check", "ok"), `Done: ${done.doc_id}`);
      last = done;
      result.replaceChildren(...outcome(done));
    } catch (error) {
      head.replaceChildren(icon("x", "bad"), "Failed");
      status.append(errorBox(error));
    } finally {
      update();
    }
  });

  showPicker();
  root.append(hint || "", h("div", {class: "card"}, radios, picker, h("div", {class: "form-actions"}, go)), status,
    result);
  if (last) result.replaceChildren(...outcome(last));
}

function drawSteps(list, steps) {
  list.replaceChildren(...steps.map(s => h("li", {class: ["job-step", `is-${s.state}`]},
    s.state === "running" ? h("span", {class: "spinner-dot", "aria-hidden": "true"})
      : icon(s.state === "done" ? "check" : "x", s.state === "done" ? "ok" : "bad"),
    h("span", {}, ...codeText(s.label)),
    s.seconds !== null && h("span", {class: "job-time"}, `${s.seconds.toFixed(1)}s`))));
}

function outcome(done) {
  const times = Object.entries(done.times).map(([name, s]) => `${name} ${Number(s).toFixed(1)}s`).join(" · ");
  const found = done.exceptions.length
    ? done.exceptions.map(e => callout(e.tone, h("strong", {}, e.label), ` ${money(e.amount_at_risk)}: ${e.detail}`,
      h("br"), e.action))
    : [callout("opportunity", h("code", {}, done.doc_id), " passed every check: ready to pay on its due date.")];
  const open = button("Open in the action queue", {disabled: !done.exceptions.length,
    onclick: () => navigate("queue", done.doc_id)});
  return [caption(times), ...found, h("div", {class: "form-actions"}, open)];
}
