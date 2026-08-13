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
from bills import money                                          # noqa: E402

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

class TestDiscretionary(unittest.TestCase):
    def _disc(self, bills, incomes, balance, buffer="0", lookahead=14):
        c = cal(bills, incomes)
        return fc.safe_discretionary(c, D(balance), TODAY, D(buffer), lookahead)

    def test_plenty_of_money(self):
        d = self._disc([], [], "5000")
        self.assertEqual(d.safe, D("5000"))

    def test_bills_this_week_reduce_it(self):
        d = self._disc([bill("b", "B", 1000, day=14)], [], "5000")
        self.assertEqual(d.safe, D("4000"))

    def test_income_this_week_increases_it(self):
        d = self._disc([], [income("i", "Pay", 800, freq="once", due="2026-08-13")],
                       "100")
        self.assertEqual(d.safe, D("900"))

    def test_buffer_is_held_back(self):
        d = self._disc([], [], "1000", buffer="500")
        self.assertEqual(d.safe, D("500"))

    def test_obligations_just_beyond_the_week_are_reserved(self):
        # A mortgage two days after the week ends must not look spendable now.
        d = self._disc([bill("m", "M", 1800, freq="once", due="2026-08-18")],
                       [], "2000")
        self.assertEqual(d.committed_beyond_week, D("1800"))
        self.assertEqual(d.safe, D("200"))

    def test_income_in_the_lookahead_offsets_those_obligations(self):
        d = self._disc(
            [bill("m", "M", 1800, freq="once", due="2026-08-18")],
            [income("i", "Pay", 1800, freq="once", due="2026-08-17")],
            "2000",
        )
        self.assertEqual(d.committed_beyond_week, D("0"))
        self.assertEqual(d.safe, D("2000"))

    def test_negative_result_is_returned_unclamped(self):
        d = self._disc([bill("b", "B", 3000, day=14)], [], "1000")
        self.assertEqual(d.safe, D("-2000"))
        self.assertLess(d.safe, 0)

    def test_explain_reconciles_to_the_answer(self):
        d = self._disc([bill("b", "B", 400, day=14)], [], "1000", buffer="100")
        lines = d.explain()
        self.assertEqual(sum(v for _, v in lines[:-1]), lines[-1][1])

    def test_not_simply_income_minus_bills(self):
        # Same month totals, different timing -> different safe number.
        early = self._disc([bill("b", "B", 900, freq="once", due="2026-08-13")],
                           [], "1000")
        late = self._disc([bill("b", "B", 900, freq="once", due="2026-09-30")],
                          [], "1000")
        self.assertNotEqual(early.safe, late.safe)


# --------------------------------------------------------------------------
# risk engine
# --------------------------------------------------------------------------

class TestRisk(unittest.TestCase):
    def _detect(self, bills, incomes, balance, allowance="0",
                floor="500", large="750"):
        c, f = project(bills, incomes, balance, allowance=allowance)
        d = fc.safe_discretionary(c, D(balance), TODAY, D(floor))
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

def household(balance, allowance="216.98"):
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
    d = fc.safe_discretionary(c, D(balance), TODAY, D("500"))
    rs = riskmod.detect(f, c, d, minimum_safe_balance=D("500"),
                        large_payment_threshold=D("750"), today=TODAY)
    return c, f, d, rs


class TestHouseholdScenarios(unittest.TestCase):
    def test_healthy_balance_has_room(self):
        _, _, d, _ = household("20000")
        self.assertGreater(d.safe, 0)

    def test_real_world_thin_balance_is_negative_and_says_so(self):
        _, f, d, rs = household("118.25")
        self.assertLess(f.minimum_balance, 0)
        self.assertTrue(rs)
        self.assertIn(riskmod.NEGATIVE_BALANCE, [r.type for r in rs])

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
        lines = d.explain()
        self.assertEqual(sum(v for _, v in lines[:-1]), lines[-1][1])


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
        d = fc.safe_discretionary(c, D("2000"), TODAY, D("0"))
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


class TestSafeToSpendRespectsTheLowPoint(unittest.TestCase):
    """Safe-to-spend may not exceed what keeps the projection above the buffer.

    The week view nets a fortnight's bills against a fortnight's income and
    ignores which lands first, so a paycheque arriving after a bill cancels it
    on paper. Every one of those errors is in the direction of offering money
    that is not there.
    """

    BUFFER, BALANCE = D("500"), D("2000")

    def _run(self, bills, incomes, allowance=D("0"), lookahead=14):
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=60))
        f = fc.run(c, self.BALANCE, TODAY, 60, allowance)
        return f, fc.safe_discretionary(c, self.BALANCE, TODAY, self.BUFFER,
                                        lookahead, projection=f)

    def _late_pay(self):
        """A bill on the 18th, the pay covering it on the 19th."""
        return (
            [bill("big", "Big", 1800, freq="once", due="2026-08-18", tier=1)],
            [income("pay", "Pay", 2500, freq="once", due="2026-08-19")],
        )

    def test_pay_arriving_after_the_bill_does_not_fund_it(self):
        bills, incomes = self._late_pay()
        f, d = self._run(bills, incomes)
        # The week view sees 2500 of income against 1800 of bills and offers the
        # balance less the buffer. The curve knows the 18th comes first.
        self.assertEqual(d.week_view, D("1500"))
        self.assertEqual(d.projected_low, D("200"))
        self.assertEqual(d.projected_low_day, date(2026, 8, 18))
        self.assertEqual(d.safe, D("-300"))
        self.assertTrue(d.limited_by_low_point)

    def test_spending_the_answer_leaves_the_low_day_exactly_at_the_buffer(self):
        """The definition, stated as a property: this is the most that can be
        spent today without the worst day ahead dropping below the floor."""
        bills, incomes = self._late_pay()
        f, d = self._run(bills, incomes)
        self.assertEqual(d.projected_low - d.safe, self.BUFFER)

    def test_the_week_view_still_wins_when_it_is_the_smaller_one(self):
        bills = [bill("soon", "Soon", 1900, freq="once", due="2026-08-14", tier=1)]
        incomes = [income("pay", "Pay", 5000, freq="once", due="2026-08-25")]
        f, d = self._run(bills, incomes)
        self.assertEqual(d.safe, d.week_view)
        self.assertFalse(d.limited_by_low_point)

    def test_the_daily_allowance_is_charged_by_the_curve(self):
        """It is real spending. The week view never sees it; the curve does."""
        bills, incomes = self._late_pay()
        _, plain = self._run(bills, incomes, allowance=D("0"))
        _, spend = self._run(bills, incomes, allowance=D("100"))
        self.assertEqual(plain.week_view, spend.week_view)
        self.assertLess(spend.safe, plain.safe)

    def test_without_a_projection_the_answer_is_the_week_view_alone(self):
        bills, incomes = self._late_pay()
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=60))
        d = fc.safe_discretionary(c, self.BALANCE, TODAY, self.BUFFER, 14)
        self.assertEqual(d.safe, d.week_view)
        self.assertIsNone(d.projected_low)
        self.assertFalse(d.limited_by_low_point)

    def test_a_crunch_past_the_lookahead_does_not_pin_this_week(self):
        """Otherwise one distant annual bill holds the figure at zero all year."""
        far = (TODAY + timedelta(days=45)).isoformat()
        bills = [bill("annual", "Annual", 9000, freq="once", due=far, tier=1)]
        f, d = self._run(bills, [])
        self.assertEqual(d.safe, d.week_view)
        self.assertLess(f.minimum_balance, D("0"))       # the curve still knows

    def test_deferring_the_bill_lifts_the_low_point_and_the_answer(self):
        bills, incomes = self._late_pay()
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=60),
                         {("big", date(2026, 8, 18))})
        f = fc.run(c, self.BALANCE, TODAY, 60, D("0"))
        d = fc.safe_discretionary(c, self.BALANCE, TODAY, self.BUFFER, 14, projection=f)
        self.assertEqual(d.safe, D("1500"))
        self.assertFalse(d.limited_by_low_point)

    def test_the_explanation_shows_the_overruled_figure_too(self):
        bills, incomes = self._late_pay()
        _, d = self._run(bills, incomes)
        labels = [lbl for lbl, _ in d.explain()]
        self.assertIn("Free on this week alone", labels)
        self.assertTrue(any(l.startswith("Lowest projected balance") for l in labels))
        self.assertEqual(labels[-1], "Safe to spend")
        self.assertEqual(d.explain()[-1][1], d.safe)


class TestClientFormulaParity(unittest.TestCase):
    """The dashboard recomputes safe-to-spend locally so ticking a box is
    instant. That arithmetic is duplicated in JavaScript, so it is pinned here:
    if the engine's definition changes, this fails and the app must follow.

    The invariant is stronger than "same formula". The browser works from a
    snapshot built with whatever was saved at the time, and adjusts it for the
    boxes since ticked; the engine, when it next runs, starts from scratch. Both
    have to land on the same cent, so each case names a saved state and a
    current one and holds the two answers against each other.
    """

    BUFFER, BALANCE, ALLOWANCE, LOOKAHEAD = D("500"), D("1200"), D("40"), 14

    def _model(self, deferrals):
        bills = [
            bill("a", "A", 300, freq="once", due="2026-08-13", tier=3),
            bill("b", "B", 900, freq="once", due="2026-08-19", tier=3),
        ]
        incomes = [income("p", "Pay", 700, freq="once", due="2026-08-14"),
                   income("q", "Pay2", 400, freq="once", due="2026-08-21")]
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=60), deferrals)
        f = fc.run(c, self.BALANCE, TODAY, 60, self.ALLOWANCE)
        d = fc.safe_discretionary(c, self.BALANCE, TODAY, self.BUFFER,
                                  self.LOOKAHEAD, projection=f)
        return c, f, d

    def _parity(self, saved, current=None):
        """Engine's answer for `current`, vs the app's from a `saved` snapshot."""
        current = saved if current is None else current
        _, _, engine_says = self._model(current)
        cal, curve, snap = self._model(saved)

        # ---- the JavaScript, transcribed -------------------------------
        we = fincal.week_end(TODAY)
        horizon = we + timedelta(days=self.LOOKAHEAD)
        # What the snapshot hands the browser.
        beyond_in = cal.total_in(we + timedelta(days=1), horizon)
        window = cal.bills_between(TODAY, horizon, include_deferred=True)

        bills_week = sum((e.amount for e in window
                          if e.day <= we and (e.item_id, e.day) not in current), D("0"))
        beyond_out = sum((e.amount for e in window
                          if e.day > we and (e.item_id, e.day) not in current), D("0"))
        committed = max(D("0"), beyond_out - beyond_in)
        week_view = (self.BALANCE + snap.income_this_week - bills_week
                     - committed - self.BUFFER)

        # Boxes changed since the snapshot shift every later day of the curve.
        shifts = []
        for e in window:
            was, now = (e.item_id, e.day) in saved, (e.item_id, e.day) in current
            if was != now:
                shifts.append((e.day, e.amount if now else -e.amount))
        low = min(
            d.closing + sum((amt for when, amt in shifts if when <= d.day), D("0"))
            for d in curve.days if TODAY <= d.day <= horizon
        )
        client = min(week_view, low - self.BUFFER)
        return engine_says.safe, money(client)

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

    def test_income_on_the_last_lookahead_day_counts_the_same_both_sides(self):
        """The window the app is given must be the window the engine uses.

        It used to be a day wider, so a paycheque landing on that extra day was
        credited in the browser and not in the engine — the figure changed on
        its own the moment the real forecast came back.
        """
        we = fincal.week_end(TODAY)
        horizon = we + timedelta(days=self.LOOKAHEAD)
        bills = [bill("a", "A", 300, freq="once", due="2026-08-13", tier=3)]
        incomes = [income("late", "Pay", 900, freq="once",
                          due=(horizon + timedelta(days=1)).isoformat())]
        c = fincal.build(bills, incomes, TODAY, TODAY + timedelta(days=60))
        d = fc.safe_discretionary(c, self.BALANCE, TODAY, self.BUFFER, self.LOOKAHEAD)
        # Income one day past the horizon is outside the window on both sides.
        self.assertEqual(c.total_in(we + timedelta(days=1), horizon), D("0"))
        self.assertEqual(d.committed_beyond_week, D("0"))


# --------------------------------------------------------------------------
# the snapshot the dashboard reads: saving a deferral has to cascade
# --------------------------------------------------------------------------

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
        """What the app totals must equal what the engine subtracted."""
        after = self._snapshot(self.DEFER_BRACES)
        listed = sum(
            D(x["amount"]) for x in after["this_week"]
            if x["direction"] == "out" and not x["deferred"]
        )
        charged = [v for label, v in after["discretionary"]["explain"]
                   if label.startswith("Bills before")][0]
        self.assertEqual(listed, -D(charged))

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
