/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ProductDetailDialog } from "./product_detail_dialog";

export class ShopDashboard extends Component {
    static template = "khulood_shop_dashboard.ShopDashboard";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");

        this.state = useState({
            warehouses: [],
            warehouseId: false,
            warehouseName: "",
            stockRows: [],
            performanceRows: [],
            generatedAt: "",
            loading: true,
            stockSearch: "",
        });

        onWillStart(async () => {
            await this.loadBootstrap();
            await this.loadDashboard();
        });
    }

    async loadBootstrap() {
        const data = await this.orm.call("shop.dashboard", "get_bootstrap_data", []);
        this.state.warehouses = data.warehouses;
        this.state.warehouseId = data.default_warehouse_id;
    }

    async loadDashboard() {
        if (!this.state.warehouseId) {
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        try {
            const data = await this.orm.call("shop.dashboard", "get_dashboard_data", [
                this.state.warehouseId,
            ]);
            this.state.warehouseName = data.warehouse_name;
            this.state.stockRows = data.stock_rows;
            this.state.performanceRows = data.performance_rows;
            this.state.generatedAt = data.generated_at;
        } catch (error) {
            this.notification.add("Could not load the dashboard. Please try again.", {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    onWarehouseChange(ev) {
        this.state.warehouseId = parseInt(ev.target.value, 10);
        this.loadDashboard();
    }

    onStockSearchInput(ev) {
        this.state.stockSearch = ev.target.value;
    }

    refresh() {
        this.loadDashboard();
    }

    openProductDetail(productId) {
        this.dialog.add(ProductDetailDialog, {
            productId,
            warehouseId: this.state.warehouseId,
        });
    }

    get filteredStockRows() {
        const term = this.state.stockSearch.trim().toLowerCase();
        if (!term) {
            return this.state.stockRows;
        }
        return this.state.stockRows.filter((r) => r.name.toLowerCase().includes(term));
    }

    formatQty(value) {
        return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    formatMoney(value) {
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }
}

registry.category("actions").add("khulood_shop_dashboard", ShopDashboard);
