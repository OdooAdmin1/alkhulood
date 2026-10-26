# -*- coding: utf-8 -*-
"""The forecast must be arithmetic anyone can check, and must never cheat.

Every number the dashboard shows is a product of stored factors, so the two
things worth pinning are: (1) the factors really multiply to the answer, and
(2) nothing from on or after the selected day is ever used to predict it.
"""
from datetime import date, datetime, time, timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

MODEL = 'al.production.forecast'
BAHRAIN = timedelta(hours=3)


@tagged('post_install', '-at_install')
class TestProductionForecast(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('stock.group_stock_user')
        cls.fc = cls.env[MODEL]
        cls.category = cls.env.ref('product.product_category_goods')

        cls.product = cls.env['product.product'].create({
            'name': 'ZF Halwa Probe',
            'is_storable': True,
            'type': 'consu',
            'categ_id': cls.category.id,
            'list_price': 4.0,
            'standard_price': 2.0,
            'uom_id': cls.env.ref('uom.product_uom_unit').id,
            'available_in_pos': True,
        })

        cfg = cls.env['pos.config'].search([], limit=1)
        cls.session = cls.env['pos.session'].search(
            [('config_id', '=', cfg.id), ('state', '!=', 'closed')], limit=1)
        if not cls.session:
            cls.session = cls.env['pos.session'].create({'config_id': cfg.id})
            cls.session.action_pos_session_open()

    def _sell(self, day, qty):
        """One POS ticket for `qty` on a given Bahrain day."""
        when = datetime.combine(day, time(12, 0)) - BAHRAIN
        return self.env['pos.order'].create({
            'session_id': self.session.id,
            'company_id': self.env.company.id,
            'date_order': when,
            'amount_tax': 0.0,
            'amount_total': qty * 4.0,
            'amount_paid': qty * 4.0,
            'amount_return': 0.0,
            'state': 'paid',
            'lines': [(0, 0, {
                'product_id': self.product.id,
                'qty': qty,
                'price_unit': 4.0,
                'price_subtotal': qty * 4.0,
                'price_subtotal_incl': qty * 4.0,
                'full_product_name': self.product.name,
                'tax_ids': [(5, 0, 0)],
            })],
        })

    def _row(self, data):
        rows = [r for r in data['products'] if r['product_id'] == self.product.id]
        self.assertEqual(len(rows), 1, "expected exactly one row for the probe product")
        return rows[0]

    # ------------------------------------------------------------------
    def test_baseline_is_the_same_weekday_median(self):
        """Ten identical Mondays must predict that same quantity next Monday."""
        target = date(2026, 6, 15)                      # a Monday
        for w in range(1, 11):
            self._sell(target - timedelta(weeks=w), 20)

        row = self._row(self.fc.get_day_forecast(fields.Date.to_string(target)))

        self.assertEqual(row['baseline_qty'], 20.0)
        self.assertEqual(row['predicted_qty'], 20.0,
                         "no event and no trend means the prediction is the baseline")

    def test_factors_multiply_to_the_prediction(self):
        """The explanation on screen must equal the number above it."""
        target = date(2026, 6, 15)
        for w in range(1, 11):
            self._sell(target - timedelta(weeks=w), 12 + w % 3)

        row = self._row(self.fc.get_day_forecast(fields.Date.to_string(target)))

        product = 1.0
        for f in row['factors']:
            if f['kind'] == 'base':
                product = f['value']
            elif f.get('value'):
                product *= f['value']
        self.assertAlmostEqual(round(product, 1), row['predicted_qty'], places=1,
                               msg="stored factors do not reproduce the forecast")

    def test_nothing_after_the_selected_day_is_used(self):
        """Picking a past date is a backtest, so the future must not leak in."""
        target = date(2026, 6, 15)
        for w in range(1, 11):
            self._sell(target - timedelta(weeks=w), 10)

        before = self._row(self.fc.get_day_forecast(fields.Date.to_string(target)))
        # an enormous sale on the day itself, and on later days
        self._sell(target, 5000)
        self._sell(target + timedelta(days=7), 5000)
        after = self._row(self.fc.get_day_forecast(fields.Date.to_string(target)))

        self.assertEqual(before['predicted_qty'], after['predicted_qty'],
                         "the forecast moved after adding same-day/later sales")
        self.assertEqual(after['actual_qty'], 5000.0,
                         "the actual column should still report what really sold")

    def test_event_day_lifts_the_forecast_and_is_explained(self):
        """A learned Eid-eve multiplier must raise the number and show its reason."""
        eid_2025 = self.env['al.demand.event'].search(
            [('event_type', '=', 'eid_fitr'), ('date_start', '=', '2025-03-30')], limit=1)
        eid_2026 = self.env['al.demand.event'].search(
            [('event_type', '=', 'eid_fitr'), ('date_start', '=', '2026-03-20')], limit=1)
        self.assertTrue(eid_2025 and eid_2026, "seeded Eid events are missing")

        eve_2025 = eid_2025.date_start - timedelta(days=1)
        eve_2026 = eid_2026.date_start - timedelta(days=1)
        # ordinary Saturdays around last year's Eid, then a 5x eve
        for w in range(1, 11):
            self._sell(eve_2025 - timedelta(weeks=w), 10)
        self._sell(eve_2025, 50)
        # ordinary history leading into this year's eve
        for w in range(1, 11):
            self._sell(eve_2026 - timedelta(weeks=w), 10)

        data = self.fc.get_day_forecast(fields.Date.to_string(eve_2026))
        row = self._row(data)

        self.assertGreater(row['predicted_qty'], row['baseline_qty'],
                           "the eve of Eid must forecast above an ordinary day")
        labels = [f['label'] for f in row['factors'] if f['kind'] == 'event']
        self.assertTrue(any('Eid al-Fitr' in label for label in labels),
                        "the Eid factor is missing from the explanation")
        self.assertTrue(any('Eid al-Fitr' in e['label'] for e in data['events']),
                        "the day does not report the Eid occasion")

    def test_both_calendars_are_reported_for_the_day(self):
        """Every day carries its Gregorian and its Hijri coordinates."""
        data = self.fc.get_day_forecast('2026-03-20')      # Eid al-Fitr 1447

        self.assertEqual(data['date'], '2026-03-20')
        self.assertEqual(data['hijri']['year'], 1447)
        self.assertEqual(data['hijri']['month'], 10)       # Shawwal
        self.assertTrue(any(e['calendar'] == 'hijri' for e in data['events']))

    def test_hijri_day_inside_ramadan_follows_the_confirmed_start(self):
        """Display must agree with the record that drives the forecast."""
        ramadan = self.env['al.demand.event'].search(
            [('event_type', '=', 'ramadan'), ('date_start', '=', '2026-02-18')], limit=1)
        ramadan.write({'confirmed': True})
        day = ramadan.date_start + timedelta(days=11)      # 12 Ramadan

        data = self.fc.get_day_forecast(fields.Date.to_string(day))

        self.assertEqual(data['hijri']['month'], 9)
        self.assertEqual(data['hijri']['day'], 12)
        self.assertEqual(data['hijri']['source'], 'confirmed')

    def test_gregorian_event_is_learned_like_a_hijri_one(self):
        """National Day must go through the identical mechanism as Eid."""
        data = self.fc.get_day_forecast('2026-12-16')

        types = [e['type'] for e in data['events']]
        self.assertIn('national_day', types)
        greg = [e for e in data['events'] if e['type'] == 'national_day'][0]
        self.assertEqual(greg['calendar'], 'gregorian')

    def test_biggest_sellers_are_listed_first(self):
        """The table is ordered by what the day is expected to sell."""
        data = self.fc.get_day_forecast('2026-06-15')
        qtys = [r['predicted_qty'] for r in data['products']]

        self.assertEqual(qtys, sorted(qtys, reverse=True),
                         "products must be listed largest forecast first")

    def test_every_row_carries_its_own_history_on_the_shared_axis(self):
        """Clicking a product charts that product, so each row ships its series.

        The series must sit on exactly the same dates as the all-products one,
        or a product's line would be drawn against the wrong days.
        """
        target = date(2026, 6, 15)
        for w in range(1, 11):
            self._sell(target - timedelta(weeks=w), 8)

        data = self.fc.get_day_forecast(fields.Date.to_string(target))
        axis = data['history']

        self.assertTrue(axis, "the chart axis should not be empty")
        for row in data['products']:
            self.assertEqual(
                len(row['history']), len(axis),
                "%s's series does not line up with the shared date axis"
                % row['name'])

    def test_a_closed_day_forecasts_nothing(self):
        """Ashura is not a quiet day, it is a shut door.

        Four Muharrams in the sales data carry zero tickets on 10 Muharram, so
        a closure must collapse the forecast to nothing rather than quietly
        predicting an ordinary day.
        """
        ashura = self.env['al.demand.event'].search(
            [('event_type', '=', 'ashura'), ('closed', '=', True),
             ('date_start', '>', '2026-01-01')],
            order='date_start', limit=1)
        self.assertTrue(ashura, "the seeded Ashura closures are missing")
        day = ashura.date_start
        for w in range(1, 11):                       # a healthy ordinary history
            self._sell(day - timedelta(weeks=w), 30)

        data = self.fc.get_day_forecast(fields.Date.to_string(day))
        row = self._row(data)

        self.assertGreater(row['baseline_qty'], 0.0,
                           "the ordinary baseline should still be visible")
        self.assertEqual(row['predicted_qty'], 0.0)
        self.assertEqual(data['kpis']['predicted_units'], 0.0)
        closed = [f for f in row['factors']
                  if f['kind'] == 'event' and f['value'] == 0.0]
        self.assertTrue(closed, "the closure must appear in the factor chain")
        self.assertIn('closed', closed[0]['label'])

    def test_muharram_is_learned_day_by_day_like_ramadan(self):
        """Day 1 and day 5 of Muharram are different days, not one average."""
        this_year = self.env['al.demand.event'].search(
            [('event_type', '=', 'muharram'), ('date_start', '=', '2026-06-17')],
            limit=1)
        last_year = self.env['al.demand.event'].search(
            [('event_type', '=', 'muharram'), ('date_start', '=', '2025-06-27')],
            limit=1)
        self.assertTrue(this_year and last_year, "the seeded Muharrams are missing")

        # last year: ordinary weeks, then a deep day 1 and a mild day 5
        for ev, day1, day5 in ((last_year, 4.0, 16.0), (None, None, None)):
            if ev is None:
                break
            for offset, qty in ((0, day1), (4, day5)):
                target = ev.date_start + timedelta(days=offset)
                for w in range(1, 11):
                    self._sell(target - timedelta(weeks=w), 20)
                self._sell(target, qty)
        # this year: ordinary history running into each day
        for offset in (0, 4):
            target = this_year.date_start + timedelta(days=offset)
            for w in range(1, 11):
                self._sell(target - timedelta(weeks=w), 20)

        d1 = self._row(self.fc.get_day_forecast(
            fields.Date.to_string(this_year.date_start)))
        d5 = self._row(self.fc.get_day_forecast(
            fields.Date.to_string(this_year.date_start + timedelta(days=4))))

        self.assertLess(d1['predicted_qty'], d5['predicted_qty'],
                        "a deep day 1 must not be smeared over the whole month")
        self.assertTrue(any('Muharram day 1' in f['label'] for f in d1['factors']))
        self.assertTrue(any('Muharram day 5' in f['label'] for f in d5['factors']))

    def test_generated_occasions_do_not_disturb_the_observed_ones(self):
        """Filling the calendar forward must not change a past forecast.

        The eve of Eid 1447 is learned from the eve of Eid 1446. Generating the
        rest of the calendar adds occasions elsewhere, and if any of them landed
        on top of an observed date the learned multiplier would shift. This
        pins that it does not.
        """
        eve = date(2026, 3, 19)                       # 1 day before Eid 1447
        for w in range(1, 11):
            self._sell(eve - timedelta(weeks=w), 10)
        before = self._row(self.fc.get_day_forecast(fields.Date.to_string(eve)))

        self.env['al.demand.event.rule'].search([])._generate(1451, 1453)
        after = self._row(self.fc.get_day_forecast(fields.Date.to_string(eve)))

        self.assertEqual(before['predicted_qty'], after['predicted_qty'],
                         "extending the calendar moved an existing forecast")
        self.assertEqual(before['baseline_qty'], after['baseline_qty'])

    def test_access_is_refused_without_the_stock_group(self):
        """A user outside Inventory must not be able to read the forecast.

        Built as a fresh user rather than by stripping a group from the current
        one: stock.group_stock_manager implies stock_user, so removing the
        inner group alone would leave access intact and the test would lie.
        """
        from odoo.exceptions import AccessError
        outsider = self.env['res.users'].create({
            'name': 'ZF Outsider',
            'login': 'zf_outsider',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.assertFalse(outsider.has_group('stock.group_stock_user'))
        with self.assertRaises(AccessError):
            self.fc.with_user(outsider).get_day_forecast('2026-06-15')
