{
    "name": "Amwal Khulood Daily Stock Report",
    "version": "19.0.1.0.2",
    "summary": "Owner-only daily stock movement report per branch: opening, received, sold, "
    "giveaway, sample, returned and closing.",
    "description": """
Daily Stock Report (owner dashboard)
====================================

Digital version of the paper "Daily Stock Report" that branches send to the owner.
For any day and branch (or all branches) it shows, for every product that has stock
or any movement:

* Opening Balance, Received, Sold, Giveaway, Sample, Returned, Closing Balance
* Transferred Out, Delivered and Other - these appear only when they have values

Read-only: the module never writes to stock, POS or accounting and stores no
snapshots. Everything is computed from stock moves, stock quants and POS lines.

Access is limited to the group "Daily Stock Report: Owner". The data methods check the
group on the server as well, so hiding the menu is not the only protection.
""",
    "category": "Inventory/Reporting",
    "author": "Amwal W.L.L.",
    "company": "Amwal W.L.L.",
    "website": "https://amwal.solutions",
    "license": "OPL-1",
    "depends": ["stock", "point_of_sale", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/setup_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "amwal_khulood_daily_stock_report/static/src/scss/stock_report.scss",
            "amwal_khulood_daily_stock_report/static/src/js/stock_report.js",
            "amwal_khulood_daily_stock_report/static/src/xml/stock_report.xml",
        ],
    },
    "installable": True,
    "application": False,
}
