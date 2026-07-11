/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";

export class ProductDetailDialog extends Component {
    static template = "khulood_shop_dashboard.ProductDetailDialog";
    static components = { Dialog };
    static props = {
        productId: Number,
        warehouseId: Number,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            loading: true,
            productName: "",
            lines: [],
            totalQty: 0,
            totalRevenue: 0,
        });

        onWillStart(async () => {
            const data = await this.orm.call("shop.dashboard", "get_product_detail", [
                this.props.productId,
                this.props.warehouseId,
            ]);
            this.state.productName = data.product_name;
            this.state.lines = data.lines;
            this.state.totalQty = data.total_qty;
            this.state.totalRevenue = data.total_revenue;
            this.state.loading = false;
        });
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
