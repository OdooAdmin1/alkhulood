import { patch } from "@web/core/utils/patch";
import { ProductCard } from "@point_of_sale/app/components/product_card/product_card";

/**
 * Adds a tax-aware, currency-formatted price getter to the POS ProductCard
 * component so a price badge can be rendered on every product tile
 * (image-based or image-less), matching the Odoo 16-style tile layout.
 */
patch(ProductCard.prototype, {
    get alkhuloodPrice() {
        const product = this.props.product;
        if (!product) {
            return "";
        }

        let price = 0;

        try {
            if (typeof product.getPrice === "function" && this.env.services?.pos) {
                const pos = this.env.services.pos;
                const order = pos.getOrder ? pos.getOrder() : null;
                const pricelist = order ? order.pricelist : pos.config?.pricelist_id;
                price = product.getPrice(pricelist, 1);
            } else if (typeof product.displayPriceUnit !== "undefined") {
                price = product.displayPriceUnit;
            } else {
                price = product.lst_price ?? product.list_price ?? 0;
            }
        } catch (error) {
            price = product.lst_price ?? product.list_price ?? 0;
        }

        try {
            return this.env.utils.formatCurrency(price);
        } catch (error) {
            return price;
        }
    },
});
