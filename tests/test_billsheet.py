"""Tests for the monthly bill statement and the XLSX writer.

Synthetic bills throughout, plus a read-only pass over the repository's real
bills.json to check the two invariants that matter for a statement: nothing is
dropped, and nothing is counted twice.

The XLSX tests check the parts of the format that fail silently. A workbook
with a fill written into one of the two reserved slots, or an autoFilter
element placed after mergeCells, opens as "unreadable content" with no clue
which part was wrong, so both are asserted directly against the XML.
"""

from __future__ import annotations

import sys
import unittest
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import billsheet as bs                                           # noqa: E402
import xlsxwrite as xw                                           # noqa: E402
from bills import money                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D = money
NS = {"m": xw.NS_MAIN}


def bill(id_, amount, freq, *, day=None, anchor=None, due=None, name=None,
         **extra):
    b = {
        "id": id_, "name": name or id_, "amount": amount, "frequency": freq,
        "due_day": day, "anchor_date": anchor, "due_date": due,
        "active": True, "variable": False, "priority_tier": 3,
        "match_keywords": [],
    }
    b.update(extra)
    return b


def model(bills, first=date(2026, 1, 1), months=12):
    """Build a Model straight from a list, bypassing the file."""
    rows, dead = [], []
    ms = bs.month_starts(first, months)
    for b in bills:
        per_month, hits = [], []
        for start in ms:
            days = bs.occurrences(b, start, bs.month_end(start))
            hits.append(len(days))
            per_month.append(D(sum((bs.expected_amount(b, d) for d in days),
                                   D(0))))
        row = bs.Row(bill=b, category=bs.category_of(b), months=per_month,
                     hits=hits, next_due=None)
        (rows if b.get("active", True) else dead).append(row)
    return bs.Model(today=first, months=ms, rows=rows, inactive=dead,
                    source=Path("bills.json"))


# ---------------------------------------------------------------------------
# occurrence expansion
# ---------------------------------------------------------------------------

class TestExpansion(unittest.TestCase):

    def test_biweekly_lands_26_times_not_24(self):
        m = model([bill("mortgage", 1000, "biweekly", anchor="2026-01-05")])
        row = m.rows[0]
        self.assertEqual(row.occurrences, 26)
        self.assertEqual(row.total, D(26000))
        # The run rate is the year divided by twelve, not the amount times two.
        self.assertEqual(row.per_month, D("2166.67"))

    def test_weekly_lands_52_times(self):
        m = model([bill("shop", 100, "weekly", anchor="2026-01-02")])
        self.assertEqual(m.rows[0].occurrences, 52)
        self.assertEqual(m.rows[0].total, D(5200))

    def test_five_week_months_are_not_smoothed(self):
        m = model([bill("shop", 100, "weekly", anchor="2026-01-02")])
        self.assertEqual(m.rows[0].months[0], D(500))     # Jan 2026 has five
        self.assertEqual(m.rows[0].months[1], D(400))     # Feb has four

    def test_quarterly_lands_four_times(self):
        m = model([bill("water", 300, "quarterly", due="2026-02-19")])
        self.assertEqual(m.rows[0].occurrences, 4)
        self.assertEqual(m.rows[0].per_month, D(100))

    def test_annual_shows_its_true_monthly_cost(self):
        m = model([bill("sewer", 1200, "annual", due="2026-09-01")])
        self.assertEqual(m.rows[0].occurrences, 1)
        self.assertEqual(m.rows[0].per_month, D(100))
        self.assertEqual(m.rows[0].months[8], D(1200))
        self.assertEqual(m.rows[0].months[0], D(0))

    def test_seasonal_profile_beats_the_stated_amount(self):
        profile = {str(i): str(i * 100) for i in range(1, 13)}
        m = model([bill("gas", 50, "monthly", day=11, variable=True,
                        monthly_expected=profile)])
        row = m.rows[0]
        self.assertEqual(row.months[0], D(100))
        self.assertEqual(row.months[11], D(1200))
        self.assertEqual(row.total, D(7800))

    def test_window_outside_the_year_yields_nothing(self):
        m = model([bill("ended", 90, "monthly", day=24, ends_on="2025-12-31")])
        self.assertEqual(m.rows[0].occurrences, 0)
        self.assertEqual(m.rows[0].total, D(0))

    def test_inactive_is_listed_but_never_counted(self):
        m = model([bill("gone", 500, "monthly", day=1, active=False),
                   bill("live", 100, "monthly", day=1)])
        self.assertEqual(len(m.rows), 1)
        self.assertEqual(len(m.inactive), 1)
        self.assertEqual(m.total, D(1200))


# ---------------------------------------------------------------------------
# the worst-case rule, made visible
# ---------------------------------------------------------------------------

class TestBasis(unittest.TestCase):

    def test_infrequent_variable_bill_is_carried_at_its_worst(self):
        m = model([bill("card", 50, "monthly", day=4, variable=True,
                        observed_min=40, observed_max=120)])
        row = m.rows[0]
        self.assertEqual(row.basis, "Worst case (observed max)")
        self.assertEqual(row.total, D(1440))
        self.assertEqual(row.typical, D(600))
        self.assertEqual(row.padding, D(840))

    def test_frequent_variable_bill_uses_the_mean_and_is_not_padded(self):
        m = model([bill("shop", 100, "weekly", anchor="2026-01-02",
                        variable=True, observed_max=700)])
        row = m.rows[0]
        self.assertEqual(row.basis, "Average (frequent draw)")
        self.assertEqual(row.total, D(5200))
        self.assertEqual(row.padding, D(0))

    def test_fixed_bills_report_no_margin(self):
        m = model([bill("rent", 1000, "monthly", day=1)])
        self.assertEqual(m.rows[0].basis, "Fixed amount")
        self.assertEqual(m.padding, D(0))

    def test_padding_is_the_difference_the_report_quotes(self):
        m = model([bill("card", 50, "monthly", day=4, variable=True,
                        observed_max=120),
                   bill("rent", 1000, "monthly", day=1)])
        self.assertEqual(m.padding, D(840))
        self.assertEqual(m.total - m.padding,
                         D(sum(r.typical if r.padding else r.total
                               for r in m.rows)))


# ---------------------------------------------------------------------------
# aggregation: nothing dropped, nothing double-counted
# ---------------------------------------------------------------------------

class TestAggregates(unittest.TestCase):

    def setUp(self):
        self.m = bs.build(ROOT / "bills.json", date(2026, 9, 1), 12)

    def test_category_totals_add_to_the_grand_total(self):
        by_cat = sum((self.m.cat_total(c) for c in self.m.categories), D(0))
        self.assertEqual(D(by_cat), self.m.total)

    def test_every_active_bill_lands_in_exactly_one_category(self):
        counted = [r.id for c in self.m.categories for r in self.m.in_category(c)]
        self.assertEqual(sorted(counted), sorted(r.id for r in self.m.rows))
        self.assertEqual(len(counted), len(set(counted)))

    def test_month_totals_add_to_the_grand_total(self):
        self.assertEqual(D(sum(self.m.month_totals(), D(0))), self.m.total)

    def test_headline_block_reconciles_to_the_total(self):
        # Indented lines are breakdowns of the line above and must not be
        # added in; the rest, with the residual, must come to the total.
        top = sum((per_yr for label, _pm, per_yr, sub in bs.headline_rows(self.m)
                   if not sub), D(0))
        self.assertEqual(D(top), self.m.total)

    def test_utility_sub_lines_reconcile_to_the_utility_line(self):
        rows = bs.headline_rows(self.m)
        subs = sum((per_yr for _l, _pm, per_yr, sub in rows if sub), D(0))
        self.assertEqual(D(subs), self.m.cat_total("Utilities"))

    def test_tier_totals_add_to_the_grand_total(self):
        self.assertEqual(D(sum((t for _n, _c, t in self.m.tier_totals()), D(0))),
                         self.m.total)

    def test_per_month_is_the_window_divided_by_twelve(self):
        self.assertEqual(self.m.per_month, D(self.m.total / 12))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

class TestRendering(unittest.TestCase):

    def test_pdf_and_workbook_are_written_and_parse(self):
        m = bs.build(ROOT / "bills.json", date(2026, 9, 1), 12)
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "bills.pdf"
            xlsx = Path(tmp) / "bills.xlsx"
            pages = bs.write_pdf(m, pdf)
            bs.write_xlsx(m, xlsx)

            self.assertGreater(pages, 5)
            head = pdf.read_bytes()
            self.assertTrue(head.startswith(b"%PDF-1.4"))
            self.assertTrue(head.rstrip().endswith(b"%%EOF"))

            with zipfile.ZipFile(xlsx) as z:
                names = z.namelist()
                self.assertIn("xl/workbook.xml", names)
                self.assertEqual(
                    5, sum(1 for n in names if n.startswith("xl/worksheets/")))
                for name in names:                 # every part must parse
                    ET.fromstring(z.read(name))

    def test_frequency_labels_name_the_date_that_anchors_them(self):
        self.assertEqual(bs.frequency_label(bill("x", 1, "monthly", day=3)),
                         "Monthly, 3rd")
        self.assertEqual(bs.frequency_label(bill("x", 1, "monthly", day=22)),
                         "Monthly, 22nd")
        self.assertEqual(bs.frequency_label(bill("x", 1, "monthly", day=11)),
                         "Monthly, 11th")
        self.assertIn("26 a year",
                      bs.frequency_label(bill("x", 1, "biweekly",
                                              anchor="2026-01-01")))
        self.assertIn("7 Oct 2026",
                      bs.frequency_label(bill("x", 1, "once", due="2026-10-07")))

    def test_unmapped_bill_is_categorised_by_keyword_not_dropped(self):
        self.assertEqual(bs.category_of(bill("brand-new-loan", 1, "monthly",
                                             day=1, name="Some loan")), "Loans")
        self.assertEqual(bs.category_of(bill("zzz", 1, "monthly", day=1,
                                             name="Unknowable")),
                         "Miscellaneous")
        self.assertEqual(bs.category_of(bill("zzz", 1, "monthly", day=1,
                                             category="Housing")), "Housing")


# ---------------------------------------------------------------------------
# the XLSX writer
# ---------------------------------------------------------------------------

class TestXlsxWriter(unittest.TestCase):

    def _book(self):
        wb = xw.Workbook()
        s = wb.sheet("Data")
        bold = wb.style(bold=True, fill="16365C", color="FFFFFF")
        cash = wb.style(fmt="$#,##0.00")
        s.append(["Bill", "Amount"], bold)
        s.append(["Rent", Decimal("1801.62")], [0, cash])
        s.append(["Total", xw.Formula("SUM(B2:B2)", 1801.62)], [bold, cash])
        s.freeze(rows=1, cols=1)
        s.autofilter(1, 1, 3, 2)
        s.merge(5, 1, 5, 2)
        s.width(1, 30)
        return wb, s

    def test_column_letters(self):
        self.assertEqual(xw.col_letter(1), "A")
        self.assertEqual(xw.col_letter(26), "Z")
        self.assertEqual(xw.col_letter(27), "AA")
        self.assertEqual(xw.col_letter(52), "AZ")
        self.assertEqual(xw.ref(4, 3), "C4")
        self.assertEqual(xw.span(1, 1, 3, 2), "A1:B3")
        with self.assertRaises(ValueError):
            xw.col_letter(0)

    def test_cell_types(self):
        _wb, s = self._book()
        root = ET.fromstring(s.xml())
        cells = {c.get("r"): c for c in root.iter(f"{{{xw.NS_MAIN}}}c")}
        self.assertEqual(cells["A1"].get("t"), "inlineStr")
        self.assertIsNone(cells["B2"].get("t"))              # numbers are bare
        self.assertEqual(cells["B2"].find(f"{{{xw.NS_MAIN}}}v").text, "1801.62")
        self.assertEqual(cells["B3"].find(f"{{{xw.NS_MAIN}}}f").text,
                         "SUM(B2:B2)")

    def test_reserved_fill_slots_are_untouched(self):
        wb, _s = self._book()
        root = ET.fromstring(wb.styles.xml())
        fills = root.find(f"{{{xw.NS_MAIN}}}fills")
        kinds = [f.find(f"{{{xw.NS_MAIN}}}patternFill").get("patternType")
                 for f in fills]
        self.assertEqual(kinds[0], "none")
        self.assertEqual(kinds[1], "gray125")

    def test_styles_are_deduped(self):
        wb = xw.Workbook()
        a = wb.style(bold=True, fmt="$#,##0.00")
        b = wb.style(bold=True, fmt="$#,##0.00")
        c = wb.style(bold=True)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_element_order_is_the_one_the_schema_demands(self):
        _wb, s = self._book()
        xml = s.xml()
        self.assertLess(xml.index("<sheetData>"), xml.index("<autoFilter"))
        self.assertLess(xml.index("<autoFilter"), xml.index("<mergeCells"))
        self.assertLess(xml.index("<sheetViews"), xml.index("<cols>"))
        self.assertLess(xml.index("<cols>"), xml.index("<sheetData>"))

    def test_freeze_pane_names_the_first_unfrozen_cell(self):
        _wb, s = self._book()
        pane = ET.fromstring(s.xml()).iter(f"{{{xw.NS_MAIN}}}pane")
        pane = next(pane)
        self.assertEqual(pane.get("topLeftCell"), "B2")
        self.assertEqual(pane.get("state"), "frozen")

    def test_illegal_characters_are_dropped_not_written(self):
        wb = xw.Workbook()
        s = wb.sheet("x")
        s.append(["a\x07b & <c>"])
        xml = s.xml()
        ET.fromstring(xml)                       # would raise if it got through
        self.assertIn("ab &amp; &lt;c&gt;", xml)

    def test_sheet_names_are_trimmed_to_what_excel_accepts(self):
        wb = xw.Workbook()
        s = wb.sheet("a/very[long]name:that*goes?on\\and on and on and on")
        self.assertLessEqual(len(s.name), 31)
        self.assertNotIn("/", s.name)
        self.assertNotIn("[", s.name)

    def test_saved_workbook_parses_and_holds_every_part(self):
        wb, _s = self._book()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.xlsx"
            wb.save(path, title="T")
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    ET.fromstring(z.read(name))
                self.assertIn("[Content_Types].xml", z.namelist())
                self.assertIn("xl/styles.xml", z.namelist())
                book = ET.fromstring(z.read("xl/workbook.xml"))
                sheets = list(book.iter(f"{{{xw.NS_MAIN}}}sheet"))
                self.assertEqual(len(sheets), 1)
                self.assertEqual(sheets[0].get("name"), "Data")


if __name__ == "__main__":
    unittest.main()
