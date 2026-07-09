import re

from odoo import api, models
from odoo.fields import Domain
from odoo.tools import SQL


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        """Restore Odoo 16-style behavior: if core name_search finds nothing,
        fall back to a PARTIAL (ilike) match on barcode. Core Odoo 19 logic
        only checks for an EXACT barcode match, so typing a few digits of a
        barcode (e.g. '0039' for '8693029030039') never surfaces suggestions.
        """
        positive_operators = ['=', 'ilike', '=ilike', 'like', '=like']
        results = super().name_search(name=name, domain=domain, operator=operator, limit=limit)
        if results or not name or operator not in positive_operators:
            return results

        base_domain = list(domain) if domain else []
        products = self.search_fetch(
            base_domain + [('barcode', 'ilike', name)],
            ['display_name'],
            limit=limit,
        )
        return [(product.id, product.display_name) for product in products.sudo()]

    @api.model
    def _search_display_name(self, operator, value):
        is_positive = operator not in Domain.NEGATIVE_OPERATORS
        template_domains = [[('name', operator, value)]]
        product_domains = [[('default_code', operator, value)]]
        if operator == 'in':
            product_domains.append([('barcode', 'in', value)])
            for v in value:
                if isinstance(v, str) and (m := re.search(r'(\[(.*?)\])', v)):
                    product_domains.append([('default_code', '=', m.group(2))])
        elif operator.endswith('like') and is_positive:
            # FIX: partial match on barcode instead of exact match.
            # Original core code did: product_domains.append([('barcode', 'in', [value])])
            # which only matched a barcode equal to the typed fragment.
            product_domains.append([('barcode', operator, value)])

        supplier_domain = []
        if partner_id := self.env.context.get('partner_id'):
            supplier_domain = [
                ('partner_id', '=', partner_id),
                '|',
                ('product_code', operator, value),
                ('product_name', operator, value),
            ]
        # AND clauses properly hit indexes so no need for custom sql in this case.
        if operator in Domain.NEGATIVE_OPERATORS:
            domains = template_domains + product_domains
            if supplier_domain:
                domains.append([('product_tmpl_id.seller_ids', 'any', supplier_domain)])
            return Domain.AND(domains)
        # Disable active_test to simplify subqueries
        self_no_active_test = self.with_context(active_test=False)
        queries = [
            self_no_active_test._search([
                ('product_tmpl_id', 'in', self_no_active_test.env['product.template']._search(Domain.OR(template_domains)))
            ]),
            self_no_active_test._search(Domain.OR(product_domains)),
        ]
        if supplier_domain:
            queries.append(
                self_no_active_test._search([
                    (
                        'product_tmpl_id',
                        'in',
                        self_no_active_test.env['product.supplierinfo']._search(supplier_domain).subselect('product_tmpl_id'),
                    )
                ])
            )
        query = SQL(
            """(%s)""",
            SQL("UNION ALL").join(
                [SQL("(%s)", query.select()) for query in queries]
            )
        )
        return [('id', 'in', query)]
