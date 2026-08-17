"""Tests for payment-date optimisation and the audit report.

Synthetic data throughout — no real balances, no network, no secrets.

The scenarios below are the ones a scheduling engine actually has to survive:
a gap one bill can close, a gap several must close together, a gap nothing can
close, month boundaries, both income cadences, duplicate rows on one day, and
a starting balance that was never enough. The invariant running under all of
them is that rescheduling never changes what is owed.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fincal                                                    # noqa: E402
import forecast as fc                                            # noqa: E402
import pdfwrite                                                  # noqa: E402
import schedule as sch                                           # noqa: E402
from bills import money                                          # noqa: E402

T = date(2026, 1, 1)          # a Thursday
D = money


def bill(id_, amount, when, *, freq="once", day=None, anchor=None,
         tier=5, deferrable=True, secured=False, window=None, name=None):
    b = {
        "id": id_, "name": name or id_, "amount": amount, "frequency": freq,
        "due_day": day, "anchor_date": anchor, "active": True,
        "due_date": when.isoformat() if isinstance(when, date) else when,
        "priority_tier": tier, "deferrable": deferrable, "secured": secured,
        "variable": False, "match_keywords": [],
    }
    if window is not None:
        b["payment_window"] = ({"latest_days": window}
                               if isinstance(window, int) else window)
    return b


def inc(id_, amount, when, *, freq="once", anchor=None):
    return {
        "id": id_, "name": id_, "amount": amount, "frequency": freq,
        "due_day": None, "anchor_date": anchor, "active": True,
        "due_date": when.isoformat() if isinstance(when, date) else when,
        "match_keywords": [],
    }


def plan(bills, incomes, balance, *, days=30, start=T, assume=0, floor=None):
    cal = fincal.build(bills, incomes, start, start + timedelta(days=days + 60))
    mv = sch.build_movabilities(bills)
    return sch.optimise(cal, money(balance), start, days, mv,
                        floor=floor, assume_unknown_days=assume)


def day(offset):
    return T + timedelta(days=offset)


# ---------------------------------------------------------------------------
# 1-4, 20: the core shapes
# ---------------------------------------------------------------------------

class TestCoreScenarios(unittest.TestCase):

    def test_normal_cash_flow_needs_no_changes(self):
        p = plan([bill("rent", 500, day(5), window=10)],
                 [inc("pay", 2000, day(2))], 1000)
        self.assertEqual(p.moves, [])
        self.assertEqual(p.negative_days_after, 0)
        self.assertTrue(p.solved)

    def test_one_movable_bill_closes_the_gap(self):
        p = plan([bill("card", 1200, day(4), window=10)],
                 [inc("pay", 2000, day(7))], 1000)
        self.assertEqual(len(p.moves), 1)
        self.assertEqual(p.moves[0].from_day, day(4))
        self.assertEqual(p.moves[0].to_day, day(7))
        self.assertEqual(p.negative_days_before, 3)
        self.assertEqual(p.negative_days_after, 0)

    def test_multiple_movable_bills_are_combined(self):
        bills = [
            bill("a", 500, day(3), window=10),
            bill("b", 500, day(4), window=10),
            bill("c", 500, day(5), window=10),
        ]
        p = plan(bills, [inc("pay", 2000, day(8))], 600)
        self.assertGreaterEqual(len(p.moves), 2)
        self.assertEqual(p.negative_days_after, 0)

    def test_gap_no_move_can_close_is_reported_not_hidden(self):
        p = plan([bill("mortgage", 2000, day(4), secured=True,
                       deferrable=False, tier=1)],
                 [inc("pay", 500, day(7))], 100)
        self.assertEqual(p.moves, [])
        self.assertEqual(len(p.shortfalls), 1)
        s = p.shortfalls[0]
        self.assertEqual(s.start, day(4))
        self.assertGreater(s.needed, 0)
        self.assertIn("legitimately move later", s.why)

    def test_insufficient_starting_balance_is_not_optimised_away(self):
        p = plan([bill("x", 900, day(2), window=20)], [], 100)
        self.assertEqual(p.negative_days_after, p.negative_days_before)
        self.assertTrue(p.shortfalls)

    def test_shortfall_names_the_next_income(self):
        p = plan([bill("m", 2000, day(3), secured=True, deferrable=False)],
                 [inc("pay", 3000, day(9))], 100)
        s = p.shortfalls[0]
        self.assertEqual(s.next_income_day, day(9))
        self.assertEqual(s.next_income_amount, D(3000))


# ---------------------------------------------------------------------------
# the invariant that matters most
# ---------------------------------------------------------------------------

class TestMoneyIsConserved(unittest.TestCase):

    def test_total_outflow_is_identical_after_optimisation(self):
        bills = [bill("a", 400, day(3), window=14),
                 bill("b", 700, day(4), window=14),
                 bill("c", 250, day(5), window=14)]
        p = plan(bills, [inc("pay", 2500, day(9))], 500)
        self.assertTrue(sch.conserved(p))

    def test_a_move_shifts_money_it_does_not_delete_it(self):
        p = plan([bill("card", 1200, day(4), window=10)],
                 [inc("pay", 2000, day(7))], 1000)
        self.assertEqual(p.baseline.days[-1].closing,
                         p.optimised.days[-1].closing)

    def test_deferral_by_contrast_removes_money_from_the_curve(self):
        """Documents the existing `defer` behaviour this module does not share.

        A deferred occurrence never lands again, so the curve ends higher by
        exactly that amount. That is why rescheduling is a separate concept.
        """
        b = [bill("card", 300, day(4))]
        cal = fincal.build(b, [], T, T + timedelta(days=40))
        cal_def = fincal.build(b, [], T, T + timedelta(days=40),
                               {("card", day(4))})
        a = fc.run(cal, D(1000), T, 20, D(0))
        c = fc.run(cal_def, D(1000), T, 20, D(0))
        self.assertEqual(c.days[-1].closing - a.days[-1].closing, D(300))


# ---------------------------------------------------------------------------
# 5-9: flexibility classification
# ---------------------------------------------------------------------------

class TestMovability(unittest.TestCase):

    def test_secured_is_fixed_and_never_moved(self):
        v = sch.movability(bill("m", 100, day(3), secured=True))
        self.assertEqual(v.kind, sch.FIXED)
        self.assertEqual(v.latest_days, 0)
        self.assertEqual(v.basis, sch.KNOWN)

    def test_non_deferrable_is_fixed(self):
        v = sch.movability(bill("irs", 100, day(3), deferrable=False))
        self.assertEqual(v.kind, sch.FIXED)

    def test_explicit_window_makes_it_flexible(self):
        v = sch.movability(bill("cc", 100, day(3), window=12))
        self.assertEqual(v.kind, sch.FLEXIBLE)
        self.assertEqual(v.latest_days, 12)
        self.assertEqual(v.basis, sch.KNOWN)

    def test_deferrable_without_a_window_is_unknown_not_movable(self):
        v = sch.movability(bill("cc", 100, day(3), deferrable=True))
        self.assertEqual(v.kind, sch.UNKNOWN)
        self.assertEqual(v.latest_days, 0)
        self.assertIn("USER CONFIRMATION REQUIRED", v.basis)

    def test_unknown_bills_are_never_moved_in_the_baseline(self):
        p = plan([bill("cc", 1200, day(4), deferrable=True)],
                 [inc("pay", 2000, day(7))], 1000)
        self.assertEqual(p.moves, [])
        self.assertFalse(p.conditional)
        self.assertTrue(p.shortfalls)

    def test_the_same_case_is_solvable_once_a_window_is_assumed(self):
        p = plan([bill("cc", 1200, day(4), deferrable=True)],
                 [inc("pay", 2000, day(7))], 1000, assume=7)
        self.assertEqual(len(p.moves), 1)
        self.assertTrue(p.conditional)
        self.assertEqual(p.moves[0].confidence, "LOW")

    def test_an_explicit_window_too_short_to_help_is_not_used(self):
        p = plan([bill("cc", 1200, day(4), window=1)],
                 [inc("pay", 2000, day(7))], 1000)
        self.assertEqual(p.moves, [])
        self.assertTrue(p.blocked)

    def test_paying_early_is_allowed_without_confirmation(self):
        for freq, cadence in (("monthly", 28), ("weekly", 7)):
            v = sch.movability(bill("x", 10, day(3), freq=freq, day=3,
                                    anchor=day(0).isoformat()))
            self.assertEqual(v.earliest_days, -(cadence - 1))

    def test_unclassified_bills_are_treated_as_fixed(self):
        cal = fincal.build([bill("x", 900, day(3))], [], T,
                           T + timedelta(days=40))
        p = sch.optimise(cal, D(100), T, 20, {})     # empty movability map
        self.assertEqual(p.moves, [])


# ---------------------------------------------------------------------------
# 5-6: timing shapes
# ---------------------------------------------------------------------------

class TestTiming(unittest.TestCase):

    def test_large_paycheck_immediately_after_a_large_bill(self):
        p = plan([bill("big", 3000, day(5), window=5)],
                 [inc("pay", 4000, day(6))], 500)
        self.assertEqual(len(p.moves), 1)
        self.assertEqual(p.moves[0].to_day, day(6))
        self.assertEqual(p.negative_days_after, 0)

    def test_bill_the_day_before_payday_moves_exactly_one_day(self):
        p = plan([bill("b", 800, day(4), window=6)],
                 [inc("pay", 1000, day(5))], 200)
        self.assertEqual(p.moves[0].days_moved, 1)

    def test_multiple_bills_on_the_same_date_all_count(self):
        bills = [bill("a", 300, day(4), window=8),
                 bill("b", 300, day(4), window=8),
                 bill("c", 300, day(4), window=8)]
        cal = fincal.build(bills, [], T, T + timedelta(days=40))
        f = fc.run(cal, D(1000), T, 10, D(0))
        self.assertEqual(f.at(day(4)).bills_total, D(900))

    def test_duplicate_identical_rows_are_not_merged(self):
        """Two identical obligations on one day are two obligations.

        The optimiser tracks events positionally for exactly this reason: a
        value-keyed structure would silently collapse them into one and lose
        real money from the projection.
        """
        dup = [bill("same", 400, day(3), window=10, name="Same"),
               bill("same", 400, day(3), window=10, name="Same")]
        cal = fincal.build(dup, [], T, T + timedelta(days=40))
        f = fc.run(cal, D(1000), T, 10, D(0))
        self.assertEqual(f.at(day(3)).bills_total, D(800))
        p = plan(dup, [inc("pay", 1000, day(6))], 500)
        self.assertTrue(sch.conserved(p))

    def test_income_lands_before_bills_on_the_same_day(self):
        p = plan([bill("b", 900, day(3), window=5)],
                 [inc("pay", 1000, day(3))], 100)
        self.assertEqual(p.negative_days_before, 0)
        self.assertEqual(p.moves, [])

    def test_a_move_never_worsens_a_later_day(self):
        bills = [bill("a", 900, day(3), window=14)]
        p = plan(bills, [inc("pay", 1200, day(6))], 200)
        for b_day, o_day in zip(p.baseline.days, p.optimised.days):
            self.assertGreaterEqual(o_day.closing, b_day.closing)


# ---------------------------------------------------------------------------
# 10-15: dates and recurrence
# ---------------------------------------------------------------------------

class TestDatesAndRecurrence(unittest.TestCase):

    def test_month_boundary_is_crossed_correctly(self):
        start = date(2026, 1, 28)
        p = plan([bill("b", 900, date(2026, 1, 30), window=8)],
                 [inc("pay", 1200, date(2026, 2, 3))], 200, start=start)
        self.assertEqual(len(p.moves), 1)
        self.assertEqual(p.moves[0].to_day, date(2026, 2, 3))

    def test_short_month_clamps_a_31st_due_day(self):
        b = bill("b", 100, None, freq="monthly", day=31)
        hits = fincal.occurrences(b, date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(hits, [date(2026, 2, 28)])

    def test_leap_year_february_gets_29_days(self):
        b = bill("b", 100, None, freq="monthly", day=31)
        hits = fincal.occurrences(b, date(2028, 2, 1), date(2028, 2, 29))
        self.assertEqual(hits, [date(2028, 2, 29)])

    def test_year_boundary_is_crossed_correctly(self):
        start = date(2026, 12, 28)
        p = plan([bill("b", 900, date(2026, 12, 30), window=10)],
                 [inc("pay", 1500, date(2027, 1, 4))], 200, start=start)
        self.assertEqual(p.moves[0].to_day, date(2027, 1, 4))

    def test_recurring_monthly_bill_produces_every_occurrence(self):
        b = bill("b", 100, None, freq="monthly", day=15)
        hits = fincal.occurrences(b, date(2026, 1, 1), date(2026, 6, 30))
        self.assertEqual(len(hits), 6)

    def test_weekly_income_lands_every_seven_days(self):
        i = inc("pay", 1000, None, freq="weekly", anchor=T.isoformat())
        hits = fincal.occurrences(i, T, T + timedelta(days=27))
        self.assertEqual(hits, [T + timedelta(days=n) for n in (0, 7, 14, 21)])

    def test_biweekly_income_lands_every_fourteen_days(self):
        i = inc("pay", 2000, None, freq="biweekly", anchor=T.isoformat())
        hits = fincal.occurrences(i, T, T + timedelta(days=41))
        self.assertEqual(len(hits), 3)

    def test_biweekly_yields_26_a_year_not_24(self):
        # 363 days inclusive of the anchor is exactly 26 steps of 14; a 365th
        # day would catch a 27th, which is a property of the window and not of
        # the cadence.
        i = inc("pay", 1000, None, freq="biweekly", anchor=T.isoformat())
        self.assertEqual(len(fincal.occurrences(i, T, T + timedelta(days=363))),
                         26)

    def test_recurring_bills_and_income_optimise_together(self):
        bills = [bill("rent", 1400, None, freq="monthly", day=1,
                      tier=1, secured=True, deferrable=False),
                 bill("cc", 600, None, freq="monthly", day=2, window=12)]
        incomes = [inc("pay", 1100, None, freq="weekly",
                       anchor=date(2026, 1, 5).isoformat())]
        p = plan(bills, incomes, 500, days=60, start=date(2026, 1, 1))
        self.assertTrue(sch.conserved(p))
        self.assertGreaterEqual(p.optimised_minimum, p.baseline_minimum)

    def test_a_move_target_beyond_the_window_is_refused(self):
        """Landing a bill past the end of the projection would drop it out of
        the totals entirely, which would silently reduce what is owed."""
        p = plan([bill("b", 5000, day(1), window=365)], [], 100, days=5)
        self.assertTrue(sch.conserved(p))


# ---------------------------------------------------------------------------
# 16-19: spending, transfers, pending
# ---------------------------------------------------------------------------

class TestSpendingAndAccountEffects(unittest.TestCase):

    def test_discretionary_is_separate_from_committed_obligations(self):
        bills = [bill("rent", 1000, day(5), deferrable=False)]
        incomes = [inc("pay", 1500, day(2))]
        cal = fincal.build(bills, incomes, T, T + timedelta(days=60))
        f = fc.run(cal, D(500), T, 30, D(0))
        disc = fc.available(cal, D(500), T, f, window_days=30)
        # The binding day is today, before the paycheque lands — not the day
        # the rent leaves. Headroom is the low point of the whole curve, and
        # the rent is comfortably funded by the time it falls.
        self.assertEqual(disc.headroom, D(500))
        self.assertEqual(f.minimum_balance, D(500))
        self.assertEqual(f.at(day(5)).closing, D(1000))

    def test_spending_the_headroom_takes_the_low_day_to_zero(self):
        bills = [bill("rent", 1000, day(5), deferrable=False)]
        cal = fincal.build(bills, [inc("pay", 1500, day(2))], T,
                           T + timedelta(days=60))
        f = fc.run(cal, D(500), T, 30, D(0))
        disc = fc.available(cal, D(500), T, f, window_days=30)
        f2 = fc.run(cal, D(500) - disc.headroom, T, 30, D(0))
        self.assertEqual(f2.minimum_balance, D(0))

    def test_a_transfer_modelled_both_ways_nets_to_nothing(self):
        """There is no transfer concept in the model. One entered as a matching
        inflow and outflow on the same day must not move the curve."""
        b = [bill("to-savings", 500, day(4), deferrable=False)]
        i = [inc("from-savings", 500, day(4))]
        cal = fincal.build(b, i, T, T + timedelta(days=40))
        f = fc.run(cal, D(1000), T, 10, D(0))
        self.assertEqual(f.at(day(4)).closing, D(1000))
        self.assertEqual(f.minimum_balance, D(1000))

    def test_a_one_way_transfer_out_is_indistinguishable_from_a_bill(self):
        p = plan([bill("to-savings", 900, day(3), deferrable=False)], [], 1000)
        self.assertEqual(p.baseline.at(day(3)).closing, D(100))

    def test_pending_transactions_are_whatever_the_entered_balance_included(self):
        """The model has no bank feed. The starting balance is taken as given,
        so a pending debit is reflected only if the bank had applied it."""
        a = plan([], [], 1000).baseline
        b = plan([], [], 900).baseline
        self.assertEqual(a.days[0].closing - b.days[0].closing, D(100))


# ---------------------------------------------------------------------------
# minimal-disruption objectives
# ---------------------------------------------------------------------------

class TestObjectives(unittest.TestCase):

    def test_lowest_priority_bill_is_moved_first(self):
        # The balance is chosen so that moving ONE of the two closes the gap;
        # the point of the test is which one gets picked, not how many.
        bills = [bill("essential", 600, day(3), tier=2, window=10),
                 bill("luxury", 600, day(3), tier=5, window=10)]
        p = plan(bills, [inc("pay", 1200, day(6))], 700)
        self.assertEqual(len(p.moves), 1)
        self.assertEqual(p.moves[0].name, "luxury")

    def test_a_bill_that_does_not_need_to_move_is_left_alone(self):
        bills = [bill("small", 50, day(3), window=10),
                 bill("big", 900, day(3), window=10)]
        p = plan(bills, [inc("pay", 1500, day(6))], 300)
        self.assertEqual([x.name for x in p.moves], ["big"])

    def test_the_smallest_covering_set_is_chosen(self):
        bills = [bill(f"b{i}", 200, day(3), window=10) for i in range(5)]
        p = plan(bills, [inc("pay", 2000, day(7))], 700)
        self.assertLessEqual(len(p.moves), 2)
        self.assertEqual(p.negative_days_after, 0)

    def test_a_floor_can_be_planned_against_instead_of_zero(self):
        p = plan([bill("b", 800, day(3), window=10)],
                 [inc("pay", 1000, day(6))], 900, floor=money(300))
        self.assertEqual(len(p.moves), 1)
        self.assertGreaterEqual(p.optimised.minimum_balance, D(300))

    def test_improvement_and_counts_are_reported(self):
        p = plan([bill("card", 1200, day(4), window=10)],
                 [inc("pay", 2000, day(7))], 1000)
        self.assertEqual(p.improvement, D(1200))
        self.assertEqual(p.dollars_moved, D(1200))
        self.assertTrue(p.solved)


# ---------------------------------------------------------------------------
# the PDF
# ---------------------------------------------------------------------------

TXT = re.compile(
    r'BT [\d.]+ [\d.]+ [\d.]+ rg /(F\d) ([\d.]+) Tf 1 0 0 1 '
    r'([-\d.]+) ([-\d.]+) Tm \((.*?)\) Tj ET'
)


class TestPdfWriter(unittest.TestCase):

    def test_text_width_is_measured_not_guessed(self):
        self.assertGreater(pdfwrite.text_width("MMMM", pdfwrite.HELV, 10),
                           pdfwrite.text_width("iiii", pdfwrite.HELV, 10))

    def test_fit_never_returns_something_too_wide(self):
        for w in (20, 40, 80, 160):
            s = pdfwrite.fit("A rather long bill description here", w)
            self.assertLessEqual(pdfwrite.text_width(s), w + 0.01)

    def test_wrap_never_returns_a_line_too_wide(self):
        lines = pdfwrite.wrap(
            "Elizabethtown Gas and a supercalifragilisticexpialidocious payee",
            70)
        for line in lines:
            self.assertLessEqual(pdfwrite.text_width(line), 70.01)

    def test_parentheses_are_escaped(self):
        pdf = pdfwrite.PDF()
        pdf.add_page().text(10, 10, "a (b) c \\ d")
        self.assertIn(rb"a \(b\) c \\ d", pdf.to_bytes())

    def test_document_is_structurally_valid(self):
        pdf = pdfwrite.PDF()
        pdf.add_page().text(20, 20, "hello")
        pdf.add_page(pdfwrite.A4_LANDSCAPE).text(20, 20, "wide")
        data = pdf.to_bytes("T")
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/Type /Catalog", data)
        self.assertEqual(data.count(b"/Type /Page\n") + data.count(b"/Type /Page "), 2)
        self.assertIn(b"startxref", data)

    def test_xref_offsets_point_at_their_objects(self):
        pdf = pdfwrite.PDF()
        pdf.add_page().text(20, 20, "x")
        data = pdf.to_bytes()
        tail = data[data.rindex(b"xref"):]
        offsets = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", tail)]
        for i, off in enumerate(offsets, start=1):
            self.assertTrue(data[off:].startswith(f"{i} 0 obj".encode()))


class TestReportLayout(unittest.TestCase):
    """The report must not clip a table or overlap text. That is checked by
    parsing the produced page streams rather than by looking at it."""

    def _doc(self):
        import report
        L = report.Layout("Layout test", "synthetic")
        L.h1("A section")
        L.para("Some prose " * 40)
        cols = [report.Col("Name", 120, wrap_cells=True),
                report.Col("Amount", 60, "r"),
                report.Col("A very wide free-text column", 300,
                           wrap_cells=True)]
        rows = [[f"A payee with a long name {i}", f"${i * 137}.55",
                 "Explanation text that is long enough to need wrapping "
                 "across several lines " * 2] for i in range(40)]
        L.table(cols, rows, page_size=report.A4_LANDSCAPE)
        return L

    def _runs(self, data):
        out = []
        boxes = [tuple(map(float, mb)) for mb in
                 re.findall(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)]
        streams = re.findall(rb"stream\n(.*?)\nendstream", data, re.S)
        for s, (w, h) in zip(streams, boxes):
            page = []
            for fk, size, x, y, txt in TXT.findall(s.decode("latin-1")):
                t = (txt.replace(r"\(", "(").replace(r"\)", ")")
                     .replace("\\\\", "\\"))
                width = pdfwrite.text_width(
                    t, pdfwrite.HELV_BOLD if fk == "F2" else pdfwrite.HELV,
                    float(size))
                page.append((float(x), float(y), width, t))
            out.append(((w, h), page))
        return out

    def test_nothing_runs_past_the_right_margin(self):
        data = self._doc().pdf.to_bytes()
        for (w, _h), runs in self._runs(data):
            for x, _y, width, t in runs:
                self.assertLessEqual(
                    x + width, w - 42.0 + 1.0,
                    f"{t!r} overflows the right margin")

    def test_no_two_text_runs_overlap_on_a_shared_baseline(self):
        from collections import defaultdict
        data = self._doc().pdf.to_bytes()
        for _box, runs in self._runs(data):
            rows = defaultdict(list)
            for x, y, width, t in runs:
                rows[round(y, 1)].append((x, width, t))
            for _y, items in rows.items():
                items.sort()
                for (x1, w1, t1), (x2, _w2, t2) in zip(items, items[1:]):
                    self.assertLessEqual(
                        x1 + w1, x2 + 0.6,
                        f"{t1!r} overlaps {t2!r}")

    def test_everything_stays_inside_the_printable_page(self):
        data = self._doc().pdf.to_bytes()
        for (_w, h), runs in self._runs(data):
            for _x, y, _width, t in runs:
                self.assertTrue(8 <= y <= h - 20, f"{t!r} sits off the page")

    def test_a_long_table_spans_pages_and_repeats_its_header(self):
        """Every page carrying table rows must also carry the column headings.

        A continuation page of unlabelled money columns is exactly the failure
        this is here to prevent.
        """
        L = self._doc()
        data = L.pdf.to_bytes()
        pages = self._runs(data)
        with_rows = 0
        for _box, runs in pages:
            texts = [t for _x, _y, _w, t in runs]
            if any(t.startswith("A payee with a long name") for t in texts):
                with_rows += 1
                self.assertIn("Amount", texts,
                              "a continuation page lost its column headings")
        self.assertGreater(with_rows, 1, "the table did not span pages")

    def test_pages_are_numbered_once_saved(self):
        import tempfile
        L = self._doc()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.pdf"
            n = L.save(out)
            data = out.read_bytes()
        self.assertGreater(n, 1)
        self.assertIn(f"Page 1 of {n}".encode(), data)
        self.assertIn(f"Page {n} of {n}".encode(), data)


class TestAuditAgainstRepositoryData(unittest.TestCase):
    """The audit must run on whatever is actually committed, and must never
    quietly invent flexibility for a bill that has none recorded."""

    def test_the_audit_builds_and_conserves_money(self):
        import report
        a = report.build_audit()
        self.assertTrue(sch.conserved(a.plan))
        self.assertTrue(sch.conserved(a.conditional))

    def test_no_bill_is_moved_without_a_recorded_window(self):
        import report
        a = report.build_audit()
        for mv_move in a.plan.moves:
            v = a.movabilities[mv_move.item_id]
            self.assertEqual(
                v.kind, sch.FLEXIBLE,
                f"{mv_move.name} was moved without a recorded payment_window")

    def test_the_conditional_plan_is_labelled_conditional(self):
        import report
        a = report.build_audit(assume_days=7)
        if a.conditional.moves:
            self.assertTrue(a.conditional.conditional)
            for x in a.conditional.moves:
                self.assertEqual(x.confidence, "LOW")


if __name__ == "__main__":
    unittest.main()
