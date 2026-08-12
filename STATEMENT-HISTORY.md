# What 47 months of statements say

Sources: TD Bank statements, **Dec 2021 – Dec 2024** (35 periods, 5,133
transactions) parsed for this analysis, plus the Dec 2024 – Jul 2026 statements
already in the model. One gap: **Oct 12 – Nov 11 2023** was never uploaded — two
copies of the November statement were sent instead.

Transaction direction was derived from the running balance rather than from
column position, because the PDF's DEBIT/CREDIT columns collapse when the text
is reconstructed. Where a page break broke the balance chain, the descriptor
settled it. Every one of the 5,133 rows ended up signed.

---

## Electric is the only utility paid on a real monthly cycle

34 JCP&L payments, present in all but three periods. That makes a month-of-year
mean meaningful, and the seasonal swing is large and consistent:

| Month | 2022 | 2023 | 2024 | 3-yr mean | Was | Now |
|---|---:|---:|---:|---:|---:|---:|
| Jan | 186 | 132 | 181 | 166.23 | 192.88 | **179.56** |
| Feb | — | 126 | 202 | 164.09 | 192.46 | **178.28** |
| Mar | 360 | 110 | 185 | 218.38 | 176.61 | **197.50** |
| Apr | 158 | 140 | 171 | 156.43 | 165.08 | **160.76** |
| May | — | 141 | 151 | 145.75 | 145.95 | **145.85** |
| Jun | — | 136 | 153 | 144.44 | 163.78 | **154.11** |
| Jul | 240/281 | 187 | 338 | 261.88 | 305.12 | **283.50** |
| Aug | 309 | 369 | 490 | 389.19 | 448.77 | **418.98** |
| Sep | 341 | 307 | 384 | 344.19 | 404.25 | **374.22** |
| Oct | 286 | 355 | 330 | 323.92 | 267.74 | **295.83** |
| Nov | 123 | — | 186 | 154.13 | 130.28 | **142.21** |
| Dec | 130 | 172 | 178 | 159.85 | 162.35 | **161.10** |

August really is the worst month, and it is getting worse each year — $309,
then $369, then $490. The old profile was built on the 2025-26 statements alone,
which meant most months rested on a single reading. Each month now averages
three or four.

The new figures are the midpoint of the 3-year history and the recent profile.
History carries the shape and the sample size; recent bills carry today's rates.
Neither alone is right. Annual moves $2,755.27 → **$2,691.90**.

## Water was under-forecast by $712 a year

| Year | Actual |
|---|---:|
| 2022 | $1,088.42 |
| 2023 | $1,121.76 |
| 2024 | $1,318.11 |
| **Mean** | **$1,176.10** |

It was configured as quarterly at $116.00 — **$464.00/year**. Corrected to
$294.03 quarterly, $1,176.12/year.

Billing is irregular: between two and five payments a year, on no fixed date,
ranging $26.54 to $873.53. The quarterly figure is the annual mean spread evenly,
not a real due date. Marked `variable` and confidence `medium` accordingly.

## Gas — a conflict I have not resolved

Elizabethtown Gas is **not paid monthly**. Across 36 months there were 24
payments: sometimes two in one period, sometimes none for four months running.
It is paid when there is money, not on a cycle.

That makes a month-of-year mean unreliable, and it collides with the current
model:

| Month | 2022-24 actual | Current profile |
|---|---:|---:|
| Jan | 197.92 | 370.38 |
| Feb | 336.68 | 440.56 |
| Mar | 226.20 | 550.83 |
| Dec | 149.23 | 186.00 |

The current profile is roughly double the older history in winter. Three
explanations, and the statements cannot distinguish them:

1. Gas rates rose steeply in 2025-26.
2. The 2025-26 payments include arrears being cleared, so each one covers more
   than a month.
3. Usage rose.

**Left unchanged.** The current profile came from recent statements and is the
safer of the two for forecasting what will actually leave the account. Averaging
it against older, cheaper years would lower the forecast on a guess. Worth
checking a recent Elizabethtown bill for a past-due line.

One outlier excluded from all gas figures: **$1,704.56 on 1 Apr 2022**, four to
six times any other payment. It reads as an arrears settlement, not a month.

## Other corrections found

**Culligan** is $38.39, not $40.39 — it stepped down in Oct 2024 and has held
there since. Two outliers in the run: **$2,063.20 on 1 May 2024**, far too large
for a service charge and most likely equipment, and $137.03 on 5 Jul 2024.

**Sewer** appears once in 36 months: $135.00 in Sep 2023. Consistent with the
annual assumption, though $135.00 against the configured $304.50 is a gap worth
confirming.

**Mortgage** shows under three different servicers across the window — Dovenmuehle
($3,400.86 mean), Cross Country ($3,460.72), then Mr. Cooper ($3,476.74). All
about $3,450/month, against $3,903/month today ($1,801.62 biweekly).

**Overdraft and NSF fees: $805.00** across the 2023-24 statements, on top of the
$1,645 already found in the recent ones.

## Ended during this window

Visible in the older statements and absent from the recent ones — all correctly
inactive in the model now: **Ally** ($344.45/mo, 23 payments), **Aetna**
($767.20/mo, 7 payments), **Immaculate Conception** ($440.54/mo, 15 payments),
**Upstart** at the old $1,244.29 rate.

## Still open

- **Oct 12 – Nov 11 2023** statement — the only gap in 47 months.
- A recent **Elizabethtown Gas bill**, to settle whether the winter figures are
  rates or arrears.
- **Sewer** — $135.00 observed vs $304.50 configured.
