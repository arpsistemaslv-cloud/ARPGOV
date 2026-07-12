"""Metas financeiras e simulação de comissões no CRM."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import extract, func

from commission_service import amounts_from_splits

# Imposto sobre o valor total da comissão (representação comercial).
COMMISSION_TAX_PERCENT = Decimal("5")
COMMISSION_NET_FACTOR = (Decimal("100") - COMMISSION_TAX_PERCENT) / Decimal("100")

MONTH_NAMES_PT = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
MONTH_NAMES_PT_SHORT = (
    "",
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)


def _d(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def get_or_create_finance_goal():
    from models import CompanyFinanceGoal, db

    row = db.session.get(CompanyFinanceGoal, 1)
    if row is None:
        row = CompanyFinanceGoal(
            id=1,
            goal_year=datetime.utcnow().year,
            goal_start_month=1,
            goal_end_month=12,
        )
        db.session.add(row)
        db.session.commit()
    return row


def _clamp_month(value, default: int) -> int:
    try:
        month = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(month, 1), 12)


def goal_period_months(goal) -> int:
    start = _clamp_month(getattr(goal, "goal_start_month", None), 1)
    end = _clamp_month(getattr(goal, "goal_end_month", None), 12)
    if start > end:
        return 12
    return end - start + 1


def goal_period_label(goal, *, short: bool = False) -> str:
    start = _clamp_month(getattr(goal, "goal_start_month", None), 1)
    end = _clamp_month(getattr(goal, "goal_end_month", None), 12)
    year = goal.goal_year or datetime.utcnow().year
    names = MONTH_NAMES_PT_SHORT if short else MONTH_NAMES_PT
    if start == 1 and end == 12:
        return f"ano {year}"
    return f"{names[start]}–{names[end]} {year}"


def monthly_goal_from_period_total(goal) -> Decimal | None:
    total = goal.goal_annual_brl
    if total is None:
        if goal.goal_monthly_brl is not None:
            return _q2(_d(goal.goal_monthly_brl))
        return None
    months = goal_period_months(goal)
    if months <= 0:
        return None
    return _q2(_d(total) / Decimal(str(months)))


def tier_simulation_totals(tier, value_brl: Decimal | None) -> dict:
    splits = amounts_from_splits(list(tier.splits), value_brl)
    total_commission = Decimal("0")
    company_share = Decimal("0")
    seller_share = Decimal("0")
    for row in splits:
        amount = _d(row.get("amount_brl"))
        total_commission += amount
        kind = row.get("recipient_kind")
        if kind == "seller":
            seller_share += amount
        elif kind in ("stakeholder", "custom"):
            company_share += amount
    value = _d(value_brl)
    return {
        "value_brl": value,
        "total_commission_brl": _q2(total_commission),
        "company_share_brl": _q2(company_share),
        "seller_share_brl": _q2(seller_share),
        "splits": splits,
    }


def enrich_simulation_line(line) -> dict:
    tier = line.commission_tier
    totals = tier_simulation_totals(tier, _d(line.value_brl))
    project = tier.project if tier else None
    return {
        "line": line,
        "tier": tier,
        "project": project,
        "totals": totals,
    }


def actual_pipeline_totals(
    *,
    year: int,
    month: int | None = None,
    start_month: int | None = None,
    end_month: int | None = None,
) -> dict:
    from models import Opportunity, OpportunityCommissionSplit, db

    opp_q = Opportunity.query.filter(
        Opportunity.commission_tier_id.isnot(None),
        Opportunity.value_brl.isnot(None),
        extract("year", Opportunity.updated_at) == year,
    )
    if month is not None:
        opp_q = opp_q.filter(extract("month", Opportunity.updated_at) == month)
    elif start_month is not None and end_month is not None:
        sm = _clamp_month(start_month, 1)
        em = _clamp_month(end_month, 12)
        if sm <= em:
            opp_q = opp_q.filter(
                extract("month", Opportunity.updated_at) >= sm,
                extract("month", Opportunity.updated_at) <= em,
            )

    operations_value = _d(
        opp_q.with_entities(func.coalesce(func.sum(Opportunity.value_brl), 0)).scalar()
    )

    split_q = (
        db.session.query(OpportunityCommissionSplit)
        .join(Opportunity)
        .filter(
            Opportunity.commission_tier_id.isnot(None),
            extract("year", Opportunity.updated_at) == year,
        )
    )
    if month is not None:
        split_q = split_q.filter(extract("month", Opportunity.updated_at) == month)
    elif start_month is not None and end_month is not None:
        sm = _clamp_month(start_month, 1)
        em = _clamp_month(end_month, 12)
        if sm <= em:
            split_q = split_q.filter(
                extract("month", Opportunity.updated_at) >= sm,
                extract("month", Opportunity.updated_at) <= em,
            )

    company_share = Decimal("0")
    total_commission = Decimal("0")
    seller_share = Decimal("0")
    for split in split_q.all():
        amount = _d(split.amount_brl)
        total_commission += amount
        if split.recipient_kind == "seller":
            seller_share += amount
        elif split.recipient_kind in ("stakeholder", "custom"):
            company_share += amount

    return {
        "operations_value_brl": _q2(operations_value),
        "total_commission_brl": _q2(total_commission),
        "company_share_brl": _q2(company_share),
        "seller_share_brl": _q2(seller_share),
        "lead_count": opp_q.count(),
    }


def simulation_totals(lines: list) -> dict:
    operations_value = Decimal("0")
    total_commission = Decimal("0")
    company_share = Decimal("0")
    seller_share = Decimal("0")
    for item in lines:
        totals = item["totals"]
        operations_value += _d(totals["value_brl"])
        total_commission += _d(totals["total_commission_brl"])
        company_share += _d(totals["company_share_brl"])
        seller_share += _d(totals["seller_share_brl"])
    return {
        "operations_value_brl": _q2(operations_value),
        "total_commission_brl": _q2(total_commission),
        "company_share_brl": _q2(company_share),
        "seller_share_brl": _q2(seller_share),
        "line_count": len(lines),
    }


def goal_progress(actual: Decimal, goal: Decimal | None) -> dict:
    goal_val = _d(goal)
    actual_val = _d(actual)
    if goal_val <= 0:
        return {
            "goal_brl": goal_val,
            "actual_brl": actual_val,
            "remaining_brl": None,
            "percent": None,
        }
    percent = float((actual_val / goal_val * Decimal("100")).quantize(Decimal("0.1")))
    remaining = _q2(max(goal_val - actual_val, Decimal("0")))
    return {
        "goal_brl": goal_val,
        "actual_brl": actual_val,
        "remaining_brl": remaining,
        "percent": min(percent, 999.9),
    }


def _split_key(row: dict) -> tuple:
    return (
        row.get("recipient_kind"),
        row.get("stakeholder_id"),
        row.get("label"),
    )


def goal_commission_projection(goal, tier) -> dict | None:
    """Meta = volume de vendas negociado; comissão = faixa % × meta (representação comercial)."""
    if tier is None or not list(getattr(tier, "splits", None) or []):
        return None

    splits = list(tier.splits)
    annual_value = _d(goal.goal_annual_brl)
    months = goal_period_months(goal)
    if annual_value > 0:
        monthly_value = _q2(annual_value / Decimal(str(months)))
    else:
        monthly_value = _d(goal.goal_monthly_brl)
    if annual_value <= 0 and monthly_value <= 0:
        return None

    annual_value = _q2(annual_value)
    monthly_value = _q2(monthly_value)

    annual_rows = amounts_from_splits(splits, annual_value)
    monthly_rows = amounts_from_splits(splits, monthly_value)
    monthly_by_key = {_split_key(row): row for row in monthly_rows}

    recipients: list[dict] = []
    annual_commission_gross = Decimal("0")
    monthly_commission_gross = Decimal("0")
    for row in annual_rows:
        key = _split_key(row)
        monthly_row = monthly_by_key.get(key, {})
        annual_gross = _d(row.get("amount_brl"))
        monthly_gross = _d(monthly_row.get("amount_brl"))
        annual_commission_gross += annual_gross
        monthly_commission_gross += monthly_gross
        annual_net = _q2(annual_gross * COMMISSION_NET_FACTOR)
        monthly_net = _q2(monthly_gross * COMMISSION_NET_FACTOR)
        kind = row.get("recipient_kind")
        if kind == "seller":
            role = "Vendedor"
        elif kind == "stakeholder":
            role = "Sócio"
        else:
            role = "Participante"
        recipients.append(
            {
                "label": row.get("label") or role,
                "role": role,
                "recipient_kind": kind,
                "share_percent": _d(row.get("share_percent")),
                "annual_gross_brl": _q2(annual_gross),
                "monthly_gross_brl": _q2(monthly_gross),
                "annual_brl": annual_net,
                "monthly_brl": monthly_net,
            }
        )

    annual_commission_gross = _q2(annual_commission_gross)
    monthly_commission_gross = _q2(monthly_commission_gross)
    annual_tax = _q2(annual_commission_gross * COMMISSION_TAX_PERCENT / Decimal("100"))
    monthly_tax = _q2(monthly_commission_gross * COMMISSION_TAX_PERCENT / Decimal("100"))
    annual_commission_net = _q2(annual_commission_gross - annual_tax)
    monthly_commission_net = _q2(monthly_commission_gross - monthly_tax)

    project = tier.project if tier else None
    tier_total_pct = sum(_d(s.share_percent) for s in splits)
    return {
        "tier": tier,
        "project": project,
        "annual_value_brl": annual_value,
        "monthly_value_brl": monthly_value,
        "tier_total_percent": _q2(tier_total_pct),
        "tax_percent": COMMISSION_TAX_PERCENT,
        "recipients": recipients,
        "totals": {
            "annual_commission_gross_brl": annual_commission_gross,
            "monthly_commission_gross_brl": monthly_commission_gross,
            "annual_tax_brl": annual_tax,
            "monthly_tax_brl": monthly_tax,
            "annual_commission_brl": annual_commission_net,
            "monthly_commission_brl": monthly_commission_net,
        },
    }


def finance_dashboard(goal, simulation_lines_enriched: list) -> dict:
    year = goal.goal_year or datetime.utcnow().year
    month = datetime.utcnow().month
    period_start = _clamp_month(getattr(goal, "goal_start_month", None), 1)
    period_end = _clamp_month(getattr(goal, "goal_end_month", None), 12)
    in_period = period_start <= month <= period_end

    pipeline_year = actual_pipeline_totals(
        year=year, start_month=period_start, end_month=period_end
    )
    pipeline_month = actual_pipeline_totals(year=year, month=month)
    sim_totals = simulation_totals(simulation_lines_enriched)

    projected_year_operations = _q2(
        _d(pipeline_year["operations_value_brl"]) + _d(sim_totals["operations_value_brl"])
    )
    projected_month_operations = _q2(_d(pipeline_month["operations_value_brl"]))
    projected_year_company = _q2(
        _d(pipeline_year["company_share_brl"]) + _d(sim_totals["company_share_brl"])
    )
    projected_month_company = _q2(_d(pipeline_month["company_share_brl"]))

    monthly_goal = monthly_goal_from_period_total(goal)

    return {
        "year": year,
        "month": month,
        "period_start_month": period_start,
        "period_end_month": period_end,
        "period_months": goal_period_months(goal),
        "period_label": goal_period_label(goal),
        "period_label_short": goal_period_label(goal, short=True),
        "in_period": in_period,
        "pipeline_year": pipeline_year,
        "pipeline_month": pipeline_month,
        "simulation": sim_totals,
        "projected_year_operations_brl": projected_year_operations,
        "projected_month_operations_brl": projected_month_operations,
        "projected_year_company_brl": projected_year_company,
        "projected_month_company_brl": projected_month_company,
        "annual_progress": goal_progress(
            projected_year_operations, goal.goal_annual_brl
        ),
        "monthly_progress": goal_progress(
            projected_month_operations if in_period else Decimal("0"),
            monthly_goal if in_period else None,
        ),
        "monthly_goal_brl": monthly_goal,
    }
