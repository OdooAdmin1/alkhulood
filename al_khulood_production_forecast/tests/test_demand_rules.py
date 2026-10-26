# -*- coding: utf-8 -*-
"""The calendar must fill itself in, without ever overwriting what we observed.

Two things are worth pinning here. First, that generating from the rules really
does reproduce the dates Bahrain used - within the one day the tabular calendar
is known to drift. Second, and more important, that a generated date can never
displace a confirmed one: the whole event-factor mechanism reads history off
``date_start + offset``, so a silently moved anchor would corrupt every
multiplier learned from it.
"""
from datetime import date, datetime, time, timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

BAHRAIN = timedelta(hours=3)

# The dates Bahrain actually observed, as shipped in demand_event.SEED.
# (hijri_year, event_type, observed_start)
OBSERVED = [
    (1444, 'ramadan', '2023-03-23'), (1445, 'ramadan', '2024-03-11'),
    (1446, 'ramadan', '2025-03-01'), (1447, 'ramadan', '2026-02-18'),
    (1444, 'eid_fitr', '2023-04-21'), (1445, 'eid_fitr', '2024-04-10'),
    (1446, 'eid_fitr', '2025-03-30'), (1447, 'eid_fitr', '2026-03-20'),
    (1444, 'eid_adha', '2023-06-28'), (1445, 'eid_adha', '2024-06-16'),
    (1446, 'eid_adha', '2025-06-06'), (1447, 'eid_adha', '2026-05-27'),
    (1445, 'muharram', '2023-07-20'), (1446, 'muharram', '2024-07-08'),
    (1447, 'muharram', '2025-06-27'), (1448, 'muharram', '2026-06-17'),
    (1445, 'ashura', '2023-07-29'), (1446, 'ashura', '2024-07-17'),
    (1447, 'ashura', '2025-07-06'), (1448, 'ashura', '2026-06-26'),
]


@tagged('post_install', '-at_install')
class TestDemandEventRules(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('stock.group_stock_manager')
        cls.Rule = cls.env['al.demand.event.rule']
        cls.Event = cls.env['al.demand.event']

    def _rule(self, xmlid):
        return self.env.ref('al_khulood_production_forecast.%s' % xmlid)

    # ------------------------------------------------------------------
    def test_rules_are_shipped_for_every_occasion_type(self):
        """Nothing in the typed calendar may be left without a rule."""
        typed = set(self.Event.search([('rule_id', '=', False)])
                    .mapped('event_type'))
        ruled = set(self.Rule.with_context(active_test=False)
                    .search([]).mapped('event_type'))

        self.assertTrue(typed, "the shipped observed calendar is missing")
        self.assertFalse(typed - ruled,
                         "no rule generates: %s" % sorted(typed - ruled))

    def test_generated_dates_match_what_bahrain_observed(self):
        """The arithmetic is allowed to be a day out, and no more.

        Measured over these 20 occasions the tabular calendar lands exactly on
        16 of them and one day away on the rest, so one day is the contract.
        """
        by_type = {r.event_type: r for r in
                   self.Rule.with_context(active_test=False).search([])}
        worst = 0
        for hijri_year, event_type, observed in OBSERVED:
            rule = by_type[event_type]
            computed = rule._start_date(hijri_year)
            drift = (computed - fields.Date.to_date(observed)).days
            self.assertLessEqual(
                abs(drift), 1,
                "%s %d computed %s against an observed %s - %+d days"
                % (event_type, hijri_year, computed, observed, drift))
            worst = max(worst, abs(drift))
        self.assertEqual(worst, 1, "the known one-day drift has disappeared - "
                                   "check the conversion before relaxing this")

    def test_generation_is_idempotent(self):
        """Upgrading the module twice must not double the calendar."""
        rules = self.Rule.search([])
        rules._generate(1449, 1451)
        before = self.Event.search_count([])
        rules._generate(1449, 1451)

        self.assertEqual(self.Event.search_count([]), before,
                         "a second run created duplicate occasions")

    def test_the_calendar_reaches_past_the_typed_list(self):
        """The typed SEED stopped in 2028; the rules must keep going."""
        self.Rule.search([])._generate(1452, 1454)      # ~2030-2033

        future = self.Event.search([('date_start', '>=', '2030-01-01'),
                                    ('event_type', '=', 'eid_fitr')])

        self.assertTrue(future, "no Eid al-Fitr generated beyond 2030")
        self.assertTrue(all(e.generated for e in future))

    def test_an_observed_date_is_never_overwritten(self):
        """A confirmed record outranks the arithmetic, always."""
        observed = self.Event.search([('event_type', '=', 'eid_adha'),
                                      ('date_start', '=', '2025-06-06')])
        self.assertTrue(observed, "the observed Eid al-Adha 1446 is missing")
        observed.write({'confirmed': True})
        rule = self._rule('rule_eid_adha')
        # the arithmetic puts this one a day late, which is exactly the trap
        self.assertNotEqual(rule._start_date(1446), observed.date_start)

        rule._generate(1446, 1446)

        self.assertEqual(observed.date_start, date(2025, 6, 6),
                         "a generated date displaced an observed one")
        self.assertEqual(
            self.Event.search_count([('event_type', '=', 'eid_adha'),
                                     ('date_start', '=', '2025-06-06')]), 1,
            "the observed record was duplicated rather than adopted")

    def test_a_generated_unconfirmed_date_is_refreshed(self):
        """Correcting a rule must flow through to dates nobody has vouched for."""
        rule = self._rule('rule_valentine')
        rule._generate(2031, 2031)
        event = self.Event.search([('rule_id', '=', rule.id),
                                   ('source_year', '=', 2031)])
        event.write({'confirmed': False, 'date_start': '2031-02-01',
                     'date_end': '2031-02-01'})

        rule._generate(2031, 2031)

        self.assertEqual(event.date_start, date(2031, 2, 14),
                         "an unconfirmed generated date was not corrected")

    def test_sighting_occasions_are_generated_unconfirmed(self):
        """Ramadan waits for a person; a fixed Gregorian date does not."""
        self.Rule.search([])._generate(1455, 1455)
        ramadan = self.Event.search([('rule_id', '=', self._rule('rule_ramadan').id),
                                     ('source_year', '=', 1455)])
        self.Rule.search([])._generate(2034, 2034)
        national = self.Event.search(
            [('rule_id', '=', self._rule('rule_national_day').id),
             ('source_year', '=', 2034)])

        self.assertTrue(ramadan and not ramadan.confirmed,
                        "a moon-sighted occasion must start out unconfirmed")
        self.assertTrue(national and national.confirmed,
                        "16 December needs no confirming")


@tagged('post_install', '-at_install')
class TestDateRecovery(TransactionCase):
    """Recovering a real date from the sales, rather than from a person."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('stock.group_stock_manager')
        cls.product = cls.env['product.product'].create({
            'name': 'ZR Halwa Probe',
            'is_storable': True,
            'type': 'consu',
            'categ_id': cls.env.ref('product.product_category_goods').id,
            'list_price': 4.0,
            'available_in_pos': True,
        })
        cfg = cls.env['pos.config'].search([], limit=1)
        cls.session = cls.env['pos.session'].search(
            [('config_id', '=', cfg.id), ('state', '!=', 'closed')], limit=1)
        if not cls.session:
            cls.session = cls.env['pos.session'].create({'config_id': cfg.id})
            cls.session.action_pos_session_open()

    def _sell(self, day, qty):
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

    def test_a_shut_day_is_found_from_the_absence_of_sales(self):
        """Ashura leaves the clearest possible mark: nothing at all."""
        real_shut = date(2022, 8, 9)
        for offset in range(-10, 11):
            day = real_shut + timedelta(days=offset)
            if day != real_shut:
                self._sell(day, 30)

        event = self.env['al.demand.event'].create({
            'name': 'ZR Ashura probe',
            'event_type': 'ashura',
            'calendar': 'hijri',
            'date_start': real_shut + timedelta(days=1),   # the computed guess
            'date_end': real_shut + timedelta(days=1),
            'closed': True,
            'generated': True,
        })

        event.action_suggest_from_sales()

        self.assertEqual(event.suggested_date, real_shut,
                         "the closed day was not recovered from the sales")
        self.assertEqual(event.date_start, real_shut + timedelta(days=1),
                         "a suggestion must not move the date on its own")

    def test_accepting_a_suggestion_fixes_the_date_for_good(self):
        """Once accepted it counts as observed, so no rule may undo it."""
        real_shut = date(2022, 8, 9)
        for offset in range(-10, 11):
            day = real_shut + timedelta(days=offset)
            if day != real_shut:
                self._sell(day, 30)
        event = self.env['al.demand.event'].create({
            'name': 'ZR Ashura probe 2',
            'event_type': 'ashura',
            'calendar': 'hijri',
            'date_start': real_shut + timedelta(days=1),
            'date_end': real_shut + timedelta(days=1),
            'closed': True,
            'generated': True,
        })
        event.action_suggest_from_sales()

        event.action_accept_suggestion()

        self.assertEqual(event.date_start, real_shut)
        self.assertTrue(event.confirmed)
        self.assertFalse(event.generated,
                         "an accepted date must be treated as observed")
        self.assertFalse(event.suggested_date)

    def test_a_date_that_already_fits_raises_no_suggestion(self):
        """Nothing to accept when the computed date is already the right one."""
        real_shut = date(2022, 8, 9)
        for offset in range(-10, 11):
            day = real_shut + timedelta(days=offset)
            if day != real_shut:
                self._sell(day, 30)
        event = self.env['al.demand.event'].create({
            'name': 'ZR Ashura already right',
            'event_type': 'ashura',
            'calendar': 'hijri',
            'date_start': real_shut,
            'date_end': real_shut,
            'closed': True,
            'generated': True,
        })

        event.action_suggest_from_sales()

        self.assertFalse(event.suggested_date,
                         "a no-op was offered as a change to accept")
        self.assertIn('already', (event.suggestion_note or '').lower())

    def test_a_future_occasion_is_never_second_guessed(self):
        """There is no sales history for a date that has not happened."""
        future = fields.Date.context_today(self.env.user) + timedelta(days=200)
        event = self.env['al.demand.event'].create({
            'name': 'ZR future probe',
            'event_type': 'eid_fitr',
            'calendar': 'hijri',
            'date_start': future,
            'date_end': future,
            'generated': True,
        })

        event.action_suggest_from_sales()

        self.assertFalse(event.suggested_date)
        self.assertIn('future', (event.suggestion_note or '').lower())
