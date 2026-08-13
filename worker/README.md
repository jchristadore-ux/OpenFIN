# Deploying the OpenFIN Worker — no terminal, no code

This holds the GitHub token so no phone has to, **and it serves the app**. The
app POSTs here, this runs the engine. **All of it is done by clicking in a
browser.**

> **Open OpenFIN at the Worker address, not the github.io one.** The Worker
> serves the same dashboard, and only that copy can save anything. See
> [Why the app is served from here](#why-the-app-is-served-from-here).

Free throughout: Workers gives 100,000 requests/day and Cloudflare Access is
free for up to 50 users. A household uses a handful a day.

Roughly 15 minutes. Do the steps in order — step 4 depends on step 3.

---

## 1. Make the GitHub token

github.com → your avatar → **Settings** → scroll to **Developer settings** →
**Personal access tokens** → **Fine-grained tokens** → **Generate new token**.

| Field | Value |
|---|---|
| Token name | `OpenFIN Worker` |
| Expiration | 1 year (put a reminder in your calendar) |
| Repository access | **Only select repositories** → `OpenFIN` |
| Permissions → Repository → **Contents** | **Read and write** |

Generate, then **copy it now** — GitHub shows it once.

> Contents: Read and write is the whole permission set. It cannot touch your
> other repositories, and it cannot change repository settings.

---

## 2. Create the Worker

dash.cloudflare.com → sign up or log in → **Workers & Pages** → **Create** →
**Create Worker**. Name it `openfin`.

Cloudflare offers two ways to get the code in. **Either works** — pick one.

### 2a. Connect this repository (recommended)

Cloudflare builds and deploys straight from GitHub, so future changes to the
Worker deploy themselves and you never open Cloudflare again.

Connect to `jchristadore-ux/OpenFIN`, then set:

| Setting | Value |
|---|---|
| Deploy command | `npx wrangler deploy` |
| Build command | *(none)* |
| **Root directory** | **`worker`** |
| Branch | `main` |

**Root directory `worker` is the one people get wrong.** `wrangler.toml` lives
in `worker/`, not at the top of the repository. Left at `/`, the build fails
with a missing-config error that does not mention the root directory at all.

Optionally set **Build watch paths** to `worker/*`. The engine commits to
`main` every time you enter a balance, and without this every one of those
triggers a rebuild of a Worker whose code did not change.

### 2b. Paste it in by hand

**Deploy** the placeholder Worker, then **Edit code**. Delete everything in the
editor. Open **`worker/index.js`** in this repository, click **Copy raw file**,
paste it in, **Deploy**.

Simpler to start, but every future change means pasting again.

---

Either way, copy the URL from the **Overview** tab — something like
`https://openfin.<your-subdomain>.workers.dev`. You need it twice more.

> `index.js` carries the repository name and allowed origin as defaults in the
> code, so there are no variables to configure. Only the secret below.

---

## 3. Add the token as a secret

**In Cloudflare, not GitHub.** Still in the Worker → **Settings** →
**Variables and Secrets** → **Add**.

| Field | Value |
|---|---|
| Type | **Secret** — not Text |
| Name | `GITHUB_TOKEN` |
| Value | the token from step 1 |

**Deploy** again so the secret takes effect.

> **If GitHub says "secret names must not start with GITHUB_", you are in the
> wrong dashboard.** GitHub reserves that prefix for its own Actions secrets and
> refuses to create one. Cloudflare has no such rule. The token belongs on
> Cloudflare because that is the entire point of the Worker — GitHub is the
> thing being called, so it does not need a copy of the key used to call it.

> Type **Secret** matters. A Text variable is readable afterwards in the
> dashboard; a Secret is write-only.

> Nothing goes in **Settings → Secrets and variables → Actions** on GitHub for
> the Worker. That page is for the engine's email credentials, listed in
> [../SETUP.md](../SETUP.md).

---

## 4. Put Cloudflare Access in front of it

This is what replaces the token on your phone. **Without this the Worker
refuses every request**, by design — see the note at the bottom.

Cloudflare dashboard → **Zero Trust** (left sidebar) → if it asks you to choose
a plan, pick **Free** → **Access** → **Applications** → **Add an application**.

On the "Select an application type" screen, stay on the **Self-hosted and
private** tab, then pick the **Workers** sub-tab from the row that reads
*Private destinations · Workers · Public DNS · Service auth*.

> **Not "Public DNS"**, which is often selected by default. That route asks for
> a domain you own and have on Cloudflare. A `workers.dev` address is not one —
> Cloudflare owns that domain — so the form cannot be completed. The **Workers**
> sub-tab exists for exactly this case, as the screen's own description says:
> destinations may be "Cloudflare Workers serverless applications".

**Continue with Self-hosted and private** → choose **`openfin`** as the
destination.

| Field | Value |
|---|---|
| Application name | `OpenFIN` |
| Session duration | 24 hours |
| Destination | the `openfin` Worker |

Next → add a policy:

| Field | Value |
|---|---|
| Policy name | `Household` |
| Action | **Allow** |
| Include | selector **Emails** → your address, and your wife's |

Next → under login methods make sure **One-time PIN** is enabled → **Add
application**.

---

## 5. Check it worked

Open the Worker URL in a browser.

| What you see | Meaning |
|---|---|
| Email code prompt, then the OpenFIN dashboard | ✅ **Correct.** Access is in front, the Worker is alive, and this is the address to use. |
| The dashboard with no email prompt | Access is not in front — redo step 4. The forecast is public as it stands. |
| `{"error":"Cloudflare Access is not in front…"}` | Same; the Worker is refusing to serve or accept anything. |
| `{"error":"GITHUB_TOKEN is not set…"}` | Step 3 was missed, or Deploy was not clicked after adding it. |
| `could not read index.html … GitHub returned 404` | The token expired or lost access to the repository. Redo step 1 and step 3. |

### If the build itself failed (route 2a only)

Deployments tab → the red build → read the last line of the log.

| Log says | Fix |
|---|---|
| `build token … has been deleted or rolled` | Settings → Build → **Build token** → create a new one → **Retry build** |
| Missing config, or `wrangler.toml not found` | Settings → Build → **Root directory** → `worker` |
| `workerd/jsg` or a syntax error | The paste was truncated. Use **Copy raw file**, not a selection from the rendered page. |

The build token and the root directory are separate faults that look like one.
A rolled token stops the build before it ever reads the root directory, so
fixing only the token gets you a second red build with a different message.
**How long the build ran tells them apart**: a token fault dies in the same
second it starts, because it never gets to do any work. Anything that runs for
twenty seconds and then fails got as far as the code.

### A red ✗ on a pull request while `main` deploys fine

These are different builds. Cloudflare builds the **production branch** and, by
default, **every other branch too**. A branch build cannot deploy — that would
put unreviewed code on the live Worker — so on some plans it fails, every time,
on every pull request, while `main` deploys perfectly.

**The tell is that the app keeps working.** If a feature that needs new Worker
code is live, `main` deployed, whatever the pull request's tick says.

Turn the noise off:

1. dash.cloudflare.com → **Workers & Pages** → **openfin**
2. **Settings** → **Build**
3. **Branch control** → **Non-production branches** → **None**
   *(or Excluded branches → `*`, depending on which the dashboard offers)*
4. **Build watch paths** → Include paths → `worker/*`
5. **Save**

Step 3 stops the failing pull-request builds. Step 4 stops the engine's own
commits triggering rebuilds — it writes to `main` every time a balance is
entered and twice a day for the risk watch, and none of those touch the Worker.
Neither step changes how `main` deploys.

---

## 6. Use the app at the Worker address

Open `https://openfin.<your-subdomain>.workers.dev` on each phone, enter the
email code, and **add that to the home screen**. That is the app. Balance
updates, bill edits and deferrals all save from there.

The github.io address still shows the forecast and is fine for a quick look,
but it cannot save. It says so at the top of the page and links here.

`app.json` still carries the Worker URL, so the github.io copy knows where to
send people. Edit it on github.com (pencil icon) if the address ever changes:

```json
"worker_url": "https://openfin.your-subdomain.workers.dev"
```

Then update your balance. The first visit on each phone asks for an email code;
after that the sign-in lasts as long as the session duration set in step 4.

---

## Why the app is served from here

The Worker used to only accept writes, with the dashboard loaded from GitHub
Pages. Every save failed with **"Failed to fetch. Nothing was saved."** Two
separate browser rules made it impossible, and neither is fixable from the page:

1. **The preflight never got through.** A cross-site POST sending JSON is
   preceded by an `OPTIONS` request, and preflights never carry cookies — that
   is fixed in the browser, not configurable. Access saw an unauthenticated
   request and answered with a login redirect. A preflight may not follow a
   redirect, so the real request was never sent.
2. **The sign-in cookie was never attached.** Access sets its cookie on the
   `workers.dev` host. To a page served from `github.io` that is a third-party
   cookie, and Safari — the browser on both phones — blocks those outright.

So the Worker serves the dashboard itself. Signing in is then an ordinary visit,
Access sets a first-party cookie, and every save is same-origin: no preflight,
no third-party cookie, nothing left for a browser to block.

It serves an allowlist — `index.html`, `snapshot.json`, `app.json` and the logo
— rather than proxying any path, because the token here can read the whole
repository.

Writes are `POST /balance`, `/bills`, `/defer` and `/income`. Each one is
shape-checked here and re-validated by the engine on the other side: the Worker
is a different codebase on a different host, and what is being written is the
household's financial model.

---

## Why it refuses when Access is missing

The Worker checks for the header Cloudflare Access adds, and returns 401 if it
is absent. If Access were ever removed or misconfigured, the alternative would
be an open endpoint that lets anyone who finds the URL write to your finances.
Failing closed is the safer wrong answer.

To test locally without Access, add a Text variable `REQUIRE_ACCESS` = `false`,
and delete it afterwards.

## Rotating the token

Generate a new one in step 1, then repeat step 3 with the new value and Deploy.
Nothing else changes.
