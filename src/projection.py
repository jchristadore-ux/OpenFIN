"""Forward ledger and tight-day detection.

The order of operations here is the specification, restated:

  1. Today's balance including pending.
  2. Bills due today, split into already-paid and still-outstanding.
  3. The double-count guard decides that split. It is the single most
     important correctness rule in the system: a bill that has already hit
     the account must not be subtracted a second time, or every projected
     day after it is wrong by that amount.
  4. Today's actual discretionary spend.
  5. End of day = balance - unpaid bills today - the allowance not yet spent.
  6. Forward days: subtract bills, subtract the full daily allowance, add income.
  7. Any day at or below the critical threshold is CRITICAL; at or below the
     warning buffer is CAUTION.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from bills import money, occurrences
from simplefin_client import Transaction

MATCH_PCT = Decimal("0.03")      # +/- 3% of the bill amount
MATCH_ABS = Decimal("5.00")      # ...or +/- $5, whichever is wider
ALERT_WORSEN_BY = Decimal("25.00")


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class BillToday:
    id: str
    name: str
    amount: Decimal
    paid: bool
    matched_description: str = ""


@dataclass
class Day:
    day: date
    opening: Decimal
    bills: list[tuple[str, Decimal]]      # (name, amount) — positive = money out
    income: list[tuple[str, Decimal]]
    discretionary: Decimal                # money out
    closing: Decimal

    @property
    def is_today(self) -> bool:
        return False


@dataclass
class Projection:
    today: date
    balance_now: Decimal
    used_fallback_balance: bool
    bills_today: list[BillToday]
    discretionary_spent_today: Decimal
    allowance: Decimal
    end_of_day: Decimal
    days: list[Day] = field(default_factory=list)

    @property
    def bills_today_total(self) -> Decimal:
        return money(sum((b.amount for b in self.bills_today), money(0)))

    @property
    def bills_today_unpaid(self) -> Decimal:
        return money(sum((b.amount for b in self.bills_today if not b.paid), money(0)))

    def critical_days(self, threshold: Decimal) -> list[Day]:
        return [d for d in self.days if d.closing <= threshold]

    def caution_days(self, buffer_: Decimal, threshold: Decimal) -> list[Day]:
        return [d for d in self.days if threshold < d.closing <= buffer_]

    def tight_days(self, buffer_: Decimal) -> list[Day]:
        """Every day at or below the warning buffer, in date order."""
        return [d for d in self.days if d.closing <= buffer_]

    def next_tight_days(self, buffer_: Decimal, n: int = 3) -> list[Day]:
        """The next n tight days by date — the soonest trouble, not the worst.

        Ordering by date rather than by size is deliberate: the day you need
        to act on is the one arriving first, even if a later one dips deeper.
        """
        return self.tight_days(buffer_)[:n]


# --------------------------------------------------------------------------
# transaction classification
# --------------------------------------------------------------------------

def amounts_match(bill_amount: Decimal, txn_amount: Decimal) -> bool:
    """Within 3% or $5 of the bill, whichever tolerance is wider.

    txn_amount arrives signed (negative for a debit); compare magnitudes.
    """
    bill = abs(money(bill_amount))
    txn = abs(money(txn_amount))
    tolerance = max(bill * MATCH_PCT, MATCH_ABS)
    return abs(bill - txn) <= tolerance


def description_matches(keywords: list[str], description: str) -> bool:
    haystack = (description or "").lower()
    return any(kw.lower() in haystack for kw in keywords or [] if kw)


def transactions_on(transactions: list[Transaction], day: date) -> list[Transaction]:
    """Today's activity: everything posted today, plus everything pending.

    A pending transaction often has posted == 0 (the spec allows it), and it
    has already reduced the spendable balance either way, so it counts as
    today's activity regardless of what date it carries.
    """
    out = []
    for t in transactions:
        if t.pending or t.posted == day:
            out.append(t)
    return out


def find_internal_transfers(transactions: list[Transaction], included: set[str]) -> set[str]:
    """Transaction ids that are one leg of a transfer between our own accounts.

    A debit in account A paired with a credit of the same magnitude in
    account B on the same day is money moving, not money spent.
    """
    internal: set[str] = set()
    credits = [t for t in transactions if t.amount > 0 and t.account_id in included]

    for debit in transactions:
        if debit.amount >= 0 or debit.account_id not in included:
            continue
        for credit in credits:
            if credit.id in internal or credit.account_id == debit.account_id:
                continue
            if credit.posted != debit.posted:
                continue
            if abs(credit.amount) == abs(debit.amount):
                internal.add(debit.id)
                internal.add(credit.id)
                break

    return internal


def resolve_bills_today(
    bills: list[dict],
    transactions: list[Transaction],
    today: date,
) -> tuple[list[BillToday], set[str]]:
    """Split today's bills into paid and unpaid — the double-count guard.

    Returns (bills, consumed_transaction_ids). A transaction claimed by a bill
    is never also counted as discretionary spending.
    """
    todays_txns = transactions_on(transactions, today)
    debits = [t for t in todays_txns if t.is_debit]
    consumed: set[str] = set()
    resolved: list[BillToday] = []

    for bill in bills:
        if not occurrences(bill, today, today):
            continue

        amount = money(bill["amount"])
        match = None
        for txn in debits:
            if txn.id in consumed:
                continue
            if not description_matches(bill.get("match_keywords", []), txn.description):
                continue
            if not amounts_match(amount, txn.amount):
                continue
            match = txn
            break

        if match is not None:
            consumed.add(match.id)

        resolved.append(
            BillToday(
                id=bill["id"],
                name=bill["name"],
                amount=amount,
                paid=match is not None,
                matched_description=match.description if match else "",
            )
        )

    return resolved, consumed


def discretionary_today(
    transactions: list[Transaction],
    today: date,
    consumed: set[str],
    exclude_keywords: list[str],
    included_accounts: set[str],
) -> Decimal:
    """Sum of today's debits that are genuinely discretionary.

    Excluded: anything already claimed by a bill, anything matching an
    exclusion keyword, and both legs of an internal transfer.
    """
    todays = transactions_on(transactions, today)
    internal = find_internal_transfers(todays, included_accounts)

    total = money(0)
    for txn in todays:
        if not txn.is_debit:
            continue
        if txn.id in consumed or txn.id in internal:
            continue
        if description_matches(exclude_keywords, txn.description):
            continue
        total += abs(txn.amount)

    return money(total)


# --------------------------------------------------------------------------
# the projection
# --------------------------------------------------------------------------

def build(
    today: date,
    balance_now: Decimal,
    used_fallback_balance: bool,
    bills: list[dict],
    income: list[dict],
    transactions: list[Transaction],
    *,
    projection_days: int,
    daily_allowance: Decimal,
    exclude_keywords: list[str],
    included_accounts: set[str],
) -> Projection:
    balance_now = money(balance_now)
    daily_allowance = money(daily_allowance)

    bills_today, consumed = resolve_bills_today(bills, transactions, today)
    spent_today = discretionary_today(
        transactions, today, consumed, exclude_keywords, included_accounts
    )

    unpaid_today = money(sum((b.amount for b in bills_today if not b.paid), money(0)))
    remaining_allowance = max(money(0), daily_allowance - spent_today)
    end_of_day = money(balance_now - unpaid_today - remaining_allowance)

    proj = Projection(
        today=today,
        balance_now=balance_now,
        used_fallback_balance=used_fallback_balance,
        bills_today=bills_today,
        discretionary_spent_today=spent_today,
        allowance=daily_allowance,
        end_of_day=end_of_day,
    )

    running = end_of_day
    for offset in range(1, projection_days + 1):
        day = today + timedelta(days=offset)
        opening = running

        day_bills = [
            (b["name"], money(b["amount"]))
            for b in bills
            if occurrences(b, day, day)
        ]
        day_income = [
            (i["name"], money(i["amount"]))
            for i in income
            if occurrences(i, day, day)
        ]

        out = money(sum((amt for _, amt in day_bills), money(0)))
        inn = money(sum((amt for _, amt in day_income), money(0)))
        running = money(opening - out - daily_allowance + inn)

        proj.days.append(
            Day(
                day=day,
                opening=opening,
                bills=day_bills,
                income=day_income,
                discretionary=daily_allowance,
                closing=running,
            )
        )

    return proj


# --------------------------------------------------------------------------
# alerting
# --------------------------------------------------------------------------

def new_or_worsened(
    proj: Projection,
    threshold: Decimal,
    alerted_days: dict[str, str],
) -> list[Day]:
    """Critical days worth waking someone up about.

    Either the day is newly critical, or it was already known and has since
    dropped by more than $25 — the point at which a re-alert stops being noise.
    """
    out: list[Day] = []
    for day in proj.critical_days(threshold):
        key = day.day.isoformat()
        if key not in alerted_days:
            out.append(day)
            continue
        try:
            previous = money(alerted_days[key])
        except Exception:                                       # noqa: BLE001
            out.append(day)
            continue
        if previous - day.closing > ALERT_WORSEN_BY:
            out.append(day)
    return out


def driver_for(day: Day) -> tuple[str, Decimal] | None:
    """The largest single bill on a day — what to name in the alert."""
    if not day.bills:
        return None
    return max(day.bills, key=lambda pair: pair[1])
