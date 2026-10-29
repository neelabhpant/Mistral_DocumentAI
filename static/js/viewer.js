/* The page viewer: a page image with an SVG overlay whose viewBox is the image's own pixel size, stretched over the
   <img>. Boxes come as page fractions [x0, y0, x1, y1], so they land on the same words at any width. Evidence marks
   and OCR 4.1 layout blocks are drawn here; colours come from the stylesheet (classes, never hex values). */

import {h, svg} from "./dom.js";

const BLOCK_TYPES = new Set(["text", "title", "table", "list", "signature", "image", "caption"]);
const TONES = new Set(["risk", "review", "opportunity"]);

/** marks: [{bbox, label, tone, primary}]; blocks: [{bbox, type}]; ratio: the page's width / height, for the
    placeholder shown while the image loads. */
export function pageViewer({src, alt, marks = [], blocks = [], ratio = 8.5 / 11}) {
  const img = h("img", {class: "viewer-img", alt});
  const overlay = svg("svg", {class: "viewer-overlay", preserveAspectRatio: "none", "aria-hidden": "true"});
  const stage = h("div", {class: "viewer-stage is-loading"}, img, overlay);
  stage.style.aspectRatio = String(ratio);
  img.addEventListener("load", () => draw(stage, img, overlay, marks, blocks), {once: true});
  img.addEventListener("error", () => failed(stage), {once: true});
  img.src = src;
  return h("div", {class: "viewer"}, stage, blocks.length > 0 && legend(blocks));
}

async function draw(stage, img, overlay, marks, blocks) {
  await document.fonts.ready;  // tag widths are measured from the rendered text
  const width = img.naturalWidth;
  const height = img.naturalHeight;
  stage.classList.remove("is-loading");
  stage.style.aspectRatio = "";
  overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const page = {overlay, width, height};
  for (const block of blocks) drawBlock(page, block);
  const secondariesFirst = [...marks].sort((a, b) => Number(Boolean(a.primary)) - Number(Boolean(b.primary)));
  for (const mark of secondariesFirst) drawMark(page, mark);
}

function failed(stage) {
  stage.classList.remove("is-loading");
  stage.classList.add("is-error");
  stage.append(h("p", {class: "viewer-error"}, "The page image couldn't be loaded."));
}

function drawMark(page, mark) {
  const primary = Boolean(mark.primary);
  const tone = primary && TONES.has(mark.tone) ? mark.tone : "evidence";
  const box = rect(page, mark.bbox, (primary ? 0.003 : 0.0015) * page.width);
  const group = svg("g", {class: ["mark", `mark-${tone}`, primary && "is-primary"]},
    svg("title", {}, mark.label), svg("rect", {class: "box", ...box}));
  page.overlay.append(group);
  if (primary) addTag(page, group, mark.label, box, page.width / 70);
}

function drawBlock(page, block) {
  const type = BLOCK_TYPES.has(block.type) ? block.type : "other";
  const box = rect(page, block.bbox, 0);
  const group = svg("g", {class: ["block", `block-${type}`]},
    svg("title", {}, block.type), svg("rect", {class: "box", ...box}));
  page.overlay.append(group);
  addTag(page, group, block.type, box, page.width / 110);
}

/** A box in image pixels, grown by pad on every side. */
function rect(page, [x0, y0, x1, y1], pad) {
  return {
    x: x0 * page.width - pad, y: y0 * page.height - pad,
    width: (x1 - x0) * page.width + 2 * pad, height: (y1 - y0) * page.height + 2 * pad,
  };
}

/** A label tag at the box's top-left corner: above the box, or inside its top edge when there's no room above. */
function addTag(page, group, label, box, size) {
  const back = svg("rect", {class: "tag-bg", rx: (size * 0.3).toFixed(1)});
  const text = svg("text", {class: "tag-text", "font-size": size.toFixed(1), "dominant-baseline": "central"}, label);
  group.append(back, text);
  const width = textWidth(text, label, size) + size;
  const height = size * 1.6;
  const x = Math.max(0, Math.min(box.x, page.width - width));
  const y = box.y - height >= 0 ? box.y - height : box.y;
  setAll(back, {x, y, width, height});
  setAll(text, {x: x + size / 2, y: y + height / 2});
}

function textWidth(text, label, size) {
  const measured = text.getComputedTextLength();
  return measured > 0 ? measured : label.length * size * 0.6;  // not rendered yet: estimate
}

function setAll(el, attrs) {
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value.toFixed(1));
}

function legend(blocks) {
  const types = [...new Set(blocks.map(block => block.type))].sort();
  return h("ul", {class: "legend", "aria-label": "Layout block types"},
    types.map(type => h("li", {class: ["chip", `block-${BLOCK_TYPES.has(type) ? type : "other"}`]},
      h("span", {class: "swatch", "aria-hidden": "true"}), type)));
}
