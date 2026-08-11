"""Deferral intelligence: what could be moved, and how much time it buys.

This module only ever *recommends*. Nothing here changes a bill, cancels an
autopay, or touches the outside world — the household decides, the software
lays out the options. That boundary is deliberate and should stay.

The ranking rule is lowest-priority-first: exhaust tier 5 before touching tier
4, and never propose deferring anything secured or marked non-deferrable. A
mortgage is never on this list, however convenient the arithmetic looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from bills import money
from fincal import Event
from forecast import Forecast

TIER_LABEL = {
    1: "critical / secured",
    2: "essential",
    3: "required debt",
    4: "scheduled",
    5: "discretionary",
}


@dataclass
class Candidate:
    event: Event
    frees: Decimal

    @property
    def label(self) -> str:
        return TIER_LABEL.get(self.event.priority_tier, "unknown")


@dataclass
class Plan:
    shortfall: Decimal
    problem_day: date | None
    candidates: list[Candidate] = field(default_factory=list)
    covered: bool = False
    freed: Decimal = money(0)
    protected: list[Event] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.problem_day is None or self.shortfall <= 0:
            return "No shortfall to solve."
        if not self.candidates:
            return (
                f"Projected shortfall of ${self.shortfall:,.2f} on "
                f"{self.problem_day:%-d %b}, and nothing deferrable falls before it. "
                f"This one needs income or an outside arrangement."
            )
        names = ", ".join(f"{c.event.name} ${c.frees:,.2f}" for c in self.candidates)
        if self.covered:
            return (
                f"Deferring {names} before {self.problem_day:%-d %b} frees "
                f"${self.freed:,.2f} and covers the ${self.shortfall:,.2f} shortfall."
            )
        return (
            f"Deferring {names} frees ${self.freed:,.2f}, which is short of the "
            f"${self.shortfall:,.2f} needed by {self.problem_day:%-d %b}."
        )


def build_plan(forecast: Forecast, *, floor: Decimal | None = None) -> Plan:
    """Work out which deferrable bills before the problem day would close the gap.

    `floor` defaults to zero — the target is simply "do not go negative". Pass a
    minimum safe balance to plan against that instead.
    """
    floor = money(floor if floor is not None else 0)

    breach = forecast.first_day_below(floor)
    if breach is None:
        return Plan(shortfall=money(0), problem_day=None, covered=True)

    worst = min(
        (d.closing for d in forecast.days if d.day <= _last_day(forecast)),
        default=money(0),
    )
    shortfall = money(floor - worst)
    if shortfall <= 0:
        return Plan(shortfall=money(0), problem_day=None, covered=True)

    # Everything payable strictly before the breach is a lever; anything after it
    # cannot help, because the account is already short by then.
    movable: list[Event] = []
    protected: list[Event] = []
    for d in forecast.days:
        if d.day > breach.day:
            break
        for e in d.bills:
            if e.secured or not e.deferrable:
                protected.append(e)
            else:
                movable.append(e)

    # Lowest priority first, then largest — fewest disruptions to close the gap.
    movable.sort(key=lambda e: (-e.priority_tier, -e.amount))

    chosen: list[Candidate] = []
    freed = money(0)
    for e in movable:
        if freed >= shortfall:
            break
        chosen.append(Candidate(event=e, frees=e.amount))
        freed = money(freed + e.amount)

    return Plan(
        shortfall=shortfall,
        problem_day=breach.day,
        candidates=chosen,
        covered=freed >= shortfall,
        freed=freed,
        protected=protected,
    )


def days_bought(forecast: Forecast, plan: Plan, floor: Decimal | None = None) -> int:
    """How much later the breach happens if the plan is followed.

    Re-runs the same balance curve with the deferred amounts added back on the
    days they would have left the account. Returns 0 when nothing changes.
    """
    if not plan.candidates:
        return 0
    floor = money(floor if floor is not None else 0)
    deferred_ids = {(c.event.item_id, c.event.day) for c in plan.candidates}

    balance = forecast.opening_balance
    new_breach: date | None = None
    for d in forecast.days:
        out = sum(
            (e.amount for e in d.bills if (e.item_id, e.day) not in deferred_ids),
            money(0),
        )
        balance = money(
            balance
            + sum((e.amount for e in d.income), money(0))
            - out
            - d.discretionary
        )
        if balance < floor:
            new_breach = d.day
            break

    if plan.problem_day is None:
        return 0
    if new_breach is None:
        return (_last_day(forecast) - plan.problem_day).days
    return max(0, (new_breach - plan.problem_day).days)


def _last_day(forecast: Forecast) -> date:
    return forecast.days[-1].day if forecast.days else forecast.start
