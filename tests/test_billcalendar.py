"""Tests for the bill calendar - the dated list and its monthly totals.

The calendar is the simplest output in the project and the one most likely to
be worked off by hand, so the tests are about the two things that would make
it wrong in a way nobody notices: a payment missing from a month, and a total
that does not match the lines above it.

The last test is a cross-check rather than a unit test. The calendar and the
bill statement compute the same year from the same file by different routes -
one groups by date, the other by bill - and if the two ever disagree, one of
them is lying about what is owed.
"""

from __future__ import annotations

import sys
import unittest
import zipfile
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import billcalendar as bc                                        # noqa: E402
import billsheet as bs                                           # noqa: E402
from bills import money                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D = money


class TestCalendar(unittest.TestCase):

    def setUp(self):
        self.cal = bc.build(ROOT / "bills.json", date(2026, 9, 1), 12)

    def test_twelve_months_are_returned_in_order(self):
        self.assertEqual(len(self.cal), 12)
        self.assertEqual(self.cal[0].first, date(2026, 9, 1))
        self.assertEqual(self.cal[-1].first, date(2027, 8, 1))
        self.assertEqual([m.first for m in self.cal],
                         sorted(m.first for m in self.cal))

    def test_every_month_total_is_the_sum_of_its_own_lines(self):
        for mo in self.cal:
            self.assertEqual(
                mo.total, D(sum((d.amount for d in mo.dues), D(0))),
                f"{mo.label} total does not match its lines")

    def test_lines_are_in_date_order_within_a_month(self):
        for mo in self.cal:
            self.assertEqual([d.day for d in mo.dues],
                             sorted(d.day for d in mo.dues))

    def test_every_line_falls_inside_its_own_month(self):
        for mo in self.cal:
            for due in mo.dues:
                self.assertEqual((due.day.year, due.day.month),
                                 (mo.first.year, mo.first.month))

    def test_two_bills_on_one_day_stay_two_lines(self):
        # 15 Sep 2026 carries both deferred past-due one-offs plus the braces
        # and the Upstart instalment. Collapsing a day into one line would
        # hide a payment.
        sep = self.cal[0]
        fifteenth = [d for d in sep.dues if d.day == date(2026, 9, 15)]
        self.assertGreater(len(fifteenth), 1)
        self.assertEqual(len({d.name for d in fifteenth}), len(fifteenth))

    def test_biweekly_mortgage_appears_three_times_in_its_long_months(self):
        counts = {mo.label: sum(1 for d in mo.dues if d.name == "Mortgage")
                  for mo in self.cal}
        self.assertEqual(sum(counts.values()), 26)
        self.assertEqual(sorted(set(counts.values())), [2, 3])

    def test_totals_agree_with_the_bill_statement(self):
        stmt = bs.build(ROOT / "bills.json", date(2026, 9, 1), 12)
        self.assertEqual(D(sum((mo.total for mo in self.cal), D(0))),
                         stmt.total)
        for i, mo in enumerate(self.cal):
            self.assertEqual(mo.total, stmt.month_totals()[i], mo.label)

    def test_pdf_and_workbook_are_written_and_parse(self):
        with TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "cal.pdf"
            xlsx = Path(tmp) / "cal.xlsx"
            pages = bc.write_pdf(self.cal, pdf)
            bc.write_xlsx(self.cal, xlsx)

            self.assertGreater(pages, 1)
            self.assertTrue(pdf.read_bytes().startswith(b"%PDF-1.4"))
            with zipfile.ZipFile(xlsx) as z:
                self.assertEqual(
                    2, sum(1 for n in z.namelist()
                           if n.startswith("xl/worksheets/")))
                for name in z.namelist():
                    ET.fromstring(z.read(name))


if __name__ == "__main__":
    unittest.main()
