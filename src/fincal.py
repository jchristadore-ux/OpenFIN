"""The financial calendar: the single place that turns obligations into dated events.

Everything downstream — forecast, risk, recommendations, the dashboard — reads
this and nothing else. That is deliberate. The old codebase computed occurrence
dates in three different places and they drifted apart; there is now exactly one
authority for the question "what moves on which day".

An Event is money crossing the account on a specific date. Outflows are positive
in `amount` and carry direction OUT; inflows carry direction IN. Nothing here
nets anything — netting is the forecast's job, and keeping it separate is what
makes "we have the money but the timing is wrong" detectable at all.

Money is Decimal throughout, as everywhere else in this project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from bills import load_items, money, occurrences

OUT = "out"
IN = "in"

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Event:
    """One dated cash movement."""

    day: date
    direction: str            # OUT | IN
    item_id: str
    name: str
    amount: Decimal           # always positive; direction carries the sign
    priority_tier: int = 5    # 1 critical .. 5 discretionary; income uses 0
    deferrable: bool = False
    secured: bool = False
    variable: bool = False
    confidence: str = "high"  # high | medium | low
    autopay: bool = False

    @property
    def signed(self) -> Decimal:
        """Positive when it increases the balance."""
        return self.amount if self.direction == IN else -self.amount

    @property
    def is_bill(self) -> bool:
        return self.direction == OUT


@dataclass
class Calendar:
    """Every event in [start, end], plus lookups the rest of the system wants."""

    start: date
    end: date
    events: list[Event] = field(default_factory=list)

    def on(self, day: date) -> list[Event]:
        return [e for e in self.events if e.day == day]

    def between(self, a: date, b: date) -> list[Event]:
        return [e for e in self.events if a <= e.day <= b]

    def bills_between(self, a: date, b: date) -> list[Event]:
        return [e for e in self.between(a, b) if e.direction == OUT]

    def income_between(self, a: date, b: date) -> list[Event]:
        return [e for e in self.between(a, b) if e.direction == IN]

    def total_out(self, a: date, b: date) -> Decimal:
        return money(sum((e.amount for e in self.bills_between(a, b)), money(0)))

    def total_in(self, a: date, b: date) -> Decimal:
        return money(sum((e.amount for e in self.income_between(a, b)), money(0)))

    def next_income_after(self, day: date) -> Event | None:
        later = sorted(
            (e for e in self.events if e.direction == IN and e.day > day),
            key=lambda e: e.day,
        )
        return later[0] if later else None


# --------------------------------------------------------------------------
# variable-amount handling
# --------------------------------------------------------------------------

def expected_amount(item: dict) -> Decimal:
    """What to forecast for this item.

    A variable bill is forecast at the TOP of its observed range, not the middle.
    Forecasting the average would make the projection right on paper and wrong in
    the direction that costs money: under-forecasting an outflow is what produces
    a surprise overdraft. The stated amount is used when no range is recorded.
    """
    amt = money(item.get("amount", 0))
    if not item.get("variable"):
        return amt
    hi = item.get("observed_max")
    return max(amt, money(hi)) if hi is not None else amt


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def _event_from(item: dict, day: date, direction: str) -> Event:
    return Event(
        day=day,
        direction=direction,
        item_id=item["id"],
        name=item["name"],
        amount=expected_amount(item) if direction == OUT else money(item["amount"]),
        priority_tier=int(item.get("priority_tier", 5)) if direction == OUT else 0,
        deferrable=bool(item.get("deferrable", False)),
        secured=bool(item.get("secured", False)),
        variable=bool(item.get("variable", False)),
        confidence=str(item.get("confidence", "high")),
        autopay=bool(item.get("autopay", False)),
    )


def build(
    bills: list[dict],
    income: list[dict],
    start: date,
    end: date,
) -> Calendar:
    """Expand every active obligation into dated events across the window."""
    events: list[Event] = []
    for item in bills:
        for day in occurrences(item, start, end):
            events.append(_event_from(item, day, OUT))
    for item in income:
        for day in occurrences(item, start, end):
            events.append(_event_from(item, day, IN))

    # Sort so that on any given day income lands before outflows. Real banks do
    # not guarantee this, but assuming the opposite would flag phantom shortfalls
    # on every payday. Ordering within a direction is by size, largest first, so
    # the biggest obligation is what a same-day shortfall gets attributed to.
    events.sort(key=lambda e: (e.day, 0 if e.direction == IN else 1, -e.amount))
    return Calendar(start=start, end=end, events=events)


def load_and_build(start: date, end: date, root: Path | None = None) -> Calendar:
    """Read bills.json and income.json, then build the calendar."""
    root = root or ROOT
    bills = load_items(root / "bills.json", "bills")
    income = load_items(root / "income.json", "income")
    return build(bills, income, start, end)


# --------------------------------------------------------------------------
# week / month helpers
# --------------------------------------------------------------------------

def week_end(day: date) -> date:
    """End of the household week, taken as the coming Sunday.

    Sunday is the boundary because groceries land every Sunday, which makes it
    the natural point for "what is left to spend this week" to reset.
    """
    return day + timedelta(days=(6 - day.weekday()) % 7)


def month_end(day: date) -> date:
    nxt = day.replace(day=28) + timedelta(days=4)
    return nxt - timedelta(days=nxt.day)
