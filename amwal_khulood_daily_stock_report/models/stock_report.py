from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero, float_round

OWNER_GROUP = "amwal_khulood_daily_stock_report.group_amwal_stock_report_owner"

# A "day" on the paper report is a Bahrain calendar day (UTC+3, no daylight saving).
REPORT_TZ = pytz.timezone("Asia/Bahrain")
PRECISION = 3

# Columns that are always part of the report.
COLUMN_KEYS = [
    "opening",
    "received",
    "sold",
    "giveaway",
    "sample",
    "returned",
    "transferred_out",
    "delivered",
    "other",
    "closing",
]
# Columns that describe something that happened during the day (everything but the balances).
MOVEMENT_KEYS = [k for k in COLUMN_KEYS if k not in ("opening", "closing")]

POS_ORDER_STATES = ("paid", "done", "invoiced")


class AmwalKhuloodStockReport(models.AbstractModel):
    """Data provider for the owner-only Daily Stock Report dashboard.

    Nothing is stored. For a branch and a day:

    * Closing  = stock now - all done moves since the end of that day
    * Opening  = stock now - all done moves since the start of that day
    * Received / Returned / Giveaway / Transferred out / Delivered come from the
      done stock moves of that day, classified by where the stock came from or went to.
    * Sold / Sample come from POS lines: lines with a price are Sold (refunds net
      off), lines at price 0 or 100% discount are Sample.
    * Other is whatever is left, so that
      Opening + Received - Sold - Giveaway - Sample - Returned - Transferred out
      - Delivered + Other = Closing always holds.

    The branch stock is read from stock.quant / stock.move because on this
    instance the `location` context key is not honoured by `qty_available`.
    """

    _name = "amwal.khulood.stock.report"
    _description = "Daily Stock Report (owner dashboard data)"

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    def _check_owner(self):
        if not self.env.user.has_group(OWNER_GROUP):
            raise AccessError(_("The Daily Stock Report is available to the owner only."))

    # ------------------------------------------------------------------
    # Public API (called from the dashboard)
    # ------------------------------------------------------------------
    @api.model
    def get_branches(self):
        self._check_owner()
        report = self.sudo()
        return [
            {"id": warehouse.id, "name": warehouse.name}
            for warehouse in report._configured_branches()
        ]

    @api.model
    def get_report(self, date_str=None, warehouse_id=False):
        self._check_owner()
        report = self.sudo()
        return report._build_report(date_str, warehouse_id)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def _setup_records(self):
        return self.env["amwal.khulood.stock.report.setup"].search([])

    def _configured_branches(self):
        return self._setup_records().filtered(lambda r: r.kind == "branch").mapped("warehouse_id")

    def _internal_locations(self, warehouses):
        Location = self.env["stock.location"].with_context(active_test=False)
        if not warehouses:
            return Location
        return Location.search(
            [
                ("id", "child_of", warehouses.mapped("view_location_id").ids),
                ("usage", "=", "internal"),
            ]
        )

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        try:
            return fields.Date.to_date(value)
        except (ValueError, TypeError):
            raise UserError(_("Invalid date: %s", value))

    @staticmethod
    def _day_bounds(day):
        """Start and end of a Bahrain calendar day as naive UTC datetimes."""
        start = REPORT_TZ.localize(datetime.combine(day, time.min))
        end = REPORT_TZ.localize(datetime.combine(day + timedelta(days=1), time.min))
        return (
            start.astimezone(pytz.utc).replace(tzinfo=None),
            end.astimezone(pytz.utc).replace(tzinfo=None),
        )

    @staticmethod
    def _clean(value):
        # `or 0.0` turns -0.0 into 0.0
        return float_round(value, precision_digits=PRECISION) or 0.0

    @staticmethod
    def _is_storable(product):
        if "is_storable" in product._fields:
            return product.is_storable
        return product.type == "product"

    @staticmethod
    def _boundary_domain(loc_ids):
        """Moves that cross the boundary of the branch locations (one side in, one side out)."""
        return [
            "|",
            "&",
            ("location_id", "in", loc_ids),
            ("location_dest_id", "not in", loc_ids),
            "&",
            ("location_id", "not in", loc_ids),
            ("location_dest_id", "in", loc_ids),
        ]

    # ------------------------------------------------------------------
    # Stock figures
    # ------------------------------------------------------------------
    def _current_stock(self, locations):
        result = defaultdict(float)
        groups = self.env["stock.quant"]._read_group(
            [("location_id", "in", locations.ids)], ["product_id"], ["quantity:sum"]
        )
        for product, quantity in groups:
            result[product.id] += quantity
        return result

    def _net_since(self, locations, since):
        """Net quantity (in - out) that crossed the branch boundary in done moves from `since`."""
        loc_ids = set(locations.ids)
        domain = [("state", "=", "done"), ("date", ">=", since)] + self._boundary_domain(
            list(loc_ids)
        )
        result = defaultdict(float)
        groups = self.env["stock.move"]._read_group(
            domain,
            ["product_id", "location_id", "location_dest_id"],
            ["product_qty:sum"],
        )
        for product, _source, destination, quantity in groups:
            result[product.id] += quantity if destination.id in loc_ids else -quantity
        return result

    def _pos_quantities(self, pos_configs, start, end):
        """(sold, sample) quantities per product from POS lines of the day."""
        sold, sample = defaultdict(float), defaultdict(float)
        if not pos_configs:
            return sold, sample
        Line = self.env["pos.order.line"]
        base = [
            ("order_id.config_id", "in", pos_configs.ids),
            ("order_id.state", "in", POS_ORDER_STATES),
            ("order_id.date_order", ">=", start),
            ("order_id.date_order", "<", end),
        ]
        paid_domain = base + [("price_unit", ">", 0), ("discount", "<", 100)]
        free_domain = base + ["|", ("price_unit", "=", 0), ("discount", "=", 100)]
        for product, quantity in Line._read_group(paid_domain, ["product_id"], ["qty:sum"]):
            sold[product.id] += quantity
        for product, quantity in Line._read_group(free_domain, ["product_id"], ["qty:sum"]):
            sample[product.id] += quantity
        return sold, sample

    def _classify_day_moves(
        self,
        locations,
        other_branch_locations,
        giveaway_locations,
        giveaway_partner_ids,
        pos_types,
        pos_return_types,
        start,
        end,
    ):
        """Classify the day's non-POS moves into report columns.

        Moves that are not classified here (inventory adjustments, scrap, customer
        returns outside POS ...) simply fall into the "Other" column.
        """
        loc_ids = set(locations.ids)
        other_branch_ids = set(other_branch_locations.ids)
        giveaway_ids = set(giveaway_locations.ids)
        buckets = defaultdict(lambda: defaultdict(float))
        domain = [
            ("state", "=", "done"),
            ("date", ">=", start),
            ("date", "<", end),
            ("picking_type_id", "not in", pos_types.ids),
        ] + self._boundary_domain(list(loc_ids))
        for move in self.env["stock.move"].search(domain):
            source, destination = move.location_id, move.location_dest_id
            quantity = move.product_qty
            bucket = None
            if destination.id in loc_ids:
                # Stock coming into the branch.
                if move.picking_type_id in pos_return_types and source.usage == "customer":
                    continue  # a POS refund; it is already netted off in Sold
                if source.usage in ("internal", "supplier", "production", "transit"):
                    bucket = "received"
            else:
                # Stock leaving the branch.
                if destination.id in giveaway_ids:
                    bucket = "giveaway"
                elif destination.usage == "customer":
                    partner = move.picking_id.partner_id
                    bucket = "giveaway" if partner.id in giveaway_partner_ids else "delivered"
                elif destination.id in other_branch_ids:
                    bucket = "transferred_out"
                elif destination.usage == "internal":
                    bucket = "returned"
            if bucket:
                buckets[bucket][move.product_id.id] += quantity
        return buckets

    # ------------------------------------------------------------------
    # The report
    # ------------------------------------------------------------------
    def _build_report(self, date_str, warehouse_id):
        today = datetime.now(REPORT_TZ).date()
        day = self._parse_date(date_str) or today
        start, end = self._day_bounds(day)

        result = {
            "error": False,
            "date": day.isoformat(),
            "is_today": day == today,
            "rows": [],
            "categories": [],
        }

        all_branches = self._configured_branches()
        if not all_branches:
            result["error"] = "no_branches"
            return result
        if warehouse_id:
            branches = all_branches.filtered(lambda w: w.id == warehouse_id)
            if not branches:
                raise UserError(_("That branch is not part of the report setup."))
        else:
            branches = all_branches

        locations = self._internal_locations(branches)
        if not locations:
            return result
        other_branch_locations = self._internal_locations(all_branches - branches)

        setup = self._setup_records()
        giveaway_locations = setup.filtered(lambda r: r.kind == "giveaway_location").mapped(
            "location_id"
        )
        giveaway_partners = setup.filtered(lambda r: r.kind == "giveaway_partner").mapped(
            "partner_id"
        )
        giveaway_partner_ids = set()
        if giveaway_partners:
            giveaway_partner_ids = set(
                self.env["res.partner"]
                .with_context(active_test=False)
                .search([("id", "child_of", giveaway_partners.ids)])
                .ids
            )

        # POS: operation types of every POS (their stock moves are represented by Sold and
        # Sample), and the POS configurations that belong to the selected branches.
        pos_configs = self.env["pos.config"].with_context(active_test=False).search([])
        pos_types = pos_configs.mapped("picking_type_id")
        pos_return_types = pos_types.mapped("return_picking_type_id")
        branch_configs = pos_configs.filtered(lambda c: c.picking_type_id.warehouse_id in branches)

        stock_now = self._current_stock(locations)
        net_since_start = self._net_since(locations, start)
        net_since_end = self._net_since(locations, end)
        sold, sample = self._pos_quantities(branch_configs, start, end)
        buckets = self._classify_day_moves(
            locations,
            other_branch_locations,
            giveaway_locations,
            giveaway_partner_ids,
            pos_types,
            pos_return_types,
            start,
            end,
        )

        product_ids = set(stock_now) | set(net_since_start) | set(sold) | set(sample)
        products = self.env["product.product"].with_context(active_test=False).browse(
            sorted(product_ids)
        )

        rows = []
        for product in products:
            if not self._is_storable(product):
                continue
            pid = product.id
            now_qty = stock_now.get(pid, 0.0)
            closing = now_qty - net_since_end.get(pid, 0.0)
            opening = now_qty - net_since_start.get(pid, 0.0)
            received = buckets["received"].get(pid, 0.0)
            giveaway = buckets["giveaway"].get(pid, 0.0)
            returned = buckets["returned"].get(pid, 0.0)
            transferred_out = buckets["transferred_out"].get(pid, 0.0)
            delivered = buckets["delivered"].get(pid, 0.0)
            sold_qty = sold.get(pid, 0.0)
            sample_qty = sample.get(pid, 0.0)
            other = (
                closing
                - opening
                - received
                + sold_qty
                + sample_qty
                + giveaway
                + returned
                + transferred_out
                + delivered
            )
            row = {
                "id": pid,
                "name": product.with_context(display_default_code=False).display_name,
                "code": product.default_code or "",
                "category": product.categ_id.name or "",
                "uom": product.uom_id.name or "",
                "opening": self._clean(opening),
                "received": self._clean(received),
                "sold": self._clean(sold_qty),
                "giveaway": self._clean(giveaway),
                "sample": self._clean(sample_qty),
                "returned": self._clean(returned),
                "transferred_out": self._clean(transferred_out),
                "delivered": self._clean(delivered),
                "other": self._clean(other),
                "closing": self._clean(closing),
            }
            if all(float_is_zero(row[key], precision_digits=PRECISION) for key in COLUMN_KEYS):
                continue
            row["moved"] = any(
                not float_is_zero(row[key], precision_digits=PRECISION) for key in MOVEMENT_KEYS
            )
            rows.append(row)

        rows.sort(key=lambda r: (r["category"].lower(), r["name"].lower()))
        result["rows"] = rows
        result["categories"] = sorted({r["category"] for r in rows if r["category"]})
        return result
