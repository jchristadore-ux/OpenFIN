# Youth sports — what it actually costs

Rebuilt from the club payment portal's historical payments for Lia, covering
May 2023 through July 2026, and cross-checked against seventeen contiguous TD
Bank statements covering 12 Dec 2024 – 11 May 2026. Every figure below is a
real posted payment except the three clearly marked estimates for the rest of
the 2026-27 season.

**The lacrosse club portal says about $2,490 a year, or $207 a month.** That
number has barely moved in three seasons, and for the club's own charges the
portal is accurate to the penny.

**It is also the wrong number to budget from.** The portal only knows about NJ
Total Select. Real lacrosse spending across the seventeen months was
**$5,574.28** — $1,403.19 of it to other lacrosse organisations the portal
never sees, including a **$100/month Trilogy Lacrosse charge that is still
running and is not in `bills.json`**.

**All youth sports together run $447.51 per month.** Budget **$450/month**,
not $207. See [the full picture](#the-full-picture) below.

## Three complete seasons

| Season | Team / program | Clinics & camps | Total incl. fees |
|---|---:|---:|---:|
| 2023-24 (2032 White) | $1,826.79 | $605.49 | **$2,432.28** |
| 2024-25 | $2,570.89 | $0.00 | **$2,570.89** |
| 2025-26 | $2,147.64 | $315.68 | **$2,463.32** |
| **Average** | $2,181.77 | $307.06 | **$2,488.83** |

Three seasons inside a $139 band. For budgeting purposes this is a fixed cost,
not a variable one.

- **Team fees alone:** $2,181.77/yr → **$181.81/month**
- **Clinics and camps:** $307.06/yr → **$25.59/month**
- **All in:** $2,488.83/yr → **$207.40/month**

The card convenience fee is a real 3.5% surcharge, not a rounding error — it
came to **$236.49** across the three seasons. The one time a payment went by
Venmo (11/1/2024, $475.00) the fee was $0.00. Paying the three remaining
2026-27 installments by Venmo or check instead of Visa saves **$49.89**.

## The cost is not spread evenly

2025-26, by the month the money actually left the account:

| Month | Paid |
|---|---:|
| Jul 2025 | $181.13 |
| Aug 2025 | — |
| Sep 2025 | $621.00 |
| Oct 2025 | $491.63 |
| Nov 2025 | — |
| Dec 2025 | — |
| Jan 2026 | $615.83 |
| Feb 2026 | $62.10 |
| Mar 2026 | — |
| Apr 2026 | $491.63 |
| May 2026 | — |
| Jun 2026 | — |

Six months carry the entire season; six months are zero. The $207/month figure
is a *savings target*, not a bill. Treated as a bill it will be wrong every
single month — either by $207 or by $400.

## 2026-27, the season now underway

Already paid:

| Date | Item | Amount |
|---|---|---:|
| 5/28/2026 | Tryout | $51.75 |
| 7/18/2026 | Deposit | $672.75 |
| | **Paid to date** | **$724.50** |

Estimated remaining, based on the installment structure that held in both
2024-25 and 2025-26:

| Est. date | Item | Amount |
|---|---|---:|
| 10/06/2026 | Installment 1 of 3 | $491.63 |
| 01/12/2027 | Installment 2 of 3 | $491.63 |
| 03/09/2027 | Installment 3 of 3 | $491.63 |
| | **Remaining** | **$1,474.89** |

**Projected 2026-27 team total: $2,199.39.** With the K-9 summer clinic
($129.38) and the six winter clinic sessions ($186.30) if she does them again:
**$2,515.07** — right in line with the three-season average.

### Why these estimates

The installment was **exactly $475.00 + $16.63 fee in both 2024-25 and
2025-26**. The dates mirror the 2025-26 due dates. The one thing that argues
for a higher number is the deposit, which rose from $600 to $650 (+8.3%)
this season. If the installments rise by the same proportion, each is about
$533 and the remaining total is closer to **$1,600**.

Replace these with the real figures the moment the club posts the 2026-27
schedule.

## What is in the ledger

Three `once` entries in `bills.json` — `lacrosse-2627-inst-1`, `-2`, `-3` —
all flagged `needs_review: true` with the estimate explained in each `note`.
They fall outside the current 45-day projection window, so today's brief is
unchanged; the first one enters the window around **22 August 2026**.

## The saving target

From August 2026, to cover the rest of this season:

| Goal | Need | Per month |
|---|---:|---:|
| First installment (Aug + Sep only) | $491.63 | **$245.82** |
| All three, spread Aug–Mar | $1,474.89 | **$184.36** |
| All three + winter clinics | $1,661.19 | **$207.65** |

That last line lands within a quarter of the three-season average of $207.40 —
two different methods agreeing is a good sign the number is right.

At the current weekly payroll of $2,853.17, the all-in monthly figure is
**7.3% of one weekly check**, or **$95.72 per biweekly pay period**.

## Two things worth knowing

**The club tolerates late payment.** In 2025-26 every installment was paid
late — 58, 9, 18 and 52 days respectively — with no late fee ever charged on
any row in three seasons. The 3/9/2026 installment was paid 4/30/2026. This is
real flexibility if a due date lands on a bad week, though it is the club's
goodwill and not a documented policy.

**Camp fees can offset tournament fees.** On 7/10/2024 the $500 summer
tournament fee was billed at $265 because the $235 camp fee already paid was
credited against it. Worth asking whether the same credit applies before
paying for a camp separately.

---

# The full picture

Everything above comes from the club portal. This section comes from seventeen
contiguous TD statements, **12 Dec 2024 – 11 May 2026**, which see things the
portal does not.

## The portal is accurate — and badly incomplete

For the club's own charges the portal reconciles to the penny. Every 2025-26
season payment appears in the bank exactly as listed:

| Bank posted | Amount | Portal row |
|---|---:|---|
| 07/02/2025 | $181.13 | Tryout + K-9 clinic, 7/1 |
| 09/15/2025 | $621.00 | PARTIAL, 9/11 |
| 10/16/2025 | $491.63 | PARTIAL, 10/15 |
| Jan–Feb 2026 | $186.30 | 6 winter clinics × $31.05 |
| 02/02/2026 | $491.63 | PARTIAL, 1/30 |
| 05/04/2026 | $491.63 | PARTIAL, 4/30 |
| **Total** | **$2,463.32** | **matches the season exactly** |

The problem is not accuracy. It is scope. **The portal only knows about NJ
Total Select.** Across the window, $4,171.09 went to the club — and another
**$1,403.19 went to lacrosse organisations the portal never sees**:

| Payee | Total | Note |
|---|---:|---|
| Trilogy Lacrosse | $725.00 | **$100/month, still running** |
| US Lacrosse | $345.00 | memberships; $155.00 renewal Feb 2026 |
| Universal Lacrosse | $125.25 | likely gear |
| Q4 Lacrosse | $101.19 | Mar 2026 |
| Team travel — Crab Cake | $80.00 | Jan 2025 |
| Roxbury Lacrosse | $26.75 | Apr 2025 |

Plus one $232.88 charge from the club itself on 01/03/2025 with no portal row
at all — almost certainly a $225.00 program plus the 3.5% card fee.

**True lacrosse cost is $5,574.28 over seventeen months**, against the
$2,488.83/yr the portal implies.

## Youth sports by month

Posted dates. December 2024 and May 2026 are partial months; everything
between is complete.

| Month | High Bridge | Field hockey | Lacrosse | Other athletics | Total |
|---|---:|---:|---:|---:|---:|
| 2024-12 \* | — | — | — | $179.00 | $179.00 |
| 2025-01 | — | — | $312.88 | $179.00 | $491.88 |
| 2025-02 | — | — | $646.63 | $199.00 | $845.63 |
| 2025-03 | — | — | $35.00 | $179.00 | $214.00 |
| 2025-04 | — | — | $518.38 | $179.00 | $697.38 |
| 2025-05 | $95.00 | — | $491.63 | $179.00 | $765.63 |
| 2025-06 | — | $120.00 | — | $339.00 | $459.00 |
| 2025-07 | — | — | $181.13 | −$30.00 | $151.13 |
| 2025-08 | — | $51.94 | — | — | $51.94 |
| 2025-09 | $125.00 | — | $621.00 | $45.00 | $791.00 |
| 2025-10 | $145.00 | — | $491.63 | — | $636.63 |
| 2025-11 | — | — | — | — | **$0.00** |
| 2025-12 | — | — | $225.00 | — | $225.00 |
| 2026-01 | — | — | $193.15 | — | $193.15 |
| 2026-02 | — | — | $839.78 | $101.94 | $941.72 |
| 2026-03 | — | — | $326.44 | $45.00 | $371.44 |
| 2026-04 | $104.57 | $120.00 | $100.00 | — | $324.57 |
| 2026-05 \* | — | — | $591.63 | — | $591.63 |
| **Total** | **$469.57** | **$291.94** | **$5,574.28** | **$1,594.94** | **$7,930.73** |

\* partial month

Across the sixteen complete months (Jan 2025 – Apr 2026): **$7,160.10, or
$447.51 per month — an annualised $5,370.08.**

The two years agree: calendar 2025 came to $5,329.22, and Jan–Apr 2026 is
running at $457.72/month. **Budget $450/month for youth sports**, against the
$207 the lacrosse portal alone suggests.

November 2025 is a genuine zero — the only clear month in seventeen.

## Who the money went to

**High Bridge — $469.57.** There is no payee named "High Bridge Recreation".
Two separate organisations, and they do not overlap:

- High Bridge Youth **Soccer** — $95.00 (05/05/2025) and $104.57
  (04/17/2026). Annual, each spring.
- High Bridge Youth **Basketball** (`hbyb.org`) — $125.00 (09/22/2025) and
  $145.00 (10/02/2025). Two payments, one season, nothing since.

**Field hockey — $291.94.** North Hunterdon Lions, $120.00 on 06/04/2025 and
again on 04/20/2026 — the same fee, one payment a year, no instalments. Plus
two USA Field Hockey memberships at $25.97 (Aug 2025).

**Other athletics — $1,594.94.** Was dominated by **Parisi Speed School at
$179.00/month**, seven charges from 12/19/2024 to 06/20/2025 totalling
$1,253.00. **It stopped in June 2025 and has not restarted** — nothing in the
eleven months since. Also Gymnastics Unlimited ($150.00 net), Girl Scouts
($45.00 twice) and a $101.94 Harlem Wizards ticket, which is a fundraiser
rather than a program fee.

## The one that should be in the ledger

**Trilogy Lacrosse bills $100 every month and is not in `bills.json`.**

| Posted | Amount |
|---|---:|
| 12/10/2025 | $225.00 |
| 01/05/2026 | $100.00 |
| 02/03/2026 | $100.00 |
| 03/27/2026 | $100.00 |
| 04/03/2026 | $100.00 |
| 05/04/2026 | $100.00 |

Around the 3rd–5th, with March apparently paid late on the 27th. Still active
at the last statement. Not added to `bills.json` yet — the record stops at
11 May 2026 and this file should not assert a live recurring debit it cannot
see. Confirm it is still running and it belongs in the projection.

## What the statements cannot show

Bank statements never print a payee for these:

- **Paper checks** — 10 totalling **$757.77**, all in 2025; none since
  December. Two at $120.00 (02/20, 03/07) and two at $160.00 (06/26, 08/05).
- **Venmo** — 50 outgoing payments totalling **$2,329.38**.
- **Zelle to individuals** — indistinguishable from any personal transfer.

That is **$3,087.15 of unattributable outbound payments**. If any was youth
sports, the totals above are low by that much. The matched pairs of checks are
the most suggestive: identical amounts, weeks apart, is what a registration
instalment looks like.

**One unclassified item:** $32.00 to Rutgers Athletics parking on 12/01/2025.
Left out — it reads as attending an event, but it could be tournament parking.

## Coverage

Seventeen statements, contiguous, no gaps: **12 Dec 2024 – 11 May 2026.**
Nothing after 11 May 2026 has been checked, so the 2026-27 tryout ($51.75,
5/28) and deposit ($672.75, 7/18) are not yet confirmed against the bank.

---

*Club figures transcribed from the portal's historical payments on 10 August
2026; bank figures parsed from seventeen TD statements. Every per-season and
per-month total reconciles to its source exactly. Deliberately excluded:
account and card numbers, transfer counterparties, and all non-sports
spending.*
