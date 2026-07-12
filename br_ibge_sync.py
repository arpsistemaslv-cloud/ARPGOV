"""
Sincronização de população (IBGE — estimativa residente) e recálculo de potencial orçamentário.
"""

from __future__ import annotations

import time
from typing import Any, Type

import requests

from br_orcamento_potencial import estimar_potencial_orcamento_anual
from brasil_geo import atualizar_populacao_uf

IBGE_SIDRA_6579 = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/{ano}/variaveis/9324"
)
IBGE_ESTADOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados"
REQUEST_TIMEOUT = 120
ANO_PADRAO = 2024
BATCH_MUNICIPIOS = 80


def _get_session() -> requests.Session:
    s = requests.Session()
    # IBGE pode responder 400 se User-Agent tiver caracteres não-ASCII.
    s.headers.update({"User-Agent": "ARPGOV/1.0"})
    return s


def _parse_sidra_series(data: list, ano: int) -> dict[str, int]:
    """localidade id (str) -> população int."""
    out: dict[str, int] = {}
    a = str(ano)
    a_prev = str(ano - 1)
    for bloco in data:
        for res in bloco.get("resultados") or []:
            for ser in res.get("series") or []:
                loc = ser.get("localidade") or {}
                lid = str(loc.get("id") or "")
                serie = ser.get("serie") or {}
                val = serie.get(a) or serie.get(a_prev)
                if lid and val is not None:
                    try:
                        out[lid] = int(str(val).replace(".", "").replace(",", ""))
                    except ValueError:
                        pass
    return out


def _map_uf_id_para_sigla(sess: requests.Session) -> dict[str, str]:
    r = sess.get(IBGE_ESTADOS_URL, timeout=60)
    r.raise_for_status()
    estados = r.json()
    return {str(e["id"]): e["sigla"] for e in estados}


def fetch_populacao_ufs(sess: requests.Session, ano: int = ANO_PADRAO) -> dict[str, int]:
    """Retorna {sigla UF: população}."""
    map_id_sigla = _map_uf_id_para_sigla(sess)
    ids = sorted(map_id_sigla.keys(), key=lambda x: int(x))
    url = (
        IBGE_SIDRA_6579.format(ano=ano)
        + f"?localidades=N3[{','.join(ids)}]"
    )
    r = sess.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    raw = r.json()
    por_id = _parse_sidra_series(raw, ano)
    por_sigla: dict[str, int] = {}
    for uid, pop in por_id.items():
        sig = map_id_sigla.get(uid)
        if sig:
            por_sigla[sig] = pop
    return por_sigla


def fetch_populacao_municipios_lote(
    sess: requests.Session, ids: list[str], ano: int = ANO_PADRAO
) -> dict[str, int]:
    if not ids:
        return {}
    url = (
        IBGE_SIDRA_6579.format(ano=ano)
        + f"?localidades=N6[{','.join(ids)}]"
    )
    r = sess.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return _parse_sidra_series(r.json(), ano)


def sincronizar_populacao_e_potencial(
    db: Any,
    BrOrgaoPublico: Type[Any],
    *,
    ano: int = ANO_PADRAO,
) -> tuple[int, int, str | None]:
    """
    Atualiza POPULACAO_UF em brasil_geo, populacao_ibge nos registros,
    potencial_orcamento_anual_brl e campos auxiliares.
    Retorna (linhas_atualizadas, linhas_orcamento, erro_opcional).
    """
    sess = _get_session()
    try:
        pop_por_uf = fetch_populacao_ufs(sess, ano=ano)
    except Exception as exc:
        return 0, 0, str(exc)

    atualizar_populacao_uf(pop_por_uf, ano=ano)

    atualizadas = 0
    # Estados (chave estado:UF)
    for row in BrOrgaoPublico.query.filter_by(tipo="orgao_estadual").all():
        u = (row.uf or "").upper()
        if u and u in pop_por_uf:
            row.populacao_ibge = pop_por_uf[u]
            row.populacao_local = pop_por_uf[u]
            row.ano_referencia_pop_ibge = ano
            atualizadas += 1

    # Municípios (prefeituras)
    rows_por_mid: dict[str, Any] = {}
    for row in (
        BrOrgaoPublico.query.filter(BrOrgaoPublico.tipo == "prefeitura")
        .filter(BrOrgaoPublico.ibge_municipio_id.isnot(None))
        .all()
    ):
        mid = str(row.ibge_municipio_id or "").strip()
        if len(mid) == 7:
            rows_por_mid[mid] = row

    mids_unicos = list(rows_por_mid.keys())
    for i in range(0, len(mids_unicos), BATCH_MUNICIPIOS):
        lote = mids_unicos[i : i + BATCH_MUNICIPIOS]
        if not lote:
            continue
        try:
            pop_map = fetch_populacao_municipios_lote(sess, lote, ano=ano)
        except Exception:
            time.sleep(0.5)
            pop_map = fetch_populacao_municipios_lote(sess, lote, ano=ano)
        for mid, pop in pop_map.items():
            row = rows_por_mid.get(mid)
            if row:
                row.populacao_ibge = pop
                row.populacao_local = pop
                row.ano_referencia_pop_ibge = ano
                atualizadas += 1
        time.sleep(0.08)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return atualizadas, 0, str(exc)

    # Recalcular potencial para todos os órgãos
    orc_n = 0
    for row in BrOrgaoPublico.query.all():
        u = (row.uf or "").upper()
        pop_uf = pop_por_uf.get(u) if u else None
        val, metodo = estimar_potencial_orcamento_anual(row, pop_uf)
        row.potencial_orcamento_anual_brl = val
        row.orcamento_metodo = metodo
        orc_n += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return atualizadas, orc_n, str(exc)

    return atualizadas, orc_n, None
