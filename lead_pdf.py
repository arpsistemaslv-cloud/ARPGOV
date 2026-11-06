"""PDF do lead completo (CRM)."""
from __future__ import annotations

import io
import re
from datetime import datetime
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pipeline_stages import STAGE_FIELD_DEFS, STAGE_LABELS, stage_label

NAVY = colors.HexColor("#1351B4")
INK = colors.HexColor("#1b1b1b")
MUTED = colors.HexColor("#5c6670")
LINE = colors.HexColor("#d0d7de")
ZEBRA = colors.HexColor("#f4f7fb")


def _brl(val) -> str:
    if val is None:
        return "—"
    try:
        d = Decimal(str(val)).quantize(Decimal("0.01"))
    except Exception:
        return "—"
    s = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _txt(val) -> str:
    s = ("" if val is None else str(val)).strip()
    return s or "—"


def _esc(val) -> str:
    s = _txt(val)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _date_br(raw) -> str:
    s = (raw or "").strip() if isinstance(raw, str) else ""
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    if hasattr(raw, "strftime"):
        return raw.strftime("%d/%m/%Y")
    return s


def _dt_br(raw) -> str:
    if raw is None:
        return "—"
    if hasattr(raw, "strftime"):
        return raw.strftime("%d/%m/%Y %H:%M")
    return _txt(raw)


def build_lead_pdf(opp) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.6 * cm,
        rightMargin=1.6 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.6 * cm,
        title=f"Lead {opp.id} — {opp.title or ''}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "LeadTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=NAVY,
        spaceAfter=2,
    )
    subtitle = ParagraphStyle(
        "LeadSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=MUTED,
        spaceAfter=10,
    )
    section = ParagraphStyle(
        "LeadSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=4,
    )
    cell = ParagraphStyle(
        "LeadCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=INK,
    )
    cell_b = ParagraphStyle(
        "LeadCellB",
        parent=cell,
        fontName="Helvetica-Bold",
    )
    label = ParagraphStyle(
        "LeadLabel",
        parent=cell,
        textColor=MUTED,
        fontSize=8,
    )

    story: list = [
        Paragraph("ARPGOV — Lead completo", title),
        Paragraph(
            f"#{opp.id} · {_esc(stage_label(opp.stage))} · atualizado em {_dt_br(opp.updated_at)}",
            subtitle,
        ),
    ]

    def heading(text: str) -> None:
        story.append(Paragraph(_esc(text), section))

    def kv_table(rows: list[tuple[str, str]]) -> None:
        data = []
        for name, value in rows:
            data.append(
                [
                    Paragraph(_esc(name), label),
                    Paragraph(_esc(value) if value != "—" else "—", cell),
                ]
            )
        table = Table(data, colWidths=[4.6 * cm, 12.6 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.2, LINE),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ]
            )
        )
        story.append(table)

    def grid(headers: list[str], body: list[list[str]], widths: list[float]) -> None:
        if not body:
            story.append(Paragraph("Nenhum registro.", cell))
            return
        data = [[Paragraph(_esc(h), cell_b) for h in headers]]
        for row in body:
            data.append([Paragraph(_esc(c), cell) for c in row])
        table = Table(data, colWidths=widths, repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ]
        for i in range(1, len(data)):
            if i % 2 == 0:
                style_cmds.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
        table.setStyle(TableStyle(style_cmds))
        story.append(table)

    heading("Identificação")
    kv_table(
        [
            ("Título", _txt(opp.title)),
            ("Órgão / empresa", _txt(opp.organization)),
            ("CNPJ", _txt(opp.cnpj)),
            ("Contato", _txt(opp.contact_name)),
            ("E-mail", _txt(opp.email)),
            ("Telefone", _txt(opp.phone)),
            ("Esfera", _txt(opp.sphere)),
            ("Nº processo / licitação", _txt(opp.process_ref)),
            ("Origem", _txt(opp.source)),
            ("Estágio", _txt(stage_label(opp.stage))),
            (
                "Representante",
                _txt(opp.sales_rep.name if opp.sales_rep else None),
            ),
            ("Criado em", _dt_br(opp.created_at)),
        ]
    )

    client = opp.portal_client
    heading("Cliente")
    if client is None:
        story.append(Paragraph("Nenhum cliente vinculado.", cell))
    else:
        city = " / ".join(
            p
            for p in [(client.address_city or "").strip(), (client.address_state or "").strip()]
            if p
        )
        role = " · ".join(
            p for p in [(client.job_title or "").strip(), (client.sector or "").strip()] if p
        )
        kv_table(
            [
                ("Nome", _txt(client.name)),
                ("Razão social", _txt(client.razao_social or client.organization)),
                ("E-mail", _txt(client.email)),
                ("Telefone", _txt(client.phone)),
                ("CNPJ", _txt(client.cnpj)),
                ("Cargo / setor", role or "—"),
                ("Cidade", city or "—"),
                ("Endereço", _txt(client.address_line)),
            ]
        )

    partner = opp.partner
    heading("Fornecedor")
    if partner is None:
        story.append(Paragraph("Nenhum fornecedor vinculado.", cell))
    else:
        kv_table(
            [
                ("Empresa", _txt(partner.company_name or partner.razao_social)),
                ("Contato", _txt(partner.name)),
                ("E-mail", _txt(partner.email)),
                ("Telefone", _txt(partner.phone)),
                ("CNPJ", _txt(partner.cnpj)),
                ("Cidade", " / ".join(p for p in [(partner.address_city or "").strip(), (partner.address_state or "").strip()] if p) or "—"),
            ]
        )

    heading("Produtos da adesão")
    product_rows = []
    for ln in opp.catalog_lines or []:
        item = ln.catalog_item
        title = item.title if item else f"Item #{ln.catalog_item_id}"
        unit = item.unit_price if item else None
        qty = int(ln.quantity or 1)
        line_total = None
        if unit is not None:
            line_total = Decimal(str(unit)) * Decimal(qty)
        product_rows.append(
            [
                title,
                str(qty),
                _brl(unit) if unit is not None else "—",
                _brl(line_total) if line_total is not None else "—",
                _txt(item.ata_owner_company if item else None),
            ]
        )
    grid(
        ["Produto", "Qtd", "Unitário", "Total", "Empresa da ata"],
        product_rows,
        [7.2 * cm, 1.3 * cm, 2.5 * cm, 2.6 * cm, 3.6 * cm],
    )
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"<b>Valor total da venda:</b> {_brl(opp.value_brl)}", cell_b))

    heading("Comissionamento")
    project = opp.commission_project
    tier = opp.commission_tier
    kv_table(
        [
            ("Tipo", _txt(project.title if project else None)),
            ("Faixa", _txt(getattr(tier, "percent_total", None) and f"{tier.percent_total}%")),
            ("Comissão do representante", _brl(opp.rep_commission_brl) if opp.rep_commission_brl is not None else "—"),
            ("Observações da comissão", _txt(opp.rep_commission_note)),
        ]
    )
    story.append(Spacer(1, 6))
    split_rows = []
    for s in opp.commission_splits or []:
        status = (s.payout_status or "pendente").replace("_", " ")
        split_rows.append(
            [
                _txt(s.recipient_name),
                f"{Decimal(str(s.share_percent or 0)):.2f}%".replace(".", ","),
                _brl(s.amount_brl),
                status,
            ]
        )
    if split_rows:
        story.append(Paragraph("Rateio", cell_b))
        story.append(Spacer(1, 3))
        grid(
            ["Beneficiário", "%", "Valor", "Status"],
            split_rows,
            [8.2 * cm, 2.4 * cm, 3.4 * cm, 3.2 * cm],
        )

    heading("Pipeline")
    pipeline_bits = []
    for key, defs in STAGE_FIELD_DEFS.items():
        block = opp.pipeline_stage_data(key)
        bits = []
        if defs.get("date") and block.get("date"):
            bits.append(f"{defs['date']}: {_date_br(block.get('date'))}")
        if defs.get("forecast_date") and block.get("forecast_date"):
            bits.append(f"{defs['forecast_date']}: {_date_br(block.get('forecast_date'))}")
        if defs.get("notes") and (block.get("notes") or "").strip():
            bits.append(f"{defs['notes']}: {(block.get('notes') or '').strip()}")
        attachments = opp.pipeline_attachments(key)
        if attachments:
            names = ", ".join((a.get("name") or "Anexo") for a in attachments)
            bits.append(f"Anexos: {names}")
        if bits:
            pipeline_bits.append((STAGE_LABELS.get(key, key), " · ".join(bits)))
    if pipeline_bits:
        kv_table(pipeline_bits)
    else:
        story.append(Paragraph("Sem dados extras de pipeline.", cell))

    heading("Observações")
    story.append(Paragraph(_esc(opp.notes) if (opp.notes or "").strip() else "—", cell))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(1.6 * cm, 0.8 * cm, "ARPGOV — documento interno do lead")
        canvas.drawRightString(A4[0] - 1.6 * cm, 0.8 * cm, f"Página {_doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def lead_pdf_filename(opp) -> str:
    raw = (opp.title or f"lead-{opp.id}").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw, flags=re.I).strip("-")[:60] or f"lead-{opp.id}"
    stamp = datetime.utcnow().strftime("%Y%m%d")
    return f"lead-{opp.id}-{slug}-{stamp}.pdf"
