"""Estágios do funil comercial e campos específicos por etapa."""
from __future__ import annotations

STAGES: list[tuple[str, str]] = [
    ("novo", "Novo"),
    ("qualified", "Qualificado"),
    ("em_andamento", "Em andamento"),
    ("doc_enviada", "Documentação enviada"),
    ("cobrar_aceite", "Cobrar aceite órgão gerenciador e empresa"),
    ("empenho_recebido", "Empenho recebido"),
    ("acompanhar_faturamento", "Acompanhar faturamento"),
    ("acompanhar_entrega", "Acompanhar entrega"),
    ("acompanhar_recebimento", "Acompanhar recebimento"),
    ("cobrar_comissionamento", "Cobrar comissionamento"),
]

STAGE_KEYS = [k for k, _ in STAGES]
STAGE_LABELS = dict(STAGES)

LEGACY_STAGE_MAP: dict[str, str] = {
    "lead": "novo",
    "proposal": "em_andamento",
    "negotiation": "em_andamento",
    "won": "cobrar_comissionamento",
    "lost": "em_andamento",
}

# Campos extras por estágio (além de novo / qualificado / em andamento).
STAGE_FIELD_DEFS: dict[str, dict] = {
    "doc_enviada": {
        "hint": "Anexe a documentação enviada.",
        "attachments": True,
    },
    "cobrar_aceite": {
        "hint": "Anexe os ofícios.",
        "attachments": True,
    },
    "empenho_recebido": {
        "hint": "Anexe o empenho recebido e informe a data.",
        "attachments": True,
        "date": "Data do empenho",
    },
    "acompanhar_faturamento": {
        "hint": "Informe a previsão de faturamento.",
        "forecast_date": "Previsão de faturamento",
    },
    "acompanhar_entrega": {
        "hint": "Informe a previsão de entrega.",
        "forecast_date": "Previsão de entrega",
    },
    "acompanhar_recebimento": {
        "hint": "Registre as interações de cobrança junto ao órgão.",
        "notes": "Interações de cobrança",
    },
    "cobrar_comissionamento": {
        "hint": "Informe a previsão de recebimento da comissão.",
        "forecast_date": "Previsão de recebimento da comissão",
    },
}


def normalize_stage_key(raw: str | None, *, default: str = "novo") -> str:
    key = (raw or "").strip().lower()
    if not key:
        return default
    if key in STAGE_LABELS:
        return key
    if key in LEGACY_STAGE_MAP:
        return LEGACY_STAGE_MAP[key]
    return default


def stage_label(stage_key: str | None) -> str:
    key = normalize_stage_key(stage_key, default="")
    if not key:
        return stage_key or ""
    return STAGE_LABELS.get(key, stage_key or key)


def stage_index(stage_key: str | None) -> int:
    key = normalize_stage_key(stage_key)
    try:
        return STAGE_KEYS.index(key)
    except ValueError:
        return 0


def stages_with_fields_up_to(stage_key: str | None) -> list[str]:
    """Estágios que exibem campos (até o estágio atual, inclusive)."""
    idx = stage_index(stage_key)
    out: list[str] = []
    for key in STAGE_KEYS:
        if key not in STAGE_FIELD_DEFS:
            continue
        if STAGE_KEYS.index(key) <= idx:
            out.append(key)
    return out
