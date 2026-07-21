"""Consulta CEP (ViaCEP) e CNPJ (múltiplas APIs) para preenchimento automático."""

from __future__ import annotations

import logging
import re

import requests

_DIGITS = re.compile(r"\D+")
_LOG = logging.getLogger(__name__)

_HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "ARPGOV-Portal/1.0 (+https://arpgov.com)",
}


def digits_only(value: str | None) -> str:
    return _DIGITS.sub("", value or "")


def _first_phone(data: dict) -> str | None:
    """Normaliza telefone a partir de formatos BrasilAPI / OpenCNPJ / MinhaReceita."""
    ddd = str(data.get("ddd_telefone_1") or data.get("ddd_fax") or "").strip()
    tel = str(data.get("telefone_1") or data.get("fax") or "").strip()
    # Alguns provedores devolvem DDD+número juntos em ddd_telefone_1
    if ddd and not tel and len(digits_only(ddd)) >= 10:
        raw = digits_only(ddd)
        return f"({raw[:2]}) {raw[2:]}"[:40]
    if ddd and tel:
        ddd_d = digits_only(ddd)
        tel_d = digits_only(tel)
        if len(ddd_d) >= 10 and not tel_d:
            return f"({ddd_d[:2]}) {ddd_d[2:]}"[:40]
        return f"({ddd_d[-2:] if len(ddd_d) > 2 else ddd_d}) {tel_d}"[:40]
    for key in ("telefone", "telefone_1", "ddd_telefone_1"):
        raw = str(data.get(key) or "").strip()
        if not raw:
            continue
        d = digits_only(raw)
        if len(d) >= 10:
            return f"({d[:2]}) {d[2:]}"[:40]
        return raw[:40]
    return None


def _format_zip(cep_raw: str | None) -> str | None:
    cep_digits = digits_only(cep_raw)
    if len(cep_digits) == 8:
        return f"{cep_digits[:5]}-{cep_digits[5:]}"
    return None


def _normalize_company_payload(
    *,
    cnpj_digits: str,
    razao: str | None,
    fantasia: str | None,
    email: str | None,
    phone: str | None,
    cep: str | None,
    street: str | None,
    number: str | None,
    complement: str | None,
    neighborhood: str | None,
    city: str | None,
    state: str | None,
) -> dict:
    razao_s = (razao or "").strip() or None
    fantasia_s = (fantasia or "").strip() or None
    email_s = (email or "").strip().lower()
    return {
        "cnpj": cnpj_digits,
        "razao_social": razao_s,
        "company_name": fantasia_s or razao_s,
        "organization": fantasia_s or razao_s,
        "phone": phone,
        "email": email_s if email_s and "@" in email_s else None,
        "address_zip": _format_zip(cep),
        "address_street": (street or "").strip() or None,
        "address_number": str(number or "").strip() or None,
        "address_complement": (complement or "").strip() or None,
        "address_neighborhood": (neighborhood or "").strip() or None,
        "address_city": (city or "").strip() or None,
        "address_state": (state or "").strip().upper() or None,
    }


def lookup_cep(cep: str) -> dict | None:
    digits = digits_only(cep)
    if len(digits) != 8:
        return None
    try:
        resp = requests.get(
            f"https://viacep.com.br/ws/{digits}/json/",
            timeout=8,
            headers=_HTTP_HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("erro"):
            return None
        return {
            "cep": data.get("cep"),
            "address_street": (data.get("logradouro") or "").strip() or None,
            "address_complement": (data.get("complemento") or "").strip() or None,
            "address_neighborhood": (data.get("bairro") or "").strip() or None,
            "address_city": (data.get("localidade") or "").strip() or None,
            "address_state": (data.get("uf") or "").strip().upper() or None,
        }
    except Exception:
        _LOG.exception("Falha ao consultar CEP %s", digits)
        return None


def _lookup_cnpj_brasilapi(digits: str) -> dict | None:
    resp = requests.get(
        f"https://brasilapi.com.br/api/cnpj/v1/{digits}",
        timeout=12,
        headers=_HTTP_HEADERS,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return _normalize_company_payload(
        cnpj_digits=digits,
        razao=data.get("razao_social"),
        fantasia=data.get("nome_fantasia"),
        email=data.get("email"),
        phone=_first_phone(data),
        cep=data.get("cep"),
        street=data.get("logradouro"),
        number=data.get("numero"),
        complement=data.get("complemento"),
        neighborhood=data.get("bairro"),
        city=data.get("municipio"),
        state=data.get("uf"),
    )


def _lookup_cnpj_minhareceita(digits: str) -> dict | None:
    resp = requests.get(
        f"https://minhareceita.org/{digits}",
        timeout=12,
        headers=_HTTP_HEADERS,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return _normalize_company_payload(
        cnpj_digits=digits,
        razao=data.get("razao_social") or data.get("nome"),
        fantasia=data.get("nome_fantasia"),
        email=data.get("email"),
        phone=_first_phone(data),
        cep=data.get("cep"),
        street=data.get("logradouro"),
        number=data.get("numero"),
        complement=data.get("complemento"),
        neighborhood=data.get("bairro"),
        city=data.get("municipio"),
        state=data.get("uf"),
    )


def _lookup_cnpj_opencnpj(digits: str) -> dict | None:
    resp = requests.get(
        f"https://api.opencnpj.org/{digits}",
        timeout=12,
        headers=_HTTP_HEADERS,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    phone = _first_phone(data)
    if not phone:
        tels = data.get("telefones")
        if isinstance(tels, list) and tels:
            t0 = tels[0] if isinstance(tels[0], dict) else {}
            ddd = str(t0.get("ddd") or "").strip()
            num = str(t0.get("numero") or "").strip()
            if ddd and num:
                phone = f"({ddd}) {num}"[:40]
            elif num:
                phone = num[:40]
    if not phone:
        phone = (str(data.get("telefone") or "").strip() or None)
    return _normalize_company_payload(
        cnpj_digits=digits,
        razao=data.get("razao_social") or data.get("nome"),
        fantasia=data.get("nome_fantasia") or data.get("fantasia"),
        email=data.get("email"),
        phone=phone,
        cep=data.get("cep"),
        street=data.get("logradouro"),
        number=data.get("numero"),
        complement=data.get("complemento"),
        neighborhood=data.get("bairro"),
        city=data.get("municipio") or data.get("cidade"),
        state=data.get("uf"),
    )


def _lookup_cnpj_receitaws(digits: str) -> dict | None:
    resp = requests.get(
        f"https://www.receitaws.com.br/v1/cnpj/{digits}",
        timeout=15,
        headers=_HTTP_HEADERS,
    )
    if resp.status_code in (404, 429):
        return None
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("status") or "").upper() == "ERROR":
        return None
    tels = data.get("telefone") or ""
    phone = None
    if isinstance(tels, str) and tels.strip():
        phone = tels.split("/")[0].strip()[:40]
    return _normalize_company_payload(
        cnpj_digits=digits,
        razao=data.get("nome"),
        fantasia=data.get("fantasia"),
        email=data.get("email"),
        phone=phone,
        cep=data.get("cep"),
        street=data.get("logradouro"),
        number=data.get("numero"),
        complement=data.get("complemento"),
        neighborhood=data.get("bairro"),
        city=data.get("municipio"),
        state=data.get("uf"),
    )


def lookup_cnpj(cnpj: str) -> dict | None:
    """Tenta várias fontes — datacenters às vezes bloqueiam uma API específica."""
    digits = digits_only(cnpj)
    if len(digits) != 14:
        return None
    providers = (
        ("brasilapi", _lookup_cnpj_brasilapi),
        ("minhareceita", _lookup_cnpj_minhareceita),
        ("opencnpj", _lookup_cnpj_opencnpj),
        ("receitaws", _lookup_cnpj_receitaws),
    )
    for name, fn in providers:
        try:
            data = fn(digits)
            if data and (data.get("razao_social") or data.get("company_name")):
                return data
        except Exception:
            _LOG.warning("Falha na consulta CNPJ via %s", name, exc_info=True)
            continue
    return None
