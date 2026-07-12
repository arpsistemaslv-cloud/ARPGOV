"""
Agrega dados da API pública de consulta do PNCP para o painel do cliente.

A API /v1/contratos expõe valor e categorias; não há campo explícito de modalidade
(pregão, dispensa, etc.). Usamos categorias de processo, tipo de instrumento e menções
no texto do objeto como referência.
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

import requests

from pncp_client import PNCP_CONTRATOS_URL, PNCP_ATAS_URL, format_pncp_date

DEFAULT_UA = "ARPGOV/1.0"
REQUEST_TIMEOUT = 120

# Palavras normalizadas (sem acento) para contar menções no objeto do contrato.
OBJETO_KEYWORDS = (
    ("pregao", "Pregão"),
    ("dispensa", "Dispensa"),
    ("inexigibilidade", "Inexigibilidade"),
    ("concorrencia", "Concorrência"),
    ("convite", "Convite"),
    ("credenciamento", "Credenciamento"),
    ("chamamento publico", "Chamamento público"),
    ("regime diferenciado", "Regime diferenciado (RDC)"),
)


def _strip_accents(s: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _money(c: dict[str, Any]) -> Decimal:
    v = c.get("valorGlobal")
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _fetch_contratos_page(
    di: str, df: str, pagina: int, tamanho: int
) -> dict[str, Any]:
    r = requests.get(
        PNCP_CONTRATOS_URL,
        params={
            "dataInicial": di,
            "dataFinal": df,
            "pagina": pagina,
            "tamanhoPagina": max(10, min(500, tamanho)),
        },
        timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
    )
    if r.status_code == 204:
        return {"data": [], "totalPaginas": 0, "totalRegistros": 0}
    r.raise_for_status()
    return r.json()


def _fetch_atas_page(di: str, df: str, pagina: int, tamanho: int) -> dict[str, Any]:
    r = requests.get(
        PNCP_ATAS_URL,
        params={
            "dataInicial": di,
            "dataFinal": df,
            "pagina": pagina,
            "tamanhoPagina": max(10, min(500, tamanho)),
        },
        timeout=REQUEST_TIMEOUT,
        headers={"Accept": "application/json", "User-Agent": DEFAULT_UA},
    )
    if r.status_code == 204:
        return {"data": [], "totalPaginas": 0, "totalRegistros": 0}
    r.raise_for_status()
    return r.json()


def coletar_resumo(
    data_inicio: date,
    data_fim: date,
    *,
    max_pages_contratos: int = 25,
    max_pages_atas: int = 15,
    page_size: int = 500,
    pause_sec: float = 0.12,
) -> dict[str, Any]:
    """
    Percorre páginas da API até os limites. Retorna estrutura pronta para gravar em PncpMercadoSnapshot.
    """
    if data_fim < data_inicio:
        data_inicio, data_fim = data_fim, data_inicio

    di = format_pncp_date(data_inicio)
    df = format_pncp_date(data_fim)

    cat_val: dict[str, dict[str, Any]] = {}
    tipo_val: dict[str, dict[str, Any]] = {}
    esfera_val: dict[str, dict[str, Any]] = {}
    kw_counts: dict[str, int] = {label: 0 for _, label in OBJETO_KEYWORDS}

    valor_despesa = Decimal("0")
    valor_receita = Decimal("0")
    contratos_proc = 0
    atas_proc = 0
    contratos_total_api: int | None = None
    atas_total_api: int | None = None
    amostra_incompleta = False
    err: str | None = None

    # — Contratos —
    try:
        pagina = 1
        total_pages = 1
        while pagina <= total_pages and pagina <= max(1, max_pages_contratos):
            payload = _fetch_contratos_page(di, df, pagina, page_size)
            if contratos_total_api is None:
                contratos_total_api = int(payload.get("totalRegistros") or 0)
            total_pages = int(payload.get("totalPaginas") or 0)
            rows = payload.get("data") or []
            for c in rows:
                contratos_proc += 1
                m = _money(c)
                rec = bool(c.get("receita"))
                if rec:
                    valor_receita += m
                else:
                    valor_despesa += m

                cat = c.get("categoriaProcesso") or {}
                cid = str(cat.get("id") or "")
                cname = (cat.get("nome") or "Sem categoria").strip() or "Sem categoria"
                key = f"{cid}:{cname}"
                cid_num = None
                if str(cat.get("id") or "").isdigit():
                    cid_num = int(cat["id"])
                if key not in cat_val:
                    cat_val[key] = {
                        "id": cid_num,
                        "nome": cname,
                        "qtd": 0,
                        "valor": Decimal("0"),
                    }
                cat_val[key]["qtd"] += 1
                if not rec:
                    cat_val[key]["valor"] += m

                tip = c.get("tipoContrato") or {}
                tname = (tip.get("nome") or "—").strip() or "—"
                if tname not in tipo_val:
                    tipo_val[tname] = {"qtd": 0, "valor": Decimal("0")}
                tipo_val[tname]["qtd"] += 1
                if not rec:
                    tipo_val[tname]["valor"] += m

                org = c.get("orgaoEntidade") or {}
                eid = (org.get("esferaId") or "?")[:2]
                labels = {"F": "Federal", "E": "Estadual", "M": "Municipal", "D": "Distrital"}
                ename = labels.get(eid, eid or "N/D")
                if ename not in esfera_val:
                    esfera_val[ename] = {"qtd": 0, "valor": Decimal("0")}
                esfera_val[ename]["qtd"] += 1
                if not rec:
                    esfera_val[ename]["valor"] += m

                obj = _strip_accents((c.get("objetoContrato") or "") + " ")
                for needle, label in OBJETO_KEYWORDS:
                    if needle in obj:
                        kw_counts[label] += 1

            if pagina >= total_pages:
                break
            pagina += 1
            if pause_sec > 0:
                time.sleep(pause_sec)

        if contratos_total_api and contratos_proc < contratos_total_api:
            amostra_incompleta = True
    except (requests.RequestException, ValueError, TypeError) as e:
        err = str(e)[:2000]
        amostra_incompleta = True

    # — Atas de registro de preço (contagem; valores não vêm na listagem) —
    try:
        pagina = 1
        total_pages = 1
        while pagina <= total_pages and pagina <= max(1, max_pages_atas):
            payload = _fetch_atas_page(di, df, pagina, page_size)
            if atas_total_api is None:
                atas_total_api = int(payload.get("totalRegistros") or 0)
            total_pages = int(payload.get("totalPaginas") or 0)
            rows = payload.get("data") or []
            for ata in rows:
                if ata.get("cancelado"):
                    continue
                atas_proc += 1
            if pagina >= total_pages:
                break
            pagina += 1
            if pause_sec > 0:
                time.sleep(pause_sec)

        if atas_total_api is not None and atas_proc < atas_total_api:
            amostra_incompleta = True
    except (requests.RequestException, ValueError, TypeError) as e2:
        if err:
            err = err + " | Atas: " + str(e2)[:500]
        else:
            err = str(e2)[:2000]
        amostra_incompleta = True

    def _ser_cat() -> list[dict[str, Any]]:
        rows = []
        for _k, v in sorted(
            cat_val.items(), key=lambda x: x[1]["valor"], reverse=True
        ):
            rows.append(
                {
                    "id": v["id"],
                    "nome": v["nome"],
                    "qtd": v["qtd"],
                    "valor": float(v["valor"]),
                }
            )
        return rows

    def _ser_simple(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for nome, v in sorted(d.items(), key=lambda x: x[1]["valor"], reverse=True):
            out.append(
                {
                    "nome": nome,
                    "qtd": v["qtd"],
                    "valor": float(v["valor"]),
                }
            )
        return out

    kw_out = [{"nome": k, "qtd": v} for k, v in kw_counts.items() if v > 0]
    kw_out.sort(key=lambda x: x["qtd"], reverse=True)

    return {
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "contratos_total_api": contratos_total_api,
        "contratos_processados": contratos_proc,
        "atas_total_api": atas_total_api,
        "atas_processadas": atas_proc,
        "valor_contratos_despesa": valor_despesa,
        "valor_contratos_receita": valor_receita,
        "amostra_incompleta": amostra_incompleta,
        "json_categorias": json.dumps(_ser_cat(), ensure_ascii=False),
        "json_tipos_contrato": json.dumps(_ser_simple(tipo_val), ensure_ascii=False),
        "json_esfera": json.dumps(_ser_simple(esfera_val), ensure_ascii=False),
        "json_keywords_objeto": json.dumps(kw_out, ensure_ascii=False),
        "erro": err,
    }
