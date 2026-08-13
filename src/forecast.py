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
        # Deferred occurrences do not leave the account, so they do not move the
        # curve. They are still on the calendar and still owed.
        bills = [e for e in calendar.on(d) if e.direction == OUT and not e.deferred]
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
# what is available to spend
# --------------------------------------------------------------------------

@dataclass
class Discretionary:
    """What is actually available to spend, derived and never assumed.

    Two numbers, both facts about the projection rather than judgements about it:

      headroom  The most that could leave the account today without any day in
                the window going below zero. It is the lowest point the curve
                reaches — spending today comes off every later day too, so the
                low point is the binding constraint.
      per_day   The largest flat daily amount that never puts a day below zero.
                Spending `x` every day means `n * x` is gone by day n, so the
                limit is the smallest `closing(n) / n` across the window. This
                is the answer to "what can we spend day to day".

    Both are negative when the window cannot be funded at all, and are returned
    unclamped: "less than nothing" is the honest answer to how much is free.

    WHAT IS DELIBERATELY NOT IN HERE

    A cash buffer, and an assumed rate of everyday spending. Both used to be,
    and both were assumptions dressed as arithmetic — the buffer was a judgement
    about how much to hold back, and the allowance was $110.56/day of spending
    the model invented on the household's behalf and then charged them for. The
    owner asked for neither. `per_day` replaces the allowance properly: instead
    of assuming a spending rate and deducting it, it derives the rate the
    forecast can actually carry.

    The curve behind both must therefore be run with `allowance=0` — bills and
    income only. `config.json` sets it there.
    """

    window_end: date
    window_days: int
    starting_balance: Decimal
    headroom: Decimal
    headroom_day: date | None
    per_day: Decimal
    per_day_day: date | None
    # The week, kept for display. Facts about what lands before Sunday, not
    # inputs to anything above.
    week_end: date
    income_this_week: Decimal
    bills_this_week: Decimal
    window_income: Decimal
    window_bills: Decimal
    window_close: Decimal

    @property
    def safe(self) -> Decimal:
        """The headline figure. Named `safe` because the whole system reads it."""
        return self.headroom

    def explain(self) -> list[tuple[str, Decimal]]:
        """Line items, in the order they are applied. Signed for display."""
        # The first three reconcile to where the window ends. The answer is not
        # that figure, though — it is the lowest point along the way, because
        # money spent today is gone on every day after it. Both are shown, in
        # that order, so the gap between them is visible rather than asserted.
        return [
            ("Balance now", self.starting_balance),
            (f"Income over the next {self.window_days} days", self.window_income),
            (f"Bills over the next {self.window_days} days", -self.window_bills),
            (f"Where that ends up, {self.window_end:%-d %b}", self.window_close),
            (
                "Lowest point along the way"
                + (f", {self.headroom_day:%-d %b}" if self.headroom_day else ""),
                self.headroom,
            ),
            ("Available to spend", self.headroom),
        ]


def available(
    calendar: Calendar,
    balance: Decimal,
    today: date,
    projection: Forecast,
    window_days: int = 30,
) -> Discretionary:
    """How much is available to spend, from the projection and nothing else.

    `projection` must have been run with no daily allowance, or the answer is
    net of spending the model assumed rather than spending that happened.

    The window is a choice and the only one left in here: 30 days covers a full
    mortgage cycle and every payday inside it, so nothing large hides just past
    the edge, while staying short enough that the figure still moves when
    something is fixed. Reaching further would let one distant annual bill hold
    the number negative for months while the next three weeks were comfortable.
    """
    # A curve that has already charged assumed spending cannot answer how much
    # spending is affordable — it has spent it. Silently returning a figure net
    # of an invented allowance is exactly the kind of quiet wrongness this
    # project refuses, so it fails here instead.
    if projection.allowance != 0:
        raise ValueError(
            f"available() needs a projection run with no daily allowance; this "
            f"one charges {projection.allowance} a day. Run forecast.run(...) "
            f"with allowance=0."
        )

    end = today + timedelta(days=window_days - 1)
    days = [d for d in projection.days if today <= d.day <= end]
    if not days:
        days = [Day(today, balance, [], [], money(0), money(balance))]

    low = min(days, key=lambda d: d.closing)

    # The tightest constraint on a flat daily spend. `n` counts today as the
    # first day, because money spent today is gone by the end of today.
    rate_day, rate = days[0], days[0].closing
    for n, d in enumerate(days, start=1):
        r = d.closing / n
        if r < rate:
            rate, rate_day = r, d

    we = week_end(today)
    return Discretionary(
        window_end=end,
        window_days=window_days,
        starting_balance=money(balance),
        headroom=money(low.closing),
        headroom_day=low.day,
        per_day=money(rate),
        per_day_day=rate_day.day,
        week_end=we,
        income_this_week=calendar.total_in(today, we),
        bills_this_week=calendar.total_out(today, we),
        window_income=calendar.total_in(today, end),
        window_bills=calendar.total_out(today, end),
        window_close=money(days[-1].closing),
    )
