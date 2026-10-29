/* Hash routes, such as #/queue/B021/BANK_MISMATCH/bank_account: they work behind any proxy prefix and need no server
   routes. Moving between views changes location.hash; a choice made inside a view (a row, a document, a tab, a page)
   replaces the current history entry instead, so the back button isn't flooded. */

const latest = {};  // view -> its most recent hash, so the nav returns to where the user was
const listeners = [];
let renderer = () => {};

/** {view, parts} from location.hash, with each segment URI-decoded. */
export function currentRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decode);
  return {view: parts[0] || "", parts: parts.slice(1)};
}

/** "#/view/part/…", each segment URI-encoded; missing trailing parts are left out. */
export function routeHash(view, ...parts) {
  while (parts.length && (parts.at(-1) === null || parts.at(-1) === undefined || parts.at(-1) === "")) parts.pop();
  return `#/${[view, ...parts].map(part => encodeURIComponent(String(part))).join("/")}`;
}

export function navigate(view, ...parts) {
  location.hash = routeHash(view, ...parts);
}

export function replaceRoute(view, ...parts) {
  const hash = routeHash(view, ...parts);
  if (hash !== location.hash) history.replaceState(history.state, "", hash);
  noteRoute();
}

/** Records the current hash as its view's latest and tells the listeners (the nav). */
export function noteRoute() {
  const route = currentRoute();
  latest[route.view] = location.hash;
  for (const listener of listeners) listener(route);
}

export function latestHash(view) {
  return latest[view] || `#/${view}`;
}

export function onRouteNoted(listener) {
  listeners.push(listener);
}

/** main.js registers how to draw the current route; rerender() redraws it, e.g. after a job changed the data. */
export function setRenderer(fn) {
  renderer = fn;
}

export function rerender() {
  renderer();
}

function decode(part) {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}
