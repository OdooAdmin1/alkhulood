#  -*- coding: utf-8 -*-
#################################################################################
#
#   Copyright (c) 2019-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
#   See LICENSE URL <https://store.webkul.com/license.html/> for full copyright and licensing details.
#################################################################################

#################################################################################


from odoo import models, fields, api, tools, _
import psycopg2
import logging
from odoo.tools import float_is_zero, float_round, float_repr, float_compare
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import Form

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    is_imported = fields.Boolean(string="Imported")

    def _process_payment_lines(self, pos_order, order, pos_session, draft):
        prec_acc = order.pricelist_id.currency_id.decimal_places

        order_bank_statement_lines= self.env['pos.payment'].search([('pos_order_id', '=', order.id)])
        order_bank_statement_lines.unlink()
        if pos_order['statement_ids']:
            for payments in pos_order['statement_ids']:
                order.add_payment(self._payment_fields(order, payments[2]))
        order.amount_paid = sum(order.payment_ids.mapped('amount'))
        if not draft and not float_is_zero(pos_order['amount_return'], prec_acc) and not order.is_imported:
            cash_payment_method = pos_session.payment_method_ids.filtered('is_cash_count')[:1]
            if not cash_payment_method:
                raise UserError(_("No cash statement found for this session. Unable to record returned cash."))
            return_payment_vals = {
                'name': _('return'),
                'pos_order_id': order.id,
                'amount': -pos_order['amount_return'],
                'payment_date': fields.Datetime.now(),
                'payment_method_id': cash_payment_method.id,
                'is_change': True,
            }
            order.add_payment(return_payment_vals)

    @api.model
    def _order_fields(self, ui_order):
        order_fields = super(PosOrder, self)._order_fields(ui_order)
        order_fields['is_imported'] = ui_order.get('is_imported')
        return order_fields

    @api.model
    def _process_order(self, order, draft, existing_order):

        order = order['data']
        pos_session = self.env['pos.session'].browse(order['pos_session_id'])
        if pos_session.state == 'closing_control' or pos_session.state == 'closed':
            order['pos_session_id'] = self._get_valid_session(order).id
        pos_order = False
        if not existing_order:
            pos_order = self.create(self._order_fields(order))

        else:
            pos_order = existing_order
            pos_order.lines.unlink()
            order['user_id'] = pos_order.user_id.id
            pos_order.write(self._order_fields(order))
        pos_order = pos_order.with_company(pos_order.company_id)
        self = self.with_company(pos_order.company_id)

        self._process_payment_lines(order, pos_order, pos_session, draft)
        if not draft:
            try:
                pos_order.action_pos_order_paid()
            except psycopg2.DatabaseError:
                # do not hide transactional errors, the order(s) won't be saved!
                raise
            except Exception as e:
                _logger.error('Could not fully process the POS Order: %s', tools.ustr(e))
            if not pos_order.is_imported:
                pos_order._create_order_picking()
                pos_order._compute_total_cost_in_real_time()

        if pos_order.to_invoice and pos_order.state == 'paid':
            pos_order._generate_pos_order_invoice()
        return pos_order.id

    @api.model
    def pos_order_auto_create(self, vals, session):
        session_id = self.env['pos.session']
        sess = session_id.create({
            'user_id': int(session[2]),
            'config_id': int(session[1]),
            'create_date': session[0],
            'start_at': session[0],
            'name': session[4]
        })

        sess.open_frontend_cb()
        sess.action_pos_session_open()
        c = 0
        for order in vals:
            c += 1
            if sess:
                main_dict = {}
                line_list = []
                for line in order['product_obj']:
                    product_obj = line['id']
                    product = False
                    if product_obj:
                        if int(product_obj) > 6107:
                            actual_product = self.env['product.product'].search([('name', '=', str(line['product_name']))], limit=1)
                            if actual_product:
                                product = actual_product
                        else:
                            product = self.env['product.product'].search([('id', '=', int(line['id']))])
                        discount = float(line['discount']) if line['discount'] else 0.0
                        line_obj = {
                            'qty': line['qty'] if line['qty'] else False,
                            'price_unit': line['price_unit'],
                            'tax_ids_after_fiscal_position': product.taxes_id.id,
                            'price_subtotal': line['price_subtotal'],
                            'discount': discount,
                            'price_subtotal_incl': line['price_subtotal_incl'],
                            'product_id': product.id,
                            'full_product_name': product.name,
                            'disc_fixed_amt': 0.0,
                            'tax_price': 0.0,
                        }

                        line_list.append(line_obj)

                main_dict['id'] = order['order']
                main_dict['data'] = {
                    'pos_reference': order['pos_reference'] if order['pos_reference'] else False,
                    'name': order['order'] if order['order'] else False,
                    'amount_paid': order['amount_paid'] if order['amount_paid'] else False,
                    'amount_total': order['amount_total'] if order['amount_total'] else False,
                    'amount_tax': order['amount_tax'] if order['amount_tax'] else False,
                    'pos_session_id': sess.id,
                    'uid': order['order'] if order['order'] else False,
                    'creation_date': order['create_date'] if order['create_date'] else False,
                    'server_id': False,
                    'user_id': order['user_id'] if order['user_id'] else False,
                    'partner_id': order['partner_id'] if order['partner_id'] else False,
                    'sequence_number': order['sequence_number'] if order['sequence_number'] else False,
                    'fiscal_position_id': order['fiscal_position_id'] if order['fiscal_position_id'] else False,
                    'pricelist_id': order['pricelist_id'] if order['pricelist_id'] else False,
                    'amount_return': order['amount_return'] if order['amount_return'] else False,
                    'to_ship': False,
                    'to_tipped': False,
                    'tip_amount': order['tip_amount'] if order['tip_amount'] else False,
                    'access_token': False,
                    'company_id': order['company_id'] if order['company_id'] else False,
                    'is_imported': True,
                    'employee_id': order['employee_id'] if order['employee_id'] else False,


                }
                main_dict['data']['lines'] = []
                for i in line_list:
                    main_dict['data']['lines'].append([0, 0, i])
                pay_list = []
                if order['payment_obj']:

                    for pay in order['payment_obj']:
                        payment_obj = {
                            'name': pay['payment_date'] if pay['payment_date'] else False,
                            'payment_method_id': pay['payment_method'] if pay['payment_method'] else False,
                            'amount': pay['amount_paid'] if pay['amount_paid'] else False,
                        }
                        pay_list.append([0, 0, payment_obj])

                main_dict['data']['statement_ids'] = pay_list
                if not pay_list:
                    main_dict['data']['statement_ids'] = False
                main_dict['to_invoice'] = order['to_invoice']
                self.create_from_ui([main_dict], draft=False)

        if sess.order_ids:

            for order in sess.order_ids:

                if order.state == 'draft':

                    order.write({'state': 'done'})
        if sess.state != 'closed' and not any(order.state == 'draft' for order in sess.order_ids):
            sess.action_pos_session_closing_control()
            if session[3]:
                sess.update({'stop_at': session[3]})
        return True


class PosSession(models.Model):
    _inherit = 'pos.session'

    def show_journal_items(self):
        self.ensure_one()
        all_related_moves = self._get_related_account_moves()
        if all_related_moves:
            return {
                'name': _('Journal Items'),
                'type': 'ir.actions.act_window',
                'res_model': 'account.move.line',
                'view_mode': 'tree',
                'view_id': self.env.ref('account.view_move_line_tree').id,
                'domain': [('id', 'in', all_related_moves.mapped('line_ids').ids)],
                'context': {
                    'journal_type': 'general',
                    'search_default_group_by_move': 1,
                    'group_by': 'move_id', 'search_default_posted': 1,
                },
            }

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):

        bank_payment_method_diffs = bank_payment_method_diffs or {}
        self.ensure_one()
        sudo = self.user_has_groups('point_of_sale.group_pos_user')
        if self.order_ids or self.statement_line_ids:
            self.cash_real_transaction = sum(self.statement_line_ids.mapped('amount'))
            if self.state == 'closed':
                raise UserError(_('This session is already closed.'))
            self._check_if_no_draft_orders()
            self._check_invoices_are_posted()
            cash_difference_before_statements = 0.0
            if self.update_stock_at_closing:
                self._create_picking_at_end_of_session()
                self.order_ids.filtered(lambda o: not o.is_total_cost_computed)._compute_total_cost_at_session_closing(self.picking_ids.move_ids)
            try:

                data = self.with_company(self.company_id)._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)

            except AccessError as e:

                if sudo:
                    data = self.sudo().with_company(self.company_id)._create_account_move(balancing_account, amount_to_balance, bank_payment_method_diffs)
                else:
                    raise e

            try:

                balance = sum(self.move_id.line_ids.mapped('balance'))

                with self.move_id._check_balanced({'records': self.move_id.sudo()}):
                    pass
            except Exception:
                return True

            self.sudo()._post_statement_difference(cash_difference_before_statements)
            if self.move_id.line_ids:

                self.move_id.sudo().with_company(self.company_id)._post()
                # Set the uninvoiced orders' state to 'done'
                self.env['pos.order'].search([('session_id', '=', self.id), ('state', '=', 'paid')]).write({'state': 'done'})
            else:
                self.move_id.sudo().unlink()
            self.sudo().with_company(self.company_id)._reconcile_account_move_lines(data)
        else:

            self.sudo()._post_statement_difference(self.cash_register_difference)

        self.write({'state': 'closed'})
        return True


class MoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.model
    def action_tax_unlink(self):
        journal_id = self.env['account.move.line'].search([('parent_state','=','posted'),
                                                           ('account_id.account_type', '=', 'liability_payable'),
                                                           ('tax_ids','!=', False)])
        for line in journal_id:
            if line.payment_id and line.tax_ids:
                list1 = []
                if line.move_id.has_reconciled_entries:
                    reconciled_ids = line.move_id.line_ids._reconciled_lines()
                    reconciled_bill_ids = self.search([('id','in',reconciled_ids)])
                    for i in reconciled_bill_ids:
                        if i.move_type == 'in_invoice':
                            list1.append(i)
                line.move_id.button_draft()
                line.tax_ids = line.account_id.tax_ids
                line.tax_audit = False
                line.move_id.action_post()
                list1.append(line)
                ## reconcile them
                (list1[0] + line).reconcile()
                line.payment_id = line.move_id.payment_id
            else:
                if line.tax_ids:
                    line.move_id.button_draft()
                    line.tax_ids.unlink()
                    line.move_id.action_post()
            line.tax_tag_ids = [(3, individual.id) for individual in line.tax_tag_ids]
            line.tax_tag_ids = [(2, individual.id) for individual in line.tax_tag_ids]
        return True

