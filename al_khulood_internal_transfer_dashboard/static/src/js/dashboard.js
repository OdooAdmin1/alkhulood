/** @odoo-module **/
import { Component, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { download } from "@web/core/network/download";

const MODEL = "al.internal.transfer.dashboard";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ---- date helpers (all on plain "YYYY-MM-DD" strings, no timezone surprises) ----
function parseIso(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return { y, m, d };
}
function toIso(date) {
    return date.toISOString().slice(0, 10);
}
function addDays(iso, n) {
    const { y, m, d } = parseIso(iso);
    return toIso(new Date(Date.UTC(y, m - 1, d + n)));
}
function monthStart(iso) {
    return iso.slice(0, 8) + "01";
}
function monthEnd(iso) {
    const { y, m } = parseIso(iso);
    return toIso(new Date(Date.UTC(y, m, 0)));
}
function shortDate(iso, withYear = false) {
    const { y, m, d } = parseIso(iso);
    return `${d} ${MONTHS[m - 1]}${withYear ? " " + String(y).slice(2) : ""}`;
}

// ---------------------------------------------------------------------------
// Multi select dropdown
// ---------------------------------------------------------------------------
class MultiSelect extends Component {
    static template = "al_internal_transfer_dashboard.MultiSelect";
    static props = {
        label: String,
        options: Array,
        selected: Array,
        onChange: Function,
        allLabel: { type: String, optional: true },
    };

    setup() {
        this.root = useRef("root");
        this.state = useState({ open: false, query: "" });
        useExternalListener(window, "click", (ev) => {
            const el = this.root.el;
            if (this.state.open && el && !ev.composedPath().includes(el)) {
                this.state.open = false;
            }
        });
    }

    get filtered() {
        const q = this.state.query.trim().toLowerCase();
        if (!q) {
            return this.props.options;
        }
        return this.props.options.filter(
            (o) => o.label.toLowerCase().includes(q) || (o.sub || "").toLowerCase().includes(q)
        );
    }

    get summary() {
        const sel = this.props.selected;
        if (!sel.length) {
            return this.props.allLabel || "All";
        }
        if (sel.length <= 2) {
            const byId = new Map(this.props.options.map((o) => [o.id, o]));
            return sel.map((id) => (byId.get(id) || {}).short || (byId.get(id) || {}).label || id).join(", ");
        }
        return `${sel.length} selected`;
    }

    isSelected(id) {
        return this.props.selected.includes(id);
    }

    toggleOpen() {
        this.state.open = !this.state.open;
        this.state.query = "";
    }

    toggle(id) {
        const sel = this.props.selected.includes(id)
            ? this.props.selected.filter((x) => x !== id)
            : [...this.props.selected, id];
        this.props.onChange(sel);
    }

    clear() {
        this.props.onChange([]);
    }

    selectVisible() {
        const ids = new Set(this.props.selected);
        for (const o of this.filtered) {
            ids.add(o.id);
        }
        this.props.onChange([...ids]);
    }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
class InternalTransferDashboard extends Component {
    static template = "al_internal_transfer_dashboard.Dashboard";
    static components = { MultiSelect };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.requestId = 0;
        this.searchTimer = null;
        this.state = useState({
            ready: false,
            loading: true,
            exporting: false,
            error: null,
            preset: "month",
            filters: {
                date_from: "",
                date_to: "",
                src_ids: [],
                dst_ids: [],
                categ_ids: [],
                search: "",
            },
            grain: "auto",
            sort: "value",
            order: "desc",
            page: 1,
            limit: 25,
            data: null,
            derived: null,
        });
        onWillStart(async () => {
            const opts = await this.orm.call(MODEL, "get_options", []);
            this.options = opts;
            this.whById = new Map(opts.warehouses.map((w) => [w.id, w]));
            this.warehouseOptions = opts.warehouses.map((w) => ({
                id: w.id,
                label: `${w.code} - ${w.name}`,
                short: w.code,
            }));
            this.categoryOptions = opts.categories.map((c) => ({ id: c.id, label: c.name }));
            this.state.filters.src_ids = [...opts.default_src_ids];
            this.applyPreset("month", false);
            await this.load();
            this.state.ready = true;
        });
    }

    // ---- formatting -----------------------------------------------------
    get decimals() {
        return this.options.currency.decimal_places;
    }
    get currencySymbol() {
        return this.options.currency.symbol;
    }
    fmtMoney(v) {
        return (v || 0).toLocaleString("en-US", {
            minimumFractionDigits: this.decimals,
            maximumFractionDigits: this.decimals,
        });
    }
    fmtInt(v) {
        return (v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }
    fmtQty(v) {
        return (v || 0).toLocaleString("en-US", { maximumFractionDigits: 2 });
    }
    fmtShort(v) {
        const a = Math.abs(v);
        if (a >= 1e6) {
            return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
        }
        if (a >= 1e3) {
            return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
        }
        return v.toFixed(a < 10 && a > 0 ? 1 : 0).replace(/\.0$/, "");
    }
    whCode(id) {
        const w = this.whById.get(id);
        return w ? w.code : String(id);
    }
    whName(id) {
        const w = this.whById.get(id);
        return w ? w.name : String(id);
    }
    periodLabel(iso) {
        const grain = this.state.data.grain;
        if (grain === "month") {
            const { y, m } = parseIso(iso);
            return `${MONTHS[m - 1]} ${String(y).slice(2)}`;
        }
        return shortDate(iso);
    }

    // ---- loading --------------------------------------------------------
    async load() {
        const myId = ++this.requestId;
        this.state.loading = true;
        this.state.error = null;
        try {
            const f = this.state.filters;
            const data = await this.orm.call(MODEL, "get_dashboard_data", [], {
                filters: {
                    date_from: f.date_from,
                    date_to: f.date_to,
                    src_ids: [...f.src_ids],
                    dst_ids: [...f.dst_ids],
                    categ_ids: [...f.categ_ids],
                    search: f.search,
                },
                grain: this.state.grain,
                sort: this.state.sort,
                order: this.state.order,
                page: this.state.page,
                limit: this.state.limit,
            });
            if (myId !== this.requestId) {
                return; // a newer request superseded this one
            }
            this.state.data = data;
            this.state.derived = this.buildDerived(data);
        } catch (e) {
            if (myId === this.requestId) {
                this.state.error = (e && e.data && e.data.message) || (e && e.message) || "Could not load the dashboard.";
            }
        } finally {
            if (myId === this.requestId) {
                this.state.loading = false;
            }
        }
    }

    reloadFromFirstPage() {
        this.state.page = 1;
        return this.load();
    }

    // ---- derived structures for charts ---------------------------------
    buildDerived(data) {
        const routes = data.routes;
        const sent = new Map();
        const received = new Map();
        const cells = {};
        let maxCell = 0;
        for (const r of routes) {
            sent.set(r.src_id, (sent.get(r.src_id) || 0) + r.value);
            received.set(r.dst_id, (received.get(r.dst_id) || 0) + r.value);
            cells[`${r.src_id}-${r.dst_id}`] = r;
            maxCell = Math.max(maxCell, r.value);
        }
        const byValue = (m) => [...m.entries()].sort((a, b) => b[1] - a[1]);
        const rowIds = byValue(sent).map(([id]) => id);
        const colIds = byValue(received).map(([id]) => id);

        const matrixRows = rowIds.map((s) => ({
            id: s,
            code: this.whCode(s),
            name: this.whName(s),
            total: sent.get(s) || 0,
            cells: colIds.map((d) => {
                const r = cells[`${s}-${d}`];
                const v = r ? r.value : 0;
                const ratio = maxCell ? v / maxCell : 0;
                const alpha = v ? 0.12 + 0.88 * Math.sqrt(ratio) : 0;
                return {
                    dst: d,
                    value: v,
                    transfers: r ? r.transfers : 0,
                    style: v ? `background: rgba(67, 56, 202, ${alpha.toFixed(3)});` : "",
                    dark: alpha > 0.5,
                };
            }),
        }));
        const matrix = {
            cols: colIds.map((d) => ({ id: d, code: this.whCode(d), name: this.whName(d), total: received.get(d) || 0 })),
            rows: matrixRows,
        };

        const topRoutes = routes.slice(0, 10);
        const maxRoute = topRoutes.length ? topRoutes[0].value : 0;
        const routeBars = topRoutes.map((r) => ({
            key: `${r.src_id}-${r.dst_id}`,
            src: r.src_id,
            dst: r.dst_id,
            label: `${this.whCode(r.src_id)} → ${this.whCode(r.dst_id)}`,
            title: `${this.whName(r.src_id)} → ${this.whName(r.dst_id)}`,
            value: r.value,
            transfers: r.transfers,
            pct: maxRoute ? Math.max((r.value / maxRoute) * 100, 1.5) : 0,
        }));

        const maxProd = data.top_products.length ? data.top_products[0].value : 0;
        const productBars = data.top_products.map((p) => ({
            ...p,
            pct: maxProd ? Math.max((p.value / maxProd) * 100, 1.5) : 0,
        }));

        return {
            matrix,
            routeBars,
            productBars,
            trend: this.buildTrend(data),
            topRoute: routes[0] || null,
            topProduct: data.top_products[0] || null,
        };
    }

    buildTrend(data) {
        const pts = data.trend;
        const W = 640;
        const H = 240;
        const padL = 46;
        const padR = 10;
        const padT = 14;
        const padB = 30;
        const plotW = W - padL - padR;
        const plotH = H - padT - padB;
        const maxV = Math.max(...pts.map((p) => p.value), 0);
        const niceMax = this.niceCeil(maxV);
        const n = pts.length || 1;
        const slot = plotW / n;
        const bw = Math.min(slot * 0.68, 46);
        const labelStep = Math.max(1, Math.ceil(n / 8));
        const bars = pts.map((p, i) => {
            const h = niceMax ? (p.value / niceMax) * plotH : 0;
            return {
                x: padL + slot * i + (slot - bw) / 2,
                y: padT + plotH - h,
                w: bw,
                h: Math.max(h, p.value ? 1 : 0),
                cx: padL + slot * i + slot / 2,
                label: this.periodLabelFor(p.period, data.grain),
                showLabel: i % labelStep === 0,
                title: `${this.periodLabelFor(p.period, data.grain, true)}: ${this.fmtMoney(p.value)} (${p.transfers} transfers)`,
            };
        });
        const ticks = [0, 1, 2, 3, 4].map((i) => {
            const v = (niceMax / 4) * i;
            return { y: padT + plotH - (plotH * i) / 4, label: this.fmtShort(v), x2: W - padR };
        });
        return { W, H, padL, padB, plotH, padT, bars, ticks, baseY: padT + plotH };
    }

    periodLabelFor(iso, grain, long = false) {
        if (grain === "month") {
            const { y, m } = parseIso(iso);
            return long ? `${MONTHS[m - 1]} ${y}` : `${MONTHS[m - 1]} ${String(y).slice(2)}`;
        }
        if (grain === "week") {
            return long ? `Week of ${shortDate(iso, true)}` : shortDate(iso);
        }
        return shortDate(iso, long);
    }

    niceCeil(v) {
        if (!v) {
            return 1;
        }
        const exp = Math.pow(10, Math.floor(Math.log10(v)));
        const f = v / exp;
        const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
        return nice * exp;
    }

    // ---- filter handlers -----------------------------------------------
    applyPreset(key, reload = true) {
        const today = this.options.today;
        let from = today;
        let to = today;
        if (key === "7d") {
            from = addDays(today, -6);
        } else if (key === "month") {
            from = monthStart(today);
        } else if (key === "last_month") {
            const prev = addDays(monthStart(today), -1);
            from = monthStart(prev);
            to = monthEnd(prev);
        } else if (key === "30d") {
            from = addDays(today, -29);
        } else if (key === "ytd") {
            from = today.slice(0, 4) + "-01-01";
        }
        this.state.preset = key;
        this.state.filters.date_from = from;
        this.state.filters.date_to = to;
        if (reload) {
            return this.reloadFromFirstPage();
        }
    }

    onDate(field, value) {
        if (!value) {
            return;
        }
        this.state.filters[field] = value;
        this.state.preset = "custom";
        return this.reloadFromFirstPage();
    }

    onSelect(field, ids) {
        this.state.filters[field] = ids;
        return this.reloadFromFirstPage();
    }

    onSearchInput(ev) {
        this.state.filters.search = ev.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.reloadFromFirstPage(), 450);
    }

    clearSearch() {
        this.state.filters.search = "";
        return this.reloadFromFirstPage();
    }

    setGrain(grain) {
        this.state.grain = grain;
        return this.load();
    }

    resetFilters() {
        this.state.filters.src_ids = [...this.options.default_src_ids];
        this.state.filters.dst_ids = [];
        this.state.filters.categ_ids = [];
        this.state.filters.search = "";
        this.state.grain = "auto";
        this.state.sort = "value";
        this.state.order = "desc";
        return this.applyPreset("month");
    }

    focusRoute(srcId, dstId) {
        this.state.filters.src_ids = [srcId];
        this.state.filters.dst_ids = [dstId];
        return this.reloadFromFirstPage();
    }

    onCell(srcId, cell) {
        if (cell.value) {
            return this.focusRoute(srcId, cell.dst);
        }
    }

    get hasRouteFocus() {
        const f = this.state.filters;
        return f.src_ids.length === 1 && f.dst_ids.length === 1;
    }

    get activeFilterCount() {
        const f = this.state.filters;
        const defaultSrc = this.options.default_src_ids;
        const srcIsDefault =
            f.src_ids.length === defaultSrc.length && f.src_ids.every((id) => defaultSrc.includes(id));
        return (srcIsDefault ? 0 : 1) + (f.dst_ids.length ? 1 : 0) + (f.categ_ids.length ? 1 : 0) + (f.search ? 1 : 0);
    }

    // ---- table ----------------------------------------------------------
    sortBy(col) {
        if (this.state.sort === col) {
            this.state.order = this.state.order === "asc" ? "desc" : "asc";
        } else {
            this.state.sort = col;
            this.state.order = ["product", "category", "from", "to"].includes(col) ? "asc" : "desc";
        }
        return this.reloadFromFirstPage();
    }

    sortIcon(col) {
        if (this.state.sort !== col) {
            return "fa fa-sort alit-sort-idle";
        }
        return this.state.order === "asc" ? "fa fa-sort-asc" : "fa fa-sort-desc";
    }

    get pageInfo() {
        const d = this.state.data.detail;
        if (!d.total) {
            return "No rows";
        }
        const from = (d.page - 1) * d.limit + 1;
        const to = Math.min(d.page * d.limit, d.total);
        return `${this.fmtInt(from)}–${this.fmtInt(to)} of ${this.fmtInt(d.total)}`;
    }
    get canPrev() {
        return this.state.data.detail.page > 1;
    }
    get canNext() {
        const d = this.state.data.detail;
        return d.page * d.limit < d.total;
    }
    prevPage() {
        if (this.canPrev) {
            this.state.page -= 1;
            return this.load();
        }
    }
    nextPage() {
        if (this.canNext) {
            this.state.page += 1;
            return this.load();
        }
    }
    onLimit(ev) {
        this.state.limit = parseInt(ev.target.value, 10) || 25;
        return this.reloadFromFirstPage();
    }

    // ---- export ---------------------------------------------------------
    async exportXlsx() {
        if (this.state.exporting) {
            return;
        }
        this.state.exporting = true;
        try {
            const f = this.state.filters;
            await download({
                url: "/al_internal_transfer_dashboard/export",
                data: {
                    filters: JSON.stringify({
                        date_from: f.date_from,
                        date_to: f.date_to,
                        src_ids: f.src_ids,
                        dst_ids: f.dst_ids,
                        categ_ids: f.categ_ids,
                        search: f.search,
                    }),
                },
            });
        } catch (e) {
            this.notification.add("The Excel export failed. Please try again or narrow the filters.", {
                type: "danger",
            });
        } finally {
            this.state.exporting = false;
        }
    }
}

registry.category("actions").add("al_internal_transfer_dashboard", InternalTransferDashboard);
