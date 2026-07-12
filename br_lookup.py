"""Consulta CEP (ViaCEP) e CNPJ (Brasil API) para preenchimento automático de formulários."""

from __future__ import annotations

import re

import requests

_DIGITS = re.compile(r"\D+")


def digits_only(value: str | None) -> str:
    return _DIGITS.sub("", value or "")


def _first_phone(data: dict) -> str | None:
    ddd = (data.get("ddd_telefone_1") or data.get("ddd_fax") or "").strip()
    tel = (data.get("telefone_1") or data.get("fax") or "").strip()
    if ddd and tel:
        return f"({ddd}) {tel}"[:40]
    for key in ("telefone", "telefone_1"):
        raw = (data.get(key) or "").strip()
        if raw:
            return raw[:40]
    return None


def lookup_cep(cep: str) -> dict | None:
    digits = digits_only(cep)
    if len(digits) != 8:
        return None
    try:
        resp = requests.get(
            f"https://viacep.com.br/ws/{digits}/json/",
            timeout=8,
            headers={"Accept": "application/json"},
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
        return None


def lookup_cnpj(cnpj: str) -> dict | None:
    digits = digits_only(cnpj)
    if len(digits) != 14:
        return None
    try:
        resp = requests.get(
            f"https://brasilapi.com.br/api/cnpj/v1/{digits}",
            timeout=12,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        cep_digits = digits_only(data.get("cep") or "")
        zip_fmt = f"{cep_digits[:5]}-{cep_digits[5:]}" if len(cep_digits) == 8 else None
        fantasia = (data.get("nome_fantasia") or "").strip()
        razao = (data.get("razao_social") or "").strip()
        email = (data.get("email") or "").strip().lower()
        return {
            "cnpj": data.get("cnpj") or digits,
            "razao_social": razao or None,
            "company_name": fantasia or razao or None,
            "organization": fantasia or razao or None,
            "phone": _first_phone(data),
            "email": email if email and "@" in email else None,
            "address_zip": zip_fmt,
            "address_street": (data.get("logradouro") or "").strip() or None,
            "address_number": str(data.get("numero") or "").strip() or None,
            "address_complement": (data.get("complemento") or "").strip() or None,
            "address_neighborhood": (data.get("bairro") or "").strip() or None,
            "address_city": (data.get("municipio") or "").strip() or None,
            "address_state": (data.get("uf") or "").strip().upper() or None,
        }
    except Exception:
        return None
