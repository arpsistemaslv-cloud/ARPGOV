"""
Consulta de rastreamento na API Braspress (api.braspress.com).
Documentação: https://api.braspress.com/home

Autenticação: Basic Auth (usuário/senha fornecidos pela Braspress).
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote

import requests

DEFAULT_BASE = "https://api.braspress.com"


def _strip_env_quotes(val: str) -> str:
    s = (val or "").strip()
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        return s[1:-1].strip()
    return s


def braspress_credentials() -> tuple[str | None, str | None]:
    u = _strip_env_quotes(os.environ.get("BRASPRESS_API_USER") or "")
    p = _strip_env_quotes(os.environ.get("BRASPRESS_API_PASSWORD") or "")
    if not u and not p:
        return None, None
    return (u or None, p or None)


def default_cnpj_remetente() -> str:
    return _strip_env_quotes(os.environ.get("BRASPRESS_CNPJ_REMETENTE") or "")


def api_base_url() -> str:
    return (os.environ.get("BRASPRESS_API_BASE") or DEFAULT_BASE).rstrip("/")


def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def build_tracking_url(
    spec: str,
    cnpj: str,
    reference: str,
    return_type: str,
) -> str:
    """
    Monta a URL conforme o endpoint escolhido.

    spec: v1_nf | v2_nf | v3_nf | v3_pedido
    """
    root = api_base_url()
    cnpj_clean = only_digits(cnpj)
    if len(cnpj_clean) not in (11, 14):
        raise ValueError("CNPJ inválido: informe 11 (CPF) ou 14 (CNPJ) dígitos.")

    ref = (reference or "").strip()
    if not ref:
        raise ValueError("Informe o número da nota fiscal ou do pedido.")

    rt = (return_type or "json").strip() or "json"
    c = quote(cnpj_clean, safe="")
    r = quote(ref, safe="")
    t = quote(rt, safe="")

    if spec == "v1_nf":
        return f"{root}/v1/tracking/{c}/{r}/{t}"
    if spec == "v2_nf":
        return f"{root}/v2/tracking/{c}/{r}/{t}"
    if spec == "v3_nf":
        return f"{root}/v3/tracking/byNf/{c}/{r}/{t}"
    if spec == "v3_pedido":
        return f"{root}/v3/tracking/byNumPedido/{c}/{r}/{t}"
    raise ValueError("Tipo de consulta inválido.")


def fetch_tracking_get(url: str, timeout: int = 30) -> tuple[int, str, str | None]:
    """GET autenticado. Retorna (status_http, corpo_texto, content-type)."""
    user, password = braspress_credentials()
    if not user or not password:
        raise RuntimeError(
            "Configure BRASPRESS_API_USER e BRASPRESS_API_PASSWORD no arquivo .env "
            "(credenciais fornecidas pela Braspress)."
        )
    resp = requests.get(
        url,
        auth=(user, password),
        timeout=timeout,
        headers={"Accept": "application/json, text/plain, */*"},
    )
    ct = resp.headers.get("Content-Type")
    return resp.status_code, resp.text, ct


def format_body_preview(body: str, content_type: str | None) -> str | None:
    """Se não for JSON estruturado, devolve None (exiba corpo cru)."""
    if not body or not body.strip():
        return None
    if content_type and "json" in content_type.lower():
        try:
            data = json.loads(body)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError):
            return None
    return None
