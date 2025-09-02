{
    "name": "Boletim de Medição (Proev)",
    "summary": "Boletim de medição de serviços com local de obra, controle mensal e integração a Contratos (OCA)",
    "version": "16.0.1.0.1",
    "author": "Proev Rental / ChatGPT",
    "website": "https://proevrental.com.br",
    "license": "LGPL-3",
    "depends": ["sale_management", "project", "account", "contract"],
    "data": [
        "security/ir.model.access.csv",
        "data/sequence.xml",
        "views/measurement_views.xml",
        "report/measurement_report.xml",
        "report/measurement_templates.xml"
    ],
    "installable": True,
    "application": True
}