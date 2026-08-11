"""Tests for the financial engine.

Run with:  python -m unittest discover -s tests -v

Synthetic data throughout — no network, no real balances, no secrets. The
household scenarios near the bottom are modelled on the real shape of this
household's finances (biweekly mortgage, weekly + biweekly income, a wall of
monthly debt) because that shape is what the engine has to get right.
"""

from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
