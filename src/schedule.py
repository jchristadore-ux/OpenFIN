"""Payment-date optimisation: when should each bill actually be paid?

This answers a different question from `recommend.py`, and the difference is the
whole point of the module.

`recommend.py` asks "what could we *not pay* this time?" — a deferral drops the
occurrence out of the balance curve and it never lands again. That is the right
model for triage and the wrong model for scheduling, because the money is still
owed. A curve built from deferrals is optimistic by exactly the deferred amount.

This module asks "on what date should this bill *leave the account*?" Every
obligation is still paid, in full, once. Only the date moves, and the money is
charged on the new date. Total outflow over the window is therefore identical
before and after optimisation — a schedule that changes the total is a bug, and
`test_schedule.py` pins that invariant.

WHY MOVING A BILL LATER IS MONOTONICALLY SAFE FOR THE CURVE

Move an occurrence from day `d` to day `t > d`. Every day in `[d, t-1]` rises by
the amount; every day from `t` onward is unchanged, because the money has left
by then either way. So a later payment date never worsens any day, and strictly
improves the days in between. The only cost is the risk of paying late — which
is precisely why the flexibility window below is not allowed to be guessed.

WHAT THIS MODULE REFUSES TO INVENT

A bill's payment flexibility is a fact about a contract, not about arithmetic.
Nothing in `bills.json` currently records a due date as distinct from a
scheduled payment date, a grace period, or a late-fee term. So the default for
anything not explicitly recorded is UNKNOWN, and an UNKNOWN bill is never moved
later in the baseline plan. It is listed instead, with the exact amount that
confirming it would unlock, so the household can answer the question the data
cannot.

The one direction that needs no confirmation is *earlier*. Paying ahead of
schedule is never late, so `earliest` is derivable for every bill: no earlier
than today, and no earlier than one cadence before its scheduled date, so a
bill never doubles up inside its own period.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal

import forecast as fc
from bills import money
from fincal import IN, OUT, Calendar, Event

# ---- flexibility classification -------------------------------------------

FIXED = "FIXED"
FLEXIBLE = "FLEXIBLE"
UNKNOWN = "UNKNOWN"

# ---- how a classification was arrived at ----------------------------------

KNOWN = "KNOWN"          # stated in the data
INFERRED = "INFERRED"    # derived from a rule that needs no confirmation
NEEDS_USER = "UNKNOWN — USER CONFIRMATION REQUIRED"

# Roughly how long one period is, per frequency. Used only to stop a bill being
# pulled earlier than its own previous occurrence.
CADENCE_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "semimonthly": 15,
    "monthly": 28,
    "quarterly": 90,
    "annual": 365,
    "once": 365,
}

MAX_PASSES = 40          # episodes resolved per run; a guard, not a limit


@dataclass(frozen=True)
class Movability:
    """How far this occurrence may legitimately move, and on whose authority."""

    kind: str                  # FIXED | FLEXIBLE | UNKNOWN
    earliest_days: int         # relative to the scheduled date, <= 0
    latest_days: int           # relative to the scheduled date, >= 0
    basis: str                 # KNOWN | INFERRED | NEEDS_USER
    reason: str

    @property
    def can_move_later(self) -> bool:
        return self.latest_days > 0


def movability(item: dict) -> Movability:
    """Classify one bill from what `bills.json` actually records.

    Order matters: an explicit window beats every inference, and the two
    "cannot move" facts beat the absence of a window.
    """
    freq = item.get("frequency", "monthly")
    cadence = CADENCE_DAYS.get(freq, 28)
    # Paying early is never late. Cap it at one period so a bill cannot be
    # pulled back on top of its own previous occurrence.
    early = -(cadence - 1)

    window = item.get("payment_window")
    if isinstance(window, dict) and (
        "earliest_days" in window or "latest_days" in window
    ):
        lo = int(window.get("earliest_days", early))
        hi = int(window.get("latest_days", 0))
        lo = min(lo, 0)
        hi = max(hi, 0)
        return Movability(
            kind=FLEXIBLE if hi > 0 or lo < 0 else FIXED,
            earliest_days=lo,
            latest_days=hi,
            basis=KNOWN,
            reason=str(
                window.get("reason")
                or f"payment_window recorded in bills.json: {lo:+d} to {hi:+d} days"
            ),
        )

    if item.get("secured"):
        return Movability(
            kind=FIXED,
            earliest_days=early,
            latest_days=0,
            basis=KNOWN,
            reason=(
                "Secured against an asset. A late payment risks repossession or "
                "foreclosure, so this is never moved later."
            ),
        )

    if not item.get("deferrable", False):
        return Movability(
            kind=FIXED,
            earliest_days=early,
            latest_days=0,
            basis=KNOWN,
            reason=(
                "Recorded as non-deferrable in bills.json — missing it has a "
                "consequence the household has already flagged."
            ),
        )

    # Marked deferrable, but nothing records HOW LATE is still safe. Deferrable
    # says missing it for a while is survivable; it does not say the due date,
    # the grace period, or the late fee. Moving it later on that basis alone
    # would be inventing the one fact that decides whether this is safe.
    return Movability(
        kind=UNKNOWN,
        earliest_days=early,
        latest_days=0,
        basis=NEEDS_USER,
        reason=(
            "Marked deferrable, but no due date, grace period or late-fee term "
            "is recorded. How many days it may safely slip is not derivable "
            "from the repository."
        ),
    )


# ---- the plan --------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """One recommended change of payment date."""

    item_id: str
    name: str
    amount: Decimal
    from_day: date
    to_day: date
    tier: int
    basis: str
    confidence: str            # HIGH | MEDIUM | LOW
    reason: str

    @property
    def days_moved(self) -> int:
        return (self.to_day - self.from_day).days


@dataclass(frozen=True)
class Shortfall:
    """A negative episode that payment-date changes cannot fix."""

    start: date
    end: date
    worst_day: date
    worst_balance: Decimal
    needed: Decimal
    bills_responsible: list[tuple[str, Decimal, date]]
    next_income_day: date | None
    next_income_amount: Decimal
    why: str


@dataclass
class SchedulePlan:
    today: date
    opening_balance: Decimal
    baseline: fc.Forecast
    optimised: fc.Forecast
    moves: list[Move] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    blocked: list[tuple[Event, Movability]] = field(default_factory=list)
    conditional: bool = False

    # ---- headline numbers -------------------------------------------------

    @property
    def baseline_minimum(self) -> Decimal:
        return self.baseline.minimum_balance

    @property
    def optimised_minimum(self) -> Decimal:
        return self.optimised.minimum_balance

    @property
    def improvement(self) -> Decimal:
        return money(self.optimised_minimum - self.baseline_minimum)

    @property
    def negative_days_before(self) -> int:
        return len(self.baseline.days_below(money(0)))

    @property
    def negative_days_after(self) -> int:
        return len(self.optimised.days_below(money(0)))

    @property
    def dollars_moved(self) -> Decimal:
        return money(sum((m.amount for m in self.moves), money(0)))

    @property
    def solved(self) -> bool:
        return self.negative_days_after == 0


# ---- projection helpers ----------------------------------------------------


def _project(
    events: list[Event],
    balance: Decimal,
    today: date,
    days: int,
    horizon: date,
) -> fc.Forecast:
    """Run the authoritative curve over a (possibly rescheduled) event set.

    Deliberately goes through `forecast.run` rather than re-implementing the
    daily rule. There is one balance curve in this project and this is not a
    second one.
    """
    cal = Calendar(start=today, end=horizon, events=sorted(
        events, key=lambda e: (e.day, 0 if e.direction == IN else 1, -e.amount)
    ))
    return fc.run(cal, opening_balance=balance, start=today, days=days,
                  allowance=money(0))


def _episodes(forecast: fc.Forecast, floor: Decimal) -> list[tuple[int, int]]:
    """Contiguous runs of days whose closing balance sits below `floor`."""
    out: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, d in enumerate(forecast.days):
        if d.closing < floor:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            out.append((run_start, i - 1))
            run_start = None
    if run_start is not None:
        out.append((run_start, len(forecast.days) - 1))
    return out


def _confidence(mv: Movability, days_moved: int) -> str:
    if mv.basis == KNOWN:
        return "HIGH" if days_moved <= 14 else "MEDIUM"
    if mv.basis == INFERRED:
        return "MEDIUM"
    return "LOW"


# ---- the optimiser ---------------------------------------------------------


def optimise(
    calendar: Calendar,
    balance: Decimal,
    today: date,
    days: int,
    movabilities: dict[str, Movability],
    *,
    floor: Decimal | None = None,
    assume_unknown_days: int = 0,
) -> SchedulePlan:
    """Find the smallest set of payment-date changes that clears the negatives.

    `movabilities` maps bill id -> Movability. Anything absent is treated as
    FIXED, because an unclassified bill is not a licence to move it.

    `assume_unknown_days` is the ONLY way an UNKNOWN bill is ever moved later,
    it defaults to zero, and any plan built with it set is marked
    `conditional=True`. It exists so the report can show what confirming those
    windows would buy, clearly labelled as conditional on the household's
    answer — never mixed in with what the data already supports.

    Objectives, in the order the user set them:
      1. no negative days
      2. keep a buffer (the floor, if one is passed)
      3. fewest date changes
      4. do not move a bill that does not need to move
      5. do not pile payments onto the days just after income
    """
    floor = money(floor if floor is not None else 0)
    horizon = calendar.end
    window_end = today + timedelta(days=days - 1)

    # Events are tracked positionally, never by value. Two identical bills can
    # legitimately fall on the same day, and a frozen dataclass keyed by value
    # would silently merge them into one.
    events = list(calendar.events)
    orig_day = [e.day for e in events]
    baseline = _project(events, balance, today, days, horizon)

    moved_to: dict[int, date] = {}
    shortfalls: list[Shortfall] = []
    blocked: list[tuple[Event, Movability]] = []
    give_up: set[date] = set()

    for _ in range(MAX_PASSES):
        current = _project(events, balance, today, days, horizon)
        eps = [
            (a, b) for a, b in _episodes(current, floor)
            if current.days[a].day not in give_up
        ]
        if not eps:
            break

        a, b = eps[0]
        ep_days = current.days[a:b + 1]
        ep_start, ep_end = ep_days[0].day, ep_days[-1].day
        worst = min(ep_days, key=lambda d: d.closing)
        deficit = money(floor - worst.closing)
        target = ep_end + timedelta(days=1)

        # Everything payable inside or before the episode is a lever; a bill
        # after it cannot help, because the money is already short by then.
        candidates: list[tuple[int, Movability]] = []
        for i, e in enumerate(events):
            if e.direction != OUT or e.deferred:
                continue
            if e.day > ep_end or e.day < today:
                continue
            mv = movabilities.get(e.item_id)
            if mv is None:
                continue
            latest_days = mv.latest_days
            if mv.kind == UNKNOWN and assume_unknown_days > 0:
                latest_days = assume_unknown_days
            # Clearing the episode means landing after it ends; anything less
            # leaves the low point exactly where it was. A target past the end
            # of the projection is not usable either — the money would simply
            # fall off the window and the totals would stop reconciling.
            if (
                latest_days <= 0
                or target > orig_day[i] + timedelta(days=latest_days)
                or target > window_end
            ):
                if e.day >= ep_start:
                    blocked.append((e, mv))
                continue
            candidates.append((i, mv))

        # Least critical first, then largest, so the gap closes in the fewest
        # moves; ties break toward the shortest displacement.
        candidates.sort(
            key=lambda c: (-events[c[0]].priority_tier, -events[c[0]].amount,
                           (target - events[c[0]].day).days)
        )

        chosen: list[int] = []
        freed = money(0)
        for i, _mv in candidates:
            if freed >= deficit:
                break
            chosen.append(i)
            freed = money(freed + events[i].amount)

        if freed < deficit:
            # Nothing available closes it. Record it honestly, apply whatever
            # partial help exists, and step past this episode so the later ones
            # are still examined.
            nxt = min(
                (e for e in events if e.direction == IN and e.day > worst.day),
                key=lambda e: e.day,
                default=None,
            )
            responsible = sorted(
                ((e.name, e.amount, e.day) for d in ep_days for e in d.bills),
                key=lambda r: -r[1],
            )[:6]
            shortfalls.append(Shortfall(
                start=ep_start,
                end=ep_end,
                worst_day=worst.day,
                worst_balance=money(worst.closing),
                needed=money(deficit - freed),
                bills_responsible=responsible,
                next_income_day=nxt.day if nxt else None,
                next_income_amount=nxt.amount if nxt else money(0),
                why=(
                    f"Bills before {ep_end:%-d %b} whose payment date may "
                    f"legitimately move total {fmt(freed)}, less than the "
                    f"{fmt(deficit)} needed. Everything else on those days is "
                    f"fixed, so no change of payment date closes this gap."
                    if freed > 0 else
                    f"Nothing due on or before {ep_end:%-d %b} has a payment "
                    f"date that may legitimately move later, so no schedule "
                    f"change closes this gap."
                ),
            ))
            give_up.add(ep_start)
        else:
            # Drop any move the episode turns out not to need. Objective 4:
            # never move a bill that was not load-bearing.
            keep = list(chosen)
            for i in sorted(chosen, key=lambda i: (events[i].priority_tier,
                                                   events[i].amount)):
                trial = [j for j in keep if j != i]
                if money(sum((events[j].amount for j in trial), money(0))) >= deficit:
                    keep = trial
            chosen = keep
            if not chosen:
                give_up.add(ep_start)

        for i in chosen:
            events[i] = replace(events[i], day=target)
            moved_to[i] = target

    optimised = _project(events, balance, today, days, horizon)

    # Turn the move map into reportable records, reading amounts and tiers off
    # the ORIGINAL events so the report never has to guess.
    records: list[Move] = []
    for i, to_day in sorted(moved_to.items(), key=lambda kv: orig_day[kv[0]]):
        e = calendar.events[i]
        from_day = orig_day[i]
        mv = movabilities.get(e.item_id)
        records.append(Move(
            item_id=e.item_id,
            name=e.name,
            amount=e.amount,
            from_day=from_day,
            to_day=to_day,
            tier=e.priority_tier,
            basis=mv.basis if mv else NEEDS_USER,
            confidence=_confidence(mv, (to_day - from_day).days) if mv else "LOW",
            reason=(
                f"{from_day:%-d %b} sits inside a projected negative stretch; "
                f"{to_day:%-d %b} is the first day the balance has recovered."
            ),
        ))

    # De-duplicate blocked entries, keeping the first sighting of each bill.
    seen: set[tuple[str, date]] = set()
    unique_blocked = []
    for e, mv in blocked:
        key = (e.item_id, e.day)
        if key in seen:
            continue
        seen.add(key)
        unique_blocked.append((e, mv))

    return SchedulePlan(
        today=today,
        opening_balance=money(balance),
        baseline=baseline,
        optimised=optimised,
        moves=records,
        shortfalls=shortfalls,
        blocked=unique_blocked,
        conditional=assume_unknown_days > 0,
    )


def fmt(v: Decimal) -> str:
    v = money(v)
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def build_movabilities(items: list[dict]) -> dict[str, Movability]:
    """Classify every bill once, keyed by id."""
    return {b["id"]: movability(b) for b in items if b.get("id")}


def conserved(plan: SchedulePlan) -> bool:
    """Total outflow must be identical before and after. A schedule that
    changes what is owed is not a schedule, it is a deletion."""
    before = money(sum(
        (d.bills_total for d in plan.baseline.days), money(0)
    ))
    after = money(sum(
        (d.bills_total for d in plan.optimised.days), money(0)
    ))
    return before == after
