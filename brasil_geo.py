"""
Dados auxiliares: região por UF e população estimada por UF (base Censo IBGE / projeções, ordem de grandeza oficial).
Usado no diretório de órgãos públicos para filtros e totais regionais.
"""

from __future__ import annotations

# Macrorregiões do IBGE
REGIOES_BR = ("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")

UF_PARA_REGIAO: dict[str, str] = {
    "AC": "Norte",
    "AM": "Norte",
    "AP": "Norte",
    "PA": "Norte",
    "RO": "Norte",
    "RR": "Norte",
    "TO": "Norte",
    "AL": "Nordeste",
    "BA": "Nordeste",
    "CE": "Nordeste",
    "MA": "Nordeste",
    "PB": "Nordeste",
    "PE": "Nordeste",
    "PI": "Nordeste",
    "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste",
    "GO": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "MT": "Centro-Oeste",
    "ES": "Sudeste",
    "MG": "Sudeste",
    "RJ": "Sudeste",
    "SP": "Sudeste",
    "PR": "Sul",
    "RS": "Sul",
    "SC": "Sul",
}

# População residente por UF (referência próxima ao Censo 2022 / contagem IBGE, em habitantes).
# Atualizado em runtime após sincronização IBGE (sincronizar população no painel).
ANO_REFERENCIA_POPULACAO_IBGE: int | None = None

POPULACAO_UF: dict[str, int] = {
    "AC": 830_526,
    "AL": 3_365_351,
    "AP": 877_613,
    "AM": 4_212_164,
    "BA": 14_130_634,
    "CE": 9_248_580,
    "DF": 2_817_381,
    "ES": 3_833_698,
    "GO": 7_206_589,
    "MA": 7_094_930,
    "MT": 3_836_399,
    "MS": 2_839_184,
    "MG": 21_292_666,
    "PA": 8_777_124,
    "PB": 4_060_518,
    "PR": 11_556_093,
    "PE": 9_670_742,
    "PI": 3_283_290,
    "RJ": 17_463_349,
    "RN": 3_666_865,
    "RS": 10_859_454,
    "RO": 1_815_278,
    "RR": 538_237,
    "SC": 7_610_369,
    "SP": 46_649_132,
    "SE": 2_417_678,
    "TO": 1_590_248,
}


def regiao_de_uf(uf: str | None) -> str | None:
    if not uf or len(uf) != 2:
        return None
    return UF_PARA_REGIAO.get(uf.upper())


def populacao_uf(uf: str | None) -> int | None:
    if not uf or len(uf) != 2:
        return None
    return POPULACAO_UF.get(uf.upper())


def populacao_total_regiao(regiao: str | None) -> int | None:
    if not regiao or regiao not in REGIOES_BR:
        return None
    return sum(pop for uf, pop in POPULACAO_UF.items() if UF_PARA_REGIAO.get(uf) == regiao)


def atualizar_populacao_uf(novos: dict[str, int], ano: int | None = None) -> None:
    """Mescla totais por UF vindos do IBGE (estimativa residente) e registra o ano de referência."""
    global ANO_REFERENCIA_POPULACAO_IBGE
    POPULACAO_UF.update(novos)
    if ano is not None:
        ANO_REFERENCIA_POPULACAO_IBGE = int(ano)
