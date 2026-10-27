/* The sidebar's "Mistral API key" control: a button that shows and hides a panel where a key can be pasted for this
   tab. The key is checked with the server first, then kept only in api.js's memory (a reload forgets it). */

import {checkKey, forgetTabKey, setTabKey, tabKeyEnding} from "./api.js";
import {button, h, icon} from "./dom.js";

const KEY_EVENT = "docai:key";  // fired on document when the tab's key changes
let toggle, panel, input;

/** The button and its panel, for the sidebar. */
export function keyPanel(config) {
  const panelId = "key-panel";
  const marker = h("span", {class: "key-marker"});
  toggle = h("button", {
    type: "button", class: "key-toggle", "aria-expanded": "false", "aria-controls": panelId,
    onclick: () => setOpen(panel.hidden),
  }, icon("key"), h("span", {class: "key-toggle-label"}, "Mistral API key"), marker, icon("chevron", "chevron"));

  input = h("input", {
    id: "mistral-key", class: "input", type: "password", autocomplete: "off", spellcheck: "false",
    placeholder: "Paste a key for this tab",
  });
  const show = h("button", {type: "button", class: "key-show", "aria-pressed": "false", "aria-controls": input.id},
    "Show");
  show.addEventListener("click", () => {
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    show.textContent = visible ? "Hide" : "Show";
    show.setAttribute("aria-pressed", String(visible));
  });
  const use = button("Use in this tab", {kind: "primary", type: "submit", className: "btn-small"});
  const forget = button("Forget", {className: "btn-small"});
  const status = h("p", {class: "key-status", role: "status"});
  const problem = h("p", {class: "key-problem", role: "alert", hidden: true});

  function refresh() {
    const ending = tabKeyEnding();
    marker.textContent = ending ? "· this tab" : "";
    forget.disabled = !ending;
    status.textContent = ending ? `Using the key ending …${ending} in this tab. Reloading the page forgets it.`
      : config.env_key ? "Using the project's MISTRAL_API_KEY." : "No key set: ingest and chat need one.";
  }

  async function save() {
    const candidate = input.value.trim();
    problem.hidden = true;
    if (!candidate) return showProblem("Paste a key first.");
    use.disabled = forget.disabled = true;
    use.textContent = "Checking…";
    try {
      const answer = await checkKey(candidate);
      if (!answer.ocr_available) return showProblem(`${answer.ocr_model} isn't available to this key.`);
      setTabKey(candidate);
      input.value = "";
      input.type = "password";
      show.textContent = "Show";
      show.setAttribute("aria-pressed", "false");
      document.dispatchEvent(new Event(KEY_EVENT));
    } catch (error) {
      showProblem(error.message);
    } finally {
      use.disabled = false;
      use.textContent = "Use in this tab";
      refresh();
    }
  }

  function showProblem(text) {
    problem.textContent = text;
    problem.hidden = false;
  }

  forget.addEventListener("click", () => {
    forgetTabKey();
    problem.hidden = true;
    refresh();
    document.dispatchEvent(new Event(KEY_EVENT));
  });

  panel = h("form", {id: panelId, class: "key-panel", hidden: true, onsubmit: event => {
    event.preventDefault();
    save();
  }},
    h("label", {class: "field-label", for: input.id}, "Mistral API key"),
    h("div", {class: "key-input"}, input, show),
    h("div", {class: "key-actions"}, use, forget),
    problem, status);
  panel.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      setOpen(false);
      toggle.focus();
    }
  });
  refresh();
  return h("div", {class: "key-control"}, toggle, panel);
}

function setOpen(open) {
  panel.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
}

/** Opens the panel and puts the cursor in the key field (the "Open" buttons on the ingest and chat screens). */
export function openKeyPanel() {
  setOpen(true);
  input.focus();
}

export function hasKey(config) {
  return Boolean(tabKeyEnding() || config.env_key);
}

/** The "no key" hint for screens that call Mistral; it removes itself once a key is set. */
export function noKeyCallout(config) {
  if (hasKey(config)) return null;
  const el = h("div", {class: "callout callout-info key-hint"},
    h("span", {}, "No Mistral API key: open ", h("strong", {}, "Mistral API key"), " in the sidebar and paste one."),
    button("Open", {className: "btn-small", onclick: openKeyPanel}));
  const onKey = () => {
    if (hasKey(config)) {
      el.remove();
      document.removeEventListener(KEY_EVENT, onKey);
    }
  };
  document.addEventListener(KEY_EVENT, onKey);
  return el;
}
