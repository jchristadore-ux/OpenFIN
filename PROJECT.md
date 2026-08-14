# OpenFIN — project state and handoff

A household cash-flow engine for one family. Not a budgeting app: it answers
"what is actually available to spend, given what is really going to leave the
account." Everything is derived from bank statements rather than estimates, and
nothing is assumed on the household's behalf.

**Owner does not code and has no terminal.** Every change reaches them as a
GitHub pull request or a settings toggle in a web dashboard. This constraint has
shaped most of the architecture and is not negotiable.

---

## 1. How it runs

There is no server and no database. GitHub Actions is the entire runtime, and
JSON files committed to this repository are the entire state.

```
phone (the Worker serves the app)  ->  same-origin POST  ->  repository_dispatch
                                                                    |
                                                             GitHub Actions
                                                                    |
                                        engine.py -> snapshot.json + email
                                                                    |
                                    committed back -> the Worker serves it again
```

**Entering the balance is the trigger for everything.** There is no scheduled
daily summary, deliberately: a summary anchored to a stale balance reads as
authoritative and is quietly wrong.

**The daily summary is the only email that sends.** The risk watch still runs
twice a day and still needs no balance, but it publishes rather than emails: it
rebuilds `snapshot.json`, so the app's Risk tile is current whether or not
anyone opened it, and the daily summary carries the risk list too. Alert emails
are one value in `config.json` away — `risk_emails_enabled` — and the SMTP
secrets stay wired up so turning them on needs no code.

### The Worker exists to keep tokens off phones, and to be the app's origin

GitHub Pages is static, so the app cannot write. Rather than put a GitHub token
on each phone, a Cloudflare Worker holds one as a server-side secret and is the
only thing that ever sees it. Cloudflare Access sits in front with email
one-time-PIN, so there is nothing to type on a phone and nothing stored on it.

**The Worker also serves the dashboard, and that is not cosmetic.** With the app
loaded from Pages, every write failed as "Failed to fetch. Nothing was saved."
Two browser rules made the cross-site write impossible, neither of them
configurable:

- a cross-site POST carrying JSON is preflighted; preflights never carry
  cookies, so Access answered the `OPTIONS` with a login redirect, and a
  preflight may not follow one. The real request was never sent.
- the Access cookie belongs to the `workers.dev` host, which makes it
  third-party to a page on `github.io`. Safari — the browser on both phones —
  blocks those outright.

Served from the Worker, sign-in is a normal top-level visit, the cookie is
first-party, and writes are same-origin: no preflight, no third-party cookie.
Pages stays as a read-only copy and says so at the top of the page.

The Worker **fails closed**: absent the `Cf-Access-Jwt-Assertion` header it
returns 401 rather than serving the forecast or accepting a write. If Access is
ever removed or misconfigured, the alternative would be an open endpoint that
lets anyone who finds the URL read and write the household's finances.

- Live at `https://openfin.christadore.workers.dev` — **this is the app**
- Deployed from this repo via Cloudflare Workers Builds, **root directory
  `worker`** (not `/` — `wrangler.toml` lives in `worker/`)
- Routes: `GET /` and an allowlist of `snapshot.json`, `app.json`,
  `assets/mark.svg`; `POST /balance`, `POST /bills`, `POST /defer`,
  `POST /income`
- Only secret: `GITHUB_TOKEN`, set **in Cloudflare, not GitHub** (GitHub reserves
  the `GITHUB_` prefix and refuses to create one)

---

## 2. Layout

| Path | What it is |
|---|---|
| `src/fincal.py` | The calendar. Single authority for what moves on which day. |
| `src/forecast.py` | Balance curve and `available()` |
| `src/risk.py` | Seven risk types, detection and deduplication |
| `src/recommend.py` | Deferral plans; never proposes a secured bill |
| `src/engine.py` | Orchestrator. Modes: `daily`, `watch`, `defer` |
| `src/notify.py` | Daily email; alert email kept for if alerts are re-enabled |
| `src/apply_edits.py` | Server-side re-validation of in-app bill and income edits |
| `src/add_income.py` | Adding an income source from the app, recurring or one-off |
| `src/bills.py` | Occurrence maths |
| `index.html` | The dashboard. Renders `snapshot.json` and computes nothing. |
| `app.json` | Browser-read settings. Holds the Worker URL. |
| `bills.json` | 41 bills, 39 active |
| `income.json` | 2 income streams |
| `snapshot.json` | Engine output. Regenerated on every run. |
| `state.json` | Alert dedup memory and recorded balances |

### Rules that hold everywhere

- **Money is `Decimal`, rounded half-up. Never float.**
- **The dashboard computes nothing.** Every figure comes from `engine.py`. If a
  number looks wrong it is wrong in the engine, and there is one place to fix it.
  The single exception is live deferral maths, which mirrors
  `forecast.available()` and is pinned by parity tests.
- **Nothing is ever deleted.** A bill missing from an upload is deactivated with
  a note. A deferred bill is marked, not removed — the money is still owed, and
  hiding it is the one thing this tool must never do.
- **Never invent a number.** On failure the engine says so rather than
  substituting a plausible figure. This includes numbers about the household:
  no assumed spending rate, no buffer held back on their behalf. If a figure is
  not derived from the statements or the calendar, it is not shown.
- **A deferred bill is listed everywhere it was listed before, struck through,
  and left out of the totals beside it.** Both halves matter: the forecast
  excludes it because it is not leaving the account, the lists keep it because
  it is still owed. `snapshot.json` carries a `deferred` flag on every row of
  `today_bills`, `this_week` and `upcoming` so both can be true at once.
- **Removing a bill or an income in the app deactivates it.** It keeps its id,
  its note and its place in the file, because the statement history is keyed to
  that id and something that turns out to still be live has to be able to come
  back. The app lists removed entries and can restore them.
- **Income added in the app is flagged `needs_review` until a statement
  confirms it.** It is the one edit that makes the forecast optimistic, so it is
  marked as unverified from the moment it is counted.
- 156 tests, all passing. `python -m unittest discover -s tests`

---

## 3. What is available to spend

Two numbers, both facts about the projection. **Nothing is assumed, held back,
or charged on the household's behalf.**

```
curve(d)  = balance + income up to d - bills up to d        (no allowance)
headroom  = min(curve(d)) for d in the next 30 days
per_day   = min(curve(d) / n) where n counts today as day 1
```

`headroom` is the most that could leave the account today without any day in the
window going below zero — money spent today comes off every later day, so the
low point binds. `per_day` is the largest flat daily amount that never puts a
day below zero: spending `x` a day means `n * x` is gone by day n.

Both are returned unclamped. Negative means the bills alone do not fit.

### What was taken out, and why

A **$500 cash buffer** and an assumed **$110.56/day of everyday spending** used
to be subtracted here. Both were judgements dressed as arithmetic, and the owner
asked for neither. The allowance was the worse of the two: the model invented a
spending rate on the household's behalf, charged them for it, and then reported
what was left as though it were a fact. `per_day` replaces it properly — instead
of assuming a rate and deducting it, it derives the rate the forecast can carry.

`minimum_safe_balance` is **0**, so alerts fire only on a real overdraft. It was
kept briefly as an early-warning line at $500 and then taken to zero at the
owner's request: a warning should mean the account actually goes below zero, not
that it crossed a line someone picked. Setting it above 0 brings that warning
back and changes nothing else — no figure is derived from it.

The curve behind both figures must be run with `allowance=0`. `available()`
raises rather than accept a projection that charges one — a figure net of
invented spending is exactly the quiet wrongness this project refuses.

### Why 30 days

Chosen by the owner from the real alternatives. It covers a full mortgage cycle
and every payday inside it, so nothing large hides just past the edge, and it is
short enough that the figure still moves when something is fixed. Reaching
further lets one distant annual bill hold the number negative for months while
the next three weeks are comfortable; reaching less far — to the next payday —
reads well but is blind to the mortgage four days after it.

Both halves are duplicated in JS for live deferral updates and pinned by parity
tests: the engine starting from scratch must agree to the cent with the browser
adjusting a snapshot built from an older deferral set. Deferring a bill raises
every projected day from its date onward by that amount, which redoes both
figures exactly.

---

## 4. Bugs worth remembering

**Safe-to-spend answered a question nobody asked.** It offered $1,442.06 as free
while the projection dipped below zero days later, then — once capped at the low
point — read −$1,793.25, which the owner reasonably read as "we must find
$1,793.25". Neither number was the thing they wanted. $1,437 of that gap was the
model's own assumed spending and $500 was a buffer it had decided to hold; the
household's actual obligations fitted. Replaced with two derived figures and no
assumptions at all. The lesson is not arithmetic: the maths was right both
times, and the question was wrong.

**Safe-to-spend offered money that was already gone.** With $2,612.92 in the
account it read **$1,442.06 free** while the projection dipped to $321.51 on the
18th — below the $500 floor, and the reason was already on screen as a risk.
$6,861.43 of pay arriving on the 19th, 24th and 26th cancelled every bill in the
lookahead window, because the week view nets totals and never asks which lands
first. Spending the $1,442.06 would have put the 18th about $1,120 overdrawn.
Capped at the projected low point, the honest figure is **−$178.49**. Reported by
the owner, who simply did not believe the number — the arithmetic was right and
the question it answered was the wrong one.

**The app and the engine used different lookahead windows.** `beyond_in` in the
snapshot ran `week_end + 1 .. week_end + 15`; `safe_discretionary` used
`week_end + 1 .. week_end + 14`. Income landing on that extra day was credited
live in the browser and not by the engine, so the figure moved on its own when
the real forecast came back. Now pinned by a parity test with income on exactly
that day.

**Nothing could be saved at all: "Failed to fetch."** The app was on GitHub
Pages and posted cross-site to the Access-protected Worker. Preflights carry no
cookies, so Access redirected the `OPTIONS` and the browser gave up; and the
Access cookie was third-party to `github.io`, which Safari blocks. Fixed by
serving the app from the Worker so writes are same-origin. Three smaller faults
were in the same path and would each have broken it on their own: the Worker
never sent `Access-Control-Allow-Credentials`, which alone voids any
`credentials: 'include'` response; the app labelled its body `application/json`,
which is what forced the preflight; and a failed dispatch reported only "GitHub
returned 404", which is what an expired token looks like and sent nobody
anywhere useful.

**A saved deferral disappeared from the lists.** `upcoming` and the "This week"
total were built from calendar helpers that exclude deferred occurrences by
default, so postponing a bill removed it from view while the money was still
owed — the one thing this tool must never do. The engine was right; the snapshot
was hiding it. Now every list row carries a `deferred` flag, the app strikes
those through, and the totals beside them count only what is actually leaving.

**Saving a deferral re-sent the daily summary.** The workflow's "send the daily
summary" step excluded `bills` dispatches but not `defer` ones, so recording a
deferral ran `engine.py daily` a second time against whatever balance was on
file. Deferring is a decision about what leaves the account, not a reading of
it.

**Variable bills forecast at their worst-ever occurrence.** `observed_max`
exists so a bill arriving once a month is covered at its worst. Applied to a
weekly item it compounds into nonsense — groceries peaked at $791.32 one week,
and 52 of those is $41,148/year against an actual $16,593.

Fixed so `observed_max` applies only to bills landing **monthly or less often**.
The cutoff is by occurrence count, not by frequency label:

```python
OCCURRENCES_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24,
                        "monthly": 12, "quarterly": 4, "annual": 1, "once": 1}
FREQUENT_DRAW = 24
```

The first attempt hard-coded `{"weekly", "biweekly"}` and left semimonthly — 24
times a year — still forecasting at maximum. Caught in review. Any frequency
added later now inherits correct behaviour from its own count.

Effect: safe-to-spend −$1,614.82 → −$186.24; lowest projected −$7,976.25 →
−$1,527.91; risks 8 → 4.

**Secured-payment false positives.** Every secured bill after the first negative
day was flagged even when funded. Fixed to test per-payment availability.

---

## 5. What the statements actually showed

**47 months parsed: Dec 2021 – Jul 2026.** One gap: Oct 12 – Nov 11 2023.
The 2021-24 half alone was 5,133 transactions across 35 periods.

PDF note: pdfminer's `extract_text` scrambles TD Bank's two-column table.
Extraction is coordinate-based — group characters by y, insert a space when
`x0 - prev_x1 > 0.45 * width`. Debit vs credit comes from the **running
balance**, not column position; page breaks that break the chain fall back to
the descriptor.

### Findings that changed the model

| Finding | Impact |
|---|---|
| Mortgage is **biweekly, not semimonthly** | $3,603.24/yr was never counted |
| Income overstated by **$837.11/week** | Employer is Insight Global at ~$2,016.06, not $2,853.17 |
| **$2,450 in overdraft/NSF fees** | Entirely untracked |
| Water configured at $464/yr | Actual $1,176.10/yr — **$712 short** |
| Republic garbage is quarterly | Was modelled monthly |
| CCU car loan found under `CONSUMERSCREDITCK-WTH` | Was missing entirely |
| Ally, Aetna, Immaculate Conception all ended | Were inflating discretionary |

### Electric is the only utility on a real monthly cycle

34 payments, present in all but three periods. August is the worst month and
worsening: **$309 (2022) → $369 (2023) → $490 (2024)**. The profile is now the
midpoint of the 3-year history and the recent statements — history carries the
shape and sample size, recent bills carry today's rates.

### Gas is an unresolved conflict — do not "fix" it casually

Elizabethtown Gas is **not paid monthly**: 24 payments across 36 months,
sometimes two in a period, sometimes none for four running. Paid when there is
money, not on a cycle.

| Month | 2022-24 actual | Current profile |
|---|---:|---:|
| Jan | 197.92 | 370.38 |
| Feb | 336.68 | 440.56 |
| Mar | 226.20 | 550.83 |

Current runs roughly double the older history in winter. That is rate rises,
arrears being cleared, or usage — the statements cannot distinguish them. Left
unchanged deliberately: averaging against cheaper years would lower the forecast
on a guess, and low is the dangerous direction to be wrong. **Needs a recent
Elizabethtown bill checked for a past-due line.**

### Calibration, recorded rather than tuned away

Modelled outflow $18,677/mo vs $16,175 actual debits. Modelled income $14,866 vs
$16,097 actual credits. The gap is known and written down; it has not been
massaged into agreement.

---

## 6. Open questions

| Question | Why it matters |
|---|---|
| **Elizabethtown Gas — arrears or rates?** | Roughly half the gas forecast |
| Upstart step-up date: 8/24 or 9/24? | $251.15 in September |
| Braces schedule from Loew & Patel | $400 is a placeholder — an invented number in a live forecast |
| Sewer: $135.00 observed vs $304.50 configured | |
| Clinton water first due date | Estimated 19 Sep |
| IRS state payment day | Assumed 19th |
| Oct 12 – Nov 11 2023 statement | Only gap in 47 months; low priority now |

---

## 7. Operational state

**Working:** Pages (read-only), email (SMTP secrets set and confirmed sending),
the Worker, Cloudflare Access, 156 tests green.

**Outstanding for the owner:**
- **Open `https://openfin.christadore.workers.dev` and use OpenFIN from there.**
  That address now serves the app; the github.io one cannot save and says so.
  Replace the home-screen icon on both phones.
- Enter a balance — confirms the chain end to end and applies the latest
  bills.json corrections
- Add **One-time PIN** as a login method (Zero Trust → Settings →
  Authentication). Only "Cloudflare" is enabled, so only the account owner can
  sign in; the second household phone cannot.

**Cloudflare gotchas already hit, documented in `worker/README.md`:** build
token rolling, root directory defaulting to `/`, the Access "Public DNS" tab
being wrong for a `workers.dev` address, `GITHUB_` being a reserved prefix on
GitHub but not Cloudflare, and pull-request branch builds failing red while
`main` deploys perfectly — different builds, and only the second one matters.

---

## 8. Other documents

| File | Contents |
|---|---|
| `SETUP.md` | Browser-only setup: secrets, Pages, day-to-day use |
| `worker/README.md` | Worker deployment, click by click, with symptom tables |
| `STATEMENT-HISTORY.md` | Full 36-month statement analysis |
| `BILLS-AUDIT.md` | Bill-by-bill reconciliation against statements |
| `LACROSSE-COSTS.md` | NJ Total Lacrosse and related youth sports costs |
