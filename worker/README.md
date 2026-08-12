# Deploying the OpenFIN Worker — no terminal, no code

This holds the GitHub token so no phone has to. The app POSTs here, this runs
the engine. **All of it is done by clicking in a browser.**

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
**Create Worker**.

- Name it `openfin`
- **Deploy** (it deploys a placeholder — that's expected)
- **Edit code**

Delete everything in the editor. Open **`worker/index.js`** in this repository,
click **Copy raw file**, paste it in, and click **Deploy**.

Copy the URL shown at the top — something like
`https://openfin.<your-subdomain>.workers.dev`. You need it twice more.

> `index.js` carries the repository name and allowed origin as defaults in the
> code, so there are no variables to configure. Only the secret below.

---

## 3. Add the token as a secret

Still in the Worker → **Settings** → **Variables and Secrets** → **Add**.

| Field | Value |
|---|---|
| Type | **Secret** — not Text |
| Name | `GITHUB_TOKEN` |
| Value | the token from step 1 |

**Deploy** again so the secret takes effect.

> Type **Secret** matters. A Text variable is readable afterwards in the
> dashboard; a Secret is write-only.

---

## 4. Put Cloudflare Access in front of it

This is what replaces the token on your phone. **Without this the Worker
refuses every request**, by design — see the note at the bottom.

Cloudflare dashboard → **Zero Trust** (left sidebar) → if it asks you to choose
a plan, pick **Free** → **Access** → **Applications** → **Add an application**
→ **Self-hosted**.

| Field | Value |
|---|---|
| Application name | `OpenFIN` |
| Session duration | 24 hours |
| Public hostname | the Worker hostname, e.g. `openfin.<sub>.workers.dev` |

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
| Email code prompt, then `{"error":"POST only"}` | ✅ **Correct.** Access is in front and the Worker is alive. |
| `{"error":"POST only"}` with no email prompt | Access is not in front — redo step 4. |
| `{"error":"Cloudflare Access is not in front…"}` | Same; the Worker is refusing to accept unauthenticated writes. |
| `{"error":"GITHUB_TOKEN is not set…"}` | Step 3 was missed, or Deploy was not clicked after adding it. |

---

## 6. Tell the app where the Worker is

Open the app. Because the address is not yet set, a **One-time setup** box is
sitting at the top. Paste the Worker URL into it and tap **Save**. That is the
whole step — no file to edit, nothing to commit.

The address is stored on that device, so do the same on the second phone. It is
not a secret; it is only where to send the request, and the Worker still refuses
anyone Access has not signed in.

To set it once for every device instead, edit **`app.json`** on github.com
(pencil icon) and put the URL in `worker_url`:

```json
"worker_url": "https://openfin.your-subdomain.workers.dev"
```

Commit, wait a minute for Pages to rebuild, and any phone that has not had the
address pasted in picks it up. A device that *has* had it pasted in keeps its
own value.

Either way, then update your balance. The first write on each phone asks for an
email code.

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
