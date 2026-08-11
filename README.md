<h1>TogetherLedger</h1>

**A household financial early-warning system.** It does not tell you what your
budget was. It tells you what is about to happen to your money, when, how much,
and what you could do about it.

**[Open the dashboard](https://jchristadore-ux.github.io/TogetherLedger/)**

---

## The daily loop

```
Enter today's balance  ->  forecast runs  ->  risk engine runs  ->  email sent
```

That is the household's only manual step. Everything else cascades from it.

Entering a balance is done from a phone, with no server and no hosting cost:
file the **Daily balance** issue from the GitHub mobile app, or run the
**Daily balance** workflow from the Actions tab. The engine parses it, projects
forward, emails the daily check, comments the result back, and closes the issue.

**No balance entered means no daily email.** A financial summary anchored to a
stale balance reads as authoritative and is quietly wrong, which is worse than
silence.

## The half that does not wait for you

Risk alerts are independent of balance entry. `watch.yml` runs twice a day,
projects from the last known position, and emails **only** when it finds
something. A mortgage about to bounce next Tuesday is true whether or not
anyone opened the app this morning.

Alerts deduplicate. An unchanged risk is not re-sent; it goes out again only
when it gets materially worse (>$100), moves by more than two days, resolves
and returns, or hits the reminder threshold.

## What it watches for

| Risk | Meaning |
|---|---|
| Secured payment | A mortgage or car payment the balance cannot cover that day |
| Negative balance | The projection crosses zero |
| Large payment | A big obligation lands with too little behind it |
| Insufficient cash | Stays positive but drops under the safe floor |
| Income timing | The month works; the order does not |
| Future crunch | Fine now, a combination bites later |
| Discretionary | Nothing free to spend this week |

## Safe discretionary

Deliberately **not** `income - bills`. That answers "did we earn more than we
owe", which is not the question anyone is actually asking.

```
  balance now
+ income before end of week
- bills before end of week
- obligations in the 14 days after that
- required cash buffer
= safe to spend
```

The lookahead is what makes it honest — spending everything not owed *this*
week is exactly how a mortgage two days into next week goes unpaid. **The
number can be negative, and it is shown negative.**

## Architecture

One authoritative engine. Business logic never lives in the dashboard.

| Module | Responsibility |
|---|---|
| `src/bills.py` | Occurrence maths — when each obligation falls |
| `src/fincal.py` | The financial calendar: obligations become dated events |
| `src/forecast.py` | Balance projection and safe discretionary |
| `src/risk.py` | Risk detection and alert deduplication |
| `src/recommend.py` | Deferral options — recommends, never acts |
| `src/notify.py` | Email composition, daily and alert |
| `src/messaging.py` | SMTP delivery |
| `src/engine.py` | The orchestrator and the only entry point |
| `src/parse_upload.py` | Reads a bill screenshot into `bills.json` |
| `index.html` | Renders `snapshot.json`. Computes nothing. |

Data lives in JSON committed to the repo: `bills.json`, `income.json`,
`config.json`, `state.json`, and the generated `snapshot.json`. No database, no
server, no runtime.

## Rules that matter

**Money is never a float.** Every amount is a `Decimal` rounded half-up. A
projection is a long chain of additions and `0.1 + 0.2` is not `0.3`.

**Variable bills forecast at the top of their range.** Under-forecasting an
outflow is what produces a surprise overdraft, so the engine uses the highest
amount actually observed, not the average.

**Never invent a number.** If the engine cannot compute something it says so
and exits non-zero. It never presents a guess as a forecast.

**Nothing is ever deferred automatically.** The recommendation engine proposes;
the household decides. Secured obligations are never proposed.

**Never delete a bill.** A bill missing from an upload is deactivated with a
note. OCR misses a line far more often than a household cancels a mortgage.

## Running it

```bash
python -m unittest discover -s tests -v      # 56 tests, no network
python src/engine.py daily --balance 4382.17 --dry-run
python src/engine.py watch --dry-run
```

## Configuration

`config.json` — safe balance floor, risk thresholds, forecast horizon,
discretionary allowance and lookahead, alert reminder cadence, balance
staleness limit. Every threshold is configurable; none are hard-coded in the
UI.

## Secrets

`SMTP_USER`, `SMTP_APP_PASSWORD`, `EMAIL_RECIPIENTS`, and optionally
`EMAIL_FROM`. Email is the only channel the system needs. The legacy SMS path
remains in `messaging.py` but nothing depends on it.
