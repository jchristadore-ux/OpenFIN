# Youth sports — what it actually costs

Rebuilt from the club payment portal's historical payments for Lia, covering
May 2023 through July 2026, and cross-checked against TD Bank statements for
12 Dec 2024 – 11 Oct 2025. Every figure below is a real posted payment except
the three clearly marked estimates for the rest of the 2026-27 season.

**The lacrosse club is about $2,490 a year, or $207 a month.** That number has
barely moved in three seasons.

**But lacrosse is roughly half of it.** The bank statements show youth sports
running at **$496.40/month** all in — see [the full picture](#the-full-picture)
below. Two things drove the gap: the club portal is not a complete record of
what was paid to the club, and several large recurring costs never appear in
the portal at all.

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

Everything above comes from the club portal. This section comes from the bank
statements, which see things the portal does not.

## The portal is not a complete record

The bank shows **$529.63 paid toward lacrosse that the portal never lists**:

| Posted | Payee | Amount | In portal? |
|---|---|---:|---|
| 01/03/2025 | NJ Total Lacrosse | $232.88 | **No** |
| 01/15/2025 | Team travel — Crab Cake tourn. | $80.00 | **No** |
| 02/03/2025 | US Lacrosse membership | $35.00 | **No** |
| 02/10/2025 | US Lacrosse membership | $60.00 | **No** |
| 02/19/2025 | US Lacrosse membership | $60.00 | **No** |
| 03/12/2025 | US Lacrosse membership | $35.00 | **No** |
| 04/08/2025 | Roxbury Lacrosse (SportsEngine) | $26.75 | **No** |

The $232.88 is almost certainly a $225.00 program plus the 3.5% card fee —
the same price point as the Girls Academy Classes. **Treat the $2,488.83/yr
season figure as a floor, not a total.**

## Youth sports by month

Posted dates, 12 Dec 2024 – 11 Oct 2025. December 2024 and October 2025 are
partial months.

| Month | High Bridge | Field hockey | Lacrosse | Other athletics | Total |
|---|---:|---:|---:|---:|---:|
| 2024-12 | — | — | — | $179.00 | $179.00 |
| 2025-01 | — | — | $312.88 | $179.00 | $491.88 |
| 2025-02 | — | — | $646.63 | $199.00 | $845.63 |
| 2025-03 | — | — | $35.00 | $179.00 | $214.00 |
| 2025-04 | — | — | $518.38 | $179.00 | $697.38 |
| 2025-05 | $95.00 | — | $491.63 | $179.00 | $765.63 |
| 2025-06 | — | $120.00 | — | $339.00 | $459.00 |
| 2025-07 | — | — | $181.13 | −$30.00 | $151.13 |
| 2025-08 | — | $51.94 | — | — | $51.94 |
| 2025-09 | $125.00 | — | $621.00 | $45.00 | $791.00 |
| 2025-10 | $145.00 | — | — | — | $145.00 |
| **Total** | **$365.00** | **$171.94** | **$2,806.65** | **$1,448.00** | **$4,791.59** |

Across the nine complete months (Jan–Sep 2025): **$4,467.59, or $496.40 per
month — an annualised $5,956.79.**

## Who the money actually went to

**High Bridge — $365.00.** There is no payee named "High Bridge Recreation".
The money went to two separate organisations:

- High Bridge Youth Soccer, $95.00 on 05/05/2025, via PayPal
- High Bridge Youth Basketball (`hbyb.org`), $125.00 on 09/22/2025 and
  $145.00 on 10/02/2025

**Field hockey — $171.94.** North Hunterdon Lions Field Hockey, $120.00 on
06/04/2025, plus two USA Field Hockey memberships at $25.97 (08/04, 08/27).

**Other athletics — $1,448.00.** Dominated by **Parisi Speed School at
$179.00/month**, charged seven times from 12/19/2024 to 06/20/2025 for
**$1,253.00**, then stopped. Nothing after June. Also Gymnastics Unlimited
($20.00 + $160.00, less a $30.00 refund) and Girl Scouts ($45.00).

Parisi alone was running at **86% of the lacrosse club's monthly cost** while
it was active, and it appears nowhere in any club portal.

## What the statements cannot tell you

Bank statements never print a payee for these, so nothing below can be ruled
in or out:

- **Paper checks** — ten in the window, including two at $120.00 (02/20,
  03/07) and two at $160.00 (06/26, 08/05).
- **Venmo** — 33 outgoing payments. The recipient is replaced by a token.
- **Zelle to individuals** — if a club treasurer collects by Zelle, it is
  indistinguishable from any other personal transfer.

If any youth-sports money moved by check or Venmo, it is not in the totals
above and the real figure is higher.

## Coverage

Ten statements, contiguous, 12 Dec 2024 – 11 Oct 2025. **Missing: 12 Oct –
11 Dec 2025.** Basketball was mid-season and billing monthly when the record
ends, so the High Bridge total is the one most likely to grow.

---

*Club figures transcribed from the portal's historical payments on 10 August
2026; bank figures parsed from TD statements the same day. Every per-season
and per-month total reconciles to its source exactly. Deliberately excluded:
account and card numbers, transfer counterparties, and all non-sports
spending.*
