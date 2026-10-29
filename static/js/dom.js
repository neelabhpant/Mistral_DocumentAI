/* DOM building blocks for the views: h() and svg() create elements (strings always become text nodes, never HTML),
   the shared components, and the number formats. */

const SVG_NS = "http://www.w3.org/2000/svg";
let lastId = 0;

// ---------- elements ----------

/** An HTML element. attrs: class (string or list), dataset, on* handlers, aria-*, booleans as properties. */
export function h(tag, attrs, ...children) {
  return build(document.createElement(tag), attrs, children);
}

/** An SVG element (attributes only). */
export function svg(tag, attrs, ...children) {
  return build(document.createElementNS(SVG_NS, tag), attrs, children);
}

/** Appends children (nodes, strings, numbers, nested lists); null, undefined, false and "" are skipped. */
export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function nextId(prefix) {
  lastId += 1;
  return `${prefix}-${lastId}`;
}

function build(el, attrs, children) {
  for (const [name, value] of Object.entries(attrs || {})) setAttribute(el, name, value);
  return append(el, children);
}

function setAttribute(el, name, value) {
  if (value === null || value === undefined) return;
  if (name === "class") el.setAttribute("class", [value].flat().filter(Boolean).join(" "));
  else if (name === "dataset") Object.assign(el.dataset, value);
  else if (name.startsWith("on") && typeof value === "function") el.addEventListener(name.slice(2).toLowerCase(), value);
  else if (name.startsWith("aria-")) el.setAttribute(name, String(value));
  else if (typeof value === "boolean" && name in el && !(el instanceof SVGElement)) el[name] = value;
  else if (value !== false) el.setAttribute(name, value === true ? "" : String(value));
}

// ---------- text ----------

/** Text whose `backtick` parts become <code>: the copy's convention, and the server's job step labels. */
export function codeText(text) {
  const parts = String(text ?? "").split("`");
  if (parts.length % 2 === 0) {  // an unclosed backtick stays text
    const tail = parts.pop();
    parts[parts.length - 1] += `\`${tail}`;
  }
  return parts.map((part, i) => (i % 2 ? h("code", {}, part) : part));
}

export function caption(...children) {
  return h("p", {class: "caption"}, ...children);
}

// ---------- components ----------

const LOGOS = "static/logos";

/** The brand header every view starts with: the partner lockup, the Mistral rainbow, the title and subtitle. */
export function pageHeader(title, subtitle) {
  return h("header", {class: "page-header"},
    h("div", {class: "brandbar"},
      h("div", {class: "lockup"},
        h("img", {class: "logo-cloudera", src: `${LOGOS}/cloudera-new-logo.png`, alt: "Cloudera", width: 736, height: 92}),
        h("span", {class: "lockup-x", "aria-hidden": "true"}, "×"),
        h("img", {class: "logo-mistral", src: `${LOGOS}/Mistral-Lockup-Gradient-RGB.png`, alt: "Mistral AI", width: 805,
                  height: 182})),
      h("div", {class: "brandbar-context"}, "Document AI · Accounts payable")),
    h("div", {class: "rainbow"}),
    h("h1", {class: "page-title"}, title),
    subtitle && h("p", {class: "page-sub"}, subtitle));
}

export function metric(label, value, help) {
  return h("div", {class: "metric"},
    h("div", {class: "metric-label"}, h("span", {}, label), help && helpTip(help)),
    h("div", {class: "metric-value"}, value));
}

function helpTip(text) {
  return h("button", {type: "button", class: "help", "aria-label": text, "data-tip": text}, "?");
}

export function tag(label, tone) {
  return h("span", {class: ["tag", `tone-${tone}`]}, label);
}

/** A tinted box: tone is risk, review, opportunity or info. */
export function callout(tone, ...children) {
  return h("div", {class: ["callout", `callout-${tone}`]}, ...children);
}

/** A button. kind: primary, secondary or tertiary; other options become attributes. */
export function button(label, {kind = "secondary", type = "button", icon: iconName, className, ...attrs} = {}) {
  return h("button", {type, class: ["btn", `btn-${kind}`, className], ...attrs}, iconName && icon(iconName), label);
}

/** A <details> box. content may be a function: it is called the first time the box opens. */
export function details(summary, content, {open = false, className} = {}) {
  const body = h("div", {class: "details-body"});
  const el = h("details", {class: ["details", className], open}, h("summary", {}, summary), body);
  const fill = () => {
    if (el.open && !body.hasChildNodes()) append(body, [typeof content === "function" ? content() : content]);
  };
  el.addEventListener("toggle", fill);
  fill();
  return el;
}

export function codeBlock(text, {wrap = false} = {}) {
  return h("pre", {class: ["code", wrap && "code-wrap"], tabindex: "0"}, h("code", {}, text));
}

/** A table. columns: [{label, cell(row) -> node or text, num, className}]; options: className (on the scroll
    wrapper), label (aria-label), rowAttrs(row, i). */
export function table(columns, rows, {className, label, rowAttrs} = {}) {
  const cls = col => [col.num && "num", col.className];
  return h("div", {class: ["table-wrap", className]},
    h("table", {class: "table", "aria-label": label},
      h("thead", {}, h("tr", {}, columns.map(col => h("th", {scope: "col", class: cls(col)}, col.label)))),
      h("tbody", {}, rows.map((row, i) => h("tr", rowAttrs ? rowAttrs(row, i) : {},
        columns.map(col => h("td", {class: cls(col)}, col.cell(row))))))));
}

/** Tabs (underline style, arrow keys move between them). onSelect(id, panel) fills the emptied panel when the
    user picks a tab; the caller fills the first one. Returns {el, panel}. */
export function tabs(items, selected, onSelect, {label, className} = {}) {
  const id = nextId("tabs");
  const panel = h("div", {class: "tab-panel", role: "tabpanel", id: `${id}-panel`});
  const buttons = items.map(item => h("button", {
    type: "button", role: "tab", class: "tab", id: `${id}-${item.id}`, "aria-controls": panel.id,
    onclick: () => choose(item.id),
  }, item.label));

  function mark() {
    items.forEach((item, i) => {
      buttons[i].setAttribute("aria-selected", String(item.id === selected));
      buttons[i].tabIndex = item.id === selected ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby", `${id}-${selected}`);
  }

  function choose(next) {
    if (next === selected) return;
    selected = next;
    mark();
    panel.replaceChildren();
    onSelect(next, panel);
  }

  function onKey(event) {
    const moves = {ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity};
    if (!(event.key in moves)) return;
    event.preventDefault();
    const at = items.findIndex(item => item.id === selected);
    const to = Math.max(0, Math.min(items.length - 1, at + moves[event.key]));
    buttons[to].focus();
    choose(items[to].id);
  }

  mark();
  const list = h("div", {class: "tabs", role: "tablist", "aria-label": label, onkeydown: onKey}, buttons);
  return {el: h("div", {class: ["tabset", className]}, list, panel), panel};
}

/** A row of toggle buttons with a label (the "Page" selector). options: [{value, label}]. */
export function segmented(label, options, selected, onSelect) {
  const labelId = nextId("segmented");
  const buttons = options.map(option => h("button", {
    type: "button", class: "segment", "aria-pressed": String(option.value === selected),
    onclick: () => pick(option.value),
  }, option.label));

  function pick(value) {
    if (value === selected) return;
    selected = value;
    options.forEach((option, i) => buttons[i].setAttribute("aria-pressed", String(option.value === value)));
    onSelect(value);
  }

  return h("div", {class: "field field-inline"},
    h("span", {class: "field-label", id: labelId}, label),
    h("div", {class: "segmented", role: "group", "aria-labelledby": labelId}, buttons));
}

/** A labelled form field. */
export function field(label, id, control, ...extra) {
  return h("div", {class: "field"}, h("label", {class: "field-label", for: id}, label), control, ...extra);
}

export function spinner(text, {delayed = false} = {}) {
  return h("div", {class: ["spinner", delayed && "spinner-delayed"], role: "status"}, text);
}

/** While data loads: a spinner that only shows up if the wait is noticeable. */
export function loading(text) {
  return spinner(text, {delayed: true});
}

/** An error callout. Server failures (5xx, failed jobs) that aren't Mistral's get the preflight hint. */
export function errorBox(error) {
  const message = error?.message || String(error);
  const hint = typeof error?.status === "number" && error.status >= 500 && !/Mistral/.test(message);
  return h("div", {class: "callout callout-risk", role: "alert"},
    h("div", {class: "error-message"}, message),
    hint && h("div", {class: "error-hint"}, "Check the Impala connection: ", h("code", {}, "python -m ap.preflight"), "."));
}

/** A confidence bar with its value; low marks it in the review tone. */
export function progressBar(value, {low = false} = {}) {
  if (value === null || value === undefined) return "";
  const fill = h("span", {class: "bar-fill"});
  fill.style.width = `${Math.max(0, Math.min(1, value)) * 100}%`;
  return h("span", {class: ["bar", low && "is-low"]},
    h("span", {class: "bar-track"}, fill), h("span", {class: "bar-value"}, conf(value)));
}

// ---------- icons (24 × 24, stroked) ----------

const ICONS = {
  key: "M3 15.5a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0M10.7 12.3 20 3M16.5 6.5l2.5 2.5M14 9l2 2",
  chevron: "M6 9l6 6 6-6",
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6 6 18",
  queue: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  upload: "M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
  refresh: "M20 11.5A8 8 0 1 0 17.7 17M20 4.5v7h-7",
  send: "M12 19V5M5.5 11.5 12 5l6.5 6.5",
  download: "M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  alert: "M12 8v5M12 16.5h.01M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0z",
  user: "M8 8a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M4.5 20a7.5 7.5 0 0 1 15 0",
};

export function icon(name, className) {
  return svg("svg", {class: ["icon", className], viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false"},
    svg("path", {d: ICONS[name]}));
}

// ---------- formats ----------

const USD = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const USD0 = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0});
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const missing = x => x === null || x === undefined || x === "";

/** "$1,234.56" ("" when missing). */
export const money = x => (missing(x) ? "" : USD.format(x));
/** "$1,235". */
export const money0 = x => (missing(x) ? "" : USD0.format(x));
/** "97.3%". */
export const pct = x => (missing(x) ? "—" : `${(x * 100).toFixed(1)}%`);
/** "0.62". */
export const conf = x => (missing(x) ? "" : Number(x).toFixed(2));
/** "1,180". */
export const int = x => (missing(x) ? "" : Number(x).toLocaleString("en-US"));

/** "2026-10-08" -> "Oct 08, 2026". */
export function date(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[3]}, ${m[1]}` : String(iso ?? "");
}
