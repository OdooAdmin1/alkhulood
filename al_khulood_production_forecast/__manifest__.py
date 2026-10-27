# -*- coding: utf-8 -*-
{
    'name': 'Al Khulood - Daily Sales Forecast',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/Reporting',
    'summary': 'Pick a day on the calendar and see what will sell, '
               'read through both the Islamic and the Gregorian calendar',
    'description': """
Daily demand forecasting for a business whose sales follow two calendars.

* Pick any day. The dashboard shows every tracked product with what a normal
  day of that weekday sells and what this particular day is expected to sell.
* Every number opens up: base weekday median x trend x each occasion's learned
  multiplier. No black box, nothing to take on trust, nothing written back.
* Both calendars are carried at once. Ramadan and the two Eids move each year
  and are matched by their own day offsets; National Day, New Year, Valentine's
  and Mother's Day sit on fixed Gregorian dates and are learned the same way.
* The occasions generate themselves. Each one is a rule anchored to a fixed
  date in its own calendar, so the calendar is never typed in by hand and never
  runs out. Only the four dates that wait on a moon sighting need confirming,
  and past ones can be recovered from the sales themselves.
* Hijri dates inside a confirmed Ramadan are counted from the moon-sighted
  start, so what is shown can never contradict what drives the forecast.
* Picking a past date is a genuine backtest: only days before it are used, and
  the actual result is shown next to the prediction.
""",
    'author': 'Amwal W.L.L.',
    'website': 'https://amwal.solutions',
    'license': 'LGPL-3',
    'depends': ['stock', 'mrp', 'point_of_sale', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/dashboard_views.xml',
        'data/demand_event_data.xml',
        'data/demand_event_rule_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'al_khulood_production_forecast/static/src/scss/forecast.scss',
            'al_khulood_production_forecast/static/src/js/forecast.js',
            'al_khulood_production_forecast/static/src/xml/forecast.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
