// Unit tests for static/js/markdown.js, the front end's only HTML producer. Run from the repo root: node --test tests/

import assert from "node:assert/strict";
import {test} from "node:test";

import {renderMarkdown as md} from "../static/js/markdown.js";

const open = n => `${n}`;  // the sentinels app.py puts around a low-confidence word
const close = "";
const MARK41 = `<mark class="lowconf" title="confidence 0.41">`;

// ---------- a strict checker: allowed tags and attributes, every tag closed in order, all text escaped ----------

const ALLOWED = {
  p: [], br: [], hr: [], h3: [], h4: [], h5: [], h6: [], ul: [], ol: ["start"], li: [], pre: [], code: [],
  blockquote: [], strong: [], em: [], del: [], a: ["href", "target", "rel"], mark: ["class", "title"],
  span: ["class"], div: ["class"], table: [], thead: [], tbody: [], tr: [], th: ["class"], td: ["class"],
};
const VOID = new Set(["br", "hr"]);
const TAG = /<(\/?)([a-z0-9]+)((?:\s[a-z-]+="[^"<>]*")*)>/y;
const BARE_AMPERSAND = /&(?!(?:amp|lt|gt|quot|#39);)/;

function assertSafe(html) {
  const stack = [];
  let at = 0;
  for (;;) {
    const lt = html.indexOf("<", at);
    const text = html.slice(at, lt === -1 ? html.length : lt);
    assert.doesNotMatch(text, />/, `a raw ">" in text: ${JSON.stringify(text)}`);
    assert.doesNotMatch(text, BARE_AMPERSAND, `an unescaped "&" in text: ${JSON.stringify(text)}`);
    if (lt === -1) break;
    TAG.lastIndex = lt;
    const tag = TAG.exec(html);
    assert.ok(tag, `not a well-formed tag: ${JSON.stringify(html.slice(lt, lt + 60))}`);
    const [whole, slash, name, attrs] = tag;
    assert.ok(name in ALLOWED, `unexpected <${name}>`);
    for (const [, attr, value] of attrs.matchAll(/([a-z-]+)="([^"]*)"/g)) {
      assert.ok(ALLOWED[name].includes(attr), `unexpected ${attr}= on <${name}>`);
      assert.doesNotMatch(value, BARE_AMPERSAND, `an unescaped "&" in ${attr}=`);
      if (attr === "href") assert.match(value, /^https?:\/\//i);
    }
    if (VOID.has(name)) assert.equal(slash, "", `</${name}>`);
    else if (!slash) stack.push(name);
    else assert.equal(stack.pop(), name, `</${name}> closes the wrong tag`);
    at = lt + whole.length;
  }
  assert.deepEqual(stack, [], "unclosed tags");
  assert.doesNotMatch(html, /[-]/, "a sentinel or placeholder left in the output");
}

function render(text, options) {
  const html = md(text, options);
  assertSafe(html);
  return html;
}

// ---------- blocks ----------

test("ATX headings become h3 to h6", () => {
  assert.equal(render("# Kestrel Scientific Supply"), "<h3>Kestrel Scientific Supply</h3>");
  assert.equal(render("## Two"), "<h4>Two</h4>");
  assert.equal(render("### Three"), "<h5>Three</h5>");
  assert.equal(render("#### Four"), "<h6>Four</h6>");
  assert.equal(render("###### Six"), "<h6>Six</h6>");
  assert.equal(render("# Closed ##"), "<h3>Closed</h3>");
  assert.equal(render("#hashtag"), "<p>#hashtag</p>");
  assert.equal(render("####### seven"), "<p>####### seven</p>");
});

test("a single newline becomes <br>, a blank line a new paragraph", () => {
  assert.equal(render("Bill to\nHalvorsen Fabrication Co.\n2200 Foundry Row\n\nThank you."),
    "<p>Bill to<br>Halvorsen Fabrication Co.<br>2200 Foundry Row</p>\n<p>Thank you.</p>");
  assert.equal(render("a\r\nb"), "<p>a<br>b</p>");
  assert.equal(render(""), "");
  assert.equal(render(null), "");
});

test("pipe tables: header, separator, body, alignment, ragged rows", () => {
  assert.equal(render("| # | Desc | Qty |\n|---|:---|---:|\n| 1 | Gloves | 4 |\n|  |  | Lot 1 |"),
    "<div class=\"md-table\"><table><thead><tr><th>#</th><th class=\"md-left\">Desc</th>"
    + "<th class=\"md-right\">Qty</th></tr></thead><tbody><tr><td>1</td><td class=\"md-left\">Gloves</td>"
    + "<td class=\"md-right\">4</td></tr><tr><td></td><td class=\"md-left\"></td><td class=\"md-right\">Lot 1</td>"
    + "</tr></tbody></table></div>");
  assert.equal(render("a | b\n--|:-:\n1 | 2"),
    "<div class=\"md-table\"><table><thead><tr><th>a</th><th class=\"md-center\">b</th></tr></thead>"
    + "<tbody><tr><td>1</td><td class=\"md-center\">2</td></tr></tbody></table></div>");
  assert.equal(render("| a \\| b | c |\n|---|---|\n| 1 | 2 | 3 |\n| only |"),
    "<div class=\"md-table\"><table><thead><tr><th>a | b</th><th>c</th></tr></thead><tbody>"
    + "<tr><td>1</td><td>2</td></tr><tr><td>only</td><td></td></tr></tbody></table></div>");
  assert.equal(render("| a |\n|---|"), "<div class=\"md-table\"><table><thead><tr><th>a</th></tr></thead></table></div>");
  assert.equal(render("Subtotal | 3,610.65\nno separator"), "<p>Subtotal | 3,610.65<br>no separator</p>");
});

test("lists: bullets, ordered with a start, continuation lines, one level", () => {
  assert.equal(render("- a\n* b\n+ c"), "<ul><li>a</li><li>b</li><li>c</li></ul>");
  assert.equal(render("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>");
  assert.equal(render("3) a\n4) b"), "<ol start=\"3\"><li>a</li><li>b</li></ol>");
  assert.equal(render("- a\n  continued\n- b\n\n- c"), "<ul><li>a continued</li><li>b</li><li>c</li></ul>");
  assert.equal(render("- a\n  - nested"), "<ul><li>a</li><li>nested</li></ul>");
  assert.equal(render("Items:\n- a\n\nAfter"), "<p>Items:</p>\n<ul><li>a</li></ul>\n<p>After</p>");
  assert.equal(render("- a\n1. b"), "<ul><li>a</li></ul>\n<ol><li>b</li></ol>");
});

test("fenced code, blockquotes and rules", () => {
  assert.equal(render("```sql\nSELECT '<b>' -- **not bold**\n```\nafter"),
    "<pre><code>SELECT &#39;&lt;b&gt;&#39; -- **not bold**</code></pre>\n<p>after</p>");
  assert.equal(render("~~~\nunclosed `x`"), "<pre><code>unclosed `x`</code></pre>");
  assert.equal(render("> quoted\n> more\n\n---\n***"),
    "<blockquote><p>quoted<br>more</p></blockquote>\n<hr>\n<hr>");
  assert.equal(render(">>>>> deep"), "<blockquote><blockquote><blockquote><p>&gt;&gt; deep</p></blockquote>"
    + "</blockquote></blockquote>");
});

// ---------- inline ----------

test("inline: code spans, bold, italic at word boundaries, strikethrough", () => {
  assert.equal(render("`code` **bold** __bold__ *it* _it_ ~~gone~~"),
    "<p><code>code</code> <strong>bold</strong> <strong>bold</strong> <em>it</em> <em>it</em> <del>gone</del></p>");
  assert.equal(render("vendor_tax_id and snake_case_name and 2*3*4"),
    "<p>vendor_tax_id and snake_case_name and 2*3*4</p>");
  assert.equal(render("**bold *and italic***"), "<p><strong>bold <em>and italic</em></strong></p>");
  assert.equal(render("***both***"), "<p><strong><em>both</em></strong></p>");
  assert.equal(render("**a**b**"), "<p><strong>a</strong>b**</p>");
  assert.equal(render("`**not bold**` and \\*not italic\\*"),
    "<p><code>**not bold**</code> and *not italic*</p>");
  assert.equal(render("**unbalanced and *half"), "<p>**unbalanced and *half</p>");
  assert.equal(render("**a *b** c*"), "<p><strong>a *b</strong> c*</p>");
});

test("links are http(s) only, images never load, table placeholders become text", () => {
  assert.equal(render("[Mistral](https://mistral.ai)"),
    "<p><a href=\"https://mistral.ai\" target=\"_blank\" rel=\"noopener noreferrer\">Mistral</a></p>");
  assert.equal(render("[**docs**](http://example.com/a?b=1&c=2 \"title\")"),
    "<p><a href=\"http://example.com/a?b=1&amp;c=2\" target=\"_blank\" rel=\"noopener noreferrer\">"
    + "<strong>docs</strong></a></p>");
  assert.equal(render("[tbl-0.md](tbl-0.md)"), "<p>tbl-0.md</p>");
  assert.equal(render("[relative](/api/queue)"), "<p>relative</p>");
  assert.equal(render("![logo](https://example.com/logo.png)"), "<p><span class=\"md-img\">[image]</span></p>");
  assert.equal(render("![img-0.jpeg](img-0.jpeg)"), "<p><span class=\"md-img\">[image]</span></p>");
});

// ---------- low-confidence sentinels ----------

test("sentinels become marks with the word's confidence", () => {
  assert.equal(render(`Invoice number ${open(0)}A05319,${close} issued`, {marks: [0.41]}),
    `<p>Invoice number ${MARK41}A05319,</mark> issued</p>`);
  assert.equal(render(`# ${open(1)}NORTHGATE${close} HVAC`, {marks: [0.9, 0.41]}), `<h3>${MARK41}NORTHGATE</mark> HVAC</h3>`);
  assert.equal(render(`- ${open(0)}a${close} and ${open(1)}b${close}`, {marks: [0.41, 0.5]}),
    `<ul><li>${MARK41}a</mark> and <mark class="lowconf" title="confidence 0.50">b</mark></li></ul>`);
  assert.equal(render(`| Item |\n|---|\n| ${open(0)}NH-8277${close} |`, {marks: [0.41]}),
    `<div class="md-table"><table><thead><tr><th>Item</th></tr></thead><tbody><tr><td>${MARK41}NH-8277</mark></td>`
    + "</tr></tbody></table></div>");
  assert.equal(render(`**${open(0)}A05319${close}** and \`${open(1)}x${close}\``, {marks: [0.41, 0.41]}),
    `<p><strong>${MARK41}A05319</mark></strong> and <code>${MARK41}x</mark></code></p>`);
  assert.equal(render(`${open(5)}x${close}`), `<p><mark class="lowconf">x</mark></p>`);
});

test("sentinels that don't pair up within a unit are dropped from that unit only", () => {
  const marks = {marks: [0.41, 0.41, 0.41]};
  assert.equal(render(`a ${open(0)}b\nc ${open(1)}d${close}`, marks), `<p>a b<br>c ${MARK41}d</mark></p>`);
  assert.equal(render(`a${close} b`, marks), "<p>a b</p>");
  assert.equal(render(`${open(0)}a ${open(1)}b${close}${close}`, marks), "<p>a b</p>");
  assert.equal(render(`x  ${open(0)}y${close}`, marks), "<p>x  y</p>");
  assert.equal(render(`z${close}`, marks), "<p>z</p>");
  assert.equal(render(`| ${open(0)}a | b${close} |\n|---|---|`, marks),
    "<div class=\"md-table\"><table><thead><tr><th>a</th><th>b</th></tr></thead></table></div>");
  assert.equal(render(`\`\`\`\n${open(0)}code${close}\n\`\`\``, marks), "<pre><code>code</code></pre>");
});

test("a mark that would cross other markup is dropped, the markup stays", () => {
  const marks = {marks: [0.41]};
  assert.equal(render(`**a ${open(0)}b** c${close}`, marks), "<p><strong>a b</strong> c</p>");
  assert.equal(render(`[a ${open(0)}b](https://x.example) c${close}`, marks),
    "<p><a href=\"https://x.example\" target=\"_blank\" rel=\"noopener noreferrer\">a b</a> c</p>");
  assert.equal(render(`[a](https://x.example/${open(0)}b${close})`, marks),
    "<p><a href=\"https://x.example/b\" target=\"_blank\" rel=\"noopener noreferrer\">a</a></p>");
  assert.equal(render(`![${open(0)}alt${close}](x.png)`, marks), "<p><span class=\"md-img\">[image]</span></p>");
});

// ---------- XSS probes ----------

test("HTML in the input is shown, never run", () => {
  assert.equal(render("<script>alert(1)</script>"), "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>");
  assert.equal(render("<img src=x onerror=alert(1)>"), "<p>&lt;img src=x onerror=alert(1)&gt;</p>");
  assert.equal(render("<svg onload=alert(1)>"), "<p>&lt;svg onload=alert(1)&gt;</p>");
  assert.equal(render("&lt;script&gt; &amp; &#60;"), "<p>&amp;lt;script&amp;gt; &amp;amp; &amp;#60;</p>");
  assert.equal(render("| <b>x</b> |\n|---|\n| <img src=x onerror=alert(1)> |"),
    "<div class=\"md-table\"><table><thead><tr><th>&lt;b&gt;x&lt;/b&gt;</th></tr></thead><tbody>"
    + "<tr><td>&lt;img src=x onerror=alert(1)&gt;</td></tr></tbody></table></div>");
  assert.equal(render("a<br>b<br/>c<br />d<BR>e"), "<p>a<br>b<br>c<br>d<br>e</p>");
  assert.equal(render("<br onclick=alert(1)>"), "<p>&lt;br onclick=alert(1)&gt;</p>");
});

test("javascript:, data: and other non-http links become plain text", () => {
  for (const url of ["javascript:alert(1)", "JaVaScRiPt:alert(document.cookie)", " javascript:alert(1)",
    "jav&#x61;script:alert(1)", "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==", "vbscript:msgbox(1)",
    "//evil.example/x", "http:/evil", "file:///etc/passwd"]) {
    const html = render(`[click](${url})`);
    assert.doesNotMatch(html, /<a /, url);
    assert.match(html, /click/, url);
  }
  assert.equal(render("![x](javascript:alert(1))"), "<p><span class=\"md-img\">[image]</span></p>");
});

test("quotes in URLs stay inside the href", () => {
  const html = render("[x](https://a.example/\"onmouseover=\"alert(1)) [y](https://a.example/'onfocus='alert(1))");
  assert.equal(html, "<p><a href=\"https://a.example/&quot;onmouseover=&quot;alert(1)\" target=\"_blank\" "
    + "rel=\"noopener noreferrer\">x</a> <a href=\"https://a.example/&#39;onfocus=&#39;alert(1)\" target=\"_blank\" "
    + "rel=\"noopener noreferrer\">y</a></p>");
  assert.doesNotMatch(html, /\son[a-z]+=/);
});

test("odd sentinel and placeholder characters can't inject markup", () => {
  assert.equal(render(`${open(0)}x${close}`, {marks: ["\"><script>alert(1)</script>"]}),
    "<p><mark class=\"lowconf\">x</mark></p>");
  assert.equal(render("0 and "), "<p>�0� and �</p>");
  assert.equal(render(`${open(0)}`.repeat(3) + close.repeat(5), {marks: [0.41]}), "<p></p>");
});

test("the output is well-formed for any input", () => {
  const pieces = ["*", "**", "_", "__", "~~", "`", "```", "[", "]", "(", ")", "![", "](", "https://x.example/",
    "javascript:", "|", "|---|", "---", "\n", "\n\n", "# ", "> ", "- ", "1. ", "<", ">", "&", "\"", "'", "\\", "a",
    "b c", "<br>", "<script>", open(0), open(1), open(12), close, "", "", "", "", " ", "\t"];
  let seed = 20261027;
  const random = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  for (let round = 0; round < 3000; round += 1) {
    const length = 1 + Math.floor(random() * 40);
    const text = Array.from({length}, () => pieces[Math.floor(random() * pieces.length)]).join("");
    try {
      render(text, {marks: [0.5, 0.75]});
    } catch (error) {
      error.message += `\n  input: ${JSON.stringify(text)}`;
      throw error;
    }
  }
});

test("a page from OCR 4.1, as app.py sends it", () => {
  const page = [
    "# NORTHGATE HVAC PARTS", "", "2590 Harbor Way, Lansing, MI 46454", "Federal Tax ID 15-2302255", "",
    `Invoice number ${open(0)}A05319,${close} issued August 29, 2026, against purchase order PO-4579.`, "",
    "| Item | Description | Qty | Unit price | Amount |", "|---|---|---|---|---|",
    "| NH-8277 | Refrigerant R-410A, 25lb | 8 | 245.00 | 1,960.00 |", "",
    `Northgate HVAC Parts | Invoice ${open(1)}A05319${close} | Page 1 of 1`,
  ].join("\n");
  const html = render(page, {marks: [0.41, 0.41]});
  assert.equal(html.split(MARK41).length - 1, 2);
  assert.match(html, /^<h3>NORTHGATE HVAC PARTS<\/h3>/);
  assert.match(html, /<td>NH-8277<\/td><td>Refrigerant R-410A, 25lb<\/td>/);
});
