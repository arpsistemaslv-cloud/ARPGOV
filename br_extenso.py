"""Valores monetários por extenso em português do Brasil (reais e centavos)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_UNIDADES = (
    "zero",
    "um",
    "dois",
    "três",
    "quatro",
    "cinco",
    "seis",
    "sete",
    "oito",
    "nove",
    "dez",
    "onze",
    "doze",
    "treze",
    "quatorze",
    "quinze",
    "dezesseis",
    "dezessete",
    "dezoito",
    "dezenove",
)

_DEZENAS = (
    "",
    "",
    "vinte",
    "trinta",
    "quarenta",
    "cinquenta",
    "sessenta",
    "setenta",
    "oitenta",
    "noventa",
)

_CENTENAS = (
    "",
    "cento",
    "duzentos",
    "trezentos",
    "quatrocentos",
    "quinhentos",
    "seiscentos",
    "setecentos",
    "oitocentos",
    "novecentos",
)


def _extenso_0_999(n: int) -> str:
    if n < 0 or n > 999:
        raise ValueError(n)
    if n == 0:
        return ""
    if n < 20:
        return _UNIDADES[n]
    if n == 100:
        return "cem"
    c, r = divmod(n, 100)
    parts: list[str] = []
    if c:
        parts.append(_CENTENAS[c])
    if r < 20:
        parts.append(_UNIDADES[r])
    else:
        d, u = divmod(r, 10)
        if u == 0:
            parts.append(_DEZENAS[d])
        else:
            parts.append(_DEZENAS[d] + " e " + _UNIDADES[u])
    return " e ".join(parts)


def _concat_grupos(partes: list[str]) -> str:
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0]
    if len(partes) == 2:
        return partes[0] + " e " + partes[1]
    return ", ".join(partes[:-1]) + " e " + partes[-1]


def extenso_inteiro_pt_br(n: int) -> str:
    """Número inteiro não negativo por extenso (até trilhões)."""
    if n < 0:
        return "menos " + extenso_inteiro_pt_br(-n)
    if n == 0:
        return "zero"

    grupos: list[int] = []
    x = n
    while x > 0:
        grupos.append(x % 1000)
        x //= 1000

    sufixos = [
        ("", ""),
        ("mil", "mil"),
        ("milhão", "milhões"),
        ("bilhão", "bilhões"),
        ("trilhão", "trilhões"),
    ]

    partes: list[str] = []
    for i in range(len(grupos) - 1, -1, -1):
        g = grupos[i]
        if g == 0:
            continue
        nivel = min(i, len(sufixos) - 1)
        sing, plur = sufixos[nivel]

        if i == 0:
            partes.append(_extenso_0_999(g))
        elif i == 1:
            if g == 1:
                partes.append("mil")
            else:
                partes.append(_extenso_0_999(g) + " mil")
        else:
            if g == 1:
                partes.append("um " + sing)
            else:
                partes.append(_extenso_0_999(g) + " " + plur)

    return _concat_grupos(partes)


def moeda_extenso_brl(valor) -> str:
    """
    Valor monetário em reais por extenso, ex.: 'mil duzentos e trinta reais e quarenta e cinco centavos'.
    Aceita Decimal, int, float, str.
    """
    try:
        d = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return ""

    negativo = d < 0
    d = abs(d)
    centavos_totais = int(d * 100)
    inteiro = centavos_totais // 100
    centavos = centavos_totais % 100

    partes: list[str] = []
    if inteiro == 0 and centavos == 0:
        partes.append("zero real")
    else:
        if inteiro == 0:
            pass
        else:
            ext = extenso_inteiro_pt_br(inteiro)
            if (
                "milhão" in ext
                or "milhões" in ext
                or "bilhão" in ext
                or "bilhões" in ext
                or "trilhão" in ext
                or "trilhões" in ext
            ):
                partes.append(ext + " de reais")
            elif inteiro == 1:
                partes.append(ext + " real")
            else:
                partes.append(ext + " reais")
        if centavos > 0:
            cext = extenso_inteiro_pt_br(centavos)
            partes.append(cext + (" centavo" if centavos == 1 else " centavos"))

    if inteiro > 0 and centavos > 0:
        s = partes[0] + " e " + partes[1]
    elif partes:
        s = partes[0] if len(partes) == 1 else partes[0] + " e " + partes[1]
    else:
        s = "zero real"

    if negativo:
        return "menos " + s
    return s[0].upper() + s[1:] if s else s


def format_inteiro_pt_br(n: int | None) -> str:
    """Inteiro com separador de milhar no padrão brasileiro (1.234.567)."""
    if n is None:
        return "—"
    x = int(n)
    neg = x < 0
    x = abs(x)
    s = f"{x:,}".replace(",", ".")
    return ("-" if neg else "") + s


def frase_contagem_masc(
    n: int | None,
    substantivo_um: str,
    substantivo_varios: str,
    complemento: str = "",
) -> str:
    """Ex.: n=3 → 'Três contratos na amostra.'"""
    if n is None:
        return ""
    suf = f" {complemento}".rstrip() if complemento else ""
    if n == 0:
        return f"Nenhum {substantivo_varios}{suf}."
    if n == 1:
        return f"Um {substantivo_um}{suf}."
    ext = extenso_inteiro_pt_br(n)
    head = ext[0].upper() + ext[1:]
    return f"{head} {substantivo_varios}{suf}."


def frase_contagem_fem(
    n: int | None,
    substantivo_uma: str,
    substantivo_varias: str,
    complemento: str = "",
) -> str:
    """Ex.: n=2 → 'Duas atas na amostra.'"""
    if n is None:
        return ""
    suf = f" {complemento}".rstrip() if complemento else ""
    if n == 0:
        return f"Nenhuma {substantivo_uma}{suf}."
    if n == 1:
        return f"Uma {substantivo_uma}{suf}."
    if n == 2:
        return f"Duas {substantivo_varias}{suf}."
    ext = extenso_inteiro_pt_br(n)
    head = ext[0].upper() + ext[1:]
    return f"{head} {substantivo_varias}{suf}."
