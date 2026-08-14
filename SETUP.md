# Setup

Everything here happens in a web browser. No terminal, no installs, no code.

There are four things to set up, in this order. Each one works on its own, so a
half-finished setup is never a broken one:

| # | Thing | Without it |
|---|---|---|
| 1 | Repository secrets | No daily summary email goes out |
| 2 | GitHub Pages | No read-only copy of the app |
| 3 | The Worker | The app can show figures but not update them |
| 4 | The Worker's address in `app.json` | The Pages copy cannot point you at the one that saves |

> **The app you actually use is the Worker's address**, not the github.io one.
> The Worker serves the same dashboard, and only that copy can save — the
> browser will not send a sign-in cookie to a page on another site. Details in
> [worker/README.md](worker/README.md).

---

## 1. Repository secrets

**Settings → Secrets and variables → Actions → New repository secret.** Names
must match exactly.

| Secret | Format | Where it comes from |
|---|---|---|
| `SMTP_USER` | `you@gmail.com` | Your Gmail address |
| `SMTP_APP_PASSWORD` | 16 characters, e.g. `abcd efgh ijkl mnop` | Below |
| `EMAIL_RECIPIENTS` | `you@gmail.com,wife@gmail.com` | Comma separated, no spaces |
| `EMAIL_FROM` | `you@gmail.com` | Usually the same as `SMTP_USER` |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-…` | Optional — only for bill screenshots |

**The app password:** turn on 2-Step Verification at
<https://myaccount.google.com/security>, then go to
<https://myaccount.google.com/apppasswords>, type any name, click **Create**.
Google shows the 16 characters once. Your normal Gmail password will not work
here — Google rejects it for SMTP.

**The API key** is only used when you drop a bill screenshot into `inbox/`.
Everything else runs without it. Get one at <https://console.anthropic.com>;
a screenshot costs well under a cent.

---

## 2. GitHub Pages

This is what puts the app at a URL you can open on a phone.

**Settings → Pages.**

| Field | Value |
|---|---|
| Source | **Deploy from a branch** |
| Branch | **`main`** |
| Folder | **`/ (root)`** — *not* `/docs`, *not* `/plan` |

Click **Save**. Give it a minute or two, then reload the Settings → Pages page:
it shows a green tick and the address, which will be

```
https://<your-username>.github.io/OpenFIN/
```

Open that on your phone to check it renders. **Do not add it to the home screen
yet** — once the Worker exists in step 3, its address serves the same app and is
the one that can save. Put that on the home screen instead. This copy is a
read-only fallback, and says so at the top of the page.

### If the page is blank, 404s, or shows a README

| What you see | Cause | Fix |
|---|---|---|
| **404** | Pages is off, or pointed at a branch with no `index.html` | Set branch `main`, folder `/ (root)` as above |
| A page of **plain text starting "# OpenFIN"** | Folder is set to `/plan`, which holds only a README | Change the folder to `/ (root)` |
| **"No forecast available"** | Pages is fine; `snapshot.json` has not been built | Enter a balance, or **Actions → Daily balance → Run workflow** |
| Old figures that will not change | Browser cache | Pull down to refresh; the app already cache-busts, so this is rare |
| Nothing at all after a commit | Pages is still building | **Actions** tab → wait for the `pages build and deployment` run |

The repository must stay **public** for Pages to serve it on a free account.
Nothing sensitive lives here: the bills and balances are in the repository, but
no credential is — the tokens are all in Actions secrets and in Cloudflare,
neither of which is readable from the repository.

### Why `/plan` breaks it

`plan/` held the original design notes and is the folder Pages defaults to if
it was ever selected. It contains a README and no `index.html`, so Pages
renders the README as the whole site. The app is `index.html` at the root, so
the root folder is the correct setting.

---

## 3. The Worker

Separate walkthrough, because it is the longest: **[worker/README.md](worker/README.md)**.

Short version: it is a small Cloudflare Worker that holds a GitHub token so
your phone does not have to. **It also serves the app.** You open its address,
it signs you in by emailed code, and everything you change posts back to the
same address. Free, and about fifteen minutes of clicking.

It serves the app rather than only accepting writes because a page on
github.io cannot send the sign-in cookie that belongs to the Worker's address —
Safari blocks another site's cookie, and no setting on either end changes that.
That is why saving from the Pages copy failed with "Failed to fetch".

Until the Worker exists, the Pages copy still shows every figure — it just
cannot update them.

---

## 4. Day to day

### Update the balance

Open the app, type today's actual bank balance, tap **Update**. That is the only
input the household is responsible for, and it is what runs everything:

```
balance entered → engine runs → snapshot rebuilt → daily summary emailed
```

There is no separate "balance received" email and nothing to reply to.

### Mark a bill as deferred

Tick its box in the app. **Available to spend** updates as you tick, before you
save anything, so you can try combinations and see the effect. **Save** commits
it.

A deferred bill is marked, never deleted — the money is still owed and the app
keeps showing it under what is deferred.

### Change a bill's amount or date

Either tell Claude in a chat, or edit it in the app and save. Both end up in
`bills.json` after the same server-side validation: a change of more than 40%,
or an amount above $10,000, is applied but flagged for review, and nothing is
ever deleted by an edit.

### Upload a bill screenshot

Click **inbox** → **Add file → Upload files** → drag in a PNG, JPG, `.xlsx` or
`.csv` → **Commit changes**. The ingest workflow starts on its own and emails
you what changed. A bill missing from the upload is switched off with a note,
never deleted, so a bad photo cannot wipe your mortgage.

### Risk

The risk sweep runs twice a day on its own (about 7am and 5pm Eastern) and **does
not need a balance entry** — that is the point of it. It rebuilds the forecast
and publishes it, so the app's **Risk** tile is up to date whether or not anyone
opened it.

**It does not email.** The daily summary is the only email that sends, and it
carries the risk list itself. To turn alert emails back on, edit `config.json`
on github.com and set `risk_emails_enabled` to `true` — nothing else needs to
change, the credentials are already wired up.

---

## 5. Checking it works

**Actions → Daily balance → Run workflow ▾**, enter a balance, run it. Open the
finished run and expand the steps: you will see the engine's figures and whether
the email sent.

| Symptom | Meaning |
|---|---|
| `EMAIL_RECIPIENTS is not set` | Step 1 is incomplete |
| `SMTP authentication failed` | The app password is wrong, or a normal Gmail password was used |
| Run is green, no email | Check spam, then that `EMAIL_RECIPIENTS` has no spaces. Note only the *daily summary* emails; risk alerts are off by default. |
| Workflow never starts when you tap Update in the app | The Worker — see its README's symptom table |

---

## 6. Changing the schedule

The risk sweep runs from `.github/workflows/watch.yml`. Two cron lines are set
(11:00 and 21:00 UTC) so the times hold across daylight saving. Editing them on
github.com is enough; nothing else needs to change. It refreshes what the app
shows; it does not email unless `risk_emails_enabled` is turned on.

The daily summary has no schedule by design. It sends when you enter a balance,
because a summary anchored to yesterday's balance reads as authoritative and is
quietly wrong.
