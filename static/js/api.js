/* Talking to app.py: JSON in and out (relative URLs, so the app works behind any proxy prefix), background jobs, a
   small cache of GET answers, and this tab's Mistral API key. */

const JOB_POLL_MS = 700;
const JOB_GIVE_UP_MS = 15 * 60 * 1000;
// The only requests that may carry the tab's key.
const KEY_PATHS = [/^api\/ingest$/, /^api\/docs\/[^/]+\/chat$/, /^api\/mistral\/check$/];

// The key pasted in the sidebar lives only in this variable: never in storage, cookies, the URL or the page, so a
// reload forgets it.
let tabKey = null;
const cache = new Map();  // path -> promise of the parsed answer

// ---------- the tab's Mistral key ----------

export function setTabKey(key) {
  tabKey = key;
}

export function forgetTabKey() {
  tabKey = null;
}

/** The last four characters of the tab's key, or null without one. */
export function tabKeyEnding() {
  return tabKey ? tabKey.slice(-4) : null;
}

/** POST api/mistral/check with a candidate key: {ok, ocr_model, ocr_available}. */
export function checkKey(candidate) {
  return request("api/mistral/check", {method: "POST", key: candidate});
}

// ---------- requests ----------

export function getJSON(path) {
  return request(path);
}

/** GET through the cache: the same path answers from memory until dropCache(). */
export function cachedJSON(path) {
  if (!cache.has(path)) {
    const answer = getJSON(path);
    cache.set(path, answer);
    answer.catch(() => {
      if (cache.get(path) === answer) cache.delete(path);  // a failure is never cached
    });
  }
  return cache.get(path);
}

export function dropCache() {
  cache.clear();
}

export function postJSON(path, body) {
  if (body === undefined) return request(path, {method: "POST"});
  return request(path, {method: "POST", body: JSON.stringify(body), headers: {"Content-Type": "application/json"}});
}

export function postForm(path, formData) {
  return request(path, {method: "POST", body: formData});
}

/** A page image: api/docs/{doc}/pages/{n}.png, versioned by the file's hash so the browser may keep it. */
export function pageImage(doc, n) {
  const version = (doc.sha256 || "").slice(0, 12);
  return `api/docs/${encodeURIComponent(doc.doc_id)}/pages/${n}.png?dpi=160&v=${version}`;
}

async function request(path, {method = "GET", body, headers = {}, key} = {}) {
  if (method === "POST") {
    headers = {...headers, "X-DocAI": "1"};  // the server's CSRF check
    const sendKey = key ?? tabKey;
    if (sendKey !== null && KEY_PATHS.some(re => re.test(path))) headers["X-Mistral-Key"] = sendKey;
  }
  let response;
  try {
    response = await fetch(path, {method, body, headers, credentials: "same-origin"});
  } catch (error) {
    throw apiError(`Can't reach the app server (${error.message}).`, 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw apiError(data?.error || response.statusText || `HTTP ${response.status}`, response.status);
  }
  return data;
}

function apiError(message, status) {
  return Object.assign(new Error(message), {status});
}

// ---------- jobs ----------

/** Waits for a job started by a POST that answered {job_id}: polls api/jobs/{id}, passing each snapshot to
    onUpdate, and returns the job's result or throws its error. The client cache is dropped either way, because
    the job may have changed what the views show. */
export async function runJob(started, onUpdate = () => {}) {
  const {job_id: id} = await started;
  const giveUp = Date.now() + JOB_GIVE_UP_MS;
  try {
    for (;;) {
      const job = await getJSON(`api/jobs/${encodeURIComponent(id)}`);
      onUpdate(job);
      if (job.state === "done") return job.result;
      if (job.state === "error") throw apiError(job.error || "The job failed.", 500);
      if (Date.now() > giveUp) throw apiError("Gave up waiting for the job after 15 minutes.", 0);
      await new Promise(resolve => setTimeout(resolve, JOB_POLL_MS));
    }
  } finally {
    dropCache();
  }
}
