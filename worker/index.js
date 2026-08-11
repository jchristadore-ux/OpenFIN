/**
 * OpenFIN write-proxy.
 *
 * GitHub Pages is static, so the app cannot write anything on its own. Rather
 * than put a GitHub token on each phone, this Worker holds one as a server-side
 * secret and is the only thing that ever sees it. The app POSTs here; this
 * fires a repository_dispatch; the workflow runs the engine.
 *
 * Auth is Cloudflare Access, configured in the dashboard rather than in code.
 * Access terminates in front of the Worker, so by the time a request arrives it
 * has already been through email one-time-PIN. The Cf-Access-Jwt-Assertion
 * header below is a defence-in-depth check that Access is actually in front —
 * if the Worker is ever exposed without it, requests are refused rather than
 * silently accepted.
 *
 * Routes:
 *   POST /balance  {"balance": "4382.17"}
 *   POST /bills    {"edits": [{"id": "netflix", "amount": "21.31", "due_day": 23}]}
 *
 * Secrets (wrangler secret put):
 *   GITHUB_TOKEN   fine-grained PAT, Contents: Read and write on the repo
 * Vars (wrangler.toml):
 *   REPO           "owner/name"
 *   ALLOWED_ORIGIN the Pages origin allowed to call this
 *   REQUIRE_ACCESS  "true" to refuse requests without an Access JWT
 */

const MONEY = /^-?\d+(\.\d{1,2})?$/;
const ID = /^[a-z0-9][a-z0-9-]{0,48}$/;

function cors(env, extra = {}) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    ...extra,
  };
}

function json(env, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: cors(env, { "Content-Type": "application/json" }),
  });
}

/** Reject anything that is not a plain money string. */
function cleanMoney(raw) {
  const s = String(raw ?? "").replace(/[$,\s]/g, "");
  return MONEY.test(s) ? s : null;
}

async function dispatch(env, event_type, client_payload) {
  const res = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "openfin-worker",
    },
    body: JSON.stringify({ event_type, client_payload }),
  });
  return res;
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }
    if (request.method !== "POST") {
      return json(env, 405, { error: "POST only" });
    }

    // Defence in depth: refuse if Access is not in front of us.
    if (
      String(env.REQUIRE_ACCESS ?? "true") === "true" &&
      !request.headers.get("Cf-Access-Jwt-Assertion")
    ) {
      return json(env, 401, {
        error:
          "Cloudflare Access is not in front of this Worker. Refusing rather " +
          "than accepting unauthenticated writes.",
      });
    }

    if (!env.GITHUB_TOKEN || !env.REPO) {
      return json(env, 500, { error: "Worker is missing GITHUB_TOKEN or REPO" });
    }

    const url = new URL(request.url);
    let body;
    try {
      body = await request.json();
    } catch {
      return json(env, 400, { error: "body must be JSON" });
    }

    // ---- balance ---------------------------------------------------------
    if (url.pathname === "/balance") {
      const balance = cleanMoney(body.balance);
      if (balance === null) {
        return json(env, 400, { error: `'${body.balance}' is not a balance` });
      }
      const res = await dispatch(env, "balance", { balance });
      if (res.status !== 204) {
        return json(env, 502, { error: `GitHub returned ${res.status}` });
      }
      return json(env, 202, { ok: true, balance });
    }

    // ---- bill edits ------------------------------------------------------
    if (url.pathname === "/bills") {
      const edits = Array.isArray(body.edits) ? body.edits : null;
      if (!edits || edits.length === 0 || edits.length > 60) {
        return json(env, 400, { error: "edits must be 1-60 items" });
      }
      const clean = [];
      for (const e of edits) {
        if (!ID.test(String(e.id ?? ""))) {
          return json(env, 400, { error: `bad bill id: ${e.id}` });
        }
        const out = { id: e.id };
        if (e.amount !== undefined && e.amount !== null && e.amount !== "") {
          const amt = cleanMoney(e.amount);
          if (amt === null || Number(amt) < 0) {
            return json(env, 400, { error: `bad amount for ${e.id}: ${e.amount}` });
          }
          out.amount = amt;
        }
        if (e.due_day !== undefined && e.due_day !== null && e.due_day !== "") {
          const d = Number(e.due_day);
          if (!Number.isInteger(d) || d < 1 || d > 31) {
            return json(env, 400, { error: `bad due_day for ${e.id}: ${e.due_day}` });
          }
          out.due_day = d;
        }
        if (e.active !== undefined) out.active = Boolean(e.active);
        if (Object.keys(out).length === 1) continue;   // nothing actually changed
        clean.push(out);
      }
      if (clean.length === 0) return json(env, 400, { error: "no changes supplied" });

      const res = await dispatch(env, "bills", { edits: JSON.stringify(clean) });
      if (res.status !== 204) {
        return json(env, 502, { error: `GitHub returned ${res.status}` });
      }
      return json(env, 202, { ok: true, count: clean.length });
    }

    return json(env, 404, { error: "unknown route" });
  },
};
