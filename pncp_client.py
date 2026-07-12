"""
Cliente da API pública de consulta do PNCP (Portal Nacional de Contratações Públicas).

Documentação: https://pncp.gov.br/api/consulta/swagger-ui/index.html

GET /v1/atas — atas por período de vigência (parâmetros dataInicial/dataFinal no formato yyyymmdd).

Nota: não é o “Comprasnet” legado; é o cadastro nacional de atas aberto ao público.
A API não retorna preço unitário por item — apenas dados da ata (objeto, órgão, vigência).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import requests

PNCP_ATAS_URL = "https://pncp.gov.br/api/consulta/v1/atas"
PNCP_ATAS_ATUALIZACAO_URL = "https://pncp.gov.br/api/consulta/v1/atas/atualizacao"
PNCP_CONTRATOS_URL = "https://pncp.gov.br/api/consulta/v1/contratos"
PNCP_APP_ATAS_SEARCH = "https://pncp.gov.br/app/atas"
DEFAULT_UA = "ARPGOV/1.0"
REQUEST_TIMEOUT = 180
PNCP_APP_ATAS_BASE = "https://pncp.gov.br/app/atas/"

PNCP_QUERY_MODES = ("vigencia", "atualizacao")
PNCP_PNCP_API = "https://pncp.gov.br/api/pncp/v1"
PNCP_MODALIDADES_URL = f"{PNCP_PNCP_API}/modalidades"

PNCP_VIGENCIA_STATUS = ("vigente", "nao_vigente", "todos")
PNCP_PERMITE_ADESAO = ("sim", "nao", "todos")

PNCP_ESFERA_CHOICES = (
    ("F", "Federal"),
    ("E", "Estadual"),
    ("M", "Municipal"),
    ("D", "Distrital"),
)
PNCP_PODER_CHOICES = (
    ("E", "Executivo"),
    ("L", "Legislativo"),
    ("J", "Judiciário"),
)
PNCP_INSTRUMENTO_CHOICES = (
    (1, "Edital"),
    (2, "Aviso de Contratação Direta"),
    (3, "Ato que autoriza a Contratação Direta"),
    (4, "Edital de Chamamento Público"),
)

PNCP_ATAS_API_FILTERS = (
    ("cnpj", "Órgão gerenciador (CNPJ)"),
    ("codigoUnidadeAdministrativa", "Unidade administrativa"),
    ("dataInicial/dataFinal", "Período de vigência"),
    ("atualizacao", "Endpoint /atas/atualizacao (opcional)"),
)
PNCP_ATAS_FIELD_FILTERS = (
    ("possibilidadeAdesao", "Permite adesão"),
    ("vigenciaInicio/vigenciaFim", "Situação vigente"),
    ("anoAta", "Ano da ata"),
    ("objetoContratacao", "Termo de busca (pós-filtro)"),
)

_modalidades_cache: list[dict[str, Any]] | None = None


@dataclass
class PncpPortalFilters:
    vigencia_status: str = "vigente"
    permite_adesao: str = "sim"
    uf: str | None = None
    esfera_id: str | None = None
    poder_id: str | None = None
    codigo_unidade: str | None = None
    municipio_ibge: str | None = None
    modalidade_id: int | None = None
    instrumento_id: int | None = None

    def normalized(self) -> PncpPortalFilters:
        vig = self.vigencia_status if self.vigencia_status in PNCP_VIGENCIA_STATUS else "vigente"
        adesao = self.permite_adesao if self.permite_adesao in PNCP_PERMITE_ADESAO else "sim"
        uf = (self.uf or "").strip().upper()[:2] or None
        esfera = (self.esfera_id or "").strip().upper()[:2] or None
        poder = (self.poder_id or "").strip().upper()[:2] or None
        unidade = (self.codigo_unidade or "").strip()[:24] or None
        ibge = re.sub(r"\D", "", (self.municipio_ibge or "").strip())[:12] or None
        mod = self.modalidade_id
        inst = self.instrumento_id
        valid_esfera = {x[0] for x in PNCP_ESFERA_CHOICES}
        valid_poder = {x[0] for x in PNCP_PODER_CHOICES}
        if esfera not in valid_esfera:
            esfera = None
        if poder not in valid_poder:
            poder = None
        return PncpPortalFilters(
            vigencia_status=vig,
            permite_adesao=adesao,
            uf=uf,
            esfera_id=esfera,
            poder_id=poder,
            codigo_unidade=unidade,
            municipio_ibge=ibge,
            modalidade_id=mod if mod and mod > 0 else None,
            instrumento_id=inst if inst and inst > 0 else None,
        )

    def to_json_dict(self) -> dict[str, Any]:
        n = self.normalized()
        return {
            "vigencia_status": n.vigencia_status,
            "permite_adesao": n.permite_adesao,
            "uf": n.uf,
            "esfera_id": n.esfera_id,
            "poder_id": n.poder_id,
            "codigo_unidade": n.codigo_unidade,
            "municipio_ibge": n.municipio_ibge,
            "modalidade_id": n.modalidade_id,
            "instrumento_id": n.instrumento_id,
        }

    @classmethod
    def from_json_dict(cls, raw: dict[str, Any] | None) -> PncpPortalFilters:
        if not raw:
            return cls()
        mod = raw.get("modalidade_id")
        inst = raw.get("instrumento_id")
        try:
            mod_i = int(mod) if mod not in (None, "", 0) else None
        except (TypeError, ValueError):
            mod_i = None
        try:
            inst_i = int(inst) if inst not in (None, "", 0) else None
        except (TypeError, ValueError):
            inst_i = None
        return cls(
            vigencia_status=str(raw.get("vigencia_status") or "vigente"),
            permite_adesao=str(raw.get("permite_adesao") or "sim"),
            uf=raw.get("uf"),
            esfera_id=raw.get("esfera_id"),
            poder_id=raw.get("poder_id"),
            codigo_unidade=raw.get("codigo_unidade"),
            municipio_ibge=raw.get("municipio_ibge"),
            modalidade_id=mod_i,
            instrumento_id=inst_i,
        )

    def needs_org_lookup(self) -> bool:
        n = self.normalized()
        return bool(n.uf or n.municipio_ibge or n.esfera_id or n.poder_id)


class PncpOrgResolver:
    """Resolve UF, município, esfera e poder a partir do cache local e API de órgãos."""

    def __init__(
        self,
        units_by_key: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.units_by_key = units_by_key or {}
        self._org_api_cache: dict[str, dict[str, Any]] = {}
        self._cnpj_agg: dict[str, dict[str, Any]] = {}
        for (cnpj, _cod), unit in self.units_by_key.items():
            if len(cnpj) != 14:
                continue
            agg = self._cnpj_agg.setdefault(
                cnpj,
                {"uf": None, "municipio_ibge": None, "esfera_id": None, "poder_id": None},
            )
            for key in agg:
                if not agg.get(key) and unit.get(key):
                    agg[key] = unit[key]

    def prewarm_for_atas(self, atas: list[dict]) -> None:
        """Carrega esfera/poder dos órgãos em lote antes de filtrar a página."""
        cnpjs: set[str] = set()
        for ata in atas:
            cnpj = _org_cnpj_digits(str(ata.get("cnpjOrgao") or ""))
            if cnpj:
                cnpjs.add(cnpj)
        pending: list[str] = []
        for cnpj in cnpjs:
            agg = self._cnpj_agg.get(cnpj) or {}
            if agg.get("esfera_id") and agg.get("poder_id"):
                continue
            if cnpj in self._org_api_cache:
                continue
            pending.append(cnpj)
        if not pending:
            return
        workers = min(8, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self._org_meta, pending))

    @classmethod
    def from_unit_rows(cls, rows: list[Any]) -> PncpOrgResolver:
        units: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            cnpj = re.sub(r"\D", "", str(getattr(row, "cnpj", "") or ""))[:14]
            if len(cnpj) != 14:
                continue
            cod = str(getattr(row, "codigo_unidade", "") or "0000").strip()[:24] or "0000"
            units[(cnpj, cod)] = {
                "uf": ((getattr(row, "uf_sigla", None) or "")[:2] or None),
                "municipio_ibge": (
                    (str(getattr(row, "codigo_municipio_ibge", "") or "").strip()[:12])
                    or None
                ),
                "municipio_nome": ((getattr(row, "municipio_nome", None) or "")[:220] or None),
                "esfera_id": ((getattr(row, "esfera_id", None) or "")[:2] or None),
                "poder_id": ((getattr(row, "poder_id", None) or "")[:2] or None),
            }
        return cls(units)

    def context_for_ata(self, ata: dict) -> dict[str, Any]:
        cnpj = _org_cnpj_digits(str(ata.get("cnpjOrgao") or ""))
        cod = str(ata.get("codigoUnidadeOrgao") or "").strip()[:24] or "0000"
        ctx: dict[str, Any] = {
            "uf": None,
            "municipio_ibge": None,
            "esfera_id": None,
            "poder_id": None,
        }
        if not cnpj:
            return ctx
        unit = self.units_by_key.get((cnpj, cod)) or self.units_by_key.get((cnpj, "0000"))
        if unit:
            ctx.update({k: unit.get(k) for k in ctx})
        agg = self._cnpj_agg.get(cnpj) or {}
        for key in ctx:
            if not ctx.get(key) and agg.get(key):
                ctx[key] = agg[key]
        org_meta = self._org_meta(cnpj)
        if not ctx.get("esfera_id"):
            ctx["esfera_id"] = org_meta.get("esfera_id")
        if not ctx.get("poder_id"):
            ctx["poder_id"] = org_meta.get("poder_id")
        return ctx

    def _org_meta(self, cnpj: str) -> dict[str, Any]:
        if cnpj in self._org_api_cache:
            return self._org_api_cache[cnpj]
        meta: dict[str, Any] = {"esfera_id": None, "poder_id": None}
        try:
            r = requests.get(
                f"{PNCP_PNCP_API}/orgaos/{cnpj}",
                timeout=30,
                headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
            )
            if r.ok:
                j = r.json()
                meta["esfera_id"] = ((j.get("esferaId") or "")[:2] or None)
                meta["poder_id"] = ((j.get("poderId") or "")[:2] or None)
        except requests.RequestException:
            pass
        self._org_api_cache[cnpj] = meta
        return meta


def get_modalidades(*, refresh: bool = False) -> list[dict[str, Any]]:
    global _modalidades_cache
    if _modalidades_cache is not None and not refresh:
        return _modalidades_cache
    try:
        r = requests.get(
            PNCP_MODALIDADES_URL,
            timeout=60,
            headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
        )
        if r.ok and isinstance(r.json(), list):
            _modalidades_cache = r.json()
            return _modalidades_cache
    except requests.RequestException:
        pass
    _modalidades_cache = []
    return _modalidades_cache


def parse_portal_filters_from_form(form: Any) -> PncpPortalFilters:
    return PncpPortalFilters(
        vigencia_status=(form.get("pncp_vigencia_status") or "vigente").strip(),
        permite_adesao=(form.get("pncp_permite_adesao") or "sim").strip(),
        uf=(form.get("pncp_uf") or "").strip() or None,
        esfera_id=(form.get("pncp_esfera") or "").strip() or None,
        poder_id=(form.get("pncp_poder") or "").strip() or None,
        codigo_unidade=(form.get("pncp_codigo_unidade") or "").strip() or None,
        municipio_ibge=(form.get("pncp_municipio_ibge") or "").strip() or None,
    ).normalized()


@dataclass
class PncpAdesaoScanStats:
    pages_read: int = 0
    total_pages_api: int = 0
    rows_api: int = 0
    rows_matched: int = 0
    rows_adesao_true: int = 0
    rows_keyword: int = 0
    errors: list[str] = field(default_factory=list)


def pncp_ata_app_url(ctrl: str) -> str:
    return f"{PNCP_APP_ATAS_BASE}{ctrl}"


def _org_cnpj_digits(raw: str | None) -> str | None:
    s = re.sub(r"\D", "", (raw or "").strip())
    return s if len(s) == 14 else None


def build_pncp_app_atas_url(*, keyword: str | None = None, page: int = 1) -> str:
    """URL da busca pública no portal (mesma área de /app/atas)."""
    from urllib.parse import urlencode

    params: dict[str, str | int] = {}
    kw = (keyword or "").strip()
    if kw:
        params["q"] = kw[:200]
    if page and page > 1:
        params["pagina"] = page
    if not params:
        return PNCP_APP_ATAS_SEARCH
    return f"{PNCP_APP_ATAS_SEARCH}?{urlencode(params)}"


def _atas_list_params(
    data_inicial_yyyymmdd: str,
    data_final_yyyymmdd: str,
    pagina: int,
    tamanho_pagina: int,
    *,
    orgao_cnpj: str | None = None,
    codigo_unidade: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "dataInicial": data_inicial_yyyymmdd,
        "dataFinal": data_final_yyyymmdd,
        "pagina": pagina,
        "tamanhoPagina": max(10, min(500, tamanho_pagina)),
    }
    cnpj = _org_cnpj_digits(orgao_cnpj)
    if cnpj:
        params["cnpj"] = cnpj
    cu = (codigo_unidade or "").strip()
    if cu:
        params["codigoUnidadeAdministrativa"] = cu[:24]
    return params


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ata_vigencia_bucket(ata: dict, ref: date | None = None) -> str:
    ref = ref or date.today()
    if ata.get("cancelado"):
        return "nao_vigente"
    vi = _parse_iso_date(ata.get("vigenciaInicio"))
    vf = _parse_iso_date(ata.get("vigenciaFim"))
    if vi and ref < vi:
        return "nao_vigente"
    if vf and ref > vf:
        return "nao_vigente"
    return "vigente"


def _ata_passes_portal_filters(
    ata: dict,
    portal: PncpPortalFilters | None,
    org_resolver: PncpOrgResolver | None,
    *,
    ref_date: date | None = None,
) -> bool:
    if not portal:
        return True
    pf = portal.normalized()
    bucket = _ata_vigencia_bucket(ata, ref_date)
    if pf.vigencia_status == "vigente" and bucket != "vigente":
        return False
    if pf.vigencia_status == "nao_vigente" and bucket != "nao_vigente":
        return False
    pa = ata.get("possibilidadeAdesao")
    if pf.permite_adesao == "sim" and pa is not True:
        return False
    if pf.permite_adesao == "nao" and pa is not False:
        return False
    if pf.codigo_unidade:
        cod = str(ata.get("codigoUnidadeOrgao") or "").strip()[:24]
        if cod != pf.codigo_unidade:
            return False
    if not org_resolver:
        if pf.uf or pf.municipio_ibge or pf.esfera_id or pf.poder_id:
            return False
        return True
    ctx = org_resolver.context_for_ata(ata)
    if pf.uf and (ctx.get("uf") or "").upper() != pf.uf:
        return False
    if pf.municipio_ibge and (ctx.get("municipio_ibge") or "") != pf.municipio_ibge:
        return False
    if pf.esfera_id and (ctx.get("esfera_id") or "").upper() != pf.esfera_id:
        return False
    if pf.poder_id and (ctx.get("poder_id") or "").upper() != pf.poder_id:
        return False
    return True


def _ata_passes_robo_filters(
    ata: dict,
    *,
    keyword: str | None,
    ano_ata: int | None,
    only_possibilidade_adesao: bool,
    exclude_canceladas: bool,
    portal_filters: PncpPortalFilters | None = None,
    org_resolver: PncpOrgResolver | None = None,
    ref_date: date | None = None,
) -> bool:
    if exclude_canceladas and ata.get("cancelado"):
        return False
    if portal_filters:
        if not _ata_passes_portal_filters(
            ata, portal_filters, org_resolver, ref_date=ref_date
        ):
            return False
    elif only_possibilidade_adesao and ata.get("possibilidadeAdesao") is not True:
        return False
    if ano_ata is not None:
        try:
            if int(ata.get("anoAta") or 0) != ano_ata:
                return False
        except (TypeError, ValueError):
            return False
    return _ata_matches_keyword(ata, keyword)


def _ata_matches_keyword(ata: dict, keyword: str | None) -> bool:
    if not keyword:
        return True
    kw = keyword.strip().lower()
    if not kw:
        return True
    blob = " ".join(
        str(ata.get(k) or "")
        for k in (
            "objetoContratacao",
            "nomeOrgao",
            "nomeUnidadeOrgao",
            "numeroAtaRegistroPreco",
            "numeroControlePNCPAta",
        )
    ).lower()
    return kw in blob


def ata_para_hit_adesao(ata: dict) -> dict[str, Any] | None:
    if ata.get("cancelado"):
        return None
    ctrl = (ata.get("numeroControlePNCPAta") or "").strip()
    if not ctrl:
        return None
    objeto = (ata.get("objetoContratacao") or "").strip()
    org = (ata.get("nomeOrgao") or "").strip()
    unidade = (ata.get("nomeUnidadeOrgao") or "").strip()
    unidade_full = f"{org} — {unidade}".strip(" —") if unidade else org

    def _fmt_br(iso: str | None) -> str | None:
        if not iso:
            return None
        try:
            return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            return None

    compra = ctrl.split("/")[0] if "/" in ctrl else ctrl
    return {
        "arp_id": None,
        "pncp_control_id": ctrl[:220],
        "numero": (ata.get("numeroAtaRegistroPreco") or "")[:40] or None,
        "unidade": unidade_full[:300] if unidade_full else None,
        "compra_ano": compra[:40] if compra else None,
        "status": "Vigente" if ata.get("possibilidadeAdesao") else None,
        "valor_total": None,
        "vigencia_inicial": _fmt_br(ata.get("vigenciaInicio")),
        "vigencia_final": _fmt_br(ata.get("vigenciaFim")),
        "modalidade": None,
        "objeto": objeto[:4000] if objeto else None,
        "pncp_ata_url": pncp_ata_app_url(ctrl)[:300],
        "pncp_compra_url": None,
        "items_adesao": [],
        "aceita_adesao": ata.get("possibilidadeAdesao") is True,
        "detail_url": pncp_ata_app_url(ctrl),
        "possibilidade_adesao": ata.get("possibilidadeAdesao"),
        "cnpj_orgao": ata.get("cnpjOrgao"),
    }


def scan_atas_inteligente(
    data_inicial: date,
    data_final: date,
    *,
    keyword: str | None = None,
    orgao_cnpj: str | None = None,
    codigo_unidade: str | None = None,
    ano_ata: int | None = None,
    query_mode: str = "vigencia",
    only_possibilidade_adesao: bool = True,
    portal_filters: PncpPortalFilters | None = None,
    org_resolver: PncpOrgResolver | None = None,
    exclude_canceladas: bool = True,
    max_pages: int = 30,
    pause_sec: float = 0.15,
) -> tuple[list[dict[str, Any]], PncpAdesaoScanStats]:
    """
    Varredura via API PNCP (mesmos dados do portal /app/atas).

    query_mode:
      - vigencia: GET /v1/atas (período de vigência)
      - atualizacao: GET /v1/atas/atualizacao (alterações no período — mais eficiente)
    orgao_cnpj: CNPJ do órgão gerenciador (não é fornecedor).
    """
    stats = PncpAdesaoScanStats()
    hits: list[dict[str, Any]] = []
    max_pages = max(1, min(500, max_pages))
    mode = query_mode if query_mode in PNCP_QUERY_MODES else "vigencia"
    pf = portal_filters.normalized() if portal_filters else None
    unit_api = (codigo_unidade or (pf.codigo_unidade if pf else None) or "").strip()[:24] or None
    di = format_pncp_date(data_inicial)
    df = format_pncp_date(data_final)
    pagina = 1
    total_pages = 1
    if pf and pf.needs_org_lookup() and not org_resolver:
        stats.errors.append(
            "UF/município/esfera/poder: sincronize órgãos PNCP (Admin) para aplicar esses filtros."
        )

    while pagina <= total_pages and pagina <= max_pages:
        try:
            if mode == "atualizacao":
                payload = fetch_atas_atualizacao_page(
                    di,
                    df,
                    pagina,
                    tamanho_pagina=50,
                    orgao_cnpj=orgao_cnpj,
                    codigo_unidade=unit_api,
                )
            else:
                payload = fetch_atas_page(
                    di,
                    df,
                    pagina,
                    tamanho_pagina=50,
                    orgao_cnpj=orgao_cnpj,
                    codigo_unidade=unit_api,
                )
        except requests.RequestException as exc:
            stats.errors.append(f"PNCP página {pagina}: {exc}")
            break

        stats.pages_read = pagina
        total_pages = int(payload.get("totalPaginas") or 0)
        stats.total_pages_api = total_pages
        rows = payload.get("data") or []
        stats.rows_api += len(rows)

        if pf and pf.needs_org_lookup() and org_resolver:
            org_resolver.prewarm_for_atas(rows)

        for ata in rows:
            if ata.get("possibilidadeAdesao") is True:
                stats.rows_adesao_true += 1
            if not _ata_passes_robo_filters(
                ata,
                keyword=keyword,
                ano_ata=ano_ata,
                only_possibilidade_adesao=only_possibilidade_adesao,
                exclude_canceladas=exclude_canceladas,
                portal_filters=pf,
                org_resolver=org_resolver,
            ):
                continue
            if keyword:
                stats.rows_keyword += 1
            hit = ata_para_hit_adesao(ata)
            if hit:
                hits.append(hit)
                stats.rows_matched += 1

        if pagina >= total_pages:
            break
        pagina += 1
        if pause_sec > 0:
            time.sleep(pause_sec)

    return hits, stats


def format_pncp_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def fetch_atas_page(
    data_inicial_yyyymmdd: str,
    data_final_yyyymmdd: str,
    pagina: int,
    tamanho_pagina: int = 500,
    max_retries: int = 4,
    *,
    orgao_cnpj: str | None = None,
    codigo_unidade: str | None = None,
) -> dict[str, Any]:
    params = _atas_list_params(
        data_inicial_yyyymmdd,
        data_final_yyyymmdd,
        pagina,
        tamanho_pagina,
        orgao_cnpj=orgao_cnpj,
        codigo_unidade=codigo_unidade,
    )
    return _pncp_get_json(PNCP_ATAS_URL, params, max_retries)


def fetch_atas_atualizacao_page(
    data_inicial_yyyymmdd: str,
    data_final_yyyymmdd: str,
    pagina: int,
    tamanho_pagina: int = 50,
    max_retries: int = 4,
    *,
    orgao_cnpj: str | None = None,
    codigo_unidade: str | None = None,
) -> dict[str, Any]:
    params = _atas_list_params(
        data_inicial_yyyymmdd,
        data_final_yyyymmdd,
        pagina,
        tamanho_pagina,
        orgao_cnpj=orgao_cnpj,
        codigo_unidade=codigo_unidade,
    )
    return _pncp_get_json(PNCP_ATAS_ATUALIZACAO_URL, params, max_retries)


def _pncp_get_json(url: str, params: dict[str, Any], max_retries: int) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
            )
            if r.status_code == 204:
                return {"data": [], "totalPaginas": 0, "totalRegistros": 0}
            if r.status_code >= 500 or r.status_code == 429:
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_exc = e
            time.sleep(1.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def fetch_contratos_page(
    data_inicial_yyyymmdd: str,
    data_final_yyyymmdd: str,
    pagina: int,
    tamanho_pagina: int = 10,
    max_retries: int = 4,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(
                PNCP_CONTRATOS_URL,
                params={
                    "dataInicial": data_inicial_yyyymmdd,
                    "dataFinal": data_final_yyyymmdd,
                    "pagina": pagina,
                    "tamanhoPagina": max(10, min(500, tamanho_pagina)),
                },
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
            )
            if r.status_code == 204:
                return {"data": [], "totalPaginas": 0, "totalRegistros": 0}
            if r.status_code >= 500 or r.status_code == 429:
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last_exc = e
            time.sleep(1.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def parse_vigencia_inicio(ata: dict) -> date | None:
    v = ata.get("vigenciaInicio")
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def ata_para_rascunho_catalogo(
    ata: dict,
    section: str = "PNCP — importação",
    title_max: int = 300,
) -> dict[str, Any] | None:
    if ata.get("cancelado"):
        return None
    ctrl = ata.get("numeroControlePNCPAta") or ""
    if not ctrl:
        return None
    objeto = (ata.get("objetoContratacao") or "").strip()
    num_ata = (ata.get("numeroAtaRegistroPreco") or "").strip()
    if objeto:
        title = objeto if len(objeto) <= title_max else objeto[: title_max - 1] + "…"
    else:
        title = f"ARP {num_ata or ctrl}"[:title_max]
    org = (ata.get("nomeOrgao") or "Órgão não informado")[:80]
    vig_fim = ata.get("vigenciaFim")
    valid_until = None
    if vig_fim and isinstance(vig_fim, str):
        try:
            valid_until = datetime.strptime(vig_fim[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    slug_seed = ctrl.replace("/", "-")
    return {
        "pncp_id": ctrl[:200],
        "title": title,
        "section": (section or "PNCP — importação")[:80],
        "sphere": org,
        "quantity": 1,
        "unit_price": Decimal("0"),
        "valid_until": valid_until,
        "slug_seed": slug_seed,
        "numero_ata": num_ata,
    }


def importar_atas(
    data_inicial: date,
    data_final: date,
    *,
    only_vigencia_inicio_year: int | None,
    max_pages: int,
    pause_sec: float,
    on_page: Any | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """
    Retorna (rascunhos com slug_seed / pncp_id, estatísticas).
    O slug final deve ser resolvido na gravação (evita colisões entre páginas).
    on_page opcional: callback(pagina, total_paginas, acumulado_filtrado).
    """
    di = format_pncp_date(data_inicial)
    df = format_pncp_date(data_final)
    out: list[dict] = []
    stats = {"pages_read": 0, "rows_api": 0, "rows_after_filter": 0, "total_pages_api": 0}

    pagina = 1
    total_pages = 1

    while pagina <= total_pages and pagina <= max_pages:
        payload = fetch_atas_page(di, df, pagina)
        stats["pages_read"] += 1
        total_pages = int(payload.get("totalPaginas") or 0)
        stats["total_pages_api"] = total_pages
        rows = payload.get("data") or []
        stats["rows_api"] += len(rows)

        for ata in rows:
            if only_vigencia_inicio_year is not None:
                vi = parse_vigencia_inicio(ata)
                if vi is None or vi.year != only_vigencia_inicio_year:
                    continue
            draft = ata_para_rascunho_catalogo(ata)
            if not draft:
                continue
            out.append(draft)
            stats["rows_after_filter"] += 1

        if on_page:
            on_page(pagina, total_pages, len(out))

        if pagina >= total_pages:
            break
        pagina += 1
        if pause_sec > 0:
            time.sleep(pause_sec)

    return out, stats


def _digits_cnpj_14(raw: str | None) -> str | None:
    s = re.sub(r"\D", "", str(raw or ""))[:14]
    return s if len(s) == 14 else None


def contrato_para_org_payload(contrato: dict) -> dict[str, Any] | None:
    """Extrai órgão + unidade de um registro da API de contratos."""
    org = contrato.get("orgaoEntidade") or {}
    uo = contrato.get("unidadeOrgao") or {}
    cnpj = _digits_cnpj_14(org.get("cnpj"))
    if not cnpj:
        return None
    cod = str(uo.get("codigoUnidade") or "").strip() or "0000"
    cod = cod[:24]
    return {
        "cnpj": cnpj,
        "codigo_unidade": cod,
        "razao_social": (org.get("razaoSocial") or "Órgão")[:320],
        "nome_unidade": ((uo.get("nomeUnidade") or "")[:420] or None),
        "uf_sigla": ((uo.get("ufSigla") or "")[:2] or None),
        "municipio_nome": ((uo.get("municipioNome") or "")[:220] or None),
        "codigo_municipio_ibge": (str(uo.get("codigoIbge") or "").strip()[:12] or None),
        "esfera_id": ((org.get("esferaId") or "")[:2] or None),
        "poder_id": ((org.get("poderId") or "")[:2] or None),
        "fonte": "contratos",
    }


def ata_para_org_payload(ata: dict) -> dict[str, Any] | None:
    """Extrai órgão + unidade de uma ata (menos campos de localização)."""
    cnpj = _digits_cnpj_14(ata.get("cnpjOrgao"))
    if not cnpj:
        return None
    cod = str(ata.get("codigoUnidadeOrgao") or "").strip() or "0000"
    cod = cod[:24]
    return {
        "cnpj": cnpj,
        "codigo_unidade": cod,
        "razao_social": (ata.get("nomeOrgao") or "Órgão")[:320],
        "nome_unidade": ((ata.get("nomeUnidadeOrgao") or "")[:420] or None),
        "uf_sigla": None,
        "municipio_nome": None,
        "codigo_municipio_ibge": None,
        "esfera_id": None,
        "poder_id": None,
        "fonte": "atas",
    }


def coletar_org_payloads_contratos(
    data_inicial: date,
    data_final: date,
    *,
    max_pages: int,
    pause_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    di = format_pncp_date(data_inicial)
    df = format_pncp_date(data_final)
    out: list[dict[str, Any]] = []
    stats = {"pages_read": 0, "rows_api": 0, "units": 0}
    pagina = 1
    total_pages = 1
    while pagina <= total_pages and pagina <= max_pages:
        payload = fetch_contratos_page(di, df, pagina, 10)
        stats["pages_read"] += 1
        total_pages = int(payload.get("totalPaginas") or 0)
        rows = payload.get("data") or []
        stats["rows_api"] += len(rows)
        for c in rows:
            p = contrato_para_org_payload(c)
            if p:
                out.append(p)
                stats["units"] += 1
        if pagina >= total_pages:
            break
        pagina += 1
        if pause_sec > 0:
            time.sleep(pause_sec)
    return out, stats


def coletar_org_payloads_atas(
    data_inicial: date,
    data_final: date,
    *,
    max_pages: int,
    pause_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    di = format_pncp_date(data_inicial)
    df = format_pncp_date(data_final)
    out: list[dict[str, Any]] = []
    stats = {"pages_read": 0, "rows_api": 0, "units": 0}
    pagina = 1
    total_pages = 1
    while pagina <= total_pages and pagina <= max_pages:
        payload = fetch_atas_page(di, df, pagina, 50)
        stats["pages_read"] += 1
        total_pages = int(payload.get("totalPaginas") or 0)
        rows = payload.get("data") or []
        stats["rows_api"] += len(rows)
        for ata in rows:
            p = ata_para_org_payload(ata)
            if p:
                out.append(p)
                stats["units"] += 1
        if pagina >= total_pages:
            break
        pagina += 1
        if pause_sec > 0:
            time.sleep(pause_sec)
    return out, stats
