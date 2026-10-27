/* "Ask the invoice": the chat model with a read_document tool over the invoice's OCR text. One conversation per
   document, kept while the page is open. */

import {postJSON} from "./api.js";
import {button, codeBlock, details, h, icon, spinner} from "./dom.js";
import {jsonTree} from "./json.js";
import {noKeyCallout} from "./keypanel.js";
import {renderMarkdown} from "./markdown.js";

const chats = new Map();  // doc_id -> {chatId, log: [{role, content, calls}]}

export function chatPanel(detail, config) {
  const docId = detail.document.doc_id;
  if (!chats.has(docId)) chats.set(docId, {chatId: null, log: []});
  const chat = chats.get(docId);
  const c = detail.chat;

  const logBox = h("div", {class: "chat-log", tabindex: "0", "aria-label": "Conversation", "aria-live": "polite"});
  const input = h("input", {class: "input", type: "text", placeholder: "Ask about this invoice", "aria-label": "Question",
    maxlength: "2000"});
  const send = button("", {kind: "primary", icon: "send", "aria-label": "Send"});
  const form = h("form", {class: "chat-input"}, input, send);
  let busy = false;

  function draw(waiting) {
    logBox.replaceChildren();
    if (!chat.log.length && !waiting) logBox.append(h("p", {class: "caption"},
      "Ask a question about this invoice, or pick a suggestion above."));
    for (const entry of chat.log) logBox.append(message(entry));
    if (waiting) logBox.append(spinner(`${c.model} is reading…`));
    logBox.scrollTop = logBox.scrollHeight;
  }

  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    busy = true;
    send.disabled = true;
    chat.log.push({role: "user", content: question});
    input.value = "";
    draw(true);
    try {
      const answer = await postJSON(`api/docs/${encodeURIComponent(docId)}/chat`, {chat_id: chat.chatId, question});
      chat.chatId = answer.chat_id;
      chat.log.push({role: "assistant", content: answer.answer, calls: answer.calls});
    } catch (error) {
      chat.log.push({role: "assistant", content: `Error: ${error.message}`, plain: true});
    } finally {
      busy = false;
      send.disabled = false;
      draw(false);
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    ask(input.value);
  });
  draw(false);

  return h("div", {class: "chat"},
    noKeyCallout(config) || "",
    h("p", {}, h("code", {}, c.model), " with a system prompt and a ", h("code", {}, "read_document"),
      " tool that returns OCR 4.1's text for the invoice (Mistral's OCR tool-usage cookbook pattern)."),
    details("What the assistant was told (system prompt + tool)",
      () => [codeBlock(c.system_prompt, {wrap: true}), jsonTree(c.tools)]),
    h("div", {class: "suggestions"}, c.suggestions.map(s => button(s, {className: "suggestion", onclick: () => ask(s)}))),
    logBox, form);
}

function message(entry) {
  const body = h("div", {class: "chat-text"});
  if (entry.role === "assistant" && !entry.plain) body.innerHTML = renderMarkdown(entry.content);  // escapes everything
  else body.textContent = entry.content;
  const calls = entry.calls?.length
    ? details(`Tool calls (${entry.calls.length})`, () => entry.calls.map(call => codeBlock(
      `${call.tool}(name="${call.name}")  → ${Number(call.chars).toLocaleString("en-US")} characters of OCR text`)))
    : "";
  return h("div", {class: ["chat-msg", `chat-${entry.role}`]},
    h("span", {class: "chat-avatar", "aria-hidden": "true"}, entry.role === "user" ? icon("user") : "AI"),
    h("div", {class: "chat-bubble"}, body, calls));
}
