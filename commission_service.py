"""Regras de comissionamento ARPGOV — sócios, vendedor e faixas por projeto."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import (
        CommissionProject,
        CommissionProjectRateioLine,
        CommissionProjectTier,
        CommissionTierSplit,
        CompanyStakeholder,
        Opportunity,
    )

SELLER_SHARE_OF_TOTAL = Decimal("30")
STAKEHOLDER_POOL_SHARE_OF_TOTAL = Decimal("70")
TIER_PERCENTS = [Decimal("0.5"), Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("2.5"), Decimal("3")]


def _q4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fallback_catalog_item_id() -> int | None:
    from models import CatalogItem, db

    return (
        db.session.query(CatalogItem.id)
        .order_by(CatalogItem.id.asc())
        .limit(1)
        .scalar()
    )


def project_effective_rateio_mode(project: CommissionProject) -> str:
    mode = (getattr(project, "rateio_mode", None) or "").strip()
    if mode in ("no_seller", "with_seller", "custom"):
        return mode
    return "with_seller" if project.with_seller else "no_seller"


def rateio_mode_label(project: CommissionProject) -> str:
    mode = project_effective_rateio_mode(project)
    if mode == "custom":
        return "Personalizado"
    if mode == "with_seller":
        return "Com vendedor (30% vendedor + 70% sócios)"
    return "Sem vendedor (100% sócios)"


def project_tier_with_seller(project: CommissionProject) -> bool:
    return project_effective_rateio_mode(project) == "with_seller"


def validate_custom_rateio(project: CommissionProject) -> tuple[bool, str | None]:
    if project_effective_rateio_mode(project) != "custom":
        return True, None
    lines = list(project.rateio_lines)
    if not lines:
        return False, "Configure o modelo de rateio personalizado antes de adicionar faixas."
    total = sum(Decimal(str(line.pool_share_percent)) for line in lines)
    if abs(total - Decimal("100")) > Decimal("0.01"):
        pct_txt = f"{total:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return False, f"As participações do rateio devem somar 100% (atual: {pct_txt}%)."
    return True, None


def compute_split_rows(
    stakeholders: list[CompanyStakeholder],
    total_percent: Decimal,
    *,
    with_seller: bool,
) -> list[dict]:
    """Retorna linhas de rateio em % sobre o valor da operação."""
    total = _q4(Decimal(str(total_percent)))
    active = [s for s in stakeholders if s.is_active]
    rows: list[dict] = []

    if with_seller:
        seller_pct = _q4(total * SELLER_SHARE_OF_TOTAL / Decimal(100))
        rows.append(
            {
                "recipient_kind": "seller",
                "stakeholder_id": None,
                "share_percent": seller_pct,
                "label": "Vendedor",
            }
        )
        pool = total - seller_pct
    else:
        pool = total

    for sh in active:
        share = _q4(pool * Decimal(str(sh.share_percent)) / Decimal(100))
        rows.append(
            {
                "recipient_kind": "stakeholder",
                "stakeholder_id": sh.id,
                "share_percent": share,
                "label": sh.name,
            }
        )
    return rows


def compute_split_rows_for_project(
    project: CommissionProject,
    stakeholders: list[CompanyStakeholder],
    total_percent: Decimal,
) -> list[dict]:
    mode = project_effective_rateio_mode(project)
    if mode != "custom":
        return compute_split_rows(
            stakeholders,
            total_percent,
            with_seller=(mode == "with_seller"),
        )

    total = _q4(Decimal(str(total_percent)))
    active = [s for s in stakeholders if s.is_active]
    rows: list[dict] = []
    lines = sorted(
        project.rateio_lines,
        key=lambda line: (line.sort_order, line.id),
    )
    for line in lines:
        pool_pct = _q4(total * Decimal(str(line.pool_share_percent)) / Decimal(100))
        if line.recipient_kind == "seller":
            rows.append(
                {
                    "recipient_kind": "seller",
                    "stakeholder_id": None,
                    "share_percent": pool_pct,
                    "label": line.label or "Vendedor",
                }
            )
        elif line.recipient_kind == "stakeholder":
            for sh in active:
                share = _q4(pool_pct * Decimal(str(sh.share_percent)) / Decimal(100))
                rows.append(
                    {
                        "recipient_kind": "stakeholder",
                        "stakeholder_id": sh.id,
                        "share_percent": share,
                        "label": sh.name,
                    }
                )
        else:
            rows.append(
                {
                    "recipient_kind": "custom",
                    "stakeholder_id": line.stakeholder_id,
                    "share_percent": pool_pct,
                    "label": line.label,
                }
            )
    return rows


def sync_tier_splits(
    tier: CommissionProjectTier,
    stakeholders: list[CompanyStakeholder] | None = None,
) -> None:
    from models import CommissionTierSplit, db

    if stakeholders is None:
        stakeholders = active_stakeholders()
    project = tier.project
    tier.splits.clear()
    for row in compute_split_rows_for_project(project, stakeholders, tier.percent_total):
        tier.splits.append(
            CommissionTierSplit(
                recipient_kind=row["recipient_kind"],
                stakeholder_id=row["stakeholder_id"],
                share_percent=row["share_percent"],
                label=row["label"],
            )
        )
    db.session.flush()


def refresh_project_tier_splits(
    project: CommissionProject,
    stakeholders: list | None = None,
) -> None:
    if stakeholders is None:
        stakeholders = active_stakeholders()
    for tier in project.tiers:
        tier.project = project
        sync_tier_splits(tier, stakeholders)


def refresh_all_commission_tier_splits(stakeholders: list | None = None) -> None:
    from models import CommissionProject
    from sqlalchemy.orm import selectinload

    if stakeholders is None:
        stakeholders = active_stakeholders()
    projects = (
        CommissionProject.query.options(
            selectinload(CommissionProject.tiers),
            selectinload(CommissionProject.rateio_lines),
        )
        .order_by(CommissionProject.id.asc())
        .all()
    )
    for project in projects:
        if project.tiers:
            refresh_project_tier_splits(project, stakeholders)


def validate_stakeholders_total(stakeholders: list | None = None) -> tuple[bool, str | None]:
    if stakeholders is None:
        stakeholders = active_stakeholders()
    if not stakeholders:
        return False, "Cadastre ao menos um sócio ativo."
    total = sum(Decimal(str(s.share_percent)) for s in stakeholders)
    if abs(total - Decimal("100")) > Decimal("0.01"):
        pct_txt = f"{total:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return False, f"As participações dos sócios devem somar 100% (atual: {pct_txt}%)."
    return True, None


def rateio_lines_total_percent(lines) -> Decimal:
    return sum(Decimal(str(line.pool_share_percent)) for line in lines)


def sync_project_tiers(project: CommissionProject, stakeholders: list[CompanyStakeholder] | None = None) -> None:
    from models import CommissionProjectTier, CompanyStakeholder, db

    if stakeholders is None:
        stakeholders = (
            CompanyStakeholder.query.filter_by(is_active=True)
            .order_by(CompanyStakeholder.sort_order.asc(), CompanyStakeholder.id.asc())
            .all()
        )
    with_seller = project_tier_with_seller(project)
    for t in list(project.tiers):
        if bool(t.with_seller) != with_seller:
            project.tiers.remove(t)
    existing = {t.percent_total: t for t in project.tiers}
    order = 0
    for pct in TIER_PERCENTS:
        order += 1
        tier = existing.get(pct)
        if tier is None:
            tier = CommissionProjectTier(
                project_id=project.id,
                percent_total=pct,
                with_seller=with_seller,
                sort_order=order,
            )
            project.tiers.append(tier)
            db.session.flush()
        else:
            tier.sort_order = order
            tier.with_seller = with_seller
        sync_tier_splits(tier, stakeholders)


def tier_percent_label(tier: CommissionProjectTier) -> str:
    pct = tier.percent_total
    pct_txt = f"{pct:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{pct_txt}%"


def tier_label(tier: CommissionProjectTier) -> str:
    project = tier.project
    mode_key = project_effective_rateio_mode(project) if project else (
        "with_seller" if tier.with_seller else "no_seller"
    )
    if mode_key == "custom":
        mode = "personalizado"
    elif mode_key == "with_seller":
        mode = "com vendedor"
    else:
        mode = "sem vendedor"
    return f"{tier_percent_label(tier)} — {mode}"


def parse_tier_percent(raw: str | None) -> Decimal | None:
    s = (raw or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        value = Decimal(s)
    except Exception:
        return None
    if value <= 0 or value > 100:
        return None
    return _q4(value)


def active_stakeholders():
    from models import CompanyStakeholder

    return (
        CompanyStakeholder.query.filter_by(is_active=True)
        .order_by(CompanyStakeholder.sort_order.asc(), CompanyStakeholder.id.asc())
        .all()
    )


def add_tier_to_project(
    project: CommissionProject,
    percent: Decimal,
    stakeholders: list | None = None,
) -> CommissionProjectTier:
    from models import CommissionProjectTier, db
    from sqlalchemy import func

    if stakeholders is None:
        stakeholders = active_stakeholders()
    tier_with_seller = project_tier_with_seller(project)
    existing = CommissionProjectTier.query.filter_by(
        project_id=project.id,
        percent_total=percent,
        with_seller=tier_with_seller,
    ).first()
    if existing:
        sync_tier_splits(existing, stakeholders)
        return existing
    max_order = (
        db.session.query(func.max(CommissionProjectTier.sort_order))
        .filter_by(project_id=project.id)
        .scalar()
        or 0
    )
    tier = CommissionProjectTier(
        project_id=project.id,
        percent_total=percent,
        with_seller=tier_with_seller,
        sort_order=int(max_order) + 1,
    )
    project.tiers.append(tier)
    db.session.flush()
    sync_tier_splits(tier, stakeholders)
    return tier


def amounts_from_splits(
    splits: list,
    value_brl: Decimal | None,
) -> list[dict]:
    """Enriquece splits com valor em R$ quando há valor da operação."""
    base = Decimal(str(value_brl)) if value_brl is not None else None
    out: list[dict] = []
    for s in splits:
        pct = Decimal(str(s.share_percent))
        amount = _q2(base * pct / Decimal(100)) if base is not None else None
        out.append(
            {
                "recipient_kind": s.recipient_kind,
                "stakeholder_id": s.stakeholder_id,
                "share_percent": pct,
                "label": s.label,
                "amount_brl": amount,
            }
        )
    return out


def apply_tier_to_opportunity(
    opp: Opportunity,
    tier: CommissionProjectTier | None,
    *,
    preserve_payout_status: bool = False,
) -> None:
    from models import OpportunityCommissionSplit, db

    old_status: dict[tuple, str] = {}
    if preserve_payout_status and opp.id:
        for row in OpportunityCommissionSplit.query.filter_by(opportunity_id=opp.id).all():
            key = (row.recipient_kind, row.stakeholder_id, row.recipient_name)
            old_status[key] = row.payout_status

    if opp.id:
        OpportunityCommissionSplit.query.filter_by(opportunity_id=opp.id).delete()
    if tier is None:
        opp.commission_project_id = None
        opp.commission_tier_id = None
        return

    opp.commission_project_id = tier.project_id
    opp.commission_tier_id = tier.id

    seller_amount: Decimal | None = None
    seller_pct: Decimal | None = None
    for split in tier.splits:
        pct = Decimal(str(split.share_percent))
        amount = None
        if opp.value_brl is not None:
            amount = _q2(Decimal(str(opp.value_brl)) * pct / Decimal(100))
        if split.recipient_kind == "seller":
            seller_amount = amount
            seller_pct = pct
        status_key = (split.recipient_kind, split.stakeholder_id, split.label)
        db.session.add(
            OpportunityCommissionSplit(
                opportunity_id=opp.id,
                tier_id=tier.id,
                recipient_kind=split.recipient_kind,
                stakeholder_id=split.stakeholder_id,
                sales_rep_id=opp.sales_rep_id if split.recipient_kind == "seller" else None,
                recipient_name=split.label,
                share_percent=pct,
                amount_brl=amount,
                payout_status=old_status.get(status_key, "pendente"),
            )
        )

    has_seller = any(split.recipient_kind == "seller" for split in tier.splits)
    if has_seller and seller_amount is not None:
        opp.rep_commission_brl = seller_amount
        pct_txt = f"{seller_pct:.4f}".rstrip("0").rstrip(".").replace(".", ",") if seller_pct else ""
        opp.rep_commission_note = (
            f"Faixa {tier_label(tier)} — R$ {seller_amount} para o vendedor ({pct_txt}% da operação)."
        )
    elif not has_seller:
        opp.rep_commission_brl = None
        opp.rep_commission_note = f"Faixa {tier_label(tier)} — rateio sem vendedor."


def reapply_all_opportunity_commissions(*, preserve_payout_status: bool = True) -> None:
    from models import CommissionProjectTier, Opportunity, db
    from sqlalchemy.orm import selectinload

    opps = (
        Opportunity.query.options(
            selectinload(Opportunity.commission_tier).selectinload(
                CommissionProjectTier.splits
            ),
        )
        .filter(Opportunity.commission_tier_id.isnot(None))
        .all()
    )
    for opp in opps:
        tier = opp.commission_tier
        if tier is None:
            tier = db.session.get(CommissionProjectTier, opp.commission_tier_id)
        if tier is None:
            continue
        apply_tier_to_opportunity(
            opp,
            tier,
            preserve_payout_status=preserve_payout_status,
        )
    db.session.flush()
