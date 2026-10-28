/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const W = 1000;
const H = 320;
const PAD = { l: 64, r: 16, t: 14, b: 30 };

function niceStep(raw) {
    if (!(raw > 0)) {
        return 1;
    }
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

export class SfDashboard extends Component {
    static template = "alkhuloodh_sales_forecast.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.svg = useRef("svg");
        this.state = useState({
            options: { warehouses: [], categories: [], products: [], run: null },
            filters: {
                warehouse_id: "",
                categ_id: "",
                product_id: "",
                metric: "revenue",
                months: 3,
                history_days: 28,
                gran: "day",
            },
            data: null,
            error: "",
            loading: false,
            hover: null,
        });
        onWillStart(async () => {
            try {
                this.state.options = await this.orm.call("alkhuloodh.sf.dashboard", "get_options", []);
            } catch (e) {
                this.state.error = (e.data && e.data.message) || e.message || String(e);
                return;
            }
            await this.load();
        });
    }

    // ---------------------------------------------------------------- data
    async load() {
        this.state.loading = true;
        this.state.hover = null;
        try {
            const data = await this.orm.call("alkhuloodh.sf.dashboard", "get_data", [this.state.filters]);
            if (data.error) {
                this.state.error = data.error;
                this.state.data = null;
            } else {
                this.state.error = "";
                this.state.data = data;
            }
        } catch (e) {
            this.state.error = (e.data && e.data.message) || e.message || String(e);
            this.state.data = null;
        }
        this.state.loading = false;
    }

    async setFilter(name, value) {
        const numeric = ["warehouse_id", "categ_id", "product_id", "months", "history_days"];
        this.state.filters[name] = numeric.includes(name) && value !== "" ? parseInt(value, 10) : value;
        if (name === "product_id" && !value && this.state.filters.metric === "qty") {
            this.state.filters.metric = "revenue";
        }
        await this.load();
    }

    // ---------------------------------------------------------------- formatting
    get isQty() {
        return this.state.data ? this.state.data.metric === "qty" : this.state.filters.metric === "qty";
    }
    fmt(v, d = 0) {
        if (v === null || v === undefined) {
            return "–";
        }
        return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    }
    pct(v) {
        if (v === null || v === undefined) {
            return "–";
        }
        return (v > 0 ? "+" : "") + v.toFixed(1) + "%";
    }
    errClass(row) {
        if (row.error_pct === null || row.error_pct === undefined) {
            return "";
        }
        const a = Math.abs(row.error_pct);
        return a <= 10 ? "o_sf_ok" : a <= 25 ? "o_sf_warn" : "o_sf_bad";
    }
    rowClass(row, i) {
        const rows = this.state.data.rows;
        let c = row.future ? "o_sf_future" : "";
        if (i > 0 && row.future && !rows[i - 1].future) {
            c += " o_sf_origin";
        }
        if (row.event) {
            c += " o_sf_event";
        }
        return c;
    }
    get unit() {
        return this.isQty ? "units" : "BHD";
    }
    get granLabel() {
        return { day: "Date", week: "Week", month: "Month", hijri: "Hijri month" }[this.state.filters.gran];
    }

    // ---------------------------------------------------------------- chart
    get chart() {
        const data = this.state.data;
        if (!data || !data.rows.length) {
            return null;
        }
        const rows = data.rows;
        const n = rows.length;
        const iw = W - PAD.l - PAD.r;
        const ih = H - PAD.t - PAD.b;
        const x = (i) => PAD.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
        let max = 0;
        for (const r of rows) {
            for (const k of ["high", "expected", "actual"]) {
                if (r[k] !== null && r[k] > max) {
                    max = r[k];
                }
            }
        }
        const step = niceStep(max / 4);
        const top = Math.max(step * Math.ceil(max / step), step);
        const y = (v) => PAD.t + ih - (v / top) * ih;
        // band (contiguous segments where low/high exist)
        let band = "";
        let seg = [];
        const flush = () => {
            if (seg.length) {
                const up = seg.map((i) => `${x(i).toFixed(1)},${y(rows[i].high).toFixed(1)}`);
                const down = seg.slice().reverse().map((i) => `${x(i).toFixed(1)},${y(rows[i].low).toFixed(1)}`);
                band += "M" + up.join("L") + "L" + down.join("L") + "Z";
            }
            seg = [];
        };
        rows.forEach((r, i) => {
            if (r.low !== null && r.high !== null) {
                seg.push(i);
            } else {
                flush();
            }
        });
        flush();
        const line = (key) => {
            let d = "";
            let pen = false;
            rows.forEach((r, i) => {
                if (r[key] === null) {
                    pen = false;
                } else {
                    d += (pen ? "L" : "M") + x(i).toFixed(1) + "," + y(r[key]).toFixed(1);
                    pen = true;
                }
            });
            return d;
        };
        const yTicks = [];
        for (let v = 0; v <= top + 1e-9; v += step) {
            yTicks.push({ y: y(v), label: this.fmt(v) });
        }
        const xTicks = [];
        const every = Math.max(1, Math.ceil(n / 8));
        rows.forEach((r, i) => {
            if (i % every === 0) {
                xTicks.push({ x: x(i), label: n > 40 || this.state.filters.gran === "day" ? r.label.slice(5) : r.label });
            }
        });
        let lastPast = -1;
        rows.forEach((r, i) => {
            if (!r.future) {
                lastPast = i;
            }
        });
        const stepPx = n > 1 ? iw / (n - 1) : 0;
        const originX = lastPast >= 0 && lastPast < n - 1 ? x(lastPast) + stepPx / 2 : null;
        const events = [];
        rows.forEach((r, i) => {
            if (r.event && this.state.filters.gran === "day") {
                events.push({ x: x(i), y: PAD.t + ih });
            }
        });
        return {
            band, expected: line("expected"), actual: line("actual"), yTicks, xTicks, originX, events,
            x, y, n, left: PAD.l, right: W - PAD.r, bottom: PAD.t + ih, top: PAD.t,
        };
    }

    onMove(ev) {
        const c = this.chart;
        const svg = this.svg.el;
        if (!c || !svg) {
            return;
        }
        const rect = svg.getBoundingClientRect();
        const px = ((ev.clientX - rect.left) / rect.width) * W;
        const iw = W - PAD.l - PAD.r;
        let i = c.n === 1 ? 0 : Math.round(((px - PAD.l) / iw) * (c.n - 1));
        i = Math.max(0, Math.min(c.n - 1, i));
        this.state.hover = i;
    }
    onLeave() {
        this.state.hover = null;
    }
    get hoverRow() {
        const h = this.state.hover;
        return h === null || !this.state.data ? null : this.state.data.rows[h];
    }
    get hoverStyle() {
        const c = this.chart;
        const h = this.state.hover;
        if (!c || h === null) {
            return "";
        }
        const left = (c.x(h) / W) * 100;
        return left > 60 ? `right:${100 - left + 1}%;` : `left:${left + 1}%;`;
    }

    // ---------------------------------------------------------------- export
    exportCsv() {
        const d = this.state.data;
        if (!d) {
            return;
        }
        const cols = [
            ["label", this.granLabel], ["hijri", "Hijri"], ["event", "Event"], ["low", "Low"], ["expected", "Expected"],
            ["high", "High"], ["actual", "Actual"], ["error", "Error"], ["error_pct", "Error %"],
            ["lw", "Last week"], ["lm", "Last month"], ["ly", "Last year"], ["hly", "Same Hijri date last year"],
        ];
        const esc = (v) => (v === null || v === undefined ? "" : `"${String(v).replace(/"/g, '""')}"`);
        const lines = [cols.map((c) => esc(c[1])).join(",")];
        for (const r of d.rows) {
            lines.push(cols.map((c) => esc(r[c[0]])).join(","));
        }
        const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `sales_forecast_${this.state.filters.gran}_${d.origin}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
    }
}

registry.category("actions").add("alkhuloodh_sf_dashboard", SfDashboard);
