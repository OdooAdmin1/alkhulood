import json

from odoo import http
from odoo.http import content_disposition, request

from .. import akf_report


class AlkhuloodhSfReportController(http.Controller):

    @http.route('/alkhuloodh_sf/report/<string:fmt>', type='http', auth='user', methods=['GET'])
    def download(self, fmt, f='{}', **kw):
        if fmt not in ('xlsx', 'pdf'):
            return request.not_found()
        try:
            filters = json.loads(f)
        except ValueError:
            return request.make_response('Bad request', [('Content-Type', 'text/plain')], status=400)
        rep = request.env['alkhuloodh.sf.report'].get_report(filters)
        if rep.get('error'):
            return request.make_response(rep['error'], [('Content-Type', 'text/plain; charset=utf-8')], status=400)
        name = 'forecast_report_%s_%s_%s' % (rep['metric'], rep['date_from'], rep['date_to'])
        if fmt == 'xlsx':
            data = akf_report.to_xlsx(rep)
            ctype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            data = akf_report.to_pdf(rep)
            ctype = 'application/pdf'
        return request.make_response(data, [('Content-Type', ctype), ('Content-Length', len(data)),
                                            ('Content-Disposition', content_disposition('%s.%s' % (name, fmt)))])
