"""Tests for the financial engine.

Run with:  python -m unittest discover -s tests -v

Synthetic data throughout — no network, no real balances, no secrets. The
household scenarios near the bottom are modelled on the real shape of this
household's finances (biweekly mortgage, weekly + biweekly income, a wall of
monthly debt) because that shape is what the engine has to get right.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fincal                                                    # noqa: E402
import forecast as fc                                            # noqa: E402
import recommend                                                 # noqa: E402
import risk as riskmod                                           # noqa: E402
from bills import BillError, money                               # noqa: E402

TODAY = date(2026, 8, 11)          # a Tuesday
D = money


def bill(id_, name, amount, freq="monthly", day=None, due=None, anchor=None,
         tier=3, deferrable=True, secured=False, variable=False, hi=None):
    b = {
        "id": id_, "name": name, "amount": amount, "frequency": freq,
        "due_day": day, "due_date": due, "anchor_date": anchor, "active": True,
        "priority_tier": tier, "deferrable": deferrable, "secured": secured,
        "variable": variable, "match_keywords": [],
    }
    if hi is not None:
        b["observed_max"] = hi
    return b


def income(id_, name, amount, freq="weekly", anchor=None, day=None, due=None):
    return {
        "id": id_, "name": name, "amount": amount, "frequency": freq,
        "due_day": day, "due_date": due, "anchor_date": anchor, "active": True,
        "match_keywords": [],
    }


def cal(bills, incomes, days=90, start=TODAY):
    return fincal.build(bills, incomes, start, start + timedelta(days=days))


def project(bills, incomes, balance, allowance="0", days=60, start=TODAY):
    c = cal(bills, incomes, days=days + 30, start=start)
    return c, fc.run(c, D(balance), start, days, D(allowance))


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------

class TestCalendar(unittest.TestCase):
    def test_monthly_bill_lands_on_due_day(self):
        c = cal([bill("b", "B", 100, day=20)], [])
        days = [e.day for e in c.events]
        self.assertIn(date(2026, 8, 20), days)
        self.assertIn(date(2026, 9, 20), days)

    def test_biweekly_bill_steps_14_days(self):
        c = cal([bill("m", "M", 1801.62, freq="biweekly", anchor="2026-08-10")], [])
        days = sorted(e.day for e in c.events)
        gaps = {(days[i + 1] - days[i]).days for i in range(len(days) - 1)}
        self.assertEqual(gaps, {14})

    def test_biweekly_yields_26_a_year_not_24(self):
        c = cal([bill("m", "M", 100, freq="biweekly", anchor="2026-01-05")],
                [], days=363, start=date(2026, 1, 5))
        self.assertEqual(len(c.events), 26)

    def test_quarterly_modelled_as_dated_once_entries(self):
        c = cal([bill("q1", "Q1", 177.30, freq="once", due="2026-10-07"),
                 bill("q2", "Q2", 177.30, freq="once", due="2027-01-07")],
                [], days=200)
        self.assertEqual([e.day for e in c.events],
                         [date(2026, 10, 7), date(2027, 1, 7)])

    def test_annual_expense_appears(self):
        c = cal([bill("a", "Sewer", 304.50, freq="annual", due="2026-09-01")], [])
        self.assertEqual([e.day for e in c.events], [date(2026, 9, 1)])

    def test_income_sorts_before_bills_same_day(self):
        c = cal([bill("b", "B", 50, day=11)], [income("i", "Pay", 500, anchor="2026-08-11")])
        same = c.on(date(2026, 8, 11))
        self.assertEqual(same[0].direction, fincal.IN)

    def test_inactive_items_are_excluded(self):
        b = bill("b", "B", 100, day=20)
        b["active"] = False
        self.assertEqual(cal([b], []).events, [])

    def test_variable_bill_forecasts_at_top_of_range(self):
        c = cal([bill("e", "Electric", 137.79, day=20, variable=True, hi=305.12)], [])
        self.assertEqual(c.events[0].amount, D("305.12"))

    def test_weekly_variable_uses_the_mean_not_the_max(self):
        # Regression: groceries were forecast at their worst-ever week, every
        # week - $791.32 x 52 = $41,148 against an actual $16,593.
        b = bill("g", "Groceries", 319.09, freq="weekly", anchor="2026-08-16",
                 variable=True, hi=791.32)
        self.assertEqual(fincal.expected_amount(b, date(2026, 8, 16)), D("319.09"))

    def test_biweekly_variable_also_uses_the_mean(self):
        b = bill("f", "Fuel", 90.77, freq="biweekly", anchor="2026-08-16",
                 variable=True, hi=447.12)
        self.assertEqual(fincal.expected_amount(b, date(2026, 8, 16)), D("90.77"))

    def test_semimonthly_variable_uses_the_mean(self):
        # Raised in review on PR #30: semimonthly fires 24 times a year, the
        # same class as biweekly, so it must not forecast at the maximum.
        b = bill("s", "Semi", 100, freq="semimonthly", day=1,
                 variable=True, hi=900)
        self.assertEqual(fincal.expected_amount(b, date(2026, 8, 1)), D("100"))

    def test_frequency_cutoff_is_by_occurrence_count(self):
        # The rule is a count, not a list of labels, so a new frequency cannot
        # silently land on the wrong side of it.
        for freq, expect_mean in [("weekly", True), ("biweekly", True),
                                  ("semimonthly", True), ("monthly", False),
                                  ("quarterly", False)]:
            per_year = fincal.OCCURRENCES_PER_YEAR[freq]
            self.assertEqual(per_year >= fincal.FREQUENT_DRAW, expect_mean, freq)

    def test_monthly_variable_still_uses_the_max(self):
        # The forecast-high rule still earns its keep where a bill lands once.
        b = bill("e", "Electric", 137.79, day=20, variable=True, hi=305.12)
        self.assertEqual(fincal.expected_amount(b, date(2026, 8, 20)), D("305.12"))

    def test_seasonal_profile_beats_observed_max(self):
        b = bill("g", "Gas", 54.03, day=11, variable=True, hi=550.83)
        b["monthly_expected"] = {"3": "550.83", "8": "54.03"}
        aug = fincal.expected_amount(b, date(2026, 8, 11))
        mar = fincal.expected_amount(b, date(2026, 3, 11))
        self.assertEqual(aug, D("54.03"))
        self.assertEqual(mar, D("550.83"))

    def test_seasonal_profile_falls_back_when_month_absent(self):
        b = bill("g", "Gas", 100, day=11, variable=True, hi=550.83)
        b["monthly_expected"] = {"3": "550.83"}
        self.assertEqual(fincal.expected_amount(b, date(2026, 8, 11)), D("550.83"))

    def test_calendar_applies_the_profile_per_occurrence(self):
        b = bill("e", "Electric", 130, day=3, variable=True, hi=448.77)
        b["monthly_expected"] = {"8": "448.77", "11": "130.28"}
        c = cal([b], [], days=120, start=date(2026, 8, 1))
        by = {e.day.month: e.amount for e in c.events}
        self.assertEqual(by[8], D("448.77"))
        self.assertEqual(by[11], D("130.28"))

    def test_week_end_is_the_coming_sunday(self):
        self.assertEqual(fincal.week_end(date(2026, 8, 11)), date(2026, 8, 16))
        self.assertEqual(fincal.week_end(date(2026, 8, 16)), date(2026, 8, 16))


# --------------------------------------------------------------------------
# forecast
# --------------------------------------------------------------------------

class TestForecast(unittest.TestCase):
    def test_end_of_day_subtracts_todays_bill_and_allowance(self):
        _, f = project([bill("b", "B", 100, day=11)], [], "1000", allowance="50")
        self.assertEqual(f.end_of_day, D("850"))

    def test_income_adds_on_its_day(self):
        _, f = project([], [income("i", "Pay", 500, anchor="2026-08-12")],
                       "100", allowance="0")
        self.assertEqual(f.closing_on(date(2026, 8, 12)), D("600"))

    def test_minimum_balance_and_its_date(self):
        _, f = project([bill("b", "B", 900, freq="once", due="2026-08-20")],
                       [], "1000", allowance="0")
        self.assertEqual(f.minimum_balance, D("100"))
        self.assertEqual(f.minimum_day.day, date(2026, 8, 20))

    def test_shortfall_is_zero_when_never_negative(self):
        _, f = project([], [], "1000", allowance="0")
        self.assertEqual(f.shortfall, D("0"))

    def test_shortfall_equals_depth_of_the_hole(self):
        _, f = project([bill("b", "B", 1500, freq="once", due="2026-08-15")],
                       [], "1000", allowance="0")
        self.assertEqual(f.shortfall, D("500"))

    def test_allowance_compounds_daily(self):
        _, f = project([], [], "1000", allowance="100", days=5)
        self.assertEqual(f.days[4].closing, D("500"))

    def test_month_rollover_is_handled(self):
        _, f = project([bill("b", "B", 10, day=31)], [], "1000",
                       allowance="0", days=200)
        days = [d.day for d in f.days if d.bills]
        self.assertIn(date(2026, 9, 30), days)      # clamps to a short month

    def test_year_rollover_is_handled(self):
        _, f = project([bill("b", "B", 10, day=5)], [], "1000",
                       allowance="0", days=200, start=date(2026, 12, 1))
        self.assertIn(date(2027, 1, 5), [d.day for d in f.days if d.bills])


# --------------------------------------------------------------------------
# discretionary
# --------------------------------------------------------------------------

class TestAvailableToSpend(unittest.TestCase):
    """The figure the whole app is built around.

    Two numbers, both read off a curve that carries bills and income and nothing
    else. No buffer, no assumed rate of everyday spending: the owner asked for
    neither, and both were judgements dressed as arithmetic.
    """

    def _avail(self, bills, incomes, balance, window=30):
        c = cal(bills, incomes, days=window + 60)
        f = fc.run(c, D(balance), TODAY, window + 30, D("0"))
        return fc.available(c, D(balance), TODAY, f, window)

    # ---- headroom: the most that could go today ---------------------------
    def test_with_no_bills_the_whole_balance_is_available(self):
        d = self._avail([], [], "5000")
        self.assertEqual(d.headroom, D("5000"))

    def test_nothing_is_held_back_as_a_buffer(self):
        # The old model subtracted a $500 floor here. It no longer does.
        self.assertEqual(self._avail([], [], "500").headroom, D("500"))

    def test_no_everyday_spending_is_assumed(self):
        # 30 days x $110.56 used to come off this figure before anyone spent it.
        self.assertEqual(self._avail([], [], "5000").headroom, D("5000"))

    def test_a_bill_in_the_window_reduces_it(self):
        d = self._avail([bill("b", "B", 1000, day=14)], [], "5000")
        self.assertEqual(d.headroom, D("4000"))

    def test_it_is_the_low_point_not_the_end_point(self):
        """A bill before the paycheque that covers it still binds."""
        d = self._avail(
            [bill("m", "M", 1800, freq="once", due="2026-08-18")],
            [income("p", "Pay", 1800, freq="once", due="2026-08-19")],
            "2000",
        )
        self.assertEqual(d.headroom, D("200"))          # not 2000
        self.assertEqual(d.headroom_day, date(2026, 8, 18))

    def test_spending_the_headroom_leaves_the_low_day_at_zero(self):
        """The definition, stated as a property."""
        d = self._avail([bill("b", "B", 1200, day=20)], [], "3000")
        c = cal([bill("b", "B", 1200, day=20)], [], days=90)
        f = fc.run(c, D("3000") - d.headroom, TODAY, 60, D("0"))
        self.assertEqual(min(x.closing for x in f.days
                             if x.day <= d.window_end), D("0"))

    def test_a_crunch_past_the_window_does_not_count(self):
        far = (TODAY + timedelta(days=45)).isoformat()
        d = self._avail([bill("a", "Annual", 9000, freq="once", due=far)], [], "3000")
        self.assertEqual(d.headroom, D("3000"))

    def test_negative_headroom_is_returned_unclamped(self):
        d = self._avail([bill("b", "B", 3000, day=14)], [], "1000")
        self.assertEqual(d.headroom, D("-2000"))

    # ---- per day: what can be spent day to day ----------------------------
    def test_per_day_spreads_the_headroom_across_the_window(self):
        d = self._avail([], [], "3000", window=30)
        # Nothing else moves, so the tightest day is the last: 3000 / 30.
        self.assertEqual(d.per_day, D("100"))

    def test_spending_the_rate_every_day_never_goes_below_zero(self):
        """The other definition, stated as a property, day by day."""
        bills = [bill("m", "M", 1800, freq="once", due="2026-08-18"),
                 bill("c", "C", 300, day=25)]
        incomes = [income("p", "Pay", 1500, freq="weekly", anchor="2026-08-14")]
        d = self._avail(bills, incomes, "2500")
        c = cal(bills, incomes, days=90)
        f = fc.run(c, D("2500"), TODAY, 60, D("0"))
        for n, day in enumerate([x for x in f.days if x.day <= d.window_end], start=1):
            self.assertGreaterEqual(day.closing - d.per_day * n, D("0"),
                                    f"day {n} ({day.day}) would go negative")

    def test_a_dollar_a_day_more_would_break_a_day(self):
        """It is the LARGEST such rate, not merely a safe one."""
        bills = [bill("m", "M", 1800, freq="once", due="2026-08-18")]
        d = self._avail(bills, [], "2500")
        c = cal(bills, [], days=90)
        f = fc.run(c, D("2500"), TODAY, 60, D("0"))
        rate = d.per_day + D("1")
        self.assertTrue(any(day.closing - rate * n < 0
                            for n, day in enumerate(
                                [x for x in f.days if x.day <= d.window_end], start=1)))

    def test_per_day_is_negative_when_the_bills_do_not_fit(self):
        d = self._avail([bill("b", "B", 3000, day=14)], [], "1000")
        self.assertLess(d.per_day, 0)

    def test_income_later_in_the_window_lifts_the_rate(self):
        without = self._avail([], [], "3000")
        with_pay = self._avail([], [income("p", "Pay", 900, freq="once",
                                           due="2026-08-20")], "3000")
        self.assertGreater(with_pay.per_day, without.per_day)

    # ---- the explanation ---------------------------------------------------
    def test_explain_ends_on_the_answer(self):
        d = self._avail([bill("b", "B", 400, day=14)], [], "1000")
        lines = d.explain()
        self.assertEqual(lines[-1][0], "Available to spend")
        self.assertEqual(lines[-1][1], d.headroom)

    def test_explain_mentions_no_buffer_and_no_allowance(self):
        labels = " ".join(l for l, _ in self._avail([], [], "1000").explain()).lower()
        self.assertNotIn("buffer", labels)
        self.assertNotIn("allowance", labels)

    def test_the_week_figures_are_still_reported_for_display(self):
        d = self._avail([bill("b", "B", 400, day=14)],
                        [income("p", "Pay", 100, freq="once", due="2026-08-13")],
                        "1000")
        self.assertEqual(d.bills_this_week, D("400"))
        self.assertEqual(d.income_this_week, D("100"))
        self.assertEqual(d.week_end, date(2026, 8, 16))

    def test_timing_changes_the_answer_not_just_totals(self):
        early = self._avail([bill("b", "B", 900, freq="once", due="2026-08-13")],
                            [], "1000")
        late = self._avail([bill("b", "B", 900, freq="once", due="2026-09-30")],
                           [], "1000")
        self.assertNotEqual(early.headroom, late.headroom)


# --------------------------------------------------------------------------
# risk engine
# --------------------------------------------------------------------------

class TestRisk(unittest.TestCase):
    def _detect(self, bills, incomes, balance, allowance="0",
                floor="500", large="750"):
        c, f = project(bills, incomes, balance, allowance=allowance)
        d = fc.available(c, D(balance), TODAY, f)
        return riskmod.detect(f, c, d, minimum_safe_balance=D(floor),
                              large_payment_threshold=D(large), today=TODAY)

    def test_clean_finances_produce_no_risk(self):
        self.assertEqual(self._detect([], [], "50000", floor="500"), [])

    def test_negative_balance_detected(self):
        rs = self._detect([bill("b", "B", 2000, freq="once", due="2026-08-15")],
                          [], "1000")
        self.assertIn(riskmod.NEGATIVE_BALANCE, [r.type for r in rs])

    def test_secured_payment_risk_is_critical(self):
        rs = self._detect(
            [bill("m", "Mortgage", 1800, freq="once", due="2026-08-20",
                  tier=1, deferrable=False, secured=True)],
            [], "100")
        sec = [r for r in rs if r.type == riskmod.SECURED_PAYMENT]
        self.assertTrue(sec)
        self.assertEqual(sec[0].severity, "critical")

    def test_secured_payment_not_flagged_when_funded(self):
        # Regression: an earlier version flagged every secured bill after the
        # first negative day, even ones with plenty of money on the day.
        rs = self._detect(
            [bill("x", "X", 5000, freq="once", due="2026-08-13"),
             bill("m", "Mortgage", 100, freq="once", due="2026-09-20",
                  tier=1, deferrable=False, secured=True)],
            [income("i", "Pay", 20000, freq="once", due="2026-08-14")],
            "1000")
        self.assertEqual([r for r in rs if r.type == riskmod.SECURED_PAYMENT], [])

    def test_insufficient_cash_when_positive_but_under_floor(self):
        rs = self._detect([bill("b", "B", 700, freq="once", due="2026-08-15")],
                          [], "1000", floor="500")
        types = [r.type for r in rs]
        self.assertIn(riskmod.INSUFFICIENT_CASH, types)
        self.assertNotIn(riskmod.NEGATIVE_BALANCE, types)

    def test_large_payment_risk(self):
        rs = self._detect([bill("b", "Big", 900, freq="once", due="2026-08-20")],
                          [], "100", large="750")
        self.assertIn(riskmod.LARGE_PAYMENT, [r.type for r in rs])

    def test_income_timing_risk_when_month_works_but_order_does_not(self):
        rs = self._detect(
            [bill("b", "B", 3000, freq="once", due="2026-08-13")],
            [income("i", "Pay", 4000, freq="once", due="2026-08-25"),
             income("w", "Weekly", 1000, freq="weekly", anchor="2026-08-20")],
            "500")
        self.assertIn(riskmod.INCOME_TIMING, [r.type for r in rs])

    def test_future_crunch_when_this_week_is_fine(self):
        rs = self._detect(
            [bill("b", "B", 2000, freq="once", due="2026-09-20")],
            [], "2200", floor="500")
        self.assertIn(riskmod.FUTURE_CRUNCH, [r.type for r in rs])

    def test_negative_discretionary_raises_a_risk(self):
        rs = self._detect([bill("b", "B", 4000, day=14)], [], "1000")
        self.assertIn(riskmod.DISCRETIONARY, [r.type for r in rs])


# --------------------------------------------------------------------------
# deduplication
# --------------------------------------------------------------------------

class TestDedup(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 11, 7, 0)
        self.r = riskmod.Risk(
            id="negative_balance:2026-08-20", type=riskmod.NEGATIVE_BALANCE,
            severity="critical", title="t", detail="d",
            amount=D("500"), when=date(2026, 8, 20))

    def test_new_risk_notifies(self):
        notify, resolved, state = riskmod.triage([self.r], {}, now=self.now)
        self.assertEqual(len(notify), 1)
        self.assertIn(self.r.id, state)

    def test_unchanged_risk_stays_quiet(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        notify, _, _ = riskmod.triage([self.r], state,
                                      now=self.now + timedelta(hours=6))
        self.assertEqual(notify, [])

    def test_materially_worse_risk_renotifies(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        worse = riskmod.Risk(**{**self.r.__dict__, "amount": D("900")})
        notify, _, _ = riskmod.triage([worse], state,
                                      now=self.now + timedelta(hours=6))
        self.assertEqual(len(notify), 1)

    def test_small_change_does_not_renotify(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        nudge = riskmod.Risk(**{**self.r.__dict__, "amount": D("540")})
        notify, _, _ = riskmod.triage([nudge], state,
                                      now=self.now + timedelta(hours=6))
        self.assertEqual(notify, [])

    def test_date_move_renotifies(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        moved = riskmod.Risk(**{**self.r.__dict__, "when": date(2026, 8, 15)})
        notify, _, _ = riskmod.triage([moved], state,
                                      now=self.now + timedelta(hours=6))
        self.assertEqual(len(notify), 1)

    def test_reminder_fires_after_configured_days(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        notify, _, _ = riskmod.triage([self.r], state,
                                      now=self.now + timedelta(days=4),
                                      reminder_days=3)
        self.assertEqual(len(notify), 1)

    def test_resolution_is_reported(self):
        _, _, state = riskmod.triage([self.r], {}, now=self.now)
        _, resolved, new_state = riskmod.triage([], state, now=self.now)
        self.assertEqual(resolved, [self.r.id])
        self.assertEqual(new_state, {})

    def test_risk_returning_after_resolution_notifies_again(self):
        _, _, s1 = riskmod.triage([self.r], {}, now=self.now)
        _, _, s2 = riskmod.triage([], s1, now=self.now)
        notify, _, _ = riskmod.triage([self.r], s2, now=self.now)
        self.assertEqual(len(notify), 1)


# --------------------------------------------------------------------------
# deferral recommendations
# --------------------------------------------------------------------------

class TestRecommend(unittest.TestCase):
    def test_no_shortfall_means_no_plan(self):
        _, f = project([], [], "5000", allowance="0")
        self.assertIsNone(recommend.build_plan(f).problem_day)

    def test_secured_bills_are_never_proposed(self):
        _, f = project(
            [bill("m", "Mortgage", 3000, freq="once", due="2026-08-13",
                  tier=1, deferrable=False, secured=True)],
            [], "1000", allowance="0")
        plan = recommend.build_plan(f)
        self.assertEqual(plan.candidates, [])
        self.assertIn("Mortgage", [e.name for e in plan.protected])

    def test_lowest_priority_is_offered_first(self):
        _, f = project(
            [bill("lux", "Netflix", 400, freq="once", due="2026-08-12", tier=5),
             bill("debt", "Loan", 400, freq="once", due="2026-08-12", tier=3),
             bill("x", "Big", 1500, freq="once", due="2026-08-14",
                  tier=1, deferrable=False, secured=True)],
            [], "1000", allowance="0")
        plan = recommend.build_plan(f)
        self.assertEqual(plan.candidates[0].event.name, "Netflix")

    def test_plan_reports_when_it_covers_the_gap(self):
        _, f = project(
            [bill("a", "Sub", 900, freq="once", due="2026-08-12", tier=5),
             bill("b", "Rent", 1000, freq="once", due="2026-08-14",
                  tier=1, deferrable=False, secured=True)],
            [], "1000", allowance="0")
        plan = recommend.build_plan(f)
        self.assertTrue(plan.covered)
        self.assertGreaterEqual(plan.freed, plan.shortfall)

    def test_plan_admits_when_it_cannot_cover_the_gap(self):
        _, f = project(
            [bill("a", "Sub", 20, freq="once", due="2026-08-12", tier=5),
             bill("b", "Rent", 5000, freq="once", due="2026-08-14",
                  tier=1, deferrable=False, secured=True)],
            [], "1000", allowance="0")
        plan = recommend.build_plan(f)
        self.assertFalse(plan.covered)
        self.assertIn("short of", plan.summary)

    def test_days_bought_is_positive_when_deferral_helps(self):
        _, f = project(
            [bill("a", "Sub", 900, freq="once", due="2026-08-12", tier=5),
             bill("b", "Rent", 1000, freq="once", due="2026-08-13",
                  tier=1, deferrable=False, secured=True)],
            [], "1000", allowance="0")
        plan = recommend.build_plan(f)
        self.assertGreater(recommend.days_bought(f, plan), 0)

    def test_recommendations_never_mutate_the_bills(self):
        b = bill("a", "Sub", 900, freq="once", due="2026-08-12", tier=5)
        before = dict(b)
        _, f = project([b, bill("r", "Rent", 1000, freq="once", due="2026-08-13",
                                tier=1, deferrable=False, secured=True)],
                       [], "1000", allowance="0")
        recommend.build_plan(f)
        self.assertEqual(b, before)


# --------------------------------------------------------------------------
# household-shaped scenarios
# --------------------------------------------------------------------------

def household(balance, allowance="0"):
    """The real household's shape. Allowance is 0: the engine no longer charges
    assumed everyday spending, and `available()` refuses a curve that does."""
    bills = [
        bill("mortgage", "Mortgage", 1801.62, freq="biweekly", anchor="2026-08-24",
             tier=1, deferrable=False, secured=True),
        bill("kia", "Kia", 742.65, day=24, tier=1, deferrable=False, secured=True),
        bill("upstart", "Upstart", 1244.29, day=15, tier=3, deferrable=False),
        bill("hanover", "Hanover", 513.27, day=12, tier=2, deferrable=False),
        bill("netflix", "Netflix", 21.31, day=23, tier=5),
        bill("lowes", "Lowes", 122.00, day=18, tier=5),
        bill("lax", "Lacrosse", 491.63, freq="once", due="2026-10-06", tier=4),
    ]
    incomes = [
        income("ig", "Insight Global", 2016.06, freq="weekly", anchor="2026-08-12"),
        income("boe", "BOE", 2829.31, freq="biweekly", anchor="2026-08-19"),
    ]
    c, f = project(bills, incomes, balance, allowance=allowance, days=60)
    d = fc.available(c, D(balance), TODAY, f)
    rs = riskmod.detect(f, c, d, minimum_safe_balance=D("500"),
                        large_payment_threshold=D("750"), today=TODAY)
    return c, f, d, rs


class TestHouseholdScenarios(unittest.TestCase):
    def test_healthy_balance_has_room(self):
        _, _, d, _ = household("20000")
        self.assertGreater(d.safe, 0)

    def test_real_world_thin_balance_leaves_nothing_to_spend(self):
        """On $118.25 the bills fit, but only just, and nothing is free.

        This used to assert the projection went negative. It did — because the
        curve charged $216.98 a day of everyday spending nobody had agreed to.
        With that gone, the honest statement is narrower and more useful: the
        bills clear, and there is nothing spare while they do.
        """
        _, f, d, rs = household("118.25")
        self.assertGreaterEqual(f.minimum_balance, 0)      # the bills do fit
        self.assertEqual(d.headroom, D("118.25"))          # nothing spare
        self.assertLess(d.per_day, D("35"))                # a few dollars a day
        self.assertTrue(rs)
        self.assertIn(riskmod.INSUFFICIENT_CASH, [r.type for r in rs])

    def test_a_thin_balance_still_warns_before_it_goes_wrong(self):
        # The $500 line survives as an alert threshold even though it is no
        # longer held back from the spending figure.
        _, _, _, rs = household("118.25")
        low = [r for r in rs if r.type == riskmod.INSUFFICIENT_CASH]
        self.assertTrue(low, "a balance under the floor must still raise a risk")

    def test_annual_expense_is_visible_months_ahead(self):
        c, _, _, _ = household("5000")
        names = [e.name for e in c.bills_between(date(2026, 10, 1), date(2026, 10, 31))]
        self.assertIn("Lacrosse", names)

    def test_biweekly_mortgage_hits_three_times_in_some_months(self):
        c, _, _, _ = household("5000")
        aug = [e for e in c.bills_between(date(2026, 8, 24), date(2026, 10, 5))
               if e.name == "Mortgage"]
        self.assertEqual(len(aug), 4)               # 24 Aug, 7, 21 Sep, 5 Oct

    def test_engine_is_deterministic(self):
        a = household("1500")[1].minimum_balance
        b = household("1500")[1].minimum_balance
        self.assertEqual(a, b)

    def test_discretionary_explanation_adds_up_on_real_shape(self):
        _, _, d, _ = household("1500")
        balance, income, bills, ends_up, low, answer = d.explain()
        self.assertEqual(balance[1] + income[1] + bills[1], ends_up[1])
        self.assertEqual(low[1], answer[1])
        self.assertLessEqual(low[1], ends_up[1])


# --------------------------------------------------------------------------
# bill edits from the app
# --------------------------------------------------------------------------

class TestApplyEdits(unittest.TestCase):
    def setUp(self):
        import json, tempfile
        import apply_edits
        self.mod = apply_edits
        self.tmp = Path(tempfile.mkdtemp()) / "bills.json"
        payload = {"bills": [
            bill("netflix", "Netflix", 21.31, day=23, tier=5),
            bill("mortgage", "Mortgage", 1801.62, freq="biweekly",
                 anchor="2026-06-29", tier=1, deferrable=False, secured=True),
        ]}
        self.tmp.write_text(json.dumps(payload), encoding="utf-8")

    def _bills(self):
        # save_items writes a bare array when the file carries no "_" keys, and
        # a wrapped object when it does. Both are valid per the data model, so
        # read it back through the loader rather than assuming a shape.
        from bills import load_items
        return {b["id"]: b for b in load_items(self.tmp, "bills")}

    def test_amount_and_day_apply(self):
        out = self.mod.apply([{"id": "netflix", "amount": "24.99", "due_day": 25}], self.tmp)
        self.assertEqual(len(out), 1)
        n = self._bills()["netflix"]
        self.assertEqual(money(n["amount"]), D("24.99"))
        self.assertEqual(n["due_day"], 25)

    def test_unknown_id_is_rejected(self):
        with self.assertRaises(self.mod.EditError):
            self.mod.apply([{"id": "nope", "amount": "5"}], self.tmp)

    def test_absurd_amount_is_rejected(self):
        with self.assertRaises(self.mod.EditError):
            self.mod.apply([{"id": "netflix", "amount": "999999"}], self.tmp)

    def test_bad_due_day_is_rejected(self):
        with self.assertRaises(self.mod.EditError):
            self.mod.apply([{"id": "netflix", "due_day": 44}], self.tmp)

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(self.mod.EditError):
            self.mod.apply([{"id": "netflix", "amount": "-5"}], self.tmp)

    def test_large_change_applies_but_flags(self):
        self.mod.apply([{"id": "netflix", "amount": "100.00"}], self.tmp)
        self.assertTrue(self._bills()["netflix"]["needs_review"])

    def test_small_change_does_not_flag(self):
        self.mod.apply([{"id": "netflix", "amount": "22.00"}], self.tmp)
        self.assertFalse(self._bills()["netflix"].get("needs_review", False))

    def test_deactivate_keeps_the_entry(self):
        self.mod.apply([{"id": "netflix", "active": False}], self.tmp)
        self.assertIn("netflix", self._bills())
        self.assertFalse(self._bills()["netflix"]["active"])

    def test_all_or_nothing(self):
        before = self._bills()["netflix"]["amount"]
        with self.assertRaises(self.mod.EditError):
            self.mod.apply(
                [{"id": "netflix", "amount": "25.00"}, {"id": "nope", "amount": "1"}],
                self.tmp,
            )
        self.assertEqual(self._bills()["netflix"]["amount"], before)

    def test_no_op_edit_changes_nothing(self):
        self.assertEqual(self.mod.apply([{"id": "netflix", "amount": "21.31"}], self.tmp), [])

    def test_previous_note_is_preserved(self):
        self.mod.apply([{"id": "netflix", "amount": "23.00"}], self.tmp)
        self.assertIn("Previous note", self._bills()["netflix"]["note"])

    def test_too_many_edits_refused(self):
        with self.assertRaises(self.mod.EditError):
            self.mod.parse_edits(json.dumps([{"id": "netflix"}] * 61))


# --------------------------------------------------------------------------
# deferrals
# --------------------------------------------------------------------------

class TestDeferrals(unittest.TestCase):
    def _setup(self, deferrals=None):
        bills = [
            bill("big", "Big loan", 1000, freq="once", due="2026-08-14", tier=3),
            bill("small", "Small", 100, freq="once", due="2026-08-13", tier=5),
            bill("later", "Later", 500, freq="once", due="2026-08-20", tier=3),
        ]
        c = fincal.build(bills, [], TODAY, TODAY + timedelta(days=60), deferrals)
        f = fc.run(c, D("2000"), TODAY, 60, D("0"))
        d = fc.available(c, D("2000"), TODAY, f)
        return c, f, d

    def test_deferred_bill_does_not_move_the_curve(self):
        _, f0, _ = self._setup()
        _, f1, _ = self._setup({("big", date(2026, 8, 14))})
        self.assertEqual(f1.end_of_week - f0.end_of_week, D("1000"))

    def test_deferred_bill_raises_safe_to_spend(self):
        _, _, d0 = self._setup()
        _, _, d1 = self._setup({("big", date(2026, 8, 14))})
        self.assertEqual(d1.safe - d0.safe, D("1000"))

    def test_deferring_beyond_the_week_also_counts(self):
        _, _, d0 = self._setup()
        _, _, d1 = self._setup({("later", date(2026, 8, 20))})
        self.assertEqual(d1.safe - d0.safe, D("500"))

    def test_deferred_event_is_marked_not_deleted(self):
        c, _, _ = self._setup({("big", date(2026, 8, 14))})
        allb = c.bills_between(TODAY, TODAY + timedelta(days=30), include_deferred=True)
        self.assertIn("Big loan", [e.name for e in allb])
        self.assertTrue([e for e in allb if e.item_id == "big"][0].deferred)

    def test_deferred_total_reports_what_is_still_owed(self):
        c, _, _ = self._setup({("big", date(2026, 8, 14)), ("small", date(2026, 8, 13))})
        self.assertEqual(c.deferred_total(TODAY, TODAY + timedelta(days=30)), D("1100"))

    def test_default_lists_exclude_deferred(self):
        c, _, _ = self._setup({("big", date(2026, 8, 14))})
        names = [e.name for e in c.bills_between(TODAY, TODAY + timedelta(days=30))]
        self.assertNotIn("Big loan", names)

    def test_a_deferral_for_another_date_does_not_match(self):
        _, _, d0 = self._setup()
        _, _, d1 = self._setup({("big", date(2026, 9, 14))})
        self.assertEqual(d0.safe, d1.safe)


class TestDeferralState(unittest.TestCase):
    def setUp(self):
        import engine
        self.engine = engine

    def test_past_deferrals_are_dropped_on_read(self):
        state = {"deferrals": [
            {"bill_id": "a", "date": "2026-08-01"},
            {"bill_id": "b", "date": "2026-08-20"},
        ]}
        got = self.engine.load_deferrals(state, TODAY)
        self.assertEqual(got, {("b", date(2026, 8, 20))})

    def test_malformed_entries_are_ignored_not_fatal(self):
        state = {"deferrals": [{"bill_id": "a"}, {"date": "nope"}, {}]}
        self.assertEqual(self.engine.load_deferrals(state, TODAY), set())

    def test_recording_replaces_rather_than_merges(self):
        now = datetime(2026, 8, 11, 9, 0)
        st = {"deferrals": [{"bill_id": "old", "date": "2026-08-20"}]}
        self.engine.record_deferrals(st, [{"bill_id": "new", "date": "2026-08-21"}], now)
        self.assertEqual([d["bill_id"] for d in st["deferrals"]], ["new"])

    def test_empty_list_clears_every_deferral(self):
        now = datetime(2026, 8, 11, 9, 0)
        st = {"deferrals": [{"bill_id": "old", "date": "2026-08-20"}]}
        self.engine.record_deferrals(st, [], now)
        self.assertEqual(st["deferrals"], [])

    def test_backdated_deferrals_are_refused(self):
        now = datetime(2026, 8, 11, 9, 0)
        st = {}
        self.engine.record_deferrals(st, [{"bill_id": "a", "date": "2026-08-01"}], now)
        self.assertEqual(st["deferrals"], [])


class TestAddingIncome(unittest.TestCase):
    """Income added from the app.

    An overstated income is the one edit that makes the forecast optimistic, so
    these tests are mostly about what is refused.
    """

    def setUp(self):
        import add_income
        self.mod = add_income
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "income.json"
        # With the documentation wrapper the real file carries, so the test
        # exercises the same save path production does.
        self.path.write_text(
            json.dumps({"_comments": ["docs"], "income": []}), encoding="utf-8")
        self.now = datetime(2026, 8, 13, 9, 0)

    def _add(self, **kw):
        spec = {"name": "Side work", "amount": "250",
                "frequency": "weekly", "date": "2026-08-21"}
        spec.update(kw)
        return self.mod.add(self.mod.parse(spec), self.path, now=self.now)

    def _saved(self):
        return json.loads(self.path.read_text())["income"]

    # ---- the date means what the frequency needs it to mean --------------
    def test_a_one_off_falls_on_the_date_given(self):
        item = self._add(frequency="once", date="2026-09-04")
        self.assertEqual(item["due_date"], "2026-09-04")
        self.assertIsNone(item["anchor_date"])
        self.assertIsNone(item["due_day"])

    def test_a_weekly_income_anchors_to_the_date_given(self):
        item = self._add(frequency="weekly", date="2026-08-21")
        self.assertEqual(item["anchor_date"], "2026-08-21")
        self.assertIsNone(item["due_day"])

    def test_a_monthly_income_takes_its_day_from_the_date_given(self):
        item = self._add(frequency="monthly", date="2026-09-19")
        self.assertEqual(item["due_day"], 19)
        self.assertIsNone(item["due_date"])
        self.assertIsNone(item["anchor_date"])

    def test_every_frequency_the_app_offers_produces_a_valid_entry(self):
        for i, freq in enumerate(sorted(self.mod.DATE_FIELD)):
            item = self._add(name=f"Income {i}", frequency=freq, date="2026-09-19")
            self.assertEqual(item["frequency"], freq)     # validate() ran inside

    # ---- what it refuses --------------------------------------------------
    def test_a_nameless_income_is_refused(self):
        with self.assertRaises(self.mod.IncomeError):
            self._add(name=" ")

    def test_zero_and_negative_are_refused(self):
        for bad in ("0", "-100"):
            with self.assertRaises(self.mod.IncomeError):
                self._add(amount=bad)

    def test_an_implausible_amount_is_refused_as_a_typo(self):
        with self.assertRaises(self.mod.IncomeError):
            self._add(amount="250000")

    def test_an_unknown_frequency_is_refused(self):
        with self.assertRaises(self.mod.IncomeError):
            self._add(frequency="fortnightly")

    def test_a_date_that_is_not_a_date_is_refused(self):
        with self.assertRaises(BillError):
            self._add(date="next tuesday")

    def test_adding_the_same_income_twice_is_refused(self):
        self._add()
        with self.assertRaises(self.mod.IncomeError) as ctx:
            self._add()
        self.assertIn("twice", str(ctx.exception))

    def test_nothing_is_written_when_an_entry_is_refused(self):
        with self.assertRaises(self.mod.IncomeError):
            self._add(amount="0")
        self.assertEqual(self._saved(), [])

    # ---- how it is stored -------------------------------------------------
    def test_a_new_income_is_flagged_for_review(self):
        self.assertTrue(self._add()["needs_review"])
        self.assertEqual(self._saved()[0]["source"], "app")

    def test_the_id_comes_from_the_name_and_stays_unique(self):
        first = self._add(name="Side work")
        second = self._add(name="Side work", amount="300")
        self.assertEqual(first["id"], "side-work")
        self.assertEqual(second["id"], "side-work-2")

    def test_a_name_of_punctuation_still_gets_a_usable_id(self):
        item = self._add(name="!!! ??? !!!")
        self.assertRegex(item["id"], r"^[a-z0-9][a-z0-9-]*$")

    def test_adding_appends_and_never_overwrites(self):
        self._add(name="One")
        self._add(name="Two")
        self.assertEqual([i["name"] for i in self._saved()], ["One", "Two"])

    def test_the_files_documentation_block_survives_a_write(self):
        self._add()
        self.assertEqual(json.loads(self.path.read_text())["_comments"], ["docs"])


class TestIncomeReachesTheForecast(unittest.TestCase):
    """Adding income has to move the forecast, not just the file."""

    def setUp(self):
        import add_income
        import apply_edits
        import engine
        self.mod, self.apply_edits, self.engine = add_income, apply_edits, engine
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bills.json").write_text(json.dumps(
            [bill("rent", "Rent", 1200, day=20, tier=1)]), encoding="utf-8")
        (self.tmp / "income.json").write_text(
            json.dumps({"_comments": ["docs"], "income": []}), encoding="utf-8")
        self._root, self._snap = engine.ROOT, engine.SNAPSHOT
        engine.ROOT, engine.SNAPSHOT = self.tmp, self.tmp / "snapshot.json"
        self.addCleanup(self._restore)

    def _restore(self):
        self.engine.ROOT, self.engine.SNAPSHOT = self._root, self._snap

    def _snapshot(self):
        settings = self.engine.Settings()
        state = {"balance": {"amount": "1000.00",
                             "entered_at": "2026-08-11T09:00:00",
                             "date": "2026-08-11"}}
        now = datetime(2026, 8, 11, 9, 0)
        r = self.engine.analyse(settings, state, now)
        return self.engine.write_snapshot(r, settings, now)

    def _add(self, **kw):
        spec = {"name": "Side work", "amount": "400",
                "frequency": "weekly", "date": "2026-08-14"}
        spec.update(kw)
        return self.mod.add(self.mod.parse(spec), self.tmp / "income.json",
                            now=datetime(2026, 8, 13, 9, 0))

    def test_recurring_income_lifts_the_whole_forecast(self):
        before = self._snapshot()
        self._add()
        after = self._snapshot()
        self.assertGreater(D(after["end_of_week"]), D(before["end_of_week"]))
        self.assertGreater(D(after["end_of_month"]), D(before["end_of_month"]))
        self.assertGreater(D(after["minimum_balance"]), D(before["minimum_balance"]))
        self.assertGreater(D(after["discretionary"]["safe"]),
                           D(before["discretionary"]["safe"]))

    def test_a_one_off_lands_once_and_only_once(self):
        self._add(frequency="once", date="2026-08-14")
        after = self._snapshot()
        hits = [x for x in after["curve"]]
        arrivals = [x for x in after["this_week"]
                    if x["direction"] == "in" and x["name"] == "Side work"]
        self.assertEqual(len(arrivals), 1)
        self.assertEqual(arrivals[0]["date"], "2026-08-14")
        self.assertTrue(hits)

    def test_a_one_off_does_not_repeat_next_week(self):
        self._add(frequency="once", date="2026-08-14")
        snap = self._snapshot()
        # Only one credit anywhere in the projected window.
        cal = self.engine.analyse(
            self.engine.Settings(),
            {"balance": {"amount": "1000.00",
                         "entered_at": "2026-08-11T09:00:00",
                         "date": "2026-08-11"}},
            datetime(2026, 8, 11, 9, 0),
        ).calendar
        credits = [e for e in cal.events
                   if e.direction == "in" and e.item_id == "side-work"]
        self.assertEqual(len(credits), 1)

    def test_weekly_income_repeats(self):
        self._add(frequency="weekly", date="2026-08-14")
        rows = [x for x in self._snapshot()["curve"]]
        self.assertTrue(rows)
        cal = self.engine.analyse(
            self.engine.Settings(),
            {"balance": {"amount": "1000.00",
                         "entered_at": "2026-08-11T09:00:00",
                         "date": "2026-08-11"}},
            datetime(2026, 8, 11, 9, 0),
        ).calendar
        credits = [e for e in cal.events
                   if e.direction == "in" and e.item_id == "side-work"]
        self.assertGreater(len(credits), 4)

    def test_the_new_income_appears_in_the_snapshot_list(self):
        self._add()
        rows = {x["id"]: x for x in self._snapshot()["income"]}
        self.assertIn("side-work", rows)
        self.assertTrue(rows["side-work"]["active"])
        self.assertTrue(rows["side-work"]["needs_review"])
        self.assertEqual(rows["side-work"]["next_date"], "2026-08-14")

    def test_removing_an_income_takes_it_back_out_of_the_forecast(self):
        before = self._snapshot()
        self._add()
        self.apply_edits.apply([{"id": "side-work", "active": False}],
                               self.tmp / "income.json", key="income")
        after = self._snapshot()
        self.assertEqual(before["minimum_balance"], after["minimum_balance"])
        kept = {x["id"]: x for x in after["income"]}["side-work"]
        self.assertFalse(kept["active"], "kept, not deleted")

    def test_an_income_added_in_error_can_be_restored(self):
        path = self.tmp / "income.json"
        self._add()
        with_it = self._snapshot()
        self.apply_edits.apply([{"id": "side-work", "active": False}], path, key="income")
        self.apply_edits.apply([{"id": "side-work", "active": True}], path, key="income")
        self.assertEqual(self._snapshot()["minimum_balance"],
                         with_it["minimum_balance"])


class TestEditableBillList(unittest.TestCase):
    """The Edit screen's list: ordered by when each bill next falls due, and
    removal that deactivates rather than deletes."""

    def setUp(self):
        import apply_edits
        import engine
        self.engine, self.apply_edits = engine, apply_edits
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.bills = [
            bill("late", "Late in the month", 100, day=28, tier=3),
            bill("early", "Early", 200, day=14, tier=1),
            bill("fortnightly", "Fortnightly", 300, freq="biweekly",
                 anchor="2026-08-17", tier=2),
            bill("annual", "Annual", 900, freq="once", due="2027-03-01", tier=4),
        ]
        (self.tmp / "bills.json").write_text(json.dumps(self.bills), encoding="utf-8")
        (self.tmp / "income.json").write_text(json.dumps([]), encoding="utf-8")
        self._root, self._snap = engine.ROOT, engine.SNAPSHOT
        engine.ROOT, engine.SNAPSHOT = self.tmp, self.tmp / "snapshot.json"
        self.addCleanup(self._restore)

    def _restore(self):
        self.engine.ROOT, self.engine.SNAPSHOT = self._root, self._snap

    def _snapshot(self):
        settings = self.engine.Settings()
        state = {"balance": {"amount": "5000.00",
                             "entered_at": "2026-08-11T09:00:00",
                             "date": "2026-08-11"}}
        now = datetime(2026, 8, 11, 9, 0)
        r = self.engine.analyse(settings, state, now)
        return self.engine.write_snapshot(r, settings, now)

    def test_bills_come_back_in_the_order_they_next_fall_due(self):
        rows = self._snapshot()["bills"]
        self.assertEqual([b["id"] for b in rows],
                         ["early", "fortnightly", "late", "annual"])

    def test_every_bill_carries_the_date_it_next_lands(self):
        rows = {b["id"]: b for b in self._snapshot()["bills"]}
        self.assertEqual(rows["early"]["next_date"], "2026-08-14")
        self.assertEqual(rows["fortnightly"]["next_date"], "2026-08-17")
        self.assertEqual(rows["annual"]["next_date"], "2027-03-01")

    def test_a_biweekly_bill_sorts_by_date_not_by_a_day_it_does_not_have(self):
        rows = {b["id"]: b for b in self._snapshot()["bills"]}
        self.assertIsNone(rows["fortnightly"]["due_day"])
        self.assertIsNotNone(rows["fortnightly"]["next_date"])

    def test_removing_a_bill_keeps_it_and_sorts_it_last(self):
        self.apply_edits.apply([{"id": "early", "active": False}],
                               path=self.tmp / "bills.json")
        rows = self._snapshot()["bills"]
        self.assertEqual(rows[-1]["id"], "early", "kept, not deleted")
        self.assertFalse(rows[-1]["active"])
        self.assertIsNone(rows[-1]["next_date"])

    def test_removing_a_bill_rebuilds_the_whole_forecast(self):
        before = self._snapshot()
        self.apply_edits.apply([{"id": "early", "active": False}],
                               path=self.tmp / "bills.json")
        after = self._snapshot()
        self.assertNotEqual(before["end_of_month"], after["end_of_month"])
        self.assertNotEqual(before["minimum_balance"], after["minimum_balance"])
        self.assertNotEqual(before["discretionary"]["safe"],
                            after["discretionary"]["safe"])
        self.assertNotIn("early", [x["id"] for x in after["deferral_window"]])

    def test_a_removed_bill_can_be_restored(self):
        path = self.tmp / "bills.json"
        before = self._snapshot()
        self.apply_edits.apply([{"id": "early", "active": False}], path=path)
        self.apply_edits.apply([{"id": "early", "active": True}], path=path)
        after = self._snapshot()
        self.assertEqual(before["minimum_balance"], after["minimum_balance"])
        self.assertEqual([b["id"] for b in after["bills"]],
                         [b["id"] for b in before["bills"]])

    def test_removal_records_why_on_the_bill(self):
        path = self.tmp / "bills.json"
        self.apply_edits.apply([{"id": "early", "active": False}], path=path)
        kept = {b["id"]: b for b in json.loads(path.read_text())}["early"]
        self.assertIn("deactivated", kept["note"])


class TestClientFormulaParity(unittest.TestCase):
    """The dashboard recomputes both figures locally so ticking a box is
    instant. That arithmetic is duplicated in JavaScript, so it is pinned here:
    if the engine's definition changes, this fails and the app must follow.

    The invariant is stronger than "same formula". The browser works from a
    snapshot built with whatever was saved at the time and adjusts it for the
    boxes since ticked; the engine, when it next runs, starts from scratch. Both
    have to land on the same cent, so each case names a saved state and a
    current one and holds the two answers against each other.
    """

    BALANCE, WINDOW = D("1200"), 30

    def _model(self, deferrals):
        bills = [
            bill("a", "A", 300, freq="once", due="2026-08-13", tier=3),
            bill("b", "B", 900, freq="once", due="2026-08-19", tier=3),
        ]
        incomes = [income("p", "Pay", 700, freq="once", due="2026-08-14"),
                   income("q", "Pay2", 400, freq="once", due="2026-08-21")]
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=90), deferrals)
        # allowance 0: the curve the app is handed carries bills and income only.
        f = fc.run(c, self.BALANCE, TODAY, 60, D("0"))
        return c, f, fc.available(c, self.BALANCE, TODAY, f, self.WINDOW)

    def _parity(self, saved, current=None):
        """Engine's answer for `current`, vs the app's from a `saved` snapshot."""
        current = saved if current is None else current
        _, _, engine_says = self._model(current)
        cal_base, curve, snap = self._model(saved)

        # ---- the JavaScript, transcribed -------------------------------
        # Boxes changed since the snapshot shift every later day of the curve.
        window = cal_base.bills_between(TODAY, snap.window_end, include_deferred=True)
        shifts = []
        for e in window:
            was, now = (e.item_id, e.day) in saved, (e.item_id, e.day) in current
            if was != now:
                shifts.append((e.day, e.amount if now else -e.amount))

        headroom = per_day = None
        for n, d in enumerate([x for x in curve.days if x.day <= snap.window_end], 1):
            closing = d.closing + sum((amt for when, amt in shifts if when <= d.day),
                                      D("0"))
            if headroom is None or closing < headroom:
                headroom = closing
            rate = closing / n
            if per_day is None or rate < per_day:
                per_day = rate
        return (engine_says.headroom, engine_says.per_day), (money(headroom),
                                                             money(per_day))

    def test_parity_with_nothing_deferred(self):
        a, b = self._parity(set())
        self.assertEqual(a, b)

    def test_parity_with_an_in_week_deferral(self):
        a, b = self._parity({("a", date(2026, 8, 13))})
        self.assertEqual(a, b)

    def test_parity_with_a_beyond_week_deferral(self):
        a, b = self._parity({("b", date(2026, 8, 19))})
        self.assertEqual(a, b)

    def test_parity_with_everything_deferred(self):
        a, b = self._parity({("a", date(2026, 8, 13)), ("b", date(2026, 8, 19))})
        self.assertEqual(a, b)

    def test_parity_when_a_box_is_ticked_after_the_snapshot(self):
        a, b = self._parity(set(), {("b", date(2026, 8, 19))})
        self.assertEqual(a, b)

    def test_parity_when_a_box_is_unticked_after_the_snapshot(self):
        a, b = self._parity({("a", date(2026, 8, 13))}, set())
        self.assertEqual(a, b)

    def test_parity_when_the_whole_set_is_swapped(self):
        a, b = self._parity({("a", date(2026, 8, 13))}, {("b", date(2026, 8, 19))})
        self.assertEqual(a, b)

    def test_the_app_is_given_the_window_the_engine_used(self):
        """The curve the app trims must be trimmed to the same last day.

        A window one day wider in the browser than in the engine made the figure
        change on its own the moment the real forecast came back.
        """
        _, _, snap = self._model(set())
        self.assertEqual(snap.window_end, TODAY + timedelta(days=self.WINDOW - 1))
        self.assertEqual(snap.window_days, self.WINDOW)


class TestSnapshotCascade(unittest.TestCase):
    """Everything downstream of a deferral moves, and nothing is hidden.

    The dashboard computes nothing, so a deferral that reaches state.json but
    not the snapshot would leave the app showing pre-deferral figures under a
    "saved" message. And the lists have to keep showing what was deferred: the
    forecast leaves it out because it is not leaving the account, the lists keep
    it because it is still owed, and the flag is what lets both be true at once.
    """

    def setUp(self):
        import engine
        self.engine = engine
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

        bills = [
            # In-week, deferrable, and big enough to reach Upcoming's floor.
            bill("braces", "Braces", 400, freq="once", due="2026-08-14", tier=3),
            # Beyond the week end (Sun 16 Aug), so it lands in Upcoming.
            bill("card", "Credit card", 300, freq="once", due="2026-08-20", tier=3),
            bill("today", "Due today", 120, freq="once", due="2026-08-11", tier=3),
        ]
        (self.tmp / "bills.json").write_text(json.dumps(bills), encoding="utf-8")
        (self.tmp / "income.json").write_text(json.dumps([]), encoding="utf-8")

        self._root, self._snap = engine.ROOT, engine.SNAPSHOT
        engine.ROOT = self.tmp
        engine.SNAPSHOT = self.tmp / "snapshot.json"
        self.addCleanup(self._restore)

    def _restore(self):
        self.engine.ROOT, self.engine.SNAPSHOT = self._root, self._snap

    def _snapshot(self, deferrals):
        settings = self.engine.Settings()
        state = {
            "balance": {"amount": "2000.00",
                        "entered_at": "2026-08-11T09:00:00",
                        "date": "2026-08-11"},
            "deferrals": deferrals,
        }
        now = datetime(2026, 8, 11, 9, 0)
        r = self.engine.analyse(settings, state, now)
        return self.engine.write_snapshot(r, settings, now)

    DEFER_BRACES = [{"bill_id": "braces", "date": "2026-08-14"}]

    def test_saving_a_deferral_moves_every_derived_figure(self):
        before = self._snapshot([])
        after = self._snapshot(self.DEFER_BRACES)
        for key in ("end_of_week", "end_of_month", "minimum_balance"):
            self.assertNotEqual(before[key], after[key], key)
        self.assertNotEqual(before["discretionary"]["safe"],
                            after["discretionary"]["safe"])
        self.assertNotEqual(before["curve"][5]["closing"],
                            after["curve"][5]["closing"])
        self.assertEqual(D(after["deferred_total"]), D("400"))

    def test_this_week_still_lists_the_deferred_bill_and_marks_it(self):
        after = self._snapshot(self.DEFER_BRACES)
        braces = [x for x in after["this_week"] if x["name"] == "Braces"]
        self.assertEqual(len(braces), 1, "a deferred bill must not vanish")
        self.assertTrue(braces[0]["deferred"])

    def test_upcoming_still_lists_the_deferred_bill_and_marks_it(self):
        after = self._snapshot([{"bill_id": "card", "date": "2026-08-20"}])
        card = [x for x in after["upcoming"] if x["name"] == "Credit card"]
        self.assertEqual(len(card), 1, "a deferred bill must not vanish")
        self.assertTrue(card[0]["deferred"])

    def test_today_still_lists_the_deferred_bill_and_marks_it(self):
        after = self._snapshot([{"bill_id": "today", "date": "2026-08-11"}])
        due = [x for x in after["today_bills"] if x["name"] == "Due today"]
        self.assertEqual(len(due), 1, "a deferred bill must not vanish")
        self.assertTrue(due[0]["deferred"])

    def test_undeferred_lists_carry_the_flag_as_false(self):
        # The dashboard sums on `not deferred`, so the key has to be there.
        before = self._snapshot([])
        for section in ("this_week", "upcoming", "today_bills"):
            for row in before[section]:
                self.assertIn("deferred", row, section)

    def test_listed_bills_less_deferred_equals_what_the_week_charges(self):
        """What the app totals must equal what the curve actually spent.

        With no assumed everyday spending, end-of-week is exactly balance plus
        the week's income less the week's undeferred bills — so the tile's own
        arithmetic and the engine's have to agree by identity.
        """
        after = self._snapshot(self.DEFER_BRACES)
        out = sum(D(x["amount"]) for x in after["this_week"]
                  if x["direction"] == "out" and not x["deferred"])
        into = sum(D(x["amount"]) for x in after["this_week"]
                   if x["direction"] == "in")
        self.assertEqual(out, D(after["balance"]) + into - D(after["end_of_week"]))

    def test_the_ticked_boxes_come_back_ticked(self):
        after = self._snapshot(self.DEFER_BRACES)
        ticked = {x["id"] for x in after["deferral_window"] if x["deferred"]}
        self.assertEqual(ticked, {"braces"})

    def test_clearing_deferrals_restores_the_original_figures(self):
        before = self._snapshot([])
        self._snapshot(self.DEFER_BRACES)
        cleared = self._snapshot([])
        self.assertEqual(before["discretionary"]["safe"],
                         cleared["discretionary"]["safe"])
        self.assertEqual(D(cleared["deferred_total"]), D("0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
