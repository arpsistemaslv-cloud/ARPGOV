"""
Orquestrador do robô de atas com adesão.

Modos:
- contratos: Contratos.gov.br — confirma adesão por item (mais preciso, mais lento)
- pncp: API oficial PNCP — rápido, filtra possibilidadeAdesao no nível da ata
- hibrido: PNCP primeiro (estimativa + filtro) + Contratos para confirmação por item
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import contratos_gov_client as cg
import pncp_client as pncp


SCAN_MODES = ("contratos", "pncp", "hibrido")


@dataclass
class ArpRobotStats:
    scan_mode: str = "contratos"
    year: int = 0
    month: int | None = None
    keyword: str | None = None
    supplier_cnpj: str | None = None
    # Contratos.gov.br
    list_pages_read: int = 0
    list_pages_total: int | None = None
    atas_listed: int = 0
    atas_checked: int = 0
    atas_with_adesao: int = 0
    duplicates_skipped: int = 0
    list_scan_complete: bool = False
    detail_limit_hit: bool = False
    item_details_fetched: int = 0
    # PNCP
    pncp_pages_read: int = 0
    pncp_total_pages: int = 0
    pncp_rows_api: int = 0
    pncp_rows_matched: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def detail_scan_complete(self) -> bool:
        return not self.detail_limit_hit and self.atas_checked >= self.atas_listed

    @property
    def scan_fully_complete(self) -> bool:
        if self.scan_mode == "pncp":
            return (
                self.pncp_total_pages > 0
                and self.pncp_pages_read >= self.pncp_total_pages
            ) or (
                self.pncp_pages_read > 0
                and self.pncp_rows_matched == 0
                and self.pncp_rows_api == 0
            )
        return bool(self.list_scan_complete and self.detail_scan_complete)

    def absorb_contratos(self, stats: cg.ContratosGovScanStats) -> None:
        self.list_pages_read = stats.list_pages_read
        self.list_pages_total = stats.list_pages_total
        self.atas_listed = stats.atas_listed
        self.atas_checked = stats.atas_checked
        self.atas_with_adesao = stats.atas_with_adesao
        self.duplicates_skipped = stats.duplicates_skipped
        self.list_scan_complete = stats.list_scan_complete
        self.detail_limit_hit = stats.detail_limit_hit
        self.item_details_fetched = stats.item_details_fetched
        self.errors.extend(stats.errors)

    def absorb_pncp(self, stats: pncp.PncpAdesaoScanStats) -> None:
        self.pncp_pages_read = stats.pages_read
        self.pncp_total_pages = stats.total_pages_api
        self.pncp_rows_api = stats.rows_api
        self.pncp_rows_matched = stats.rows_matched
        self.errors.extend(stats.errors)


def period_dates(year: int, month: int | None) -> tuple[date, date]:
    if month is None or month < 1 or month > 12:
        return date(year, 1, 1), date(year, 12, 31)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def period_label(year: int, month: int | None) -> str:
    if month is None or month < 1 or month > 12:
        return str(year)
    names = (
        "",
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
        "Outubro",
        "Novembro",
        "Dezembro",
    )
    return f"{names[month]}/{year}"


def _pncp_hit_to_result(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        **hit,
        "verification_level": "pncp",
        "aceita_adesao": True,
    }


def run_arp_robot(
    year: int,
    *,
    month: int | None = None,
    keyword: str | None = None,
    supplier_cnpj: str | None = None,
    orgao_cnpj: str | None = None,
    pncp_query_mode: str = "vigencia",
    pncp_ano_ata: int | None = None,
    pncp_portal_filters: pncp.PncpPortalFilters | None = None,
    org_resolver: pncp.PncpOrgResolver | None = None,
    scan_mode: str = "contratos",
    include_vigente: bool = True,
    include_nao_vigente: bool = True,
    max_list_pages: int = 20,
    max_detail_checks: int = 150,
    max_pncp_pages: int = 30,
    enrich_suppliers: bool = True,
    only_pncp_adesao: bool = True,
) -> tuple[list[dict[str, Any]], ArpRobotStats]:
    mode = scan_mode if scan_mode in SCAN_MODES else "contratos"
    kw = (keyword or "").strip() or None
    cnpj_forn = cg._normalize_supplier_cnpj((supplier_cnpj or "").strip() or None)
    cnpj_org = pncp._org_cnpj_digits((orgao_cnpj or "").strip() or None)
    if cnpj_forn and mode == "pncp":
        mode = "contratos"
    qmode = pncp_query_mode if pncp_query_mode in pncp.PNCP_QUERY_MODES else "vigencia"
    stats = ArpRobotStats(
        scan_mode=mode,
        year=year,
        month=month,
        keyword=kw,
        supplier_cnpj=cnpj_forn,
    )
    d0, d1 = period_dates(year, month)
    hits: list[dict[str, Any]] = []
    pncp_hits: list[dict[str, Any]] = []

    if mode in ("pncp", "hibrido") and not cnpj_forn:
        pncp_hits, pncp_stats = pncp.scan_atas_inteligente(
            d0,
            d1,
            keyword=kw,
            orgao_cnpj=cnpj_org,
            codigo_unidade=(
                pncp_portal_filters.codigo_unidade if pncp_portal_filters else None
            ),
            ano_ata=pncp_ano_ata,
            query_mode=qmode,
            only_possibilidade_adesao=only_pncp_adesao and not pncp_portal_filters,
            portal_filters=pncp_portal_filters,
            org_resolver=org_resolver,
            max_pages=max_pncp_pages,
        )
        stats.absorb_pncp(pncp_stats)
        if mode == "pncp":
            return [_pncp_hit_to_result(h) for h in pncp_hits], stats

    if mode in ("contratos", "hibrido"):
        cg_hits, cg_stats = cg.scan_atas_com_adesao(
            year,
            month=month,
            keyword=kw,
            supplier_cnpj=cnpj_forn,
            include_vigente=include_vigente,
            include_nao_vigente=include_nao_vigente,
            max_list_pages=max_list_pages,
            max_detail_checks=max_detail_checks,
            enrich_suppliers=enrich_suppliers,
        )
        stats.absorb_contratos(cg_stats)
        for h in cg_hits:
            h["verification_level"] = "item"
            h["source"] = "contratos"
            hits.append(h)

        if mode == "hibrido" and not cnpj_forn:
            seen_pncp = {
                (h.get("pncp_control_id") or "").strip()
                for h in hits
                if h.get("pncp_control_id")
            }
            for ph in pncp_hits:
                pid = (ph.get("pncp_control_id") or "").strip()
                if pid and pid in seen_pncp:
                    continue
                ph = dict(ph)
                ph["verification_level"] = "pncp_only"
                ph["source"] = "pncp"
                hits.append(ph)

    return hits, stats
