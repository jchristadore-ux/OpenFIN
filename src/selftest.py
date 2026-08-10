"""Self-test: fixed synthetic data with known-correct answers.

Run it from the Actions tab (the daily-brief workflow runs it before every
send) or locally with `python src/selftest.py`. It needs no secrets and makes
no network calls.

Covers the four cases that are easy to get wrong:

  1. A bill due on the 31st in February — must clamp to the 28th (and to the
     29th in a leap year).
  2. A biweekly bill crossing a month boundary.
  3. A bill already paid today, appearing as a PENDING transaction — must be
     listed as PAID and must NOT be subtracted again.
  4. A projection that goes negative on day 12 — the arithmetic is checked
     day by day against a hand-computed figure.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

import projection
from bills import clamp_day, money, occurrences
from simplefin_client import Transaction, parse_account_set, spendable_balance

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}\n          expected: {expected!r}\n          actual:   {actual!r}")
        FAILURES.append(label)


# --------------------------------------------------------------------------

def test_month_end_clamping() -> None:
    print("\n1. A bill on the 31st, in February")
    bill = {"id": "rent", "name": "Rent", "amount": 100, "frequency": "monthly",
            "due_day": 31, "active": True}

    check("31st clamps to 28 Feb 2026 (non-leap)",
          occurrences(bill, date(2026, 2, 1), date(2026, 2, 28)), [date(2026, 2, 28)])
    check("31st clamps to 29 Feb 2028 (leap)",
          occurrences(bill, date(2028, 2, 1), date(2028, 2, 29)), [date(2028, 2, 29)])
    check("31st stays the 31st in March",
          occurrences(bill, date(2026, 3, 1), date(2026, 3, 31)), [date(2026, 3, 31)])
    check("30th clamps to 28 Feb 2026",
          clamp_day(2026, 2, 30), date(2026, 2, 28))

    print("   ...and across a Jan-Mar window it fires exactly three times")
    hits = occurrences(bill, date(2026, 1, 1), date(2026, 3, 31))
    check("three occurrences, February clamped",
          hits, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])


def test_biweekly_across_month_boundary() -> None:
    print("\n2. A biweekly bill crossing a month boundary")
    bill = {"id": "tw", "name": "Trash", "amount": 92, "frequency": "biweekly",
            "anchor_date": "2026-08-05", "active": True}

    check("Aug 5 -> Sep 16, stepping 14 days over the boundary",
          occurrences(bill, date(2026, 8, 1), date(2026, 9, 20)),
          [date(2026, 8, 5), date(2026, 8, 19), date(2026, 9, 2), date(2026, 9, 16)])

    print("   ...and a window that starts mid-cycle picks up only later hits")
    check("window starting Aug 20 skips Aug 5 and Aug 19",
          occurrences(bill, date(2026, 8, 20), date(2026, 9, 5)), [date(2026, 9, 2)])

    print("   ...and an anchor in the future still resolves correctly")
    future = {"id": "f", "name": "F", "amount": 10, "frequency": "biweekly",
              "anchor_date": "2027-01-06", "active": True}
    check("anchor a year out, window on the anchor month",
          occurrences(future, date(2027, 1, 1), date(2027, 2, 1)),
          [date(2027, 1, 6), date(2027, 1, 20)])

    print("   ...weekly steps 7 days")
    weekly = {"id": "w", "name": "W", "amount": 10, "frequency": "weekly",
              "anchor_date": "2026-08-14", "active": True}
    check("weekly across the month boundary",
          occurrences(weekly, date(2026, 8, 25), date(2026, 9, 8)),
          [date(2026, 8, 28), date(2026, 9, 4)])


def test_already_paid_today() -> None:
    print("\n3. A bill already paid today, sitting as a PENDING transaction")
    today = date(2026, 8, 10)
    bill_list = [
        {"id": "mortgage", "name": "Mortgage", "amount": 1801.62, "frequency": "monthly",
         "due_day": 10, "match_keywords": ["crosscountry", "mortgage"], "active": True},
        {"id": "kia", "name": "Kia", "amount": 578.64, "frequency": "monthly",
         "due_day": 10, "match_keywords": ["kia"], "active": True},
    ]
    txns = [
        # Pending, posted == 0 (the spec allows it), 1.5% under the bill amount.
        Transaction("t1", "acct", None, money("-1775.00"),
                    "CROSSCOUNTRY MTG PMT", pending=True),
        # Genuinely discretionary.
        Transaction("t2", "acct", today, money("-64.20"), "WEGMANS", pending=False),
        # Excluded by keyword.
        Transaction("t3", "acct", today, money("-500.00"), "ONLINE PAYMENT THANK YOU",
                    pending=False),
    ]

    resolved, consumed = projection.resolve_bills_today(bill_list, txns, today)
    by_name = {b.name: b for b in resolved}

    check("both bills are due today", sorted(by_name), ["Kia", "Mortgage"])
    check("Mortgage is marked PAID", by_name["Mortgage"].paid, True)
    check("Kia is NOT marked paid", by_name["Kia"].paid, False)
    check("the matched transaction is consumed", consumed, {"t1"})

    disc = projection.discretionary_today(
        txns, today, consumed,
        exclude_keywords=["payment thank you"], included_accounts={"acct"},
    )
    check("discretionary excludes the bill match and the keyword hit",
          disc, money("64.20"))

    proj = projection.build(
        today=today, balance_now=money("3000.00"), used_fallback_balance=False,
        bills=bill_list, income=[], transactions=txns,
        projection_days=1, daily_allowance=money("100.00"),
        exclude_keywords=["payment thank you"], included_accounts={"acct"},
    )
    # 3000.00 - 578.64 (Kia only) - 35.80 (100 allowance less 64.20 spent)
    check("end of day subtracts the unpaid bill only",
          proj.end_of_day, money("2385.56"))
    check("bills-today total still shows both", proj.bills_today_total, money("2380.26"))
    check("unpaid subtotal is the Kia alone", proj.bills_today_unpaid, money("578.64"))

    print("   ...tolerance: 3% or $5, whichever is wider")
    check("$100 bill vs $103 charge matches (3%)",
          projection.amounts_match(money("100.00"), money("-103.00")), True)
    check("$100 bill vs $106 charge does not",
          projection.amounts_match(money("100.00"), money("-106.00")), False)
    check("$20 bill vs $24 charge matches ($5 floor beats 3%)",
          projection.amounts_match(money("20.00"), money("-24.00")), True)


def test_negative_on_day_twelve() -> None:
    print("\n4. A projection that goes negative on day 12")
    today = date(2026, 8, 10)
    # Start at 1,000, burn 50/day, and drop a 500 bill on day 12.
    # Today itself consumes one allowance, so the arithmetic is:
    #   today  close = 1000 - 50                =   950
    #   day 11 close =  950 - 50*11             =   400
    #   day 12 close =  400 - 500 - 50          =  -150   <- first negative
    #   day 13 close = -150 - 50                =  -200
    bill_list = [
        {"id": "big", "name": "Big Bill", "amount": 500.00, "frequency": "once",
         "due_date": "2026-08-22", "active": True},
    ]

    proj = projection.build(
        today=today, balance_now=money("1000.00"), used_fallback_balance=False,
        bills=bill_list, income=[], transactions=[],
        projection_days=20, daily_allowance=money("50.00"),
        exclude_keywords=[], included_accounts=set(),
    )

    check("today closes at the opening balance less the full allowance",
          proj.end_of_day, money("950.00"))

    by_date = {d.day: d for d in proj.days}
    check("day 11 (Aug 21) closes at 400", by_date[date(2026, 8, 21)].closing, money("400.00"))
    check("day 12 (Aug 22) is the first negative day, at -150",
          by_date[date(2026, 8, 22)].closing, money("-150.00"))
    check("day 13 (Aug 23) closes at -200", by_date[date(2026, 8, 23)].closing, money("-200.00"))

    critical = proj.critical_days(money("0.00"))
    check("first critical day is Aug 22", critical[0].day, date(2026, 8, 22))
    check("every day from Aug 22 on is critical", len(critical), 9)

    driver = projection.driver_for(by_date[date(2026, 8, 22)])
    check("the alert names the bill that caused it", driver, ("Big Bill", money("500.00")))

    print("   ...income lifts the line back up")
    proj2 = projection.build(
        today=today, balance_now=money("1000.00"), used_fallback_balance=False,
        bills=bill_list,
        income=[{"id": "pay", "name": "Payroll", "amount": 2000.00, "frequency": "once",
                 "due_date": "2026-08-24", "active": True}],
        transactions=[], projection_days=20, daily_allowance=money("50.00"),
        exclude_keywords=[], included_accounts=set(),
    )
    d24 = {d.day: d for d in proj2.days}[date(2026, 8, 24)]
    check("Aug 24 closes at 1750 after the deposit", d24.closing, money("1750.00"))
    check("only Aug 22 and Aug 23 remain critical",
          [d.day for d in proj2.critical_days(money("0.00"))],
          [date(2026, 8, 22), date(2026, 8, 23)])


def test_balance_and_transfers() -> None:
    print("\n5. Balance selection and internal transfers")

    v1 = {
        "errors": [],
        "accounts": [{
            "org": {"name": "TD Bank", "domain": "td.com"},
            "id": "ACT-1", "name": "Checking", "currency": "USD",
            "balance": "1000.00", "available-balance": "900.00",
            "balance-date": 1786000000,
            "transactions": [
                {"id": "p1", "posted": 0, "amount": "-100.00",
                 "description": "PENDING COFFEE", "pending": True},
            ],
        }],
    }
    parsed = parse_account_set(v1)
    total, fallback = spendable_balance(parsed.accounts)
    check("v1 shape parses", parsed.accounts[0].id, "ACT-1")
    check("institution name is folded in", parsed.accounts[0].name, "TD Bank Checking")
    check("available-balance is preferred", total, money("900.00"))
    check("no fallback flag when available-balance exists", fallback, False)

    v2 = {
        "errlist": [{"code": "con.auth", "msg": "Authentication required"}],
        "connections": [{"conn_id": "C1", "name": "My Bank", "org_id": "I1", "sfin_url": "x"}],
        "accounts": [{
            "id": "ACT-2", "name": "Savings", "conn_id": "C1", "conn_name": "My Bank",
            "currency": "USD", "balance": "1000.00", "balance-date": 1786000000,
            "transactions": [
                {"id": "p2", "posted": 0, "amount": "-250.00",
                 "description": "PENDING RENT", "pending": True},
                {"id": "p3", "posted": 0, "amount": "40.00",
                 "description": "PENDING REFUND", "pending": True},
            ],
        }],
    }
    parsed2 = parse_account_set(v2)
    total2, fallback2 = spendable_balance(parsed2.accounts)
    check("v2 errlist is surfaced", parsed2.errors, ["Authentication required"])
    check("fallback nets pending: 1000 - 250 + 40", total2, money("790.00"))
    check("fallback flag is set", fallback2, True)

    print("   ...internal transfers are not discretionary")
    today = date(2026, 8, 10)
    txns = [
        Transaction("d", "A", today, money("-300.00"), "TRANSFER TO SAVINGS"),
        Transaction("c", "B", today, money("300.00"), "TRANSFER FROM CHECKING"),
        Transaction("x", "A", today, money("-42.00"), "TARGET"),
    ]
    internal = projection.find_internal_transfers(txns, {"A", "B"})
    check("both legs are flagged", internal, {"d", "c"})
    disc = projection.discretionary_today(txns, today, set(), [], {"A", "B"})
    check("only the real purchase counts", disc, money("42.00"))


def test_alerting() -> None:
    print("\n6. Alert suppression and re-alerting")
    today = date(2026, 8, 10)
    proj = projection.build(
        today=today, balance_now=money("100.00"), used_fallback_balance=False,
        bills=[], income=[], transactions=[],
        projection_days=3, daily_allowance=money("60.00"),
        exclude_keywords=[], included_accounts=set(),
    )
    # closes: today 40, +1 -20, +2 -80, +3 -140
    first_negative = date(2026, 8, 11)

    fresh = projection.new_or_worsened(proj, money("0.00"), {})
    check("all three negative days alert when nothing is known", len(fresh), 3)

    known = {d.day.isoformat(): str(d.closing) for d in proj.critical_days(money("0.00"))}
    check("nothing re-alerts when nothing changed",
          projection.new_or_worsened(proj, money("0.00"), known), [])

    slightly_worse = dict(known)
    slightly_worse[first_negative.isoformat()] = "-10.00"    # was -10, now -20: a $10 drop
    check("a $10 drop does not re-alert",
          projection.new_or_worsened(proj, money("0.00"), slightly_worse), [])

    much_worse = dict(known)
    much_worse[first_negative.isoformat()] = "20.00"         # was +20, now -20: a $40 drop
    re_alerted = projection.new_or_worsened(proj, money("0.00"), much_worse)
    check("a $40 drop does re-alert", [d.day for d in re_alerted], [first_negative])


def main() -> int:
    print("Self-test — synthetic data, no network, no secrets.")
    test_month_end_clamping()
    test_biweekly_across_month_boundary()
    test_already_paid_today()
    test_negative_on_day_twelve()
    test_balance_and_transfers()
    test_alerting()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} CHECK(S) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
