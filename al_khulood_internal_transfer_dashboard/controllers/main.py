# -*- coding: utf-8 -*-
import json

from odoo import http
from odoo.http import request


class InternalTransferDashboardController(http.Controller):

    @http.route('/al_internal_transfer_dashboard/export', type='http', auth='user',
                methods=['POST'], csrf=True)
    def export_xlsx(self, filters='{}', **kwargs):
        try:
            filters = json.loads(filters or '{}')
        except ValueError:
            filters = {}
        model = request.env['al.internal.transfer.dashboard']
        content, filename = model._build_xlsx(filters)
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', 'attachment; filename="%s"' % filename),
            ('Content-Length', len(content)),
        ])
