// Checks the Worker against a stubbed GitHub, with no network and no secrets.
//
// It earns its place because this file is deployed by pasting it into a
// dashboard and clicking Deploy, with no build and no way to try it first. The
// last fault here was invisible from the app: writes failed with "Failed to
// fetch" and nothing said why.
//
//     node --test worker/          (or: node worker/index.test.mjs)
import assert from "node:assert/strict";
import worker from "./index.js";

const ENV = { GITHUB_TOKEN: "t0ken" };
const ACCESS = { "Cf-Access-Jwt-Assertion": "jwt" };
const PAGES = "https://jchristadore-ux.github.io";
const SELF = "https://openfin.christadore.workers.dev";

let calls = [];
let ghStatus = 204;
let ghBody = "";
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), init });
  if (String(url).includes("/contents/")) {
    const file = String(url).split("/contents/")[1].split("?")[0];
    const body = file === "index.html"
      ? "<html><head><!--OPENFIN_HOST--></head><body>app</body></html>"
      : `{"file":"${file}"}`;
    return new Response(ghStatus === 204 ? body : "nope", { status: ghStatus === 204 ? 200 : ghStatus });
  }
  return new Response(ghStatus === 204 ? null : ghBody, { status: ghStatus });
};

const req = (path, opts = {}) =>
  new Request(SELF + path, {
    method: opts.method || "POST",
    headers: { ...(opts.access === false ? {} : ACCESS), ...(opts.headers || {}) },
    body: opts.body,
  });

const run = (r, env = ENV) => worker.fetch(r, env);
let passed = 0;
async function t(name, fn) {
  calls = []; ghStatus = 204; ghBody = "";
  try { await fn(); passed++; console.log("  ok  " + name); }
  catch (e) { console.log("FAIL  " + name + "\n      " + e.message); process.exitCode = 1; }
}

// ---- serving the app -------------------------------------------------------
await t("GET / serves index.html with the host marker injected", async () => {
  const res = await run(req("/", { method: "GET" }));
  assert.equal(res.status, 200);
  const html = await res.text();
  assert.match(html, /window\.OPENFIN_WORKER_HOSTED=true/);
  assert.doesNotMatch(html, /<!--OPENFIN_HOST-->/);
  assert.match(res.headers.get("Content-Type"), /text\/html/);
});

await t("GET /snapshot.json is served and never cached", async () => {
  const res = await run(req("/snapshot.json", { method: "GET" }));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  assert.equal(JSON.parse(await res.text()).file, "snapshot.json");
});

await t("GET of anything not on the allowlist is refused", async () => {
  const res = await run(req("/bills.json", { method: "GET" }));
  assert.equal(res.status, 404);
});

await t("a token that cannot read the repo explains itself", async () => {
  ghStatus = 404;
  const res = await run(req("/", { method: "GET" }));
  assert.equal(res.status, 502);
  assert.match(await res.text(), /expired or lost access/);
});

// ---- writes ----------------------------------------------------------------
await t("a text/plain balance is accepted and dispatched", async () => {
  const res = await run(req("/balance", {
    headers: { "Content-Type": "text/plain;charset=UTF-8" },
    body: JSON.stringify({ balance: "$4,382.17" }),
  }));
  assert.equal(res.status, 202);
  assert.equal(JSON.parse(await res.text()).balance, "4382.17");
  const gh = calls.find((c) => c.url.endsWith("/dispatches"));
  assert.equal(JSON.parse(gh.init.body).event_type, "balance");
});

await t("deferrals dispatch the whole ticked set", async () => {
  const items = [{ bill_id: "braces", date: "2026-08-15" }];
  const res = await run(req("/defer", { body: JSON.stringify({ items }) }));
  assert.equal(res.status, 202);
  const gh = calls.find((c) => c.url.endsWith("/dispatches"));
  const payload = JSON.parse(gh.init.body);
  assert.equal(payload.event_type, "defer");
  assert.deepEqual(JSON.parse(payload.client_payload.items), items);
});

await t("an empty deferral list is a legitimate clear-everything", async () => {
  const res = await run(req("/defer", { body: JSON.stringify({ items: [] }) }));
  assert.equal(res.status, 202);
  assert.equal(JSON.parse(await res.text()).count, 0);
});

await t("bill edits dispatch only what changed", async () => {
  const res = await run(req("/bills", {
    body: JSON.stringify({ edits: [{ id: "netflix", amount: "21.31" }, { id: "hulu" }] }),
  }));
  assert.equal(res.status, 202);
  assert.equal(JSON.parse(await res.text()).count, 1);
});

await t("a bad amount is refused before it reaches GitHub", async () => {
  const res = await run(req("/balance", { body: JSON.stringify({ balance: "lots" }) }));
  assert.equal(res.status, 400);
  assert.equal(calls.filter((c) => c.url.endsWith("/dispatches")).length, 0);
});

await t("an expired token is named, not reduced to a status code", async () => {
  ghStatus = 404; ghBody = JSON.stringify({ message: "Not Found" });
  const res = await run(req("/balance", { body: JSON.stringify({ balance: "10" }) }));
  assert.equal(res.status, 502);
  const err = JSON.parse(await res.text()).error;
  assert.match(err, /expired token looks exactly like this/);
  assert.match(err, /Not Found/);
});

// ---- income ----------------------------------------------------------------
await t("adding income dispatches the four answers and nothing else", async () => {
  const add = { name: " Side work ", amount: "$1,250.50", frequency: "weekly", date: "2026-08-21" };
  const res = await run(req("/income", { body: JSON.stringify({ add }) }));
  assert.equal(res.status, 202);
  const gh = calls.find((c) => c.url.endsWith("/dispatches"));
  const payload = JSON.parse(gh.init.body);
  assert.equal(payload.event_type, "income");
  assert.deepEqual(JSON.parse(payload.client_payload.add), {
    name: "Side work", amount: "1250.50", frequency: "weekly", date: "2026-08-21",
  });
  // The id, the date plumbing and the review flag are the engine's to decide.
  assert.equal(payload.client_payload.edits, undefined);
});

await t("every frequency the app offers is accepted", async () => {
  for (const frequency of ["once", "weekly", "biweekly", "semimonthly",
                           "monthly", "quarterly", "annual"]) {
    const res = await run(req("/income", {
      body: JSON.stringify({ add: { name: "Side work", amount: "10", frequency, date: "2026-08-21" } }),
    }));
    assert.equal(res.status, 202, frequency);
  }
});

await t("a made-up frequency never reaches GitHub", async () => {
  const res = await run(req("/income", {
    body: JSON.stringify({ add: { name: "Side work", amount: "10", frequency: "fortnightly", date: "2026-08-21" } }),
  }));
  assert.equal(res.status, 400);
  assert.equal(calls.filter((c) => c.url.endsWith("/dispatches")).length, 0);
});

await t("a nameless, zero, or undated income is refused", async () => {
  // A valid baseline, so each case fails for the reason it names and not on
  // some other field.
  const base = { name: "Side work", amount: "10", frequency: "weekly", date: "2026-08-21" };
  for (const bad of [{ name: "" }, { amount: "0" }, { amount: "-5" }, { date: "soon" }]) {
    const res = await run(req("/income", { body: JSON.stringify({ add: { ...base, ...bad } }) }));
    assert.equal(res.status, 400, JSON.stringify(bad));
  }
  assert.equal(calls.filter((c) => c.url.endsWith("/dispatches")).length, 0);
});

await t("removing an income sends only the id and the flag", async () => {
  const res = await run(req("/income", {
    body: JSON.stringify({ edits: [{ id: "side-work", active: false, amount: "99999" }] }),
  }));
  assert.equal(res.status, 202);
  const payload = JSON.parse(calls.find((c) => c.url.endsWith("/dispatches")).init.body);
  assert.deepEqual(JSON.parse(payload.client_payload.edits), [{ id: "side-work", active: false }]);
});

await t("an empty income request is refused", async () => {
  const res = await run(req("/income", { body: JSON.stringify({}) }));
  assert.equal(res.status, 400);
});

// ---- CORS, for the Pages copy ---------------------------------------------
await t("a cross-site write from Pages is allowed WITH credentials", async () => {
  const res = await run(req("/balance", {
    headers: { Origin: PAGES },
    body: JSON.stringify({ balance: "10" }),
  }));
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), PAGES);
  assert.equal(res.headers.get("Access-Control-Allow-Credentials"), "true");
  assert.equal(res.headers.get("Vary"), "Origin");
});

await t("an unknown origin gets no CORS grant at all", async () => {
  const res = await run(req("/balance", {
    headers: { Origin: "https://evil.example" },
    body: JSON.stringify({ balance: "10" }),
  }));
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), null);
});

await t("preflight answers before the Access check, since it carries no cookie", async () => {
  const res = await run(req("/balance", { method: "OPTIONS", access: false, headers: { Origin: PAGES } }));
  assert.equal(res.status, 204);
  assert.equal(res.headers.get("Access-Control-Allow-Credentials"), "true");
});

// ---- failing closed --------------------------------------------------------
await t("without Access in front, the app is not served either", async () => {
  const res = await run(req("/", { method: "GET", access: false }));
  assert.equal(res.status, 401);
  assert.match(JSON.parse(await res.text()).error, /Access is not in front/);
});

await t("a missing token is reported before anything else", async () => {
  const res = await run(req("/", { method: "GET" }), {});
  assert.equal(res.status, 500);
  assert.match(JSON.parse(await res.text()).error, /GITHUB_TOKEN is not set/);
});

console.log(`\n${passed} worker checks passed`);
