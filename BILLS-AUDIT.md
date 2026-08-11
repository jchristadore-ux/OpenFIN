# Bill audit — household sheet vs. what the bank actually does

Every bill on the household sheet checked against nineteen contiguous TD
statements, **12 Dec 2024 – 11 Jul 2026**. Amounts and dates below are what
actually cleared, not what was expected to.

**Headline: the account runs at −$77.99 a month.** Average credits $16,097.39,
average debits $16,175.38 across the last five statement periods. That is the
overdraft explanation — not one bad month, a structural shortfall.

## The five that matter

**1. The mortgage is biweekly, not twice a month.** Fifteen consecutive
payments from 12/15/2025 to 06/29/2026 sit exactly 14 days apart. `bills.json`
had it as `semimonthly` — 24 payments a year. Biweekly is 26.

> **$1,801.62 × 2 = $3,603.24 a year the projection never counted.**

The sheet has it as "CCM #1 day 10, CCM #2 day 26", which is the same mistake.
There is no fixed day; it walks the calendar.

**2. Income was overstated by $837.11 a week.** `income.json` listed a primary
payroll of $2,853.17/week. The actual employer is Insight Global and the modal
deposit is **$2,016.06**.

> **Roughly $3,627 a month of income being projected and never received.**

**3. Overdraft fees are a major recurring cost, and nobody is tracking them.**
$1,645.00 year-to-date across 25 charges — confirmed by the statement's own YTD
field. That averages **$274.17/month**, more than every utility combined.

**4. Republic garbage is quarterly, not monthly.** Charged 01/07 ($168.12) and
04/07 ($177.30) — exactly 90 days apart. The sheet budgets $168.12 every month,
overstating by about $109/month.

**5. Discretionary spending is more than double the configured allowance.**
Modelled bills come to $9,665.99/month against $16,175.38 of actual debits,
leaving **$6,509.39/month — $216.98/day** in everyday spending.
`config.json` assumes **$100.00/day**. Left alone, the forward projection is
optimistic by roughly $3,500 every month.

*Not changed here.* The allowance is a budget target, not an observed fact, so
it is the household's call whether to move it to reality or hold the line.

## Every difference found

| Bill | Sheet | Actual | Sheet day | Actual day | Problem |
|---|---:|---:|---:|---:|---|
| CCM mortgage | $1,801.62 | $1,801.62 | 10 & 26 | biweekly | **cadence wrong** |
| Kia | $678.66 | $742.65 | 19 | ~every 28d | under by $63.99 |
| Hanover | $530.00 | $513.27 | 12 | 12 | over by $16.73 |
| Elizabethtown gas | $370.38 | $180.61 | 9 | 10–13 | **over by $189.77** |
| FirstEnergy | $192.88 | ~$200.00 | 2 | 2–6 | varies $137–305 |
| Republic garbage | $168.12 | $177.30 | 7 | 7 | **quarterly, not monthly** |
| Capital One | $163.00 | $154.00 | 20 | 16–18 | day off by 2–4 |
| Comcast | $147.69 | $138.93 | 20 | 18 | over by $8.76 |
| Xfinity Mobile | $123.31 | $88.82 | 29 | 29 | over by $34.49 |
| PNC | $95.18 | $70.00 | 5 | varies | varies $70–150 |
| Lowes | $87.30 | $122.00 | 7 | 18–21 | under by $34.70, day off 11 |
| Chase — Amazon | $80.00 | $136.00 | 16 | 16–18 | **under by $56.00** |
| Affirm | $73.07 | $73.07 | 18 | 29 | day off by 11 |
| Chase — United | $46.00 | $80.00 | 21 | 21–23 | under by $34.00 |
| T-Mobile | $30.21 | $20.75 | 12 | 10–12 | over by $9.46 |
| Netflix | $19.19 | $21.31 | 22 | 22–26 | under by $2.12 |

Correct on the sheet: Upstart debt consolidation ($1,244.29 / 15), One Main
($388.24 / 6), Upstart past bills ($345.82 / 26), Upgrade ($287.85 / 20),
Home Depot ($100.00 / 13), Barclay ($57.98 / 27), TJMaxx, Culligan.

## Missing from the sheet entirely

| Bill | Monthly | When |
|---|---:|---|
| Affirm — second loan | $373.13 | new since 05/2026 |
| Transfer to credit card | $646.49 | ~biweekly, $280–325 |
| **Overdraft fees** | **$274.17** | ongoing |
| Trilogy Lacrosse | $100.00 | 3rd–5th |
| Comenity | $50.00 | irregular |

## On the sheet but never seen in this account

| Bill | Sheet | Monthly |
|---|---:|---:|
| Upstart — OBX | $464.91 | 6 |
| CCU — JD car | $306.90 | 20 |
| Town of Clinton water | $116.00 | 19 |
| IRS federal taxes | $103.00 | 19 |
| **Total** | **$990.81** | |

Nineteen months of statements contain no payment to any of these. Either they
are paid from a different account, or they are not being paid. Worth
confirming — $990.81/month is not a rounding error.

Two more sheet entries are not bills at all. **NJTOTAL $207.00** is the
lacrosse *savings target* from `LACROSSE-COSTS.md`, not a debit — the club
bills in lumps of $491.63, and carrying both double-counts. **REC SPORTS** is
blank; the real figure is **$450/month** for all youth sports.

## The corrected monthly breakdown

| | Monthly |
|---|---:|
| CCM mortgage (biweekly × $1,801.62) | $3,903.51 |
| Upstart — debt consolidation | $1,244.29 |
| Kia (13/yr × $742.65) | $804.54 |
| Transfer to credit card | $646.49 |
| Hanover car insurance | $513.27 |
| One Main | $388.24 |
| Affirm — second loan | $373.13 |
| Upstart — past bills | $345.82 |
| Upgrade — basement | $287.85 |
| **Overdraft fees** | **$274.17** |
| FirstEnergy electric | $200.00 |
| Elizabethtown gas | $180.61 |
| Capital One | $154.00 |
| Comcast | $138.93 |
| Chase — Amazon | $136.00 |
| Lowes | $122.00 |
| Home Depot | $100.00 |
| Trilogy Lacrosse | $100.00 |
| Xfinity Mobile | $88.82 |
| Chase — United | $80.00 |
| Affirm | $73.07 |
| PNC | $70.00 |
| Barclay | $60.74 |
| Republic garbage (quarterly × $177.30) | $59.10 |
| Comenity | $50.00 |
| Culligan | $40.39 |
| TJMaxx | $36.00 |
| Netflix | $21.31 |
| T-Mobile | $20.75 |
| **Fixed subtotal** | **$10,513.03** |
| Groceries ($282.35/wk) | $1,223.52 |
| Gas ($95.48/wk) | $413.75 |
| **Total, bank-verified** | **$12,150.30** |
| Not seen in this account | $990.81 |
| **Grand total if all paid from here** | **$13,141.11** |

Against average credits of **$16,097.39/month**, that leaves about **$3,000**
for everything else — while actual everyday spending runs **$6,509/month**.
That gap is the deficit, and it is why the balance sits at $118.25.

Not included above: **Loew & Patel orthodontics**, which is real but irregular
— $1,000.00 on 06/10/2026, then $129.47 and $15.00. The sheet lists $400.00
"TBD".

## What changed in the ledger

`bills.json` went from 7 entries to 32. `income.json` had both entries
corrected. Anything estimated or varying carries `needs_review: true` with the
evidence in its `note`. `config.json` was **not** touched.

---

*Parsed from nineteen TD statements on 11 August 2026. Every total reconciles
to the statements' own account-summary figures.*
