"""Pipeline de análise prévia de ARPs e acompanhamento de licitações."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from models import ArpAnalysis, CatalogItem, ContratosGovScanResult, LicitacaoWatch, Opportunity, OpportunityCatalogLine, db

ARP_ANALYSIS_STATUSES: tuple[tuple[str, str], ...] = (
    ("preliminar", "Análise preliminar"),
    ("negociando", "Negociando parceiro"),
    ("aprovado", "Aprovado p/ site"),
    ("publicado", "Publicado no site"),
    ("descartado", "Descartado"),
)

LICITACAO_WATCH_STATUSES: tuple[tuple[str, str], ...] = (
    ("acompanhando", "Acompanhando"),
    ("participando", "Participando"),
    ("aguardando_ata", "Aguardando ARP"),
    ("ata_disponivel", "ARP disponível"),
    ("encerrado", "Encerrado"),
    ("desistencia", "Desistência"),
)

ARP_STATUS_LABELS = dict(ARP_ANALYSIS_STATUSES)
LICITACAO_STATUS_LABELS = dict(LICITACAO_WATCH_STATUSES)


def _valid_status(value: str, choices: tuple[tuple[str, str], ...], default: str) -> str:
    keys = {k for k, _ in choices}
    v = (value or "").strip().lower()
    return v if v in keys else default


def _normalize_url(raw: str | None) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url[:500]


def _parse_br_date(raw: str | None):
    if not raw:
        return None
    raw = raw.strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def arp_analysis_from_scan_result(result: ContratosGovScanResult) -> ArpAnalysis:
    """Registra ata do robô no pipeline (ou retorna registro existente)."""
    existing = ArpAnalysis.query.filter_by(scan_result_id=result.id).first()
    if existing:
        return existing
    if result.numero_ata:
        existing = ArpAnalysis.query.filter_by(numero_ata=result.numero_ata).first()
        if existing:
            existing.scan_result_id = result.id
            return existing

    supplier = result.primary_supplier
    arp_url = result.pncp_ata_url or result.detail_url
    titulo = (result.objeto or f"ARP {result.numero_ata or result.id}")[:400]
    row = ArpAnalysis(
        titulo=titulo,
        orgao=(result.unidade or "")[:400] or None,
        arp_url=arp_url[:500],
        pncp_url=(result.pncp_ata_url or "")[:500] or None,
        contratos_url=(result.detail_url or "")[:500] or None,
        numero_ata=(result.numero_ata or "")[:40] or None,
        objeto=result.objeto,
        vigencia_inicial=(result.vigencia_inicial or "")[:20] or None,
        vigencia_final=(result.vigencia_final or "")[:20] or None,
        fornecedor_nome=(supplier.get("fornecedor")[:300] if supplier else None),
        fornecedor_cnpj=(supplier.get("cnpj")[:22] if supplier else None),
        status="preliminar",
        scan_result_id=result.id,
    )
    db.session.add(row)
    db.session.flush()
    return row


def save_arp_analysis_from_form(form: Any, row: ArpAnalysis | None = None) -> ArpAnalysis:
    titulo = (form.get("titulo") or "").strip()[:400]
    arp_url = _normalize_url(form.get("arp_url"))
    if not titulo:
        raise ValueError("Informe um título.")
    if not arp_url:
        raise ValueError("Informe o link da ARP.")

    if row is None:
        row = ArpAnalysis(titulo=titulo, arp_url=arp_url)
        db.session.add(row)
    else:
        row.titulo = titulo
        row.arp_url = arp_url

    row.orgao = (form.get("orgao") or "").strip()[:400] or None
    row.pncp_url = _normalize_url(form.get("pncp_url")) or None
    row.contratos_url = _normalize_url(form.get("contratos_url")) or None
    row.numero_ata = (form.get("numero_ata") or "").strip()[:40] or None
    row.objeto = (form.get("objeto") or "").strip() or None
    row.vigencia_inicial = (form.get("vigencia_inicial") or "").strip()[:20] or None
    row.vigencia_final = (form.get("vigencia_final") or "").strip()[:20] or None
    row.fornecedor_nome = (form.get("fornecedor_nome") or "").strip()[:300] or None
    row.fornecedor_cnpj = re.sub(r"\D", "", (form.get("fornecedor_cnpj") or ""))[:22] or None
    row.partner_contact = (form.get("partner_contact") or "").strip()[:300] or None
    row.notes = (form.get("notes") or "").strip() or None
    row.status = _valid_status(form.get("status"), ARP_ANALYSIS_STATUSES, "preliminar")
    if row.catalog_item_id and row.status not in ("publicado", "descartado"):
        row.status = "publicado"
    row.updated_at = datetime.utcnow()
    db.session.flush()
    return row


def save_licitacao_watch_from_form(form: Any, row: LicitacaoWatch | None = None) -> LicitacaoWatch:
    titulo = (form.get("titulo") or "").strip()[:400]
    link = _normalize_url(form.get("link"))
    if not titulo:
        raise ValueError("Informe um título.")
    if not link:
        raise ValueError("Informe o link da licitação.")

    if row is None:
        row = LicitacaoWatch(titulo=titulo, link=link)
        db.session.add(row)
    else:
        row.titulo = titulo
        row.link = link

    row.orgao = (form.get("orgao") or "").strip()[:400] or None
    row.numero_edital = (form.get("numero_edital") or "").strip()[:160] or None
    row.modalidade = (form.get("modalidade") or "").strip()[:120] or None
    row.permite_adesao = form.get("permite_adesao") == "1"
    row.data_abertura = _parse_br_date(form.get("data_abertura"))
    row.data_resultado = _parse_br_date(form.get("data_resultado"))
    row.notes = (form.get("notes") or "").strip() or None
    row.status = _valid_status(form.get("status"), LICITACAO_WATCH_STATUSES, "acompanhando")
    row.updated_at = datetime.utcnow()
    db.session.flush()
    return row


def create_catalog_from_arp_analysis(
    analysis: ArpAnalysis,
    *,
    section: str = "ARP — análise prévia",
    slugify_fn,
    unique_slug_fn,
    parse_br_date_fn,
    sphere_from_unidade_fn,
) -> CatalogItem:
    """Publica ARP aprovada no catálogo do site."""
    if analysis.catalog_item_id:
        linked = db.session.get(CatalogItem, analysis.catalog_item_id)
        if linked:
            analysis.status = "publicado"
            return linked

    source_id = None
    if analysis.numero_ata:
        source_id = f"arp-analysis:{analysis.numero_ata}:{analysis.id}"
    else:
        source_id = f"arp-analysis:{analysis.id}"

    existing = CatalogItem.query.filter_by(source_pncp_id=source_id).first()
    if existing:
        analysis.catalog_item_id = existing.id
        analysis.status = "publicado"
        return existing

    valid_until = parse_br_date_fn(analysis.vigencia_final)
    item = CatalogItem(
        title=analysis.titulo[:300],
        section=section[:80],
        sphere=sphere_from_unidade_fn(analysis.orgao),
        quantity=1,
        unit_price=0,
        valid_until=valid_until,
        slug=unique_slug_fn(
            slugify_fn(f"ata-{analysis.numero_ata or analysis.id}")
        ),
        highlight=False,
        source_pncp_id=source_id,
        ata_owner_company=(analysis.fornecedor_nome or "")[:200] or None,
        technical_description=_technical_notes_from_analysis(analysis),
    )
    db.session.add(item)
    db.session.flush()
    analysis.catalog_item_id = item.id
    analysis.status = "publicado"
    analysis.updated_at = datetime.utcnow()
    return item


def create_arp_analysis_from_licitacao(watch: LicitacaoWatch) -> ArpAnalysis:
    """Cria entrada no pipeline de ARP a partir de licitação concluída."""
    if watch.arp_analysis_id:
        linked = db.session.get(ArpAnalysis, watch.arp_analysis_id)
        if linked:
            return linked

    row = ArpAnalysis(
        titulo=watch.titulo[:400],
        orgao=(watch.orgao or "")[:400] or None,
        arp_url=watch.link[:500],
        numero_ata=(watch.numero_edital or "")[:40] or None,
        objeto=watch.notes,
        status="preliminar",
        notes=f"Originada da licitação #{watch.id}. {watch.notes or ''}".strip(),
    )
    db.session.add(row)
    db.session.flush()
    watch.arp_analysis_id = row.id
    watch.status = "ata_disponivel"
    watch.updated_at = datetime.utcnow()
    return row


def create_opportunity_from_arp_analysis(analysis: ArpAnalysis) -> Opportunity:
    if analysis.opportunity_id:
        linked = db.session.get(Opportunity, analysis.opportunity_id)
        if linked:
            return linked

    title = f"Parceria ARP {analysis.numero_ata or analysis.id}"
    opp = Opportunity(
        title=title[:200],
        organization=(analysis.orgao or "")[:200] or None,
        cnpj=analysis.fornecedor_cnpj,
        sphere="Federal",
        stage="novo",
        source="Análise prévia ARP",
        notes=_technical_notes_from_analysis(analysis),
    )
    db.session.add(opp)
    db.session.flush()
    if analysis.catalog_item_id:
        cat = db.session.get(CatalogItem, analysis.catalog_item_id)
        if cat and not any(
            ln.catalog_item_id == cat.id for ln in opp.catalog_lines
        ):
            opp.catalog_lines.append(
                OpportunityCatalogLine(catalog_item_id=cat.id, quantity=1)
            )
    analysis.opportunity_id = opp.id
    analysis.updated_at = datetime.utcnow()
    return opp


def _technical_notes_from_analysis(analysis: ArpAnalysis) -> str:
    lines: list[str] = []
    if analysis.objeto:
        lines.append(f"Objeto: {analysis.objeto}")
    if analysis.orgao:
        lines.append(f"Órgão: {analysis.orgao}")
    if analysis.fornecedor_nome:
        lines.append(f"Fornecedor: {analysis.fornecedor_nome}")
    if analysis.fornecedor_cnpj:
        lines.append(f"CNPJ: {analysis.fornecedor_cnpj}")
    if analysis.partner_contact:
        lines.append(f"Contato parceiro: {analysis.partner_contact}")
    if analysis.vigencia_inicial or analysis.vigencia_final:
        lines.append(f"Vigência: {analysis.vigencia_inicial or '?'} → {analysis.vigencia_final or '?'}")
    lines.append(f"ARP: {analysis.arp_url}")
    if analysis.pncp_url:
        lines.append(f"PNCP: {analysis.pncp_url}")
    if analysis.contratos_url:
        lines.append(f"Contratos: {analysis.contratos_url}")
    if analysis.notes:
        lines.append(f"Notas: {analysis.notes}")
    return "\n".join(lines)
