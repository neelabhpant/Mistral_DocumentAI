/* A collapsible JSON tree. Objects and arrays are <details> whose children are built the first time they open, so a
   raw OCR response with thousands of word scores costs nothing until someone looks. */

import {h} from "./dom.js";

/** The tree for value; open shows the first level. */
export function jsonTree(value, {open = false} = {}) {
  return h("div", {class: "json"}, node(null, value, open));
}

function node(key, value, open) {
  if (value !== null && typeof value === "object") return branch(key, value, open);
  return h("div", {class: "json-row"}, keyLabel(key), leaf(value));
}

function branch(key, value, open) {
  const isArray = Array.isArray(value);
  const entries = isArray ? value.map((item, i) => [i, item]) : Object.entries(value);
  const count = entries.length;
  const summary = h("summary", {class: "json-row"},
    keyLabel(key),
    h("span", {class: "json-brace"}, isArray ? "[…]" : "{…}"),
    h("span", {class: "json-count"}, isArray ? `${count} item${count === 1 ? "" : "s"}`
      : `${count} key${count === 1 ? "" : "s"}`));
  const el = h("details", {class: "json-branch"}, summary);
  const build = () => {
    if (!el.open || el.childElementCount > 1) return;
    el.append(h("div", {class: "json-children"}, entries.map(([k, v]) => node(k, v, false))));
  };
  el.addEventListener("toggle", build);
  if (open && count) {
    el.open = true;
    build();
  }
  return el;
}

function keyLabel(key) {
  if (key === null) return null;
  return h("span", {class: typeof key === "number" ? "json-index" : "json-key"},
    typeof key === "number" ? `${key}:` : `${JSON.stringify(key)}:`);
}

function leaf(value) {
  if (typeof value === "string") return h("span", {class: "json-string"}, JSON.stringify(value));
  if (typeof value === "number") return h("span", {class: "json-number"}, String(value));
  if (typeof value === "boolean") return h("span", {class: "json-bool"}, String(value));
  return h("span", {class: "json-null"}, "null");
}
