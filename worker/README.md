# OpenFIN write-proxy

Holds the GitHub token so no phone has to. The app POSTs here, this fires the
engine. **No token is ever entered on a device.**

Free tier throughout — Workers gives 100,000 requests/day and Cloudflare Access
is free for up to 50 users. A household uses a handful of requests a day.

## Setup, once

**1. Create the GitHub token.**
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained.
Repository access: only `jchristadore-ux/TogetherLedger`. Permissions:
**Contents: Read and write**. Copy the token.

**2. Deploy the Worker.**

```bash
cd worker
npx wrangler login
npx wrangler secret put GITHUB_TOKEN     # paste the token
npx wrangler deploy
```

Wrangler prints a URL like `https://openfin.<your-subdomain>.workers.dev`.

**3. Put Cloudflare Access in front of it.**
Cloudflare dashboard → Zero Trust → Access → Applications → Add an application
→ Self-hosted. Point it at the Worker's hostname. Add a policy:

* Action: **Allow**
* Include → **Emails** → your address and your wife's

Choose **One-time PIN** as the login method. Each phone signs in once by email
code; the session lasts as long as you set it (24 hours is sensible).

**4. Tell the app where the Worker is.**
Edit `WORKER` at the top of the script in `index.html` to the Worker URL, then
commit. Until that is set, the app says so instead of failing silently.

## What it accepts

```
POST /balance   {"balance": "4382.17"}
POST /bills     {"edits": [{"id": "netflix", "amount": "21.31", "due_day": 23}]}
```

Everything is validated before it reaches GitHub: amounts must be plain money,
`due_day` must be 1–31, bill ids must match a safe slug pattern, and no more
than 60 edits arrive at once.

## Why the Access check is in the code too

The Worker refuses any request without a `Cf-Access-Jwt-Assertion` header.
Access normally adds it. If the Worker is ever deployed or reconfigured without
Access in front, requests fail closed rather than becoming an open write
endpoint for anyone who finds the URL. Set `REQUIRE_ACCESS = "false"` in
`wrangler.toml` only to test locally, and put it back.
