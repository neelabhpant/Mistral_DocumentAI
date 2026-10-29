/* The app shell: the sidebar (brand, nav, the Mistral key control, settings, Refresh data), the router that draws one
   view into <main>, and a banner for anything that goes wrong in the page itself. */

import {dropCache, getJSON, postJSON} from "./api.js";
import {button, date, errorBox, h, icon, loading} from "./dom.js";
import {hoodView} from "./hood.js";
import {ingestView} from "./ingest.js";
import {keyPanel} from "./keypanel.js";
import {queueView} from "./queue.js";
import {currentRoute, latestHash, noteRoute, onRouteNoted, rerender, setRenderer} from "./router.js";

const VIEWS = [
  {id: "queue", label: "Action queue", icon: "queue", draw: queueView},
  {id: "ingest", label: "Ingest an invoice", icon: "upload", draw: ingestView},
  {id: "hood", label: "Under the hood", icon: "layers", draw: hoodView},
];
const LOGOS = "static/logos";

window.addEventListener("error", event => showBanner(event.error || event.message));
window.addEventListener("unhandledrejection", event => showBanner(event.reason));

boot();

async function boot() {
  const main = document.getElementById("main");
  main.replaceChildren(loading("Loading…"));
  let config;
  try {
    config = await getJSON("api/config");
  } catch (error) {
    main.replaceChildren(errorBox(error));
    return;
  }
  document.getElementById("sidebar").replaceChildren(...sidebar(config));
  setRenderer(() => draw(main, config));
  window.addEventListener("hashchange", rerender);
  rerender();
}

function draw(main, config) {
  const route = currentRoute();
  const view = VIEWS.find(v => v.id === route.view);
  if (!view) {
    history.replaceState(history.state, "", "#/queue");
    return draw(main, config);
  }
  noteRoute();
  const root = h("div", {class: `view view-${view.id}`});
  main.replaceChildren(root);
  window.scrollTo(0, 0);
  view.draw(root, config, route.parts);
}

function sidebar(config) {
  const links = VIEWS.map(view => h("a", {class: "nav-link", href: `#/${view.id}`, dataset: {view: view.id}},
    icon(view.icon), view.label));
  onRouteNoted(route => {
    for (const link of links) {
      const here = link.dataset.view === route.view;
      if (here) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
      link.href = here ? `#/${link.dataset.view}` : latestHash(link.dataset.view);
    }
  });

  const refresh = button("Refresh data", {icon: "refresh", className: "btn-sidebar"});
  refresh.addEventListener("click", async () => {
    refresh.disabled = true;
    try {
      await postJSON("api/refresh");
      dropCache();
      rerender();
    } catch (error) {
      showBanner(error);
    } finally {
      refresh.disabled = false;
    }
  });

  const settings = h("dl", {class: "settings"},
    setting("OCR", h("code", {}, config.ocr_model), " (schema mode)"),
    setting("Data", "Iceberg ", h("code", {}, config.db), " via Impala"),
    setting("Files", h("code", {}, config.s3_root || "(not set)")),
    setting("Business date", date(config.as_of)));

  return [
    h("div", {class: "sidebrand"},
      h("img", {class: "logo-mistral", src: `${LOGOS}/Mistral-Lockup-Gradient-inverted-RGB.png`, alt: "Mistral AI"}),
      h("img", {class: "logo-cloudera", src: `${LOGOS}/cloudera-new-logo.png`, alt: "Cloudera"}),
      h("div", {class: "rainbow"}),
      h("div", {class: "app-name"}, "Document AI"),
      h("div", {class: "app-sub"}, "Accounts payable · read, join, act")),
    h("nav", {class: "nav", "aria-label": "Views"}, links),
    h("div", {class: "side-section"}, keyPanel(config)),
    h("details", {class: "side-settings", open: true}, h("summary", {}, "Settings"), settings, refresh),
    h("p", {class: "legal"},
      "Cloudera and Mistral AI logos are trademarks of their owners. Vendors and invoices are fictitious."),
  ];
}

function setting(name, ...value) {
  return h("div", {class: "setting"}, h("dt", {}, `${name}:`), h("dd", {}, ...value));
}

function showBanner(error) {
  if (!error) return;
  const box = errorBox(error instanceof Error ? error : new Error(String(error)));
  const close = button("Dismiss", {className: "btn-small"});
  const banner = h("div", {class: "banner"}, box, close);
  close.addEventListener("click", () => banner.remove());
  document.body.append(banner);
}
