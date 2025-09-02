
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from datetime import date
import calendar

MONTH_SELECTION = [(str(i), calendar.month_name[i]) for i in range(1, 13)]

class BMSheet(models.Model):
    _name = "bm.sheet"
    _description = "Boletim de Medição"
    _order = "period_year desc, period_month desc, id desc"

    name = fields.Char(default="/", copy=False, readonly=True)
    state = fields.Selection([("draft","Rascunho"),("submitted","Enviado"),("approved","Aprovado"),("invoiced","Faturado"),("cancelled","Cancelado")], default="draft", tracking=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company.id, readonly=True)
    partner_id = fields.Many2one("res.partner", required=True)
    project_id = fields.Many2one("project.project")
    analytic_account_id = fields.Many2one(related="project_id.analytic_account_id", store=True, readonly=True)
    sale_id = fields.Many2one("sale.order")
    contract_id = fields.Many2one("contract.contract")
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id", store=True, readonly=True)

    period_year = fields.Integer(default=lambda self: fields.Date.context_today(self).year, required=True)
    period_month = fields.Selection(MONTH_SELECTION, default=lambda self: str(fields.Date.context_today(self).month), required=True)
    date_start = fields.Date()
    date_end = fields.Date()

    measurement_type = fields.Selection([("quantity","Quantidade"),("percent","Percentual do Contrato/Pedido")], default="quantity", required=True)

    site_partner_id = fields.Many2one("res.partner")
    site_street = fields.Char()
    site_street2 = fields.Char()
    site_city = fields.Char()
    site_state_id = fields.Many2one("res.country.state")
    site_zip = fields.Char()
    site_country_id = fields.Many2one("res.country")
    site_reference = fields.Char()

    line_ids = fields.One2many("bm.line", "sheet_id")

    amount_subtotal = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    retention_percent = fields.Float(digits=(16,2))
    retention_amount = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")
    amount_total = fields.Monetary(compute="_compute_amounts", store=True, currency_field="currency_id")

    invoice_id = fields.Many2one("account.move", readonly=True, copy=False)

    _sql_constraints = [("bm_unique_so_period","unique(company_id, sale_id, period_year, period_month)","Já existe um Boletim para este Pedido e período (mês/ano) nesta empresa.")]

    @api.constrains("contract_id","period_year","period_month","company_id","state")
    def _check_unique_contract_period(self):
        for rec in self:
            if rec.contract_id:
                domain=[("id","!=",rec.id),("company_id","=",rec.company_id.id),("contract_id","=",rec.contract_id.id),("period_year","=",rec.period_year),("period_month","=",rec.period_month),("state","!=","cancelled")]
                if self.search_count(domain):
                    raise ValidationError(_("Já existe um Boletim para este Contrato e período (mês/ano) nesta empresa."))

    @api.onchange("period_year","period_month")
    def _onchange_period_set_dates(self):
        for rec in self:
            if rec.period_year and rec.period_month:
                m=int(rec.period_month); y=rec.period_year; last=calendar.monthrange(y,m)[1]
                rec.date_start=date(y,m,1); rec.date_end=date(y,m,last)

    @api.onchange("site_partner_id")
    def _onchange_site_partner_fill(self):
        for rec in self:
            p=rec.site_partner_id
            if p:
                rec.site_street=p.street; rec.site_street2=p.street2; rec.site_city=p.city
                rec.site_state_id=p.state_id.id; rec.site_zip=p.zip; rec.site_country_id=p.country_id.id

    @api.depends("line_ids.subtotal","retention_percent")
    def _compute_amounts(self):
        for rec in self:
            subtotal=sum(rec.line_ids.mapped("subtotal"))
            rec.amount_subtotal=subtotal
            ret=(subtotal*(rec.retention_percent/100.0)) if rec.retention_percent else 0.0
            rec.retention_amount=ret; rec.amount_total=subtotal-ret

    def action_submit(self): 
        for rec in self:
            if not rec.line_ids: raise UserError(_("Adicione ao menos uma linha ao boletim antes de enviar."))
            rec.state="submitted"

    def action_approve(self):
        for rec in self:
            if rec.state not in ("submitted","draft"): raise UserError(_("Somente boletins em Rascunho/Enviado podem ser aprovados."))
            for line in rec.line_ids:
                if line.approved_qty<0: raise UserError(_("Quantidade aprovada não pode ser negativa."))
            rec.state="approved"

    def action_set_to_draft(self):
        for rec in self:
            if rec.state=="invoiced": raise UserError(_("Não é possível voltar para rascunho um boletim já faturado."))
            rec.state="draft"

    def action_cancel(self):
        for rec in self:
            if rec.invoice_id and rec.invoice_id.state not in ("draft","cancel"): raise UserError(_("Cancele a fatura antes de cancelar o boletim."))
            rec.state="cancelled"

    def _prepare_invoice_vals(self):
        self.ensure_one()
        if not self.partner_id: raise UserError(_("Informe o cliente no boletim."))
        if not self.line_ids: raise UserError(_("Não há linhas para faturar."))
        order=self.sale_id; contract=self.contract_id

        vals={
            "move_type":"out_invoice",
            "invoice_origin":" / ".join([v for v in [self.name, order.name if order else False, contract.name if contract else False] if v]),
            "partner_id":self.partner_id.id,
            "invoice_user_id":self.env.user.id,
            "invoice_payment_term_id":(order.payment_term_id.id if order and order.payment_term_id else False),
            "currency_id":(order.pricelist_id.currency_id.id if order and order.pricelist_id else self.currency_id.id),
            "invoice_line_ids":[],
            "company_id":self.company_id.id
        }

        for line in self.line_ids:
            if line.approved_qty<=0.0:
                continue
            product=line.product_id
            uom=line.product_uom
            name=line.name or (product.display_name if product else _("Serviço"))
            taxes=line.sale_line_id.tax_id if line.sale_line_id else (product.taxes_id if product else False)

            line_vals = {
                "name": name,
                "product_id": product.id if product else False,
                "quantity": line.approved_qty,
                "price_unit": line.price_unit or 0.0,
                "tax_ids": [(6,0,taxes.ids)] if taxes else False,
                "product_uom_id": uom.id if uom else False,
            }
            # v16: usar analytic_distribution (JSON) na move line
            if self.analytic_account_id:
                line_vals["analytic_distribution"] = {str(self.analytic_account_id.id): 100}

            vals["invoice_line_ids"].append((0,0,line_vals))

        if self.retention_amount:
            vals["invoice_line_ids"].append((0,0,{"name":_("Retenção de Garantia"),"quantity":1.0,"price_unit":-self.retention_amount,"tax_ids":False}))

        if not vals["invoice_line_ids"]:
            raise UserError(_("Não há linhas aprovadas para faturar."))
        return vals

    def action_create_invoice(self):
        for rec in self:
            if rec.state not in ("approved","submitted"):
                raise UserError(_("Apenas boletins Enviados/Aprovados podem gerar fatura."))
            move=self.env["account.move"].create(rec._prepare_invoice_vals())
            rec.invoice_id=move.id; rec.state="invoiced"
        return True

    @api.model
    def create(self, vals):
        if not vals.get("name") or vals.get("name")=="/":
            vals["name"]=self.env["ir.sequence"].next_by_code("bm.sheet") or "/"
        if not vals.get("date_start") or not vals.get("date_end"):
            y=vals.get("period_year") or fields.Date.context_today(self).year
            m=int(vals.get("period_month") or fields.Date.context_today(self).month)
            last=calendar.monthrange(y,m)[1]
            vals.setdefault("date_start", date(y,m,1)); vals.setdefault("date_end", date(y,m,last))
        return super().create(vals)

class BMLine(models.Model):
    _name = "bm.line"
    _description = "Linha do Boletim de Medição"
    _order = "sequence, id"

    sheet_id = fields.Many2one("bm.sheet", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char()

    sale_line_id = fields.Many2one("sale.order.line")
    contract_line_id = fields.Many2one("contract.line")

    # Campo related para usar em attrs no tree (evita refs compostas)
    sheet_measurement_type = fields.Selection(related="sheet_id.measurement_type", store=False)

    product_id = fields.Many2one("product.product", compute="_compute_sources", store=True, readonly=False)
    product_uom = fields.Many2one("uom.uom", compute="_compute_sources", store=True, readonly=False)
    price_unit = fields.Float(digits=(16,2), compute="_compute_sources", store=True)

    measured_qty = fields.Float()
    measured_percent = fields.Float(digits=(16,2))
    approved_qty = fields.Float(compute="_compute_approved_qty", store=True)
    previous_approved_qty = fields.Float(compute="_compute_previous_approved", store=True)

    currency_id = fields.Many2one("res.currency", related="sheet_id.currency_id", store=True, readonly=True)
    subtotal = fields.Monetary(compute="_compute_subtotal", store=True, currency_field="currency_id")

    @api.depends("sale_line_id","contract_line_id")
    def _compute_sources(self):
        for line in self:
            product=uom=price=False
            if line.sale_line_id:
                product=line.sale_line_id.product_id; uom=line.sale_line_id.product_uom; price=line.sale_line_id.price_unit
                if not line.name: line.name=line.sale_line_id.name
            elif line.contract_line_id:
                product=line.contract_line_id.product_id; uom=getattr(line.contract_line_id,"uom_id",False) or (product.uom_id if product else False); price=getattr(line.contract_line_id,"price_unit",0.0)
                if not line.name: line.name=line.contract_line_id.name or (product.display_name if product else _("Serviço"))
            else:
                product=line.product_id; uom=line.product_uom; price=line.price_unit or 0.0
            line.product_id=product.id if product else False
            line.product_uom=uom.id if uom else False
            line.price_unit=price or 0.0

    @api.depends("measured_qty","measured_percent","sheet_id.measurement_type","sale_line_id.product_uom_qty","contract_line_id.quantity")
    def _compute_approved_qty(self):
        for line in self:
            if line.sheet_id.measurement_type=="quantity":
                line.approved_qty=max(line.measured_qty,0.0)
            else:
                base=0.0
                if line.sale_line_id: base=line.sale_line_id.product_uom_qty or 0.0
                elif line.contract_line_id: base=getattr(line.contract_line_id,"quantity",0.0) or 0.0
                pct=max(line.measured_percent,0.0)/100.0
                line.approved_qty=max(base*pct,0.0)

    @api.depends("sheet_id.partner_id","sale_line_id","contract_line_id","sheet_id.state")
    def _compute_previous_approved(self):
        for line in self:
            qty = 0.0
            base_domain = [
                ("partner_id", "=", line.sheet_id.partner_id.id),
                ("state", "in", ["approved", "invoiced"]),
            ]
            current_id = line.sheet_id.id
            if isinstance(current_id, int):
                domain = [("id", "!=", current_id)] + base_domain
            else:
                domain = list(base_domain)

            for s in self.env["bm.sheet"].search(domain):
                for l in s.line_ids:
                    if (line.sale_line_id and l.sale_line_id == line.sale_line_id) or \
                       (line.contract_line_id and l.contract_line_id == line.contract_line_id):
                        qty += l.approved_qty
            line.previous_approved_qty = qty

    @api.depends("approved_qty","price_unit")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal=(line.approved_qty or 0.0)*(line.price_unit or 0.0)
