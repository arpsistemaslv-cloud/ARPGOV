"""
Estimativa de potencial de orçamento público anual (ordem de grandeza para prospecção B2G).

Não substitui LOA/LDO oficiais: coeficientes são referências agregadas (per capita e faixas por esfera).
Ajuste os valores em *_PER_CAPITA_* conforme sua base de negócio.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


# R$/hab/ano — referência agregada de despesas correntes municipais (ordem de grandeza).
PER_CAPITA_ORCAMENTO_MUNICIPAL = Decimal("8800")

# Ponderação sobre a população da UF para o executivo estadual.
PER_CAPITA_ORCAMENTO_ESTADUAL = Decimal("7500")

# Participação média estimada em autarquias estaduais / segurança sobre “massa” estadual.
FATOR_AUTARQUIA_ESTADUAL = Decimal("0.08")
FATOR_SEGURANCA_POR_POP_UF = Decimal("420")

# Instituições federais de ensino (por UF) e IFs (valor-base quando não há pop municipal).
ORCAMENTO_BASE_IF_BRL = Decimal("95000000")
ORCAMENTO_UF_FEDERAL_POR_HAB = Decimal("3800")

# Órgãos federais em Brasília (faixas por tipo).
ORCAMENTO_MINISTERIO_ESTIMADO = Decimal("1800000000")
ORCAMENTO_AUTARQUIA_FEDERAL_BASE = Decimal("450000000")
ORCAMENTO_ORGAO_JURIDICO_SUPERIOR = Decimal("350000000")
ORCAMENTO_JUSTICA_TRABALHO_TRT = Decimal("85000000")
ORCAMENTO_MPT_REGIONAL = Decimal("42000000")
ORCAMENTO_LEGISLATIVO_FEDERAL = Decimal("6000000000")
ORCAMENTO_LEGISLATIVO_ESTADUAL = Decimal("280000000")
ORCAMENTO_PNCP_SEM_DADO = None


def estimar_potencial_orcamento_anual(org: Any, pop_uf: int | None) -> tuple[Decimal | None, str]:
    """
    Retorna (valor BRL/año ou None, código curto do método).
    org: instância BrOrgaoPublico com tipo, uf, chave_externa, populacao_ibge, populacao_local.
    pop_uf: população IBGE da UF quando aplicável (para linhas sem pop própria).
    """
    tipo = (org.tipo or "").strip()
    uf = (org.uf or "").strip().upper()
    chave = (org.chave_externa or "").strip()
    pop = getattr(org, "populacao_ibge", None)
    if pop is None:
        pop = getattr(org, "populacao_local", None)
    if pop is None and pop_uf is not None and tipo in (
        "orgao_estadual",
        "autarquia_estadual",
        "seguranca_publica",
        "educacao_instituicoes",
    ):
        pop = pop_uf

    if tipo == "prefeitura" and pop and pop > 0:
        return (Decimal(pop) * PER_CAPITA_ORCAMENTO_MUNICIPAL, "per_capita_municipal")

    if tipo == "orgao_estadual" and pop and pop > 0:
        return (Decimal(pop) * PER_CAPITA_ORCAMENTO_ESTADUAL, "per_capita_estadual")

    if tipo == "autarquia_estadual" and pop and pop > 0:
        x = Decimal(pop) * PER_CAPITA_ORCAMENTO_ESTADUAL * FATOR_AUTARQUIA_ESTADUAL
        return (x, "fat_autarquia_estadual")

    if tipo == "seguranca_publica" and pop and pop > 0:
        return (Decimal(pop) * FATOR_SEGURANCA_POR_POP_UF, "seguranca_por_pop")

    if tipo == "educacao_instituicoes":
        if chave.startswith("edu:uf:") and pop and pop > 0:
            return (Decimal(pop) * ORCAMENTO_UF_FEDERAL_POR_HAB, "edu_uf_per_capita")
        if chave.startswith("edu:if:"):
            return (ORCAMENTO_BASE_IF_BRL, "edu_if_base")
        if chave.startswith("edu:org:"):
            return (Decimal("250000000"), "org_mec_base")
        if chave.startswith("edu:estadual:") and pop and pop > 0:
            return (Decimal(pop) * Decimal("25"), "edu_estadual_guia")

    if tipo == "federal_executivo" and uf == "DF":
        return (ORCAMENTO_MINISTERIO_ESTIMADO, "ministerio_df")

    if tipo == "autarquia_federal" and uf == "DF":
        return (ORCAMENTO_AUTARQUIA_FEDERAL_BASE, "autarquia_federal_df")

    if tipo == "orgao_juridico":
        if chave.startswith("jud:TRF"):
            return (ORCAMENTO_ORGAO_JURIDICO_SUPERIOR * Decimal("0.22"), "trf")
        if chave.startswith("jud:TJ") or chave.startswith("jud:TJDFT"):
            return (ORCAMENTO_ORGAO_JURIDICO_SUPERIOR * Decimal("0.35"), "tj")
        if chave.startswith(("jud:STF", "jud:STJ", "jud:TST", "jud:TSE", "jud:STM")):
            return (ORCAMENTO_ORGAO_JURIDICO_SUPERIOR, "superior")
        if chave.startswith("jud:CNJ") or chave.startswith("jud:CNMP"):
            return (Decimal("450000000"), "conselho_sup")
        if chave.startswith("jud:TCU"):
            return (Decimal("900000000"), "tcu")
        if chave.startswith("jud:DPU") or chave.startswith("jud:MPF"):
            return (Decimal("380000000"), "mp_defensoria")
        if chave.startswith("jud:MPE:"):
            return (Decimal("120000000"), "mpe")

    if tipo == "justica_trabalho":
        if chave.startswith("trt:"):
            return (ORCAMENTO_JUSTICA_TRABALHO_TRT, "trt")
        if chave.startswith("mpt:PRT:"):
            return (ORCAMENTO_MPT_REGIONAL, "prt")
        if chave.startswith("mpt:PGT"):
            return (Decimal("220000000"), "pgt")

    if tipo == "orgao_legislativo":
        if chave in ("leg:cdep", "leg:senado"):
            return (ORCAMENTO_LEGISLATIVO_FEDERAL, "congresso")
        if chave and chave.startswith("leg:"):
            return (ORCAMENTO_LEGISLATIVO_ESTADUAL, "legislativo_estadual")

    if tipo == "sistema_s" and pop and pop > 0:
        return (Decimal(pop) * Decimal("35"), "sistema_s")

    if tipo == "servico_aprendizagem" and pop and pop > 0:
        return (Decimal(pop) * Decimal("40"), "svc_aprend")

    if tipo == "pncp":
        return (ORCAMENTO_PNCP_SEM_DADO, "sem_modelo")

    if pop and pop > 0:
        return (Decimal(pop) * Decimal("2000"), "fallback_per_capita")

    return (None, "sem_pop")
