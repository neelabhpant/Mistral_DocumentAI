/* Markdown to HTML for OCR page text and chat answers: the only code in the app that produces HTML strings.

   Everything is escaped first, so the only tags in the output are the ones built here, and each is closed in order.
   app.py marks low-confidence words with sentinels ("{n}" before word n, "" after it); they become
   <mark class="lowconf" title="confidence 0.62"> per inline unit (heading, paragraph line, list item, table cell).
   A unit whose sentinels don't pair up, or whose mark would cross other markup, loses its marks. */

const OPEN = "";  // OPEN n MID word CLOSE: a low-confidence word
const CLOSE = "";
const TOKEN = "";  // TOKEN i TOKEN_END: HTML built here, out of reach of the inline rules that run later
const TOKEN_END = "";
const RESERVED = /[]/g;
const SENTINELS = /\d*|[-]/g;
const SENTINEL_PARTS = /(\d{1,9})||[]/g;

/** HTML for markdown text. marks[n] is the confidence of the word between sentinel n and its close. */
export function renderMarkdown(text, {marks} = {}) {
  const source = String(text ?? "").replace(/\r\n?/g, "\n").replace(RESERVED, "�");
  const ctx = {marks: Array.isArray(marks) ? marks : []};
  return renderBlocks(escapeHTML(source).split("\n"), ctx, 0);
}

function escapeHTML(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ---------- blocks ----------

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const QUOTE = /^ {0,3}&gt; ?/;  // ">" after escaping
const BULLET = /^[ \t]*[-*+][ \t]+(.*)$/;
const ORDERED = /^[ \t]*(\d{1,9})[.)][ \t]+(.*)$/;
const MAX_QUOTE_DEPTH = 3;

function renderBlocks(lines, ctx, depth) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) i += 1;
    else if (FENCE.test(line)) i = codeBlock(lines, i, out);
    else if (HEADING.test(line)) i = heading(lines, i, ctx, out);
    else if (RULE.test(line)) i = rule(i, out);
    else if (QUOTE.test(line) && depth < MAX_QUOTE_DEPTH) i = blockquote(lines, i, ctx, depth, out);
    else if (tableStarts(lines, i)) i = table(lines, i, ctx, out);
    else if (listItem(line)) i = list(lines, i, ctx, depth, out);
    else i = paragraph(lines, i, ctx, depth, out);
  }
  return out.join("\n");
}

/** Whether line i begins a block, which ends a paragraph or a list item's continuation lines. */
function startsBlock(lines, i, depth) {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || (QUOTE.test(line) && depth < MAX_QUOTE_DEPTH)
    || tableStarts(lines, i) || listItem(line) !== null;
}

function codeBlock(lines, i, out) {
  const fence = FENCE.exec(lines[i])[1];
  const body = [];
  for (i += 1; i < lines.length && !closesFence(lines[i], fence); i += 1) body.push(lines[i]);
  out.push(`<pre><code>${body.join("\n").replace(SENTINELS, "")}</code></pre>`);
  return i + 1;  // past the closing fence (or the end)
}

function closesFence(line, fence) {
  const m = FENCE_CLOSE.exec(line);
  return m !== null && m[1][0] === fence[0] && m[1].length >= fence.length;
}

/** "#" to "######" become h3 to h6, so a page title doesn't dwarf the app's own headings. */
function heading(lines, i, ctx, out) {
  const [, hashes, text = ""] = HEADING.exec(lines[i]);
  const level = Math.min(hashes.length + 2, 6);
  out.push(`<h${level}>${inline(text, ctx)}</h${level}>`);
  return i + 1;
}

function rule(i, out) {
  out.push("<hr>");
  return i + 1;
}

function blockquote(lines, i, ctx, depth, out) {
  const inner = [];
  for (; i < lines.length && QUOTE.test(lines[i]); i += 1) inner.push(lines[i].replace(QUOTE, ""));
  out.push(`<blockquote>${renderBlocks(inner, ctx, depth + 1)}</blockquote>`);
  return i;
}

function paragraph(lines, i, ctx, depth, out) {
  const units = [];
  do {
    units.push(inline(lines[i].trim(), ctx));
    i += 1;
  } while (i < lines.length && lines[i].trim() && !startsBlock(lines, i, depth));
  out.push(`<p>${units.join("<br>")}</p>`);  // single newlines stay, so addresses keep their lines
  return i;
}

// ---------- lists ----------

function listItem(line) {
  const bullet = BULLET.exec(line);
  if (bullet) return {ordered: false, text: bullet[1]};
  const ordered = ORDERED.exec(line);
  return ordered ? {ordered: true, start: Number(ordered[1]), text: ordered[2]} : null;
}

/** One level of list: nested items are flattened, and continuation lines join their item. */
function list(lines, i, ctx, depth, out) {
  const first = listItem(lines[i]);
  const items = [];
  while (i < lines.length) {
    const item = listItem(lines[i]);
    if (item && item.ordered !== first.ordered) break;
    if (item) {
      items.push(item.text);
      i += 1;
    } else if (!lines[i].trim()) {
      const next = nextFilled(lines, i);
      const after = next < lines.length ? listItem(lines[next]) : null;
      if (!after || after.ordered !== first.ordered) break;
      i = next;
    } else if (startsBlock(lines, i, depth)) {
      break;
    } else {
      items[items.length - 1] += ` ${lines[i].trim()}`;
      i += 1;
    }
  }
  const tag = first.ordered ? "ol" : "ul";
  const start = first.ordered && first.start !== 1 ? ` start="${first.start}"` : "";
  out.push(`<${tag}${start}>${items.map(text => `<li>${inline(text, ctx)}</li>`).join("")}</${tag}>`);
  return i;
}

function nextFilled(lines, i) {
  while (i < lines.length && !lines[i].trim()) i += 1;
  return i;
}

// ---------- tables ----------

function tableStarts(lines, i) {
  return i + 1 < lines.length && lines[i].includes("|") && isSeparator(lines[i + 1]);
}

function isSeparator(line) {
  return line.includes("|") && cells(line).every(cell => /^:?-+:?$/.test(cell));
}

/** A row's cells, split on unescaped pipes; the outer pipes are optional. */
function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map(cell => cell.trim());
}

function alignment(cell) {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  return right ? "right" : left ? "left" : "";
}

function table(lines, i, ctx, out) {
  const head = cells(lines[i]);
  const align = cells(lines[i + 1]).map(alignment);
  const rows = [];
  for (i += 2; i < lines.length && lines[i].trim() && lines[i].includes("|"); i += 1) rows.push(cells(lines[i]));
  const row = (tag, values) => `<tr>${head.map((_, j) => {
    const cls = align[j] ? ` class="md-${align[j]}"` : "";
    return `<${tag}${cls}>${inline(values[j] ?? "", ctx)}</${tag}>`;
  }).join("")}</tr>`;
  const body = rows.length ? `<tbody>${rows.map(values => row("td", values)).join("")}</tbody>` : "";
  out.push(`<div class="md-table"><table><thead>${row("th", head)}</thead>${body}</table></div>`);
  return i;
}

// ---------- inline ----------

// A code span, or a backslash escape (whichever starts first wins, as in CommonMark).
const CODE_OR_ESCAPE = /(?<!`)(`+)(?!`)([\s\S]*?[^`])\1(?!`)|\\([!#$%()*+,\-./:;=?@[\\\]^_`{|}~])/g;
const LINE_BREAK = /&lt;br\s*\/?&gt;/gi;  // OCR writes <br> inside table cells
// Link targets may hold one level of balanced parentheses, as in GFM; a title after the URL is ignored.
const IMAGE = /!\[[^\]]*\]\((?:[^()]|\([^()]*\))*\)/g;
const LINK = /\[([^\]]*)\]\(\s*((?:[^\s()]|\([^\s()]*\))*)[^)]*\)/g;
const SAFE_URL = /^https?:\/\/[^\s-]+$/i;
const VOID_TAGS = new Set(["br", "hr"]);

/** One inline unit, with its low-confidence marks when they pair up and nest cleanly. */
function inline(text, ctx) {
  const marked = pairMarks(text);
  if (marked) {
    const html = applyMarks(renderInline(marked.text), marked.numbers, ctx.marks);
    if (html !== null) return html;
  }
  return renderInline(text.replace(SENTINELS, ""));
}

/** The unit with each "OPEN n MID" shortened to OPEN, and the n's in order; null if they don't pair up. */
function pairMarks(text) {
  const numbers = [];
  let open = false;
  for (const [part, n] of text.matchAll(SENTINEL_PARTS)) {
    if (n !== undefined && !open) {
      numbers.push(Number(n));
      open = true;
    } else if (part === CLOSE && open) {
      open = false;
    } else {
      return null;
    }
  }
  if (open) return null;
  return {text: text.replace(/\d{1,9}/g, OPEN), numbers};
}

/** Turns the OPEN and CLOSE characters left in rendered HTML into <mark> tags; null unless each pair survived
    and nests inside the other tags. */
function applyMarks(html, numbers, marks) {
  let k = 0;
  let open = false;
  let ok = true;
  const out = html.replace(/[]/g, ch => {
    if (ch === OPEN && !open && k < numbers.length) {
      open = true;
      return markTag(marks[numbers[k++]]);
    }
    if (ch === CLOSE && open) {
      open = false;
      return "</mark>";
    }
    ok = false;
    return "";
  });
  return ok && !open && k === numbers.length && wellNested(out) ? out : null;
}

function markTag(confidence) {
  const known = typeof confidence === "number" && Number.isFinite(confidence);
  return known ? `<mark class="lowconf" title="confidence ${confidence.toFixed(2)}">` : `<mark class="lowconf">`;
}

function wellNested(html) {
  const stack = [];
  for (const [, slash, name] of html.matchAll(/<(\/?)([a-z0-9]+)[^>]*>/g)) {
    if (VOID_TAGS.has(name)) continue;
    if (!slash) stack.push(name);
    else if (stack.pop() !== name) return false;
  }
  return stack.length === 0;
}

/** Inline markup for escaped text. Each piece of HTML built here is stored as a token, so a later rule can't pair a
    delimiter inside it with one outside it: every tag is closed where it opened. */
function renderInline(text) {
  const tokens = [];
  const keep = html => {
    tokens.push(html);
    return `${TOKEN}${tokens.length - 1}${TOKEN_END}`;
  };
  let s = text.replace(CODE_OR_ESCAPE, (_, ticks, code, escaped) => keep(escaped ?? `<code>${code}</code>`));
  s = s.replace(LINE_BREAK, () => keep("<br>"));
  s = s.replace(IMAGE, () => keep(`<span class="md-img">[image]</span>`));  // images never load
  s = s.replace(LINK, (_, label, url) => keep(link(emphasis(label, keep), url)));
  s = emphasis(s, keep);
  return expand(s, tokens).replace(RESERVED, "");
}

/** A link for http(s) URLs only; anything else (javascript:, relative, the "tbl-0.md" placeholders) is its text. */
function link(label, url) {
  if (!SAFE_URL.test(url)) return label;
  return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

/** Bold, italic and strikethrough. Underscore and single-star emphasis only at word boundaries, so vendor_tax_id
    stays as it is. */
function emphasis(s, keep) {
  const wrap = (tag, inner) => keep(`<${tag}>${emphasis(inner, keep)}</${tag}>`);
  return s
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*(?!\*)/g, (_, inner) => wrap("strong", inner))
    .replace(/(^|[^A-Za-z0-9_])__(?=\S)([\s\S]*?\S)__(?![A-Za-z0-9_])/g,
      (_, before, inner) => before + wrap("strong", inner))
    .replace(/(^|[^A-Za-z0-9_*])\*(?=[^\s*])([\s\S]*?[^\s*])\*(?![A-Za-z0-9_*])/g,
      (_, before, inner) => before + wrap("em", inner))
    .replace(/(^|[^A-Za-z0-9_])_(?=[^\s_])([\s\S]*?[^\s_])_(?![A-Za-z0-9_])/g,
      (_, before, inner) => before + wrap("em", inner))
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, (_, inner) => wrap("del", inner));
}

function expand(s, tokens) {
  return s.replace(/(\d+)/g, (_, i) => expand(tokens[Number(i)] ?? "", tokens));
}
