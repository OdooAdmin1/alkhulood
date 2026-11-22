/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const MODEL = "amwal.khulood.stock.report";

// Column order follows the paper sheet. Columns without `always` only appear on days
// when at least one product has a value in them.
const COLUMNS = [
    { key: "opening", label: "Opening Balance", always: true },
    { key: "received", label: "Received", always: true },
    { key: "sold", label: "Sold", always: true },
    { key: "giveaway", label: "Giveaway", always: true },
    { key: "sample", label: "Sample", always: true },
    { key: "returned", label: "Returned", always: true },
    {
        key: "transferred_out",
        label: "Transferred Out",
        hint: "Sent to another branch",
    },
    {
        key: "delivered",
        label: "Delivered",
        hint: "Delivered to a customer against a sales order (not a POS sale)",
    },
    {
        key: "other",
        label: "Other",
        warn: true,
        hint: "Stock changes not covered by the other columns: adjustments, scrap, or POS sales recorded on a different day",
    },
    { key: "closing", label: "Closing Balance", always: true },
];

function bahrainToday() {
    // en-CA formats as YYYY-MM-DD
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bahrain" }).format(new Date());
}

function shiftDate(iso, days) {
    const [year, month, day] = iso.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

export class AmwalKhuloodStockReport extends Component {
    static template = "amwal_khulood_daily_stock_report.Report";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.requestId = 0;
        this.state = useState({
            loading: true,
            error: "",
            date: bahrainToday(),
            branchId: false,
            branches: [],
            data: null,
            search: "",
            category: "",
            uom: "",
            showTotals: false,
            onlyMovement: false,
            sortKey: "name",
            sortDir: 1,
        });
        onWillStart(async () => {
            await this.loadBranches();
            if (!this.state.error) {
                await this.load();
            }
        });
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    errorMessage(error) {
        return (error && error.data && error.data.message) || (error && error.message) || "Something went wrong.";
    }

    async loadBranches() {
        try {
            this.state.branches = await this.orm.call(MODEL, "get_branches", []);
        } catch (error) {
            this.state.error = this.errorMessage(error);
            this.state.loading = false;
        }
    }

    async load() {
        const requestId = ++this.requestId;
        this.state.loading = true;
        this.state.error = "";
        try {
            const data = await this.orm.call(MODEL, "get_report", [], {
                date_str: this.state.date,
                warehouse_id: this.state.branchId || false,
            });
            if (requestId !== this.requestId) {
                return; // a newer request is already running
            }
            this.state.data = data;
            if (this.state.category && !data.categories.includes(this.state.category)) {
                this.state.category = "";
            }
            if (this.state.uom && !data.rows.some((row) => row.uom === this.state.uom)) {
                this.state.uom = "";
            }
        } catch (error) {
            if (requestId !== this.requestId) {
                return;
            }
            this.state.error = this.errorMessage(error);
            this.state.data = null;
        } finally {
            if (requestId === this.requestId) {
                this.state.loading = false;
            }
        }
    }

    // ------------------------------------------------------------------
    // Derived values
    // ------------------------------------------------------------------
    get today() {
        return bahrainToday();
    }

    get isToday() {
        return this.state.date >= this.today;
    }

    get dateLabel() {
        return new Date(this.state.date + "T00:00:00Z").toLocaleDateString("en-GB", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric",
            timeZone: "UTC",
        });
    }

    get branchName() {
        if (!this.state.branchId) {
            return "All branches";
        }
        const branch = this.state.branches.find((b) => b.id === this.state.branchId);
        return branch ? branch.name : "";
    }

    get noBranches() {
        return Boolean(this.state.data && this.state.data.error === "no_branches");
    }

    get categories() {
        return (this.state.data && this.state.data.categories) || [];
    }

    get uoms() {
        const data = this.state.data;
        return data ? [...new Set(data.rows.map((row) => row.uom))].sort() : [];
    }

    get noMovementDay() {
        const data = this.state.data;
        return Boolean(data && data.rows.length && !data.rows.some((row) => row.moved));
    }

    get view() {
        const data = this.state.data;
        if (!data) {
            return { rows: [], cols: [], totals: [] };
        }
        // Columns that are optional only show when some product has a value on that day.
        const cols = COLUMNS.filter((col) => col.always || data.rows.some((row) => row[col.key]));

        const query = this.state.search.trim().toLowerCase();
        const rows = data.rows.filter((row) => {
            if (this.state.category && row.category !== this.state.category) {
                return false;
            }
            if (this.state.uom && row.uom !== this.state.uom) {
                return false;
            }
            if (this.state.onlyMovement && !row.moved) {
                return false;
            }
            if (query && !(row.name.toLowerCase().includes(query) || row.code.toLowerCase().includes(query))) {
                return false;
            }
            return true;
        });

        const { sortKey, sortDir } = this.state;
        rows.sort((a, b) => {
            if (sortKey === "name") {
                return sortDir * a.name.localeCompare(b.name);
            }
            return sortDir * (a[sortKey] - b[sortKey]) || a.name.localeCompare(b.name);
        });

        // Quantities can only be added together when they share a unit of measure.
        const byUom = new Map();
        for (const row of rows) {
            if (!byUom.has(row.uom)) {
                byUom.set(row.uom, { uom: row.uom, count: 0 });
            }
            const total = byUom.get(row.uom);
            total.count += 1;
            for (const col of COLUMNS) {
                total[col.key] = (total[col.key] || 0) + row[col.key];
            }
        }
        return { rows, cols, totals: [...byUom.values()] };
    }

    // ------------------------------------------------------------------
    // Template helpers
    // ------------------------------------------------------------------
    fmt(value) {
        if (!value || Math.abs(value) < 0.0005) {
            return "–";
        }
        return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
    }

    cellClass(row, col) {
        const value = row[col.key];
        const classes = ["amw-num"];
        if (!value) {
            classes.push("amw-zero");
        }
        if (col.warn && value) {
            classes.push("amw-warn");
        }
        if (value < 0) {
            classes.push("amw-neg");
        }
        if (col.key === "opening" || col.key === "closing") {
            classes.push("amw-balance");
        }
        return classes.join(" ");
    }

    totalCellClass(total, col) {
        const classes = ["amw-num"];
        if (!total[col.key]) {
            classes.push("amw-zero");
        }
        if (col.warn && total[col.key]) {
            classes.push("amw-warn");
        }
        return classes.join(" ");
    }

    sortIcon(key) {
        if (this.state.sortKey !== key) {
            return "fa-sort amw-sort-idle";
        }
        return this.state.sortDir === 1 ? "fa-sort-asc" : "fa-sort-desc";
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------
    async shiftDay(days) {
        const next = shiftDate(this.state.date, days);
        this.state.date = next > this.today ? this.today : next;
        await this.load();
    }

    async goToday() {
        this.state.date = this.today;
        await this.load();
    }

    async onDateChange(ev) {
        const value = ev.target.value;
        if (!value) {
            return;
        }
        this.state.date = value > this.today ? this.today : value;
        await this.load();
    }

    async selectBranch(branchId) {
        this.state.branchId = branchId;
        await this.load();
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
    }

    onCategory(ev) {
        this.state.category = ev.target.value;
    }

    onUom(ev) {
        this.state.uom = ev.target.value;
    }

    onToggleTotals(ev) {
        this.state.showTotals = ev.target.checked;
    }

    onToggleMovement(ev) {
        this.state.onlyMovement = ev.target.checked;
    }

    sortBy(key) {
        if (this.state.sortKey === key) {
            this.state.sortDir *= -1;
        } else {
            this.state.sortKey = key;
            this.state.sortDir = key === "name" ? 1 : -1;
        }
    }
}

registry.category("actions").add("amwal_khulood_daily_stock_report", AmwalKhuloodStockReport);
