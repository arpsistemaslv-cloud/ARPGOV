"""
Cliente para consulta pública de Atas de Registro de Preços no Contratos.gov.br.

Portal: https://contratos.sistema.gov.br/transparencia/arp
Detalhe: /transparencia/arpshow/{id}/show
Item:   /transparencia/arpshow/itens/{numero}/{arp_id}/show

A listagem exige ao menos 3 filtros no site; usamos ano da compra, vigência no ano
e situação (vigente / não vigente).
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = "https://contratos.sistema.gov.br"
ARP_LIST_URL = f"{BASE_URL}/transparencia/arp"
DEFAULT_UA = "ARPGOV-Robo/1.0 (+consulta transparencia publica)"
REQUEST_TIMEOUT = 120
PAUSE_SEC = 0.35
ATAS_PER_LIST_PAGE = 10


@dataclass
class ContratosGovScanStats:
    list_pages_read: int = 0
    list_pages_total: int | None = None
    atas_listed: int = 0
    atas_checked: int = 0
    atas_with_adesao: int = 0
    duplicates_skipped: int = 0
    list_scan_complete: bool = False
    detail_limit_hit: bool = False
    item_details_fetched: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def detail_scan_complete(self) -> bool:
        return not self.detail_limit_hit and self.atas_checked >= self.atas_listed

    @property
    def scan_fully_complete(self) -> bool:
        return self.list_scan_complete and self.detail_scan_complete


def period_date_strings(year: int, month: int | None) -> tuple[str, str]:
    if month is None or month < 1 or month > 12:
        return f"01/01/{year}", f"31/12/{year}"
    last = calendar.monthrange(year, month)[1]
    return f"01/{month:02d}/{year}", f"{last:02d}/{month:02d}/{year}"


def _keyword_matches(keyword: str | None, detail: dict[str, Any]) -> bool:
    if not keyword:
        return True
    kw = keyword.strip().lower()
    if not kw:
        return True
    for field in ("unidade", "numero", "compra_ano", "modalidade", "objeto"):
        if kw in (detail.get(field) or "").lower():
            return True
    for it in detail.get("items_adesao") or []:
        for field in ("descricao", "descricao_detalhada"):
            if kw in (it.get(field) or "").lower():
                return True
    return False


def _clean_cell(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def _field_value(html: str, label_prefix: str) -> str | None:
    pat = (
        rf"<label>{label_prefix}[^<]*</label>\s*<br>\s*"
        rf"<label[^>]*>([^<]+)</label>"
    )
    m = re.search(pat, html, re.I | re.S)
    if m:
        return _clean_cell(m.group(1))
    return None


def _field_value_row(html: str, label: str) -> str | None:
    pat = rf"<td[^>]*>\s*{re.escape(label)}\s*</td>\s*<td[^>]*>(.*?)</td>"
    m = re.search(pat, html, re.I | re.S)
    if m:
        return _clean_cell(m.group(1))
    return None


def parse_list_total_pages(html: str) -> int | None:
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    if pages:
        return max(pages)
    if re.search(r"/transparencia/arpshow/\d+/show", html):
        return 1
    return None


def parse_item_detail(html: str) -> dict[str, Any]:
    suppliers: list[dict[str, str]] = []
    in_supplier_table = False
    descricao_detalhada: str | None = None

    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        if "<th" in row:
            headers = [
                _clean_cell(c)
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
            ]
            if any("cnpj" in h.lower() for h in headers) and any(
                "fornecedor" in h.lower() for h in headers
            ):
                in_supplier_table = True
            continue
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if not cells:
            continue
        clean = [_clean_cell(c) for c in cells]
        if len(clean) == 2 and clean[0].lower().startswith("descri"):
            descricao_detalhada = clean[1]
            continue
        if in_supplier_table and len(clean) >= 5:
            cnpj = clean[1]
            if re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", cnpj):
                suppliers.append(
                    {
                        "classificacao": clean[0],
                        "cnpj": cnpj,
                        "fornecedor": clean[2],
                        "quantidade": clean[3],
                        "valor_unitario": clean[4],
                    }
                )

    aceita = _field_value_row(html, "Aceita adesão:") or _field_value_row(
        html, "Aceita adesão"
    )
    return {
        "descricao_detalhada": descricao_detalhada,
        "fornecedores": suppliers,
        "aceita_adesao_detalhe": aceita,
    }


def parse_arp_detail(html: str, arp_id: int) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        if "<th" in row:
            continue
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
        if len(cells) < 4:
            continue
        clean = [_clean_cell(c) for c in cells]
        items.append(
            {
                "numero": clean[0],
                "descricao": clean[1],
                "qtd_adesao": clean[2],
                "aceita_adesao": clean[3],
            }
        )

    def _aceita_sim(val: str) -> bool:
        v = (val or "").strip().lower()
        return v.startswith("sim") or v == "s"

    items_adesao = [it for it in items if _aceita_sim(it.get("aceita_adesao", ""))]

    pncp_ata_m = re.search(r"(https://pncp\.gov\.br/app/atas/[^\"'\s<]+)", html)
    pncp_compra_m = re.search(
        r"(https://pncp\.gov\.br/app/(?:compras|contratos)/[^\"'\s<]+)", html
    )
    modalidade = _field_value(html, "Modalidade da compra") or _field_value(
        html, "Modalidade"
    )

    return {
        "arp_id": arp_id,
        "numero": _field_value(html, "Número da ata de registro de preços")
        or _field_value(html, "Nmero da ata de registro de preos"),
        "unidade": _field_value(html, "Unidade gerenciadora"),
        "compra_ano": _field_value(html, "Número da compra/ Ano")
        or _field_value(html, "Nmero da compra/ Ano"),
        "status": _field_value(html, "Status da ata"),
        "valor_total": _field_value(html, "Valor total"),
        "vigencia_inicial": _field_value(html, "Vigência inicial")
        or _field_value(html, "Vigncia inicial"),
        "vigencia_final": _field_value(html, "Vigência final")
        or _field_value(html, "Vigncia final"),
        "modalidade": modalidade,
        "pncp_ata_url": pncp_ata_m.group(1) if pncp_ata_m else None,
        "pncp_compra_url": pncp_compra_m.group(1) if pncp_compra_m else None,
        "items": items,
        "items_adesao": items_adesao,
        "aceita_adesao": bool(items_adesao),
        "detail_url": f"{BASE_URL}/transparencia/arpshow/{arp_id}/show",
    }


def _normalize_supplier_cnpj(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", (raw or "").strip())
    if len(digits) != 14:
        return None
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"


def _supplier_cnpj_digits(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", (raw or "").strip())
    return digits if len(digits) == 14 else None


def _filter_items_by_supplier_cnpj(
    items_adesao: list[dict[str, Any]], supplier_cnpj: str | None
) -> list[dict[str, Any]]:
    digits = _supplier_cnpj_digits(supplier_cnpj)
    if not digits:
        return items_adesao
    out: list[dict[str, Any]] = []
    for it in items_adesao:
        matched = [
            f
            for f in (it.get("fornecedores") or [])
            if _supplier_cnpj_digits(f.get("cnpj")) == digits
        ]
        if matched:
            row = dict(it)
            row["fornecedores"] = matched
            out.append(row)
    return out


def _list_params(
    year: int,
    *,
    month: int | None = None,
    keyword: str | None = None,
    supplier_cnpj: str | None = None,
    include_vigente: bool,
    include_nao_vigente: bool,
    page: int,
) -> list[tuple[str, str]]:
    situacao: list[str] = []
    if include_vigente:
        situacao.append("vigente")
    if include_nao_vigente:
        situacao.append("nao_vigente")
    if not situacao:
        situacao = ["vigente", "nao_vigente"]
    data_inicio, data_fim = period_date_strings(year, month)
    params: list[tuple[str, str]] = [
        ("dataInicio", data_inicio),
        ("dataFim", data_fim),
        ("anoCompra", str(year)),
        ("page", str(max(1, page))),
    ]
    if keyword:
        params.append(("palavra_chave", keyword.strip()[:100]))
    cnpj_fmt = _normalize_supplier_cnpj(supplier_cnpj)
    if cnpj_fmt:
        params.append(("cnpjFornecedor", cnpj_fmt))
    for s in situacao:
        params.append(("situacao", s))
    return params


def fetch_arp_list_page(
    session: requests.Session,
    year: int,
    *,
    month: int | None = None,
    keyword: str | None = None,
    supplier_cnpj: str | None = None,
    include_vigente: bool,
    include_nao_vigente: bool,
    page: int,
) -> tuple[set[int], str]:
    params = _list_params(
        year,
        month=month,
        keyword=keyword,
        supplier_cnpj=supplier_cnpj,
        include_vigente=include_vigente,
        include_nao_vigente=include_nao_vigente,
        page=page,
    )
    r = session.get(ARP_LIST_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    ids = {int(x) for x in re.findall(r"/transparencia/arpshow/(\d+)/show", r.text)}
    return ids, r.text


def fetch_arp_detail(session: requests.Session, arp_id: int) -> dict[str, Any]:
    url = f"{BASE_URL}/transparencia/arpshow/{arp_id}/show"
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return parse_arp_detail(r.text, arp_id)


def fetch_item_detail(
    session: requests.Session, arp_id: int, item_numero: str
) -> dict[str, Any]:
    num = (item_numero or "").strip().zfill(5)
    url = f"{BASE_URL}/transparencia/arpshow/itens/{num}/{arp_id}/show"
    r = session.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return parse_item_detail(r.text)


def _enrich_items_with_suppliers(
    session: requests.Session,
    arp_id: int,
    items_adesao: list[dict[str, Any]],
    stats: ContratosGovScanStats,
    pause_sec: float,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for it in items_adesao:
        item = dict(it)
        numero = item.get("numero") or ""
        try:
            extra = fetch_item_detail(session, arp_id, numero)
            stats.item_details_fetched += 1
            item.update(extra)
        except requests.RequestException as exc:
            stats.errors.append(f"Item {numero} ata {arp_id}: {exc}")
        enriched.append(item)
        time.sleep(pause_sec)
    return enriched


def scan_atas_com_adesao(
    year: int,
    *,
    month: int | None = None,
    keyword: str | None = None,
    supplier_cnpj: str | None = None,
    include_vigente: bool = True,
    include_nao_vigente: bool = True,
    max_list_pages: int = 20,
    max_detail_checks: int = 150,
    enrich_suppliers: bool = True,
    pause_sec: float = PAUSE_SEC,
) -> tuple[list[dict[str, Any]], ContratosGovScanStats]:
    """
    Percorre a listagem pública e retorna atas com ao menos um item Aceita adesão = Sim.

    Período: ano inteiro ou mês específico (dataInicio/dataFim).
    Palavra-chave: enviada ao portal (palavra_chave).
    CNPJ fornecedor: enviado ao portal (cnpjFornecedor) e revalidado nos itens.
    """
    kw = (keyword or "").strip() or None
    cnpj = _normalize_supplier_cnpj(supplier_cnpj)
    if cnpj and not enrich_suppliers:
        enrich_suppliers = True
    stats = ContratosGovScanStats()
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "pt-BR,pt;q=0.9"})

    all_ids: list[int] = []
    seen: set[int] = set()
    max_list_pages = max(1, min(500, max_list_pages))
    max_detail_checks = max(1, min(5000, max_detail_checks))

    last_page_ids: set[int] = set()
    stopped_by_page_limit = False

    for page in range(1, max_list_pages + 1):
        try:
            ids, html = fetch_arp_list_page(
                session,
                year,
                month=month,
                keyword=kw,
                supplier_cnpj=cnpj,
                include_vigente=include_vigente,
                include_nao_vigente=include_nao_vigente,
                page=page,
            )
        except requests.RequestException as exc:
            stats.errors.append(f"Lista página {page}: {exc}")
            break
        stats.list_pages_read = page
        if stats.list_pages_total is None:
            stats.list_pages_total = parse_list_total_pages(html)
        last_page_ids = ids
        if not ids:
            stats.list_scan_complete = True
            break
        new_on_page = 0
        for arp_id in sorted(ids):
            if arp_id not in seen:
                seen.add(arp_id)
                all_ids.append(arp_id)
                new_on_page += 1
            else:
                stats.duplicates_skipped += 1
        if new_on_page == 0:
            stats.list_scan_complete = True
            break
        if page >= max_list_pages:
            stopped_by_page_limit = True
            break
        time.sleep(pause_sec)

    if not stopped_by_page_limit:
        stats.list_scan_complete = True
    elif stats.list_pages_total and stats.list_pages_read >= stats.list_pages_total:
        stats.list_scan_complete = True
    elif last_page_ids and len(last_page_ids) < ATAS_PER_LIST_PAGE:
        stats.list_scan_complete = True

    stats.atas_listed = len(all_ids)
    hits: list[dict[str, Any]] = []

    ids_to_check = all_ids[:max_detail_checks]
    if len(all_ids) > max_detail_checks:
        stats.detail_limit_hit = True

    for arp_id in ids_to_check:
        try:
            detail = fetch_arp_detail(session, arp_id)
        except requests.RequestException as exc:
            stats.errors.append(f"Ata {arp_id}: {exc}")
            stats.atas_checked += 1
            time.sleep(pause_sec)
            continue
        stats.atas_checked += 1
        if detail.get("aceita_adesao"):
            if enrich_suppliers and detail.get("items_adesao"):
                detail["items_adesao"] = _enrich_items_with_suppliers(
                    session,
                    arp_id,
                    detail["items_adesao"],
                    stats,
                    pause_sec,
                )
            if cnpj:
                detail["items_adesao"] = _filter_items_by_supplier_cnpj(
                    detail.get("items_adesao") or [], cnpj
                )
                if not detail["items_adesao"]:
                    time.sleep(pause_sec)
                    continue
            if items_adesao := detail.get("items_adesao"):
                descs = [
                    (it.get("descricao_detalhada") or it.get("descricao") or "").strip()
                    for it in items_adesao
                ]
                if any(descs):
                    detail["objeto"] = " · ".join(d for d in descs[:3] if d)[:500]
            stats.atas_with_adesao += 1
            hits.append(detail)
        time.sleep(pause_sec)

    return hits, stats
