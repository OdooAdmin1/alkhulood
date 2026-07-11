# Khulood Shop Dashboard

## Install
1. Copy `khulood_shop_dashboard` into your Odoo addons path.
2. Apps > Update Apps List > install "Khulood Shop Dashboard".

This install does **not** modify any existing product, user, or warehouse
form - it only adds one new Product Tag ("Native") and a new menu with the
dashboard itself. Nothing else in your Odoo should visibly change.

## One-time setup

### 1. Tell it which warehouses are real shops
Go to **Settings > Technical > Parameters > System Parameters** > Create:
- Key: `khulood_shop_dashboard.shop_warehouse_ids`
- Value: comma-separated warehouse IDs, e.g. based on what you showed me:
  `1,3,4,5,6,7,8,25,26`
  (Tubli, Jid Ali, Riffa, Budaiya, Muharraq, Galali, Hamad Town, Manama, Al Baraha -
  leaving out MAIN STORE / Basalar / SHOP RETURN / the production kitchens,
  since those aren't shops staff should be switching to.)

If you skip this step, the dashboard falls back to showing every warehouse
in the switcher, including the internal ones - so it's worth setting.

### 2. Tag the native products
Run this in the Odoo shell (the same one you've been using) - it tags
exactly the 80 products from your file, without touching any other tags
they already have:

```python
tag = env.ref('khulood_shop_dashboard.product_tag_native')
xmlids = [
    '__export__.product_template_5289_3aba0744',
    # ... (I'll give you the full list of 80 as a ready-to-paste script -
    # see setup_tag_native_products.py in this folder)
]
templates = env['ir.model.data'].search([
    ('module', '=', '__export__'),
    ('name', 'in', [x.split('.')[1] for x in xmlids]),
]).mapped('res_id')
products = env['product.template'].browse(templates)
products.write({'product_tag_ids': [(4, tag.id)]})
print(len(products), 'products tagged as Native')
```

A ready-to-paste version with the full list already filled in is at
`setup_tag_native_products.py` next to this README - just open it, copy
everything, and paste into the shell.

### 3. Make sure each shop user's "Default Warehouse" is correct
This is the **existing** `property_warehouse_id` field on the user record
(Settings > Users > [user] > Preferences tab). From what you showed me,
a few are blank or pointing at MAIN STORE by mistake (Admin, Al Baraha,
HR, Jid Ali, Haseeb, Manama, Rashid) - worth fixing those so each shop's
staff land on their own branch automatically.

## Using it
Menu: **Shop Dashboard**

- Opens to the user's own shop (from their Default Warehouse).
- Dropdown at the top to switch to any other shop (useful for checking
  if another branch has stock to pull from).
- Section 1: native products and current stock on hand at the selected shop.
- Section 2 (scroll down): previous day's sales performance for those
  products, best sellers first.
- Click any product row in Section 2 to see that product's individual
  sales from yesterday (time, qty, amount per order).

## Notes
- "Previous day" = the full previous calendar day in the viewing user's
  timezone, using POS orders in state `paid`, `done`, or `invoiced`.
- A shop's sales are matched via its POS config(s) -> operation type ->
  warehouse, so branches with two tills (Tubli, Riffa, Budaiya, Muharraq,
  Hamad Town) are combined correctly under one warehouse.
- Stock on hand = `stock.quant` at internal locations under the selected
  warehouse.
