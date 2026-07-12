/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { serializeDateTime } from "@web/core/l10n/dates";

const { Component, useState, onWillStart, useRef } = owl;

class AlKhuloodStockDashboard extends Component {
    static template = "al_khulood_stock_dashboard.Dashboard";
    static components = { DateTimeInput };

    setup() {
        this.orm = useService("orm");
        this.searchInput = useRef("searchInput");

        this.state = useState({
            branches: [],
            selectedBranchIds: [],
            showBranchDropdown: false,

            categories: [],
            selectedCategIds: [],
            showCategDropdown: false,
            groupByCategory: false,

            negativeOnly: false,

            asOfValue: false,        // luxon DateTime or false, bound to DateTimeInput
            asOfUtc: null,           // serialized UTC string sent to the server
            appliedAsOfDisplay: "",  // human-readable label for the banner

            products: [],
            totalCount: 0,
            search: "",
            loading: true,
            loadingMore: false,
            offset: 0,
            limit: 60,
        });

        this._searchTimeout = null;

        onWillStart(async () => {
            const [defaultBranchId, categories] = await Promise.all([
                this.orm.call("al.khulood.stock.dashboard", "get_default_branch", []),
                this.orm.call("al.khulood.stock.dashboard", "get_categories", []),
            ]);
            this.state.categories = categories;
            this.state.selectedCategIds = categories.map((c) => c.id);

            await this.loadData();

            if (defaultBranchId && this.state.branches.some((b) => b.id === defaultBranchId)) {
                this.state.selectedBranchIds = [defaultBranchId];
            } else {
                this.state.selectedBranchIds = this.state.branches.map((b) => b.id);
            }
        });
    }

    _categFilterParam() {
        // Empty array (or all selected) = no category filter server-side
        if (!this.state.categories.length) {
            return [];
        }
        if (this.state.selectedCategIds.length === this.state.categories.length) {
            return [];
        }
        return this.state.selectedCategIds;
    }

    async loadData(append = false) {
        if (append) {
            this.state.loadingMore = true;
        } else {
            this.state.loading = true;
            this.state.offset = 0;
        }

        const data = await this.orm.call(
            "al.khulood.stock.dashboard",
            "get_dashboard_data",
            [],
            {
                search: this.state.search,
                offset: append ? this.state.offset : 0,
                limit: this.state.limit,
                categ_ids: this._categFilterParam(),
                group_by_category: this.state.groupByCategory,
                negative_only: this.state.negativeOnly,
                branch_ids: this.state.selectedBranchIds,
                as_of_datetime: this.state.asOfUtc || false,
            }
        );

        this.state.branches = data.branches;
        this.state.totalCount = data.total_count;

        if (append) {
            this.state.products = this.state.products.concat(data.products);
        } else {
            this.state.products = data.products;
        }

        this.state.loading = false;
        this.state.loadingMore = false;
    }

    onSearchInput(ev) {
        const value = ev.target.value;
        this.state.search = value;
        clearTimeout(this._searchTimeout);
        this._searchTimeout = setTimeout(() => {
            this.loadData(false);
        }, 300);
    }

    async loadMore() {
        this.state.offset += this.state.limit;
        await this.loadData(true);
    }

    get hasMore() {
        return this.state.products.length < this.state.totalCount;
    }

    getImageUrl(product) {
        if (!product.has_image) {
            return "/al_khulood_stock_dashboard/static/src/img/placeholder.svg";
        }
        return `/web/image/product.template/${product.id}/image_128`;
    }

    qtyClass(qty) {
        if (qty < 0) {
            return "aks-qty-negative";
        }
        if (qty === 0) {
            return "aks-qty-zero";
        }
        if (qty < 10) {
            return "aks-qty-low";
        }
        return "aks-qty-ok";
    }

    // ---------- Branch (location) selector ----------

    get visibleBranches() {
        return this.state.branches.filter((b) => this.state.selectedBranchIds.includes(b.id));
    }

    toggleBranchDropdown() {
        this.state.showBranchDropdown = !this.state.showBranchDropdown;
        this.state.showCategDropdown = false;
    }

    toggleBranch(branchId) {
        const idx = this.state.selectedBranchIds.indexOf(branchId);
        if (idx >= 0) {
            this.state.selectedBranchIds.splice(idx, 1);
        } else {
            this.state.selectedBranchIds.push(branchId);
        }
    }

    isBranchSelected(branchId) {
        return this.state.selectedBranchIds.includes(branchId);
    }

    selectAllBranches() {
        this.state.selectedBranchIds = this.state.branches.map((b) => b.id);
    }

    clearAllBranches() {
        this.state.selectedBranchIds = [];
    }

    get branchSelectorLabel() {
        const total = this.state.branches.length;
        const selected = this.state.selectedBranchIds.length;
        if (selected === total) {
            return "All locations";
        }
        if (selected === 0) {
            return "No locations";
        }
        if (selected === 1) {
            const b = this.state.branches.find((br) => br.id === this.state.selectedBranchIds[0]);
            return b ? b.name : "1 location";
        }
        return `${selected} locations`;
    }

    // ---------- Category selector ----------

    toggleCategDropdown() {
        this.state.showCategDropdown = !this.state.showCategDropdown;
        this.state.showBranchDropdown = false;
    }

    toggleCateg(categId) {
        const idx = this.state.selectedCategIds.indexOf(categId);
        if (idx >= 0) {
            this.state.selectedCategIds.splice(idx, 1);
        } else {
            this.state.selectedCategIds.push(categId);
        }
        this.loadData(false);
    }

    isCategSelected(categId) {
        return this.state.selectedCategIds.includes(categId);
    }

    selectAllCategories() {
        this.state.selectedCategIds = this.state.categories.map((c) => c.id);
        this.loadData(false);
    }

    clearAllCategories() {
        this.state.selectedCategIds = [];
        this.loadData(false);
    }

    get categSelectorLabel() {
        const total = this.state.categories.length;
        const selected = this.state.selectedCategIds.length;
        if (selected === total) {
            return "All categories";
        }
        if (selected === 0) {
            return "No categories";
        }
        if (selected === 1) {
            const c = this.state.categories.find((ct) => ct.id === this.state.selectedCategIds[0]);
            return c ? c.name : "1 category";
        }
        return `${selected} categories`;
    }

    async toggleGroupByCategory() {
        this.state.groupByCategory = !this.state.groupByCategory;
        await this.loadData(false);
    }

    // ---------- Negative stock filter ----------

    async toggleNegativeOnly() {
        this.state.negativeOnly = !this.state.negativeOnly;
        await this.loadData(false);
    }

    // ---------- As-of date/time (historical stock) ----------
    // Uses Odoo's native DateTimeInput widget (same calendar + time-list
    // picker as any Odoo datetime field). serializeDateTime() converts the
    // picked value from the logged-in user's Odoo timezone to the UTC
    // string the server expects - no manual offset math needed.

    async onAsOfChange(value) {
        this.state.asOfValue = value;
        if (value) {
            this.state.asOfUtc = serializeDateTime(value);
            this.state.appliedAsOfDisplay = value.toFormat("dd/MM/yyyy HH:mm");
        } else {
            this.state.asOfUtc = null;
            this.state.appliedAsOfDisplay = "";
        }
        await this.loadData(false);
    }

    async resetToLive() {
        this.state.asOfValue = false;
        this.state.asOfUtc = null;
        this.state.appliedAsOfDisplay = "";
        await this.loadData(false);
    }

    get isHistorical() {
        return !!this.state.asOfUtc;
    }

    // ---------- Grouped rendering ----------

    get groupedProducts() {
        const groups = [];
        const indexByCateg = {};
        for (const product of this.state.products) {
            let group = indexByCateg[product.categ_id];
            if (!group) {
                group = { categId: product.categ_id, categName: product.categ_name, products: [] };
                indexByCateg[product.categ_id] = group;
                groups.push(group);
            }
            group.products.push(product);
        }
        return groups;
    }
}

registry.category("actions").add("al_khulood_stock_dashboard", AlKhuloodStockDashboard);

export default AlKhuloodStockDashboard;
