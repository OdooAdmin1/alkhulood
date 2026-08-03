/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const BUSINESS_CLOSE_TIME = "23:55:00"; // fixed - the client's actual closing hour, not user-editable

function pad(n) {
    return String(n).padStart(2, "0");
}

function toDateInputValue(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Default "as of" date = the most recently completed business day (today,
 * unless it's not 23:55 yet locally, in which case yesterday). */
function defaultAsOfDate() {
    const now = new Date();
    const closeToday = new Date(now);
    closeToday.setHours(23, 55, 0, 0);
    const target = now >= closeToday ? now : new Date(now.getTime() - 24 * 3600 * 1000);
    return toDateInputValue(target);
}

const CATEGORY_PALETTE = ["#1F3864", "#BF9000", "#6E9887", "#A24936", "#5C6784", "#C08497", "#3B7A9E", "#8A6D3B"];
const STORE_PALETTE = ["#1F3864", "#BF9000", "#6E9887", "#A24936", "#5C6784", "#C08497", "#3B7A9E", "#8A6D3B", "#4C6E5D"];

export class EodStockDashboard extends Component {
    static template = "al_khulood_eod_stock_dashboard.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.chartCanvas = useRef("trendChart");
        this.chart = null;

        this.state = useState({
            loading: true,
            data: { stores: [], products: [], live: [], closing: [], as_of: null, server_now: null },
            categories: [],
            tab: "closing", // "live" | "closing" | "trend"
            selectedStoreIds: [],
            selectedCategoryIds: [],
            search: "",
            asOfDate: defaultAsOfDate(),
            selectedProductId: null,
            trend: { rows: [] },
            trendDays: 7,
            trendLoading: false,
            trendLoaded: false,
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await Promise.all([this.loadData(), this.loadCategories()]);
        });

        onMounted(() => this.renderChartIfNeeded());
        onPatched(() => this.renderChartIfNeeded());
    }

    get asOfDatetimeString() {
        return `${this.state.asOfDate} ${BUSINESS_CLOSE_TIME}`;
    }

    async loadCategories() {
        this.state.categories = await this.orm.call(
            "al.khulood.eod.stock.report",
            "get_categories",
            [],
            {}
        );
    }

    async loadData() {
        this.state.loading = true;
        const data = await this.orm.call(
            "al.khulood.eod.stock.report",
            "get_dashboard_data",
            [],
            {
                warehouse_ids: this.state.selectedStoreIds.length ? this.state.selectedStoreIds : false,
                product_ids: false,
                category_ids: this.state.selectedCategoryIds.length ? this.state.selectedCategoryIds : false,
                as_of: this.asOfDatetimeString,
            }
        );
        this.state.data = data;
        this.state.loading = false;
    }

    async applyAsOf() {
        await this.loadData();
        if (this.state.trendLoaded) this.loadTrend();
    }

    setQuickAsOf(which) {
        const now = new Date();
        const base = which === "today" ? now : new Date(now.getTime() - 24 * 3600 * 1000);
        this.state.asOfDate = toDateInputValue(base);
        this.applyAsOf();
    }

    async resetFilters() {
        this.state.selectedStoreIds = [];
        this.state.selectedCategoryIds = [];
        this.state.search = "";
        this.state.asOfDate = defaultAsOfDate();
        await this.loadData();
        if (this.state.trendLoaded) this.loadTrend();
    }

    setTab(tab) {
        this.state.tab = tab;
        if (tab === "trend" && !this.state.trendLoaded) {
            this.loadTrend();
        }
    }

    async loadTrend() {
        this.state.trendLoading = true;
        try {
            const trend = await this.orm.call(
                "al.khulood.eod.stock.report",
                "get_trend_data",
                [],
                {
                    warehouse_ids: this.state.selectedStoreIds.length ? this.state.selectedStoreIds : false,
                    product_ids: false,
                    category_ids: this.state.selectedCategoryIds.length ? this.state.selectedCategoryIds : false,
                    days: this.state.trendDays,
                    closing_time: BUSINESS_CLOSE_TIME,
                }
            );
            this.state.trend = trend;
            this.state.trendLoaded = true;
            if (!this.state.selectedProductId && this.state.data.products.length) {
                this.state.selectedProductId = this.state.data.products[0].id;
            }
        } catch (err) {
            this.notification.add(
                (err && err.data && err.data.message) || "Could not compute the trend.",
                { type: "danger" }
            );
        } finally {
            this.state.trendLoading = false;
        }
    }

    toggleStore(storeId) {
        const idx = this.state.selectedStoreIds.indexOf(storeId);
        if (idx >= 0) {
            this.state.selectedStoreIds.splice(idx, 1);
        } else {
            this.state.selectedStoreIds.push(storeId);
        }
        this.loadData();
        if (this.state.trendLoaded) this.loadTrend();
    }

    toggleCategory(categoryId) {
        const idx = this.state.selectedCategoryIds.indexOf(categoryId);
        if (idx >= 0) {
            this.state.selectedCategoryIds.splice(idx, 1);
        } else {
            this.state.selectedCategoryIds.push(categoryId);
        }
        this.loadData();
        if (this.state.trendLoaded) this.loadTrend();
    }

    categoryColor(categoryId) {
        const idx = this.state.categories.findIndex((c) => c.id === categoryId);
        return CATEGORY_PALETTE[idx % CATEGORY_PALETTE.length];
    }

    selectProduct(productId) {
        this.state.selectedProductId = productId;
    }

    get filteredProducts() {
        const term = this.state.search.trim().toLowerCase();
        if (!term) return this.state.data.products;
        return this.state.data.products.filter((p) => p.name.toLowerCase().includes(term));
    }

    get visibleStores() {
        if (!this.state.selectedStoreIds.length) return this.state.data.stores;
        return this.state.data.stores.filter((s) => this.state.selectedStoreIds.includes(s.id));
    }

    get activeRows() {
        return this.state.tab === "live" ? this.state.data.live : this.state.data.closing;
    }

    qtyFor(productId, storeId) {
        const row = this.activeRows.find(
            (r) => r.product_id === productId && r.warehouse_id === storeId
        );
        return row ? row.qty : 0;
    }

    /** Colorful tiering: dark red (zero) / red (at-or-below threshold) /
     * amber (within 1.5x threshold, "watch") / green (healthy). Falls back
     * to a neutral green tint when no threshold is configured. */
    cellClass(productId, storeId) {
        const row = this.activeRows.find(
            (r) => r.product_id === productId && r.warehouse_id === storeId
        );
        if (!row) return "";
        if (row.qty <= 0) return "eod-cell-zero";
        if (row.threshold) {
            if (row.qty <= row.threshold) return "eod-cell-low";
            if (row.qty <= row.threshold * 1.5) return "eod-cell-watch";
        }
        return "eod-cell-healthy";
    }

    get lowStockCount() {
        return this.activeRows.filter((r) => r.threshold && r.qty <= r.threshold).length;
    }

    get activeFilterCount() {
        return this.state.selectedStoreIds.length + this.state.selectedCategoryIds.length + (this.state.search ? 1 : 0);
    }

    renderChartIfNeeded() {
        if (this.state.tab !== "trend" || !this.chartCanvas.el) return;
        const productId = this.state.selectedProductId;
        if (!productId) return;

        const stores = this.visibleStores;
        const rows = this.state.trend.rows.filter((r) => r.product_id === productId);
        const dates = [...new Set(rows.map((r) => r.date))].sort();

        const datasets = stores.map((store, i) => ({
            label: store.name,
            data: dates.map((d) => {
                const point = rows.find((r) => r.date === d && r.warehouse_id === store.id);
                return point ? point.qty : null;
            }),
            borderColor: STORE_PALETTE[i % STORE_PALETTE.length],
            backgroundColor: STORE_PALETTE[i % STORE_PALETTE.length],
            tension: 0.3,
            spanGaps: true,
        }));

        if (this.chart) {
            this.chart.destroy();
        }
        this.chart = new Chart(this.chartCanvas.el, {
            type: "line",
            data: { labels: dates, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom" } },
                scales: { y: { beginAtZero: true } },
            },
        });
    }
}

registry.category("actions").add("al_khulood_eod_dashboard", EodStockDashboard);
