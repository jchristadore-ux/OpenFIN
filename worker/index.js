/**
 * OpenFIN write-proxy, and the place the app is served from.
 *
 * GitHub Pages is static, so the app cannot write anything on its own. Rather
 * than put a GitHub token on each phone, this Worker holds one as a server-side
 * secret and is the only thing that ever sees it. The app POSTs here; this
 * fires a repository_dispatch; the workflow runs the engine.
 *
 * WHY THIS ALSO SERVES THE DASHBOARD
 * It used to only accept writes, with the app itself loaded from GitHub Pages.
 * That cannot work, and failed as "Failed to fetch" with nothing saved:
 *
 *   1. A cross-site POST carrying Content-Type: application/json is preflighted.
 *      The browser sends OPTIONS with no cookies, ever. Cloudflare Access sees an
 *      unauthenticated request and answers with a login redirect. A preflight may
 *      not follow a redirect, so the real request was never sent.
 *   2. Even past that, the Access session cookie belongs to this workers.dev
 *      host, which makes it third-party to a page served from github.io. Safari
 *      blocks those outright and Chrome is going the same way, so the cookie was
 *      never attached and Access answered with a login redirect again.
 *
 * Neither is fixable from the page: no CORS header grants a cookie a browser has
 * decided not to send. So the app is served from this origin instead. Signing in
 * is then a normal top-level visit — Access shows its email-code page, sets a
 * first-party cookie — and every write afterwards is same-origin, with no
 * preflight and no third-party cookie involved.
 *
 * GitHub Pages still works for reading. Writes from there are still attempted,
 * with the CORS headers below, because a browser that does allow third-party
 * cookies will manage it — but the app tells the household where to go when it
 * cannot.
 *
 * Routes:
 *   GET  /              the dashboard, and /snapshot.json /app.json /assets/*
 *   POST /balance  {"balance": "4382.17"}
 *   POST /bills    {"edits": [{"id": "netflix", "amount": "21.31", "due_day": 23}]}
 *   POST /defer    {"items": [{"bill_id": "netflix", "date": "2026-08-23"}]}
 *
 * DEPLOYING WITHOUT A TERMINAL
 * Cloudflare's dashboard has a code editor, so this whole file can be pasted in
 * and deployed by clicking. See worker/README.md. The settings below are
 * defaults in code precisely so that the only thing that has to be added by
 * hand is the one secret:
 *
 *   GITHUB_TOKEN   fine-grained PAT, Contents: Read and write on the repo.
 *                  Add it in the dashboard under Settings -> Variables and
 *                  Secrets -> Add -> type Secret. Never a plaintext variable.
 *
 * Each default can still be overridden by a dashboard variable of the same
 * name, which is what an env value is checked for first below.
 */

const DEFAULTS = {
  REPO: "jchristadore-ux/OpenFIN",
  BRANCH: "main",
  // Comma-separated. Kept so the GitHub Pages copy can still write on browsers
  // that allow third-party cookies.
  ALLOWED_ORIGIN: "https://jchristadore-ux.github.io",
  REQUIRE_ACCESS: "true",
};

/** Dashboard variable if present, otherwise the default above. */
function cfg(env, key) {
  const v = env[key];
  return v === undefined || v === null || v === "" ? DEFAULTS[key] : v;
}

const MONEY = /^-?\d+(\.\d{1,2})?$/;
const ID = /^[a-z0-9][a-z0-9-]{0,48}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Files served from this origin so the app runs first-party. An allowlist, not
 * a passthrough: this Worker holds a token that can write the repository, and
 * an arbitrary-path proxy would happily serve anything the token can read.
 */
const STATIC = {
  "/": { file: "index.html", type: "text/html; charset=utf-8", cache: "no-store" },
  "/index.html": { file: "index.html", type: "text/html; charset=utf-8", cache: "no-store" },
  "/snapshot.json": { file: "snapshot.json", type: "application/json; charset=utf-8", cache: "no-store" },
  "/app.json": { file: "app.json", type: "application/json; charset=utf-8", cache: "no-store" },
  "/assets/mark.svg": { file: "assets/mark.svg", type: "image/svg+xml", cache: "public, max-age=3600" },
};

/**
 * Which origins may write cross-site. The origin is echoed rather than
 * wildcarded because a credentialed request refuses `*`.
 */
function allowedOrigin(env, request) {
  const origin = request.headers.get("Origin");
  if (!origin) return null;
  const list = String(cfg(env, "ALLOWED_ORIGIN"))
    .split(",")
    .map((s) => s.trim().replace(/\/+$/, ""))
    .filter(Boolean);
  if (origin === new URL(request.url).origin) return origin;   // same-origin
  return list.includes(origin) ? origin : null;
}

function cors(env, request, extra = {}) {
  const origin = allowedOrigin(env, request);
  const headers = { Vary: "Origin", ...extra };
  if (origin) {
    headers["Access-Control-Allow-Origin"] = origin;
    // Without this the browser discards the response of any request made with
    // credentials: 'include' and reports only "Failed to fetch". Its absence
    // was one of the reasons nothing ever saved.
    headers["Access-Control-Allow-Credentials"] = "true";
    headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
    headers["Access-Control-Allow-Headers"] = "Content-Type";
    headers["Access-Control-Max-Age"] = "86400";
  }
  return headers;
}

function json(env, request, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: cors(env, request, { "Content-Type": "application/json", "Cache-Control": "no-store" }),
  });
}

/** Reject anything that is not a plain money string. */
function cleanMoney(raw) {
  const s = String(raw ?? "").replace(/[$,\s]/g, "");
  return MONEY.test(s) ? s : null;
}

function ghHeaders(env, accept) {
  return {
    Accept: accept,
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "openfin-worker",
  };
}

async function dispatch(env, event_type, client_payload) {
  return fetch(`https://api.github.com/repos/${cfg(env, "REPO")}/dispatches`, {
    method: "POST",
    headers: { ...ghHeaders(env, "application/vnd.github+json"), "Content-Type": "application/json" },
    body: JSON.stringify({ event_type, client_payload }),
  });
}

/**
 * A dispatch that did not return 204 gets explained rather than reduced to a
 * status code. 404 here almost always means the token expired or lost access,
 * and "GitHub returned 404" sent nobody to the right place.
 */
async function dispatchFailed(env, request, res) {
  let detail = "";
  try {
    const body = await res.json();
    if (body && body.message) detail = ` GitHub said: ${body.message}`;
  } catch { /* empty or non-JSON body */ }
  const hint = {
    401: "The GITHUB_TOKEN secret is not valid. Generate a new fine-grained token and re-add it in Cloudflare.",
    403: "The GITHUB_TOKEN is not allowed to do this. It needs Contents: Read and write on the repository.",
    404: `Either the repository name is wrong or the token cannot see ${cfg(env, "REPO")}. An expired token looks exactly like this.`,
  }[res.status] || "GitHub did not accept the request.";
  return json(env, request, 502, { error: `${hint}${detail}` });
}

/** Serve one allowlisted repository file from this origin. */
async function serveStatic(env, request, entry) {
  const res = await fetch(
    `https://api.github.com/repos/${cfg(env, "REPO")}/contents/${entry.file}?ref=${cfg(env, "BRANCH")}`,
    { headers: ghHeaders(env, "application/vnd.github.raw"), cf: { cacheTtl: 0 } },
  );
  if (!res.ok) {
    return new Response(
      `OpenFIN could not read ${entry.file} from ${cfg(env, "REPO")} (GitHub returned ${res.status}). ` +
      `If this says 404, the GITHUB_TOKEN has expired or lost access to the repository.`,
      { status: 502, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } },
    );
  }
  let body = await res.arrayBuffer();

  // Tell the app it is running first-party, so it posts to this origin with
  // relative paths instead of the address in app.json. Done here rather than
  // sniffed in the browser, because the page has to know before the first write
  // and guessing it wrong is the failure this whole change exists to remove.
  if (entry.file === "index.html") {
    const html = new TextDecoder().decode(body).replace(
      "<!--OPENFIN_HOST-->",
      "<script>window.OPENFIN_WORKER_HOSTED=true;</script>",
    );
    body = new TextEncoder().encode(html);
  }

  return new Response(body, {
    headers: {
      "Content-Type": entry.type,
      "Cache-Control": entry.cache,
      "Referrer-Policy": "same-origin",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env, request) });
    }

    // Defence in depth: refuse if Access is not in front of us. This covers the
    // dashboard as well as the writes — served from here it carries the whole
    // household's finances, so it is not public either.
    if (
      String(cfg(env, "REQUIRE_ACCESS")) === "true" &&
      !request.headers.get("Cf-Access-Jwt-Assertion")
    ) {
      return json(env, request, 401, {
        error:
          "Cloudflare Access is not in front of this Worker. Refusing rather " +
          "than accepting unauthenticated requests.",
      });
    }

    if (!env.GITHUB_TOKEN) {
      return json(env, request, 500, {
        error:
          "GITHUB_TOKEN is not set. Add it in the Cloudflare dashboard under " +
          "Settings -> Variables and Secrets, as a Secret.",
      });
    }

    // ---- the app itself --------------------------------------------------
    if (request.method === "GET" || request.method === "HEAD") {
      const entry = STATIC[url.pathname];
      if (!entry) return json(env, request, 404, { error: "unknown route" });
      const res = await serveStatic(env, request, entry);
      if (request.method !== "HEAD") return res;
      return new Response(null, { status: res.status, headers: res.headers });
    }

    if (request.method !== "POST") {
      return json(env, request, 405, { error: "GET or POST only" });
    }

    // Parsed by hand rather than by Content-Type. The app labels its body
    // text/plain deliberately: that keeps the request "simple", so the browser
    // sends no preflight for Access to reject.
    let body;
    try {
      body = JSON.parse(await request.text());
    } catch {
      return json(env, request, 400, { error: "body must be JSON" });
    }
    if (!body || typeof body !== "object") {
      return json(env, request, 400, { error: "body must be a JSON object" });
    }

    // ---- balance ---------------------------------------------------------
    if (url.pathname === "/balance") {
      const balance = cleanMoney(body.balance);
      if (balance === null) {
        return json(env, request, 400, { error: `'${body.balance}' is not a balance` });
      }
      const res = await dispatch(env, "balance", { balance });
      if (res.status !== 204) return dispatchFailed(env, request, res);
      return json(env, request, 202, { ok: true, balance });
    }

    // ---- bill edits ------------------------------------------------------
    if (url.pathname === "/bills") {
      const edits = Array.isArray(body.edits) ? body.edits : null;
      if (!edits || edits.length === 0 || edits.length > 60) {
        return json(env, request, 400, { error: "edits must be 1-60 items" });
      }
      const clean = [];
      for (const e of edits) {
        if (!ID.test(String(e.id ?? ""))) {
          return json(env, request, 400, { error: `bad bill id: ${e.id}` });
        }
        const out = { id: e.id };
        if (e.amount !== undefined && e.amount !== null && e.amount !== "") {
          const amt = cleanMoney(e.amount);
          if (amt === null || Number(amt) < 0) {
            return json(env, request, 400, { error: `bad amount for ${e.id}: ${e.amount}` });
          }
          out.amount = amt;
        }
        if (e.due_day !== undefined && e.due_day !== null && e.due_day !== "") {
          const d = Number(e.due_day);
          if (!Number.isInteger(d) || d < 1 || d > 31) {
            return json(env, request, 400, { error: `bad due_day for ${e.id}: ${e.due_day}` });
          }
          out.due_day = d;
        }
        if (e.active !== undefined) out.active = Boolean(e.active);
        if (Object.keys(out).length === 1) continue;   // nothing actually changed
        clean.push(out);
      }
      if (clean.length === 0) return json(env, request, 400, { error: "no changes supplied" });

      const res = await dispatch(env, "bills", { edits: JSON.stringify(clean) });
      if (res.status !== 204) return dispatchFailed(env, request, res);
      return json(env, request, 202, { ok: true, count: clean.length });
    }

    // ---- deferrals -------------------------------------------------------
    if (url.pathname === "/defer") {
      const items = Array.isArray(body.items) ? body.items : null;
      if (!items || items.length > 200) {
        return json(env, request, 400, { error: "items must be a list of at most 200" });
      }
      const clean = [];
      for (const it of items) {
        if (!ID.test(String(it.bill_id ?? ""))) {
          return json(env, request, 400, { error: `bad bill id: ${it.bill_id}` });
        }
        if (!DATE.test(String(it.date ?? ""))) {
          return json(env, request, 400, { error: `bad date: ${it.date}` });
        }
        clean.push({ bill_id: it.bill_id, date: it.date });
      }
      // An empty list is legitimate: it means every box was unticked.
      const res = await dispatch(env, "defer", { items: JSON.stringify(clean) });
      if (res.status !== 204) return dispatchFailed(env, request, res);
      return json(env, request, 202, { ok: true, count: clean.length });
    }

    return json(env, request, 404, { error: "unknown route" });
  },
};
