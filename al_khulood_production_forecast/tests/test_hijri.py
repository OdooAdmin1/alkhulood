# -*- coding: utf-8 -*-
"""The Hijri conversion is load-bearing: a one-day slip shifts a whole year of
learned festival multipliers onto the wrong days. These anchors are the defence.
"""
from datetime import date, timedelta

from odoo.tests import TransactionCase, tagged

from ..tools import hijri


@tagged('post_install', '-at_install')
class TestHijriConversion(TransactionCase):

    def test_known_anchor_dates(self):
        """Dates Bahrain actually observed must convert exactly."""
        anchors = [
            (date(2023, 7, 19), 1445, 1, 1),    # Hijri new year 1445
            (date(2025, 3, 1), 1446, 9, 1),     # 1 Ramadan 1446
            (date(2026, 2, 18), 1447, 9, 1),    # 1 Ramadan 1447
            (date(2026, 3, 20), 1447, 10, 1),   # Eid al-Fitr 1447
        ]
        for greg, hy, hm, hd in anchors:
            self.assertEqual(
                hijri.gregorian_to_hijri(greg), (hy, hm, hd),
                "%s should be %d-%02d-%02d AH" % (greg, hy, hm, hd))

    def test_round_trip_over_six_years(self):
        """Every day must survive Gregorian -> Hijri -> Gregorian untouched."""
        d = date(2023, 1, 1)
        while d <= date(2028, 12, 31):
            hy, hm, hd = hijri.gregorian_to_hijri(d)
            self.assertEqual(hijri.hijri_to_gregorian(hy, hm, hd),
                             (d.year, d.month, d.day),
                             "round trip failed on %s" % d)
            d += timedelta(days=1)

    def test_months_are_only_29_or_30_days(self):
        """The classic off-by-one: a 28- or 31-day Hijri month means a bug."""
        lengths = {}
        d = date(2024, 1, 1)
        while d <= date(2027, 12, 31):
            hy, hm, hd = hijri.gregorian_to_hijri(d)
            key = (hy, hm)
            lengths[key] = max(lengths.get(key, 0), hd)
            d += timedelta(days=1)
        # the first and last Hijri months are clipped by the scan window
        keys = sorted(lengths)
        full = {k: lengths[k] for k in keys[1:-1]}
        self.assertGreater(len(full), 40, "too few complete months were scanned")
        self.assertEqual(sorted(set(full.values())), [29, 30])

    def test_ramadan_is_the_ninth_month(self):
        self.assertEqual(hijri.month_name(9), 'Ramadan')
        self.assertEqual(hijri.gregorian_to_hijri(date(2026, 3, 1))[1], 9)
