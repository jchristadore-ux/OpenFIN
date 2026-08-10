# TogetherLedger

Two things live here.

**Daily cash-flow SMS** — a text every morning at 7am Eastern saying what is in
the account, what comes out today, and the next day the balance gets tight.
Runs entirely on GitHub Actions. Setup is browser-only: **[SETUP.md](SETUP.md)**.

**[The shared plan page](plan/)** — the encrypted August–September operating
plan with a live payment tracker, at
`https://jchristadore-ux.github.io/TogetherLedger/plan/`. See
[plan/README.md](plan/README.md).

---

## What the daily text looks like

```
CASH 08/09
Now: $3,512.20 (incl pending)
Bills today: $750.00
 - Hanover Auto $750.00 (PAID)
Disc spent: $140.36 of $100.00
End of day: $3,512.20

WATCH: 08/16 -$1,337.62
Next 3 tight: 08/16 -1338, 08/17 -1438, 08/18 -1538
```

`Clear through 09/23` replaces the last two lines when nothing in the window
gets tight. If a day newly drops below zero — or an already-known bad day gets
more than $25 worse — a second text follows:

```
ALERT: 08/16 projects to -$1,337.62
Driver: Mortgage $1,801.62 on 08/16
Balance today: $1,400.00
```

One alert per run, naming the nearest new problem. Later negative days are
consequences of that one; texting all of them would just mean a phone nobody
reads.

## How it works

| Piece | What it does |
|---|---|
| `.github/workflows/daily-brief.yml` | 7am cron. Two UTC entries, one hour apart; the script ignores whichever isn't 7am locally, so daylight saving needs no action. |
| `.github/workflows/ingest-bills.yml` | Fires when you drop a screenshot into `inbox/`. |
| `.github/workflows/setup-simplefin.yml` | One-time token exchange, run from the Actions tab. |
| `src/simplefin_client.py` | Reads the bank. Handles both v1 and v2 Account Set shapes. |
| `src/bills.py` | Occurrence maths — when each bill falls, with month-end clamping. |
| `src/projection.py` | The forward ledger, the double-count guard, tight-day detection. |
| `src/messaging.py` | Twilio, or a carrier email-to-SMS gateway. |
| `src/parse_upload.py` | Reads an uploaded screenshot or spreadsheet, diffs it, commits. |
| `src/daily_brief.py` | The morning run. |
| `src/selftest.py` | 45 checks on synthetic data. Runs before every send. |

State lives in JSON files committed to the repo: `config.json`, `bills.json`,
`income.json`, `state.json`, and a daily snapshot in `logs/`. No database, no
server, no local runtime.

## The rules that matter

**Never subtract a bill twice.** Before a bill due today is taken off the
projection, today's posted *and* pending transactions are searched for a debit
matching its keywords within 3% or $5. If one is found the bill is listed as
`(PAID)` and not subtracted. Get this wrong and every day after it is off by
that amount.

**Pending money is spent money.** A pending debit has already reduced what is
actually available. The bank's own `available-balance` is used when the
institution provides one; otherwise the balance is taken and every pending
transaction applied by hand, and the text says `[bal-pending]` so you know
which method produced the number.

**Never send a number it isn't sure about.** If the bank feed fails, the text
says so, quotes the last known balance *with its timestamp*, and the Actions
run goes red:

```
CASH DATA STALE — bank feed unavailable.
Last known: $3,704.45 as of 2026-08-08 20:23
Reason: SimpleFIN returned HTTP 503: upstream bank is down
Figures above are NOT current. Check the bank directly.
```

**Never delete a bill.** A bill missing from an uploaded screenshot is set to
`"active": false` with a note. OCR misses a line far more often than a
household actually cancels a mortgage.

**Money is never a float.** Every amount is a `Decimal` rounded half-up to the
cent. A projection is a long chain of additions and `0.1 + 0.2` is not `0.3`.

## Changing things

Edit `config.json` in the GitHub web editor — allowance, thresholds, how many
days to project, which model reads screenshots, whether SMS goes via Twilio or
the email gateway. Edit `bills.json` and `income.json` the same way; both files
carry a comment block at the top explaining every field.

To preview without sending: **Actions → Daily cash brief → Run workflow**, leave
**dry_run** ticked. The exact message prints to the log.

## Dependencies

Python 3.11, plus `requests` and `openpyxl`. Everything else is the standard
library.
