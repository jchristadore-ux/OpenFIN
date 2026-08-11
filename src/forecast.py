"""The forecast engine: run the calendar forward from a known balance.

One authoritative projection. The old `projection.py` mixed three concerns —
reading the bank, deciding whether a bill had already cleared, and projecting
forward. Reading the bank is gone with SimpleFIN; what remains is arithmetic on
the calendar, which is testable without a network.

The daily rule, in order:

    opening = yesterday's closing (or the entered balance on day zero)
    + income landing today
    - bills landing today
    - the daily discretionary allowance
    = closing

Discretionary is charged every day including today. Charging it only on future
days would make today's number flattering, and today is the number someone acts
on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from bills import money
from fincal import IN, OUT, Calendar, Event, month_end, week_end


@dataclass
class Day:
    day: date
    opening: Decimal
    income: list[Event]
    bills: list[Event]
    discretionary: Decimal
    closing: Decimal

    @property
    def income_total(self) -> Decimal:
        return money(sum((e.amount for e in self.income), money(0)))

    @property
    def bills_total(self) -> Decimal:
        return money(sum((e.amount for e in self.bills), money(0)))


@dataclass
class Forecast:
    """A dated balance curve plus the summary numbers everything else reads."""

    start: date
    opening_balance: Decimal
    allowance: Decimal
    days: list[Day] = field(default_factory=list)

    # ---- point-in-time ----------------------------------------------------

    def at(self, day: date) -> Day | None:
        for d in self.days:
            if d.day == day:
                return d
        return None

    def closing_on(self, day: date) -> Decimal | None:
        d = self.at(day)
        return d.closing if d else None

    @property
    def end_of_day(self) -> Decimal:
        return self.days[0].closing if self.days else self.opening_balance

    @property
    def end_of_week(self) -> Decimal:
        return self.closing_on(week_end(self.start)) or self.end_of_day

    @property
    def end_of_month(self) -> Decimal:
        return self.closing_on(month_end(self.start)) or self.end_of_day

    # ---- the low point ----------------------------------------------------

    @property
    def minimum_day(self) -> Day | None:
        return min(self.days, key=lambda d: d.closing) if self.days else None

    @property
    def minimum_balance(self) -> Decimal:
        d = self.minimum_day
        return d.closing if d else self.opening_balance

    def first_day_below(self, threshold: Decimal) -> Day | None:
        for d in self.days:
            if d.closing < threshold:
                return d
        return None

    def days_below(self, threshold: Decimal) -> list[Day]:
        return [d for d in self.days if d.closing < threshold]

    @property
    def shortfall(self) -> Decimal:
        """How much would have to appear to keep every day at or above zero."""
        low = self.minimum_balance
        return money(-low) if low < 0 else money(0)


def run(
    calendar: Calendar,
    opening_balance: Decimal,
    start: date,
    days: int,
    allowance: Decimal,
) -> Forecast:
    """Project `days` days forward from `opening_balance`."""
    balance = money(opening_balance)
    allowance = money(allowance)
    out: list[Day] = []

    for offset in range(days):
        d = start + timedelta(days=offset)
        income = [e for e in calendar.on(d) if e.direction == IN]
        bills = [e for e in calendar.on(d) if e.direction == OUT]
        opening = balance
        balance = money(
            balance
            + sum((e.amount for e in income), money(0))
            - sum((e.amount for e in bills), money(0))
            - allowance
        )
        out.append(
            Day(
                day=d,
                opening=opening,
                income=income,
                bills=bills,
                discretionary=allowance,
                closing=balance,
            )
        )

    return Forecast(
        start=start,
        opening_balance=money(opening_balance),
        allowance=allowance,
        days=out,
    )


# --------------------------------------------------------------------------
# safe discretionary
# --------------------------------------------------------------------------

@dataclass
class Discretionary:
    """What is genuinely free to spend this week, and the arithmetic behind it.

    Deliberately NOT `income - bills`. That answers "did we earn more than we
    owe this month", which is a different and much less useful question than
    "can we spend money today without breaking something next week".
    """

    week_end: date
    starting_balance: Decimal
    income_this_week: Decimal
    bills_this_week: Decimal
    committed_beyond_week: Decimal
    buffer: Decimal
    safe: Decimal
    lookahead_days: int

    def explain(self) -> list[tuple[str, Decimal]]:
        """Line items, in the order they are applied. Signed for display."""
        return [
            ("Balance now", self.starting_balance),
            ("Income before end of week", self.income_this_week),
            ("Bills before end of week", -self.bills_this_week),
            (
                f"Committed in the {self.lookahead_days} days after that",
                -self.committed_beyond_week,
            ),
            ("Required cash buffer", -self.buffer),
            ("Safe to spend", self.safe),
        ]


def safe_discretionary(
    calendar: Calendar,
    balance: Decimal,
    today: date,
    buffer: Decimal,
    lookahead_days: int = 14,
) -> Discretionary:
    """Money that can be spent this week without creating a problem later.

    The lookahead is what makes this honest. Spending everything that is not
    owed *this week* is exactly how a mortgage two days into next week goes
    unpaid, so obligations landing shortly beyond the week boundary are reserved
    now. Income arriving in that same lookahead window is credited against them
    — reserving a bill while ignoring the paycheque that covers it would be just
    as wrong in the other direction.

    The result can be negative. A negative number is the honest answer to "how
    much can we spend" when the answer is "less than nothing"; it is returned
    unclamped and callers are expected to show it.
    """
    we = week_end(today)
    horizon = we + timedelta(days=lookahead_days)

    income_week = calendar.total_in(today, we)
    bills_week = calendar.total_out(today, we)

    beyond_out = calendar.total_out(we + timedelta(days=1), horizon)
    beyond_in = calendar.total_in(we + timedelta(days=1), horizon)
    committed = money(max(money(0), beyond_out - beyond_in))

    safe = money(balance + income_week - bills_week - committed - buffer)

    return Discretionary(
        week_end=we,
        starting_balance=money(balance),
        income_this_week=income_week,
        bills_this_week=bills_week,
        committed_beyond_week=committed,
        buffer=money(buffer),
        safe=safe,
        lookahead_days=lookahead_days,
    )
