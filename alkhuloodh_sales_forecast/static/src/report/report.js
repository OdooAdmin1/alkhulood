/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component, onMounted, onWillStart, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SfReport extends Component {
    static template = "alkhuloodh_sales_forecast.Report";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.root = useRef("root");
        this.state = useState({
            options: { branches: [], categories: [], origin: "", horizon_end: "", default_date: "" },
            filters: { date_from: "", date_to: "", metric: "qty", categ_id: "", branch_ids: [] },
            data: null,
            error: "",
            loading: false,
            busy: "",
        });
        onWillStart(async () => {
            try {
                const o = await this.orm.call("alkhuloodh.sf.report", "get_options", []);
                this.state.options = o;
                this.state.filters.date_from = o.default_date;
                this.state.filters.date_to = o.default_date;
                this.state.filters.branch_ids = o.branches.map((b) => b.id);
            } catch (e) {
                this.state.error = (e.data && e.data.message) || e.message || String(e);
                return;
            }
            await this.load();
        });
        onMounted(() => this.applyTheme());
    }

    // The sticky columns need a solid background. Take the colours the page really has (light or dark mode), so the text
    // is always readable: no guessing about theme variables.
    applyTheme() {
        const el = this.root.el;
        if (!el) {
            return;
        }
        let bg = null;
        for (let n = el; n; n = n.parentElement) {
            const c = getComputedStyle(n).backgroundColor;
            if (c && c !== "transparent" && !/,\s*0\)$/.test(c)) {
                bg = c;
                break;
            }
        }
        el.style.setProperty("--sf-bg", bg || "#ffffff");
        el.style.setProperty("--sf-fg", getComputedStyle(el).color || "#222222");
    }

    // ---------------------------------------------------------------- quick date buttons
    // Dates are the browser's local calendar days (never converted through UTC). Every range is cut to the days the
    // forecast really covers: from the day after the last actuals to the end of the forecast.
    fmtDate(d) {
        const p = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }
    get presets() {
        const o = this.state.options;
        if (!o.default_date || !o.horizon_end) {
            return [];
        }
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const add = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
        const weekStart = add(today, -today.getDay()); // the week runs Sunday to Saturday
        const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
        const monthEnd = (d) => new Date(d.getFullYear(), d.getMonth() + 1, 0);
        const defs = [
            ["today", "Today", today, today],
            ["tomorrow", "Tomorrow", add(today, 1), add(today, 1)],
            ["next7", "Next 7 days", today, add(today, 6)],
            ["week", "This week", weekStart, add(weekStart, 6)],
            ["nextweek", "Next week", add(weekStart, 7), add(weekStart, 13)],
            ["month", "This month", monthStart, monthEnd(monthStart)],
        ];
        return defs.map(([key, label, a, b]) => {
            let from = this.fmtDate(a);
            let to = this.fmtDate(b);
            if (from < o.default_date) {
                from = o.default_date;
            }
            if (to > o.horizon_end) {
                to = o.horizon_end;
            }
            return { key, label, from, to, disabled: from > to };
        });
    }
    isActivePreset(p) {
        return !p.disabled && this.state.filters.date_from === p.from && this.state.filters.date_to === p.to;
    }
    async pickPreset(p) {
        if (p.disabled) {
            return;
        }
        this.state.filters.date_from = p.from;
        this.state.filters.date_to = p.to;
        await this.load();
    }

    // payload sent to the server (all branches ticked = no restriction)
    get payload() {
        const f = this.state.filters;
        const all = this.state.options.branches.length;
        return {
            date_from: f.date_from,
            date_to: f.date_to || f.date_from,
            metric: f.metric,
            categ_id: f.categ_id === "" ? null : f.categ_id,
            branch_ids: f.branch_ids.length === all ? [] : f.branch_ids,
        };
    }

    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call("alkhuloodh.sf.report", "get_report", [this.payload]);
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
        this.applyTheme();
    }

    async setFilter(name, value) {
        this.state.filters[name] = name === "categ_id" && value !== "" ? parseInt(value, 10) : value;
        if (name === "date_from" && (!this.state.filters.date_to || this.state.filters.date_to < value)) {
            this.state.filters.date_to = value;
        }
        await this.load();
    }

    async toggleBranch(id) {
        const cur = this.state.filters.branch_ids;
        this.state.filters.branch_ids = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
        if (!this.state.filters.branch_ids.length) {
            this.state.filters.branch_ids = [id];
        }
        await this.load();
    }

    isChecked(id) {
        return this.state.filters.branch_ids.includes(id);
    }

    fmt(v) {
        if (v === null || v === undefined) {
            return "–";
        }
        const nd = this.state.data ? this.state.data.nd : 0;
        return Number(v).toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd });
    }

    async download(fmt) {
        if (!this.state.data) {
            return;
        }
        this.state.busy = fmt;
        try {
            const url = `/alkhuloodh_sf/report/${fmt}?f=${encodeURIComponent(JSON.stringify(this.payload))}`;
            const res = await fetch(url, { credentials: "same-origin" });
            if (!res.ok) {
                this.state.error = (await res.text()) || "Download failed.";
            } else {
                const blob = await res.blob();
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `forecast_report_${this.state.filters.metric}_${this.state.data.date_from}_${this.state.data.date_to}.${fmt}`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(() => URL.revokeObjectURL(a.href), 1000);
            }
        } catch (e) {
            this.state.error = e.message || String(e);
        }
        this.state.busy = "";
    }

    print() {
        window.print();
    }
}

registry.category("actions").add("alkhuloodh_sf_report", SfReport);
