# OpenFIN — project state and handoff

A household cash-flow engine for one family. Not a budgeting app: it answers
"what is actually safe to spend this week, given what is really going to leave
the account." Everything is derived from bank statements rather than estimates.

**Owner does not code and has no terminal.** Every change reaches them as a
GitHub pull request or a settings toggle in a web dashboard. This constraint has
shaped most of the architecture and is not negotiable.

---

## 1. How it runs

There is no server and no database. GitHub Actions is the entire runtime, and
JSON files committed to this repository are the entire state.

```
phone (GitHub Pages)  ->  Cloudflare Worker  ->  repository_dispatch
                                                        |
                                                 GitHub Actions
                                                        |
                                    engine.py -> snapshot.json + email
                                                        |
                                          committed back -> Pages serves it
```

**Entering the balance is the trigger for everything.** There is no scheduled
daily summary, deliberately: a summary anchored to a stale balance reads as
authoritative and is quietly wrong. Risk alerts are the exception — they run on
a schedule twice a day and never depend on a balance being entered, because the
whole point of an early warning is that it arrives when nobody is looking.

### The Worker exists to keep tokens off phones

GitHub Pages is static, so the app cannot write. Rather than put a GitHub token
on each phone, a Cloudflare Worker holds one as a server-side secret and is the
only thing that ever sees it. Cloudflare Access sits in front with email
one-time-PIN, so there is nothing to type on a phone and nothing stored on it.

The Worker **fails closed**: absent the `Cf-Access-Jwt-Assertion` header it
returns 401 rather than accepting an unauthenticated write. If Access is ever
removed or misconfigured, the alternative would be an open endpoint that lets
anyone who finds the URL write to the household's finances.

- Live at `https://openfin.christadore.workers.dev`
- Deployed from this repo via Cloudflare Workers Builds, **root directory
  `worker`** (not `/` — `wrangler.toml` lives in `worker/`)
- Routes: `POST /balance`, `POST /bills`, `POST /defer`
- Only secret: `GITHUB_TOKEN`, set **in Cloudflare, not GitHub** (GitHub reserves
  the `GITHUB_` prefix and refuses to create one)

---

## 2. Layout

| Path | What it is |
|---|---|
| `src/fincal.py` | The calendar. Single authority for what moves on which day. |
| `src/forecast.py` | Balance curve and `safe_discretionary()` |
| `src/risk.py` | Seven risk types, detection and deduplication |
| `src/recommend.py` | Deferral plans; never proposes a secured bill |
| `src/engine.py` | Orchestrator. Modes: `daily`, `watch`, `defer` |
| `src/apply_edits.py` | Server-side re-validation of in-app bill edits |
| `src/notify.py` | Daily and alert email, text + HTML |
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
  `forecast.safe_discretionary()` and is pinned by parity tests.
- **Nothing is ever deleted.** A bill missing from an upload is deactivated with
  a note. A deferred bill is marked, not removed — the money is still owed, and
  hiding it is the one thing this tool must never do.
- **Never invent a number.** On failure the engine says so rather than
  substituting a plausible figure.
- 92 tests, all passing. `python -m unittest discover -s tests`

---

## 3. The discretionary formula

```
safe = balance + income_week - bills_week - committed_beyond - buffer
committed_beyond = max(0, beyond_out - beyond_in)
```

Duplicated in JS for live deferral updates and pinned by parity tests — same
inputs must produce the same cent on both sides.

---

## 4. Two bugs worth remembering

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

**Working:** Pages, email (SMTP secrets set and confirmed sending), the Worker,
Cloudflare Access, the app wired to the Worker via `app.json`, 92 tests green.

**Outstanding for the owner:**
- Enter a balance — confirms the chain end to end and applies the latest
  bills.json corrections
- Add **One-time PIN** as a login method (Zero Trust → Settings →
  Authentication). Only "Cloudflare" is enabled, so only the account owner can
  sign in; the second household phone cannot.

**Cloudflare gotchas already hit, documented in `worker/README.md`:** build
token rolling, root directory defaulting to `/`, the Access "Public DNS" tab
being wrong for a `workers.dev` address, and `GITHUB_` being a reserved prefix
on GitHub but not Cloudflare.

---

## 8. Other documents

| File | Contents |
|---|---|
| `SETUP.md` | Browser-only setup: secrets, Pages, day-to-day use |
| `worker/README.md` | Worker deployment, click by click, with symptom tables |
| `STATEMENT-HISTORY.md` | Full 36-month statement analysis |
| `BILLS-AUDIT.md` | Bill-by-bill reconciliation against statements |
| `LACROSSE-COSTS.md` | NJ Total Lacrosse and related youth sports costs |
