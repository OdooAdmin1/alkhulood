/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const MODEL = "al.production.forecast";
const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
];
// Bahrain works Sun-Thu, so the week is shown starting on Sunday.
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function todayIso() {
    const d = new Date();
    return [
        d.getFullYear(),
        String(d.getMonth() + 1).padStart(2, "0"),
        String(d.getDate()).padStart(2, "0"),
    ].join("-");
}
function parseIso(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return { y, m, d };
}
function addDays(iso, n) {
    const { y, m, d } = parseIso(iso);
    const dt = new Date(Date.UTC(y, m - 1, d + n));
    return dt.toISOString().slice(0, 10);
}

function shortDate(iso) {
    const { m, d } = parseIso(iso);
    return `${d} ${MONTHS[m - 1].slice(0, 3)}`;
}

// Chart geometry, in viewBox units. The SVG scales to the pane width, so these
// are only ever ratios.
const CH_W = 1000;
const CH_H = 170;
const CH_PAD = { l: 34, r: 8, t: 12, b: 18 };

/**
 * Catmull-Rom through every point, converted to cubic beziers. Daily sales are
 * spiky, so a straight polyline reads as noise; a gentle spline keeps the shape
 * without inventing peaks the data does not have.
 */
function splinePath(pts) {
    if (pts.length < 2) {
        return pts.length ? `M ${pts[0].x} ${pts[0].y}` : "";
    }
    let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
        const p0 = pts[i - 1] || pts[i];
        const p1 = pts[i];
        const p2 = pts[i + 1];
        const p3 = pts[i + 2] || p2;
        const c1x = p1.x + (p2.x - p0.x) / 6;
        const c1y = p1.y + (p2.y - p0.y) / 6;
        const c2x = p2.x - (p3.x - p1.x) / 6;
        const c2y = p2.y - (p3.y - p1.y) / 6;
        d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}` +
             ` ${c2x.toFixed(1)} ${c2y.toFixed(1)}` +
             ` ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return d;
}

export class ProductionForecast extends Component {
    static template = "al_khulood_production_forecast.Dashboard";
    // The web client always hands a client action its own props (action,
    // actionId, updateActionState...). Declaring {} made OWL reject them,
    // which crashed the dashboard whenever debug mode turned validation on.
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            date: todayIso(),
            loading: true,
            calLoading: false,
            data: null,
            cal: null,
            calYear: 0,
            calMonth: 0,
            hoverIdx: null,    // index into history, for the chart tooltip
            productId: null,   // null = chart every product together
        });
        onWillStart(async () => {
            const { y, m } = parseIso(this.state.date);
            await Promise.all([this.loadMonth(y, m), this.load()]);
        });
    }

    // ---------------------------------------------------------- loading
    async load() {
        this.state.loading = true;
        this.state.hoverIdx = null;
        try {
            this.state.data = await this.orm.call(MODEL, "get_day_forecast", [
                this.state.date,
            ]);
        } finally {
            this.state.loading = false;
        }
    }

    async loadMonth(year, month) {
        this.state.calLoading = true;
        try {
            this.state.cal = await this.orm.call(MODEL, "get_month_calendar", [
                year,
                month,
            ]);
            this.state.calYear = year;
            this.state.calMonth = month;
        } finally {
            this.state.calLoading = false;
        }
    }

    // ----------------------------------------------------------- actions
    async pick(iso) {
        if (iso === this.state.date) {
            return;
        }
        this.state.date = iso;
        const { y, m } = parseIso(iso);
        if (y !== this.state.calYear || m !== this.state.calMonth) {
            await this.loadMonth(y, m);
        }
        await this.load();
    }

    async step(n) {
        await this.pick(addDays(this.state.date, n));
    }

    async goToday() {
        await this.pick(todayIso());
    }

    async shiftMonth(n) {
        let y = this.state.calYear;
        let m = this.state.calMonth + n;
        if (m < 1) {
            m = 12;
            y -= 1;
        } else if (m > 12) {
            m = 1;
            y += 1;
        }
        await this.loadMonth(y, m);
    }

    /** Click a row to chart that product; click it again to go back to all. */
    selectProduct(productId) {
        this.state.productId =
            this.state.productId === productId ? null : productId;
        this.state.hoverIdx = null;
    }

    // ------------------------------------------------------------ view
    get monthLabel() {
        return `${MONTHS[this.state.calMonth - 1]} ${this.state.calYear}`;
    }

    /** The Hijri month(s) this Gregorian month covers. */
    get hijriMonthLabel() {
        return (this.state.cal && this.state.cal.hijri_label) || "";
    }

    get weekdayHeads() {
        return DOW;
    }

    /** Calendar cells, padded so the 1st lands under the right weekday. */
    get cells() {
        const cal = this.state.cal;
        if (!cal) {
            return [];
        }
        // python weekday(): Mon=0..Sun=6  ->  column with Sunday first
        const lead = (cal.first_weekday + 1) % 7;
        const out = [];
        for (let i = 0; i < lead; i++) {
            out.push(null);
        }
        for (const d of cal.days) {
            out.push(d);
        }
        return out;
    }

    get rows() {
        return (this.state.data && this.state.data.products) || [];
    }

    /** The product the chart is showing, or null for the all-products total. */
    get chartProduct() {
        if (!this.state.productId) {
            return null;
        }
        return this.rows.find((r) => r.product_id === this.state.productId) || null;
    }

    get chartTitle() {
        const p = this.chartProduct;
        return p ? `${p.name} - units sold` : "Units sold";
    }

    get chartUnit() {
        const p = this.chartProduct;
        return p ? p.uom : "units";
    }

    /**
     * Line-chart geometry for the 45 days running up to the selected date,
     * for one product or for every product added together.
     */
    get chart() {
        const data = this.state.data;
        const axis = (data && data.history) || [];
        if (axis.length < 2) {
            return null;
        }
        const p = this.chartProduct;
        // Each row's series is emitted on this same axis, so the two can be
        // swapped without the dates moving underneath.
        const h =
            p && p.history && p.history.length === axis.length
                ? axis.map((pt, i) => ({ ...pt, qty: p.history[i] }))
                : axis;
        const innerW = CH_W - CH_PAD.l - CH_PAD.r;
        const innerH = CH_H - CH_PAD.t - CH_PAD.b;
        const max = Math.max(1, ...h.map((p) => p.qty));
        const baseY = CH_PAD.t + innerH;
        const xAt = (i) => CH_PAD.l + (i / (h.length - 1)) * innerW;
        const yAt = (v) => baseY - (v / max) * innerH;

        const pts = h.map((p, i) => ({ ...p, i, x: xAt(i), y: yAt(p.qty) }));
        const line = splinePath(pts);
        const area =
            `${line} L ${pts[pts.length - 1].x.toFixed(1)} ${baseY.toFixed(1)}` +
            ` L ${pts[0].x.toFixed(1)} ${baseY.toFixed(1)} Z`;
        const ticks = [0, 0.5, 1].map((f) => ({ v: max * f, y: yAt(max * f) }));

        // Three dates is enough to orient the eye; the tooltip gives the rest.
        const last = h.length - 1;
        const xlabels = [0, Math.round(last / 2), last]
            .filter((v, i, arr) => arr.indexOf(v) === i)
            .map((i) => ({
                x: pts[i].x,
                label: shortDate(pts[i].date),
                anchor: i === 0 ? "start" : i === last ? "end" : "middle",
            }));

        return {
            w: CH_W, h: CH_H, padT: CH_PAD.t, baseY,
            pts, line, area, ticks, max, xlabels,
            events: pts.filter((p) => p.is_event),
            selected: pts.find((p) => p.date === this.state.date) || null,
        };
    }

    get hoverPoint() {
        const c = this.chart;
        if (!c || this.state.hoverIdx === null) {
            return null;
        }
        return c.pts[this.state.hoverIdx] || null;
    }

    /** Nearest data point to the cursor, in viewBox units. */
    onChartMove(ev) {
        const c = this.chart;
        if (!c) {
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        if (!rect.width) {
            return;
        }
        const xUser = ((ev.clientX - rect.left) / rect.width) * c.w;
        let best = 0;
        let bestDist = Infinity;
        for (const p of c.pts) {
            const dist = Math.abs(p.x - xUser);
            if (dist < bestDist) {
                bestDist = dist;
                best = p.i;
            }
        }
        this.state.hoverIdx = best;
    }

    cellTitle(cell) {
        const bits = [`${cell.date}  ·  ${cell.hijri.label}`];
        for (const ev of cell.events) {
            bits.push(ev.label);
        }
        return bits.join("\n");
    }

    cellClass(cell) {
        if (!cell) {
            return "alf-cell alf-cell-empty";
        }
        const parts = ["alf-cell"];
        if (cell.date === this.state.date) {
            parts.push("alf-cell-sel");
        }
        if (cell.date === (this.state.cal && this.state.cal.today)) {
            parts.push("alf-cell-today");
        }
        if (cell.is_weekend) {
            parts.push("alf-cell-weekend");
        }
        if (cell.events.length) {
            parts.push(
                cell.events.some((e) => e.calendar === "hijri")
                    ? "alf-cell-hijri"
                    : "alf-cell-greg"
            );
        }
        return parts.join(" ");
    }

    fmt(n, digits = 1) {
        if (n === null || n === undefined) {
            return "-";
        }
        return Number(n).toLocaleString(undefined, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        });
    }

    fmtMoney(n) {
        if (n === null || n === undefined) {
            return "-";
        }
        return Number(n).toLocaleString(undefined, {
            minimumFractionDigits: 3,
            maximumFractionDigits: 3,
        });
    }

    /** Prediction vs what really happened, when the day is in the past. */
    accuracy(row) {
        if (row.actual_qty === null || row.actual_qty === undefined) {
            return null;
        }
        const diff = row.predicted_qty - row.actual_qty;
        const pct = row.actual_qty ? (diff / row.actual_qty) * 100 : null;
        return { diff, pct };
    }
}

registry.category("actions").add("al_production_forecast", ProductionForecast);
