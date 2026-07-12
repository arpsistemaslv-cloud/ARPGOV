"""
CRM ARPGOV v2 — Clientes, Produtos, Leads (comissão + processo) e Financeiro.
Substitui o CRM legado (Athenas) mantendo as tabelas existentes.
"""

from __future__ import annotations

import os
import json
import unicodedata
from datetime import datetime
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    CatalogItem,
    CommissionProject,
    CommissionProjectRateioLine,
    CommissionProjectTier,
    CommissionSale,
    CommissionSaleSplit,
    CompanyFinanceGoal,
    CompanyStakeholder,
    FinanceSimulationLine,
    LeadMessage,
    Opportunity,
    OpportunityCatalogLine,
    OpportunityCommissionSplit,
    Partner,
    PortalClient,
    RepFinancialEntry,
    SalesRepresentative,
    SiteSettings,
    db,
)
from finance_goals_service import (
    enrich_simulation_line,
    finance_dashboard,
    get_or_create_finance_goal,
    goal_commission_projection,
)
from commission_service import (
    add_tier_to_project,
    apply_tier_to_opportunity,
    fallback_catalog_item_id,
    parse_tier_percent,
    project_effective_rateio_mode,
    rateio_mode_label,
    refresh_project_tier_splits,
    refresh_all_commission_tier_splits,
    sync_tier_splits,
    tier_label,
    tier_percent_label,
    validate_custom_rateio,
    validate_stakeholders_total,
)
from pipeline_stages import (
    STAGE_FIELD_DEFS,
    STAGE_LABELS,
    STAGES,
    normalize_stage_key,
    stage_label as pipeline_stage_label,
    stages_with_fields_up_to,
)

crm_bp = Blueprint("crm", __name__, url_prefix="/crm")

COMMISSION_STATUSES = [
    ("enviado", "Enviado"),
    ("em_analise", "Em análise"),
    ("aprovado", "Aprovado"),
    ("pago", "Pago"),
    ("recusado", "Recusado"),
]

PAYOUT_STATUSES = [
    ("pendente", "Pendente"),
    ("aprovado", "Aprovado"),
    ("pago", "Pago"),
]


def _finance_rateio_groups(*, paid_only: bool) -> list[dict]:
    """Agrupa rateio por lead ou venda avulsa; separa quitados vs em aberto."""
    groups: list[dict] = []

    opps = (
        Opportunity.query.filter(Opportunity.commission_splits.any())
        .options(
            selectinload(Opportunity.commission_splits),
            selectinload(Opportunity.sales_rep),
            selectinload(Opportunity.commission_tier),
        )
        .order_by(Opportunity.updated_at.desc())
        .limit(1000)
        .all()
    )
    for opp in opps:
        splits = list(opp.commission_splits or [])
        if not splits:
            continue
        total = Decimal(0)
        paid = Decimal(0)
        for s in splits:
            amt = Decimal(str(s.amount_brl)) if s.amount_brl is not None else Decimal(0)
            total += amt
            if s.payout_status == "pago":
                paid += amt
        all_paid = all(s.payout_status == "pago" for s in splits)
        if paid_only and not all_paid:
            continue
        if not paid_only and all_paid:
            continue
        groups.append(
            {
                "kind": "lead",
                "opportunity": opp,
                "sale": None,
                "title": opp.title,
                "process_ref": opp.process_ref,
                "subtitle": opp.sales_rep.name if opp.sales_rep else None,
                "splits": splits,
                "split_kind": "lead",
                "total_amount": total,
                "paid_amount": paid,
                "open_amount": total - paid,
                "all_paid": all_paid,
                "sort_at": opp.updated_at,
            }
        )

    sales = (
        CommissionSale.query.options(selectinload(CommissionSale.splits))
        .order_by(CommissionSale.updated_at.desc())
        .limit(1000)
        .all()
    )
    for sale in sales:
        splits = list(sale.splits or [])
        if not splits:
            continue
        total = Decimal(0)
        paid = Decimal(0)
        for s in splits:
            amt = Decimal(str(s.amount_brl)) if s.amount_brl is not None else Decimal(0)
            total += amt
            if s.payout_status == "pago":
                paid += amt
        all_paid = all(s.payout_status == "pago" for s in splits)
        if paid_only and not all_paid:
            continue
        if not paid_only and all_paid:
            continue
        groups.append(
            {
                "kind": "sale",
                "opportunity": None,
                "sale": sale,
                "title": sale.title,
                "process_ref": sale.process_ref,
                "subtitle": sale.organization,
                "splits": splits,
                "split_kind": "sale",
                "total_amount": total,
                "paid_amount": paid,
                "open_amount": total - paid,
                "all_paid": all_paid,
                "sort_at": sale.updated_at,
            }
        )

    groups.sort(key=lambda g: g["sort_at"] or "", reverse=True)
    return groups


def _finance_lead_groups(*, paid_only: bool) -> list[dict]:
    return _finance_rateio_groups(paid_only=paid_only)


def _parse_rateio_mode(raw: str | None) -> str:
    mode = (raw or "").strip()
    if mode in ("no_seller", "with_seller", "custom"):
        return mode
    return "no_seller"


def _apply_project_rateio_mode(project: CommissionProject, mode: str) -> None:
    project.rateio_mode = mode
    project.with_seller = mode == "with_seller"


def _rateio_line_kind_label(kind: str) -> str:
    return {
        "seller": "Vendedor",
        "stakeholder": "Sócios (participação cadastrada)",
        "custom": "Participante fixo",
    }.get(kind, kind)


def _parse_rateio_line_form() -> tuple[str | None, str | None, Decimal | None, str | None]:
    kind = (request.form.get("recipient_kind") or "").strip()
    if kind not in ("seller", "stakeholder", "custom"):
        return None, None, None, "Selecione o tipo de participante."
    label = (request.form.get("label") or "").strip()
    if kind == "seller":
        label = label or "Vendedor"
    elif kind == "stakeholder":
        label = label or "Sócios"
    if not label:
        return None, None, None, "Informe o nome do participante."
    pool_share = parse_tier_percent(request.form.get("pool_share_percent"))
    if pool_share is None:
        return None, None, None, "Informe um percentual válido para a participação."
    return kind, label[:160], pool_share, None


def _rateio_lines_total(lines) -> Decimal:
    return sum(Decimal(str(line.pool_share_percent)) for line in lines)


def _stakeholder_role_label(role_key: str) -> str:
    return "Fluxo de caixa" if role_key == "fluxo_caixa" else "Sócio"


def _parse_stakeholder_form() -> tuple[str | None, Decimal | None, str | None, str | None]:
    name = (request.form.get("name") or "").strip()
    if not name:
        return None, None, None, "Informe o nome do sócio."
    share = parse_tier_percent(request.form.get("share_percent"))
    if share is None:
        return None, None, None, "Informe uma participação válida (%)."
    if share <= 0 or share > 100:
        return None, None, None, "A participação deve ser entre 0 e 100%."
    role = (request.form.get("role_key") or "socio").strip()
    if role not in ("socio", "fluxo_caixa"):
        role = "socio"
    return name[:160], share, role, None


def _finalize_stakeholder_save(success_msg: str, *, edit_socio_id: int | None = None):
    ok, err = validate_stakeholders_total()
    if ok:
        refresh_all_commission_tier_splits()
        db.session.commit()
        flash(f"{success_msg} Faixas de comissão recalculadas.", "ok")
        return redirect(url_for("crm.crm_commission_projects"))
    db.session.commit()
    flash(
        f"{success_msg} {err} Você pode ajustar os demais sócios até completar 100%.",
        "warning",
    )
    if edit_socio_id:
        return redirect(
            url_for("crm.crm_commission_projects", edit_socio=edit_socio_id)
        )
    return redirect(url_for("crm.crm_commission_projects"))


def _commission_project_form_ctx(project: CommissionProject | None = None) -> dict:
    return {
        "project": project,
        "rateio_mode_label_fn": rateio_mode_label,
    }


def _parse_sale_date(raw: str | None):
    return _main()._parse_pipeline_date(raw)


def _parse_commission_sale_participants(
    value_brl: Decimal | None,
) -> list[dict]:
    names = request.form.getlist("participant_name[]")
    if not names:
        names = request.form.getlist("participant_name")
    orgs = request.form.getlist("participant_org[]")
    if not orgs:
        orgs = request.form.getlist("participant_org")
    amounts = request.form.getlist("participant_amount[]")
    if not amounts:
        amounts = request.form.getlist("participant_amount")
    shares = request.form.getlist("participant_share[]")
    if not shares:
        shares = request.form.getlist("participant_share")

    rows: list[dict] = []
    for i, raw_name in enumerate(names):
        name = (raw_name or "").strip()
        if not name:
            continue
        org = (orgs[i] if i < len(orgs) else "") or ""
        org = org.strip() or None
        amt = _parse_money(amounts[i] if i < len(amounts) else None)
        share_raw = (shares[i] if i < len(shares) else "") or ""
        share = None
        if share_raw.strip():
            try:
                share = Decimal(share_raw.strip().replace(",", "."))
            except Exception:
                share = None
        rows.append(
            {
                "recipient_name": name,
                "organization": org,
                "amount_brl": amt,
                "share_percent": share,
            }
        )

    if not rows:
        return []

    if value_brl is not None and all(r["amount_brl"] is None for r in rows):
        base = Decimal(str(value_brl))
        if rows and all(r["share_percent"] is not None for r in rows):
            for r in rows:
                r["amount_brl"] = (base * r["share_percent"] / Decimal(100)).quantize(
                    Decimal("0.01")
                )
        elif len(rows) == 1:
            rows[0]["amount_brl"] = base

    return rows


def _apply_commission_sale_splits(
    sale: CommissionSale,
    value_brl: Decimal | None,
    rows: list[dict] | None = None,
) -> None:
    if rows is None:
        rows = _parse_commission_sale_participants(value_brl)
    old_status = {s.recipient_name: s.payout_status for s in sale.splits}
    sale.splits.clear()
    db.session.flush()
    for i, row in enumerate(rows):
        name = row["recipient_name"]
        sale.splits.append(
            CommissionSaleSplit(
                recipient_name=name,
                organization=row.get("organization"),
                share_percent=row.get("share_percent"),
                amount_brl=row.get("amount_brl"),
                sort_order=i,
                payout_status=old_status.get(name, "pendente"),
            )
        )


def _commission_sale_form_ctx(sale: CommissionSale | None = None) -> dict:
    return {"sale": sale, "stakeholders": CompanyStakeholder.query.filter_by(is_active=True).order_by(CompanyStakeholder.sort_order).all()}


def _partners_for_form() -> list[Partner]:
    return (
        Partner.query.order_by(
            Partner.is_active.desc(),
            Partner.company_name.asc().nulls_last(),
            Partner.name.asc(),
        ).all()
    )


def _validate_lead_links(
    portal_client_id_raw: str | None,
    partner_id_raw: str | None,
) -> tuple[int | None, int | None, str | None]:
    pid = (portal_client_id_raw or "").strip()
    if not pid.isdigit():
        return None, None, "Selecione um cliente cadastrado antes de salvar o lead."
    client = db.session.get(PortalClient, int(pid))
    if not client:
        return None, None, "Cliente inválido. Cadastre o cliente em Clientes antes de salvar."

    prid = (partner_id_raw or "").strip()
    if not prid.isdigit():
        return None, None, "Selecione um fornecedor cadastrado antes de salvar o lead."
    partner = db.session.get(Partner, int(prid))
    if not partner:
        return None, None, "Fornecedor inválido. Cadastre o fornecedor em Fornecedores antes de salvar."

    return client.id, partner.id, None


def _apply_lead_links(opp: Opportunity) -> str | None:
    client_id, partner_id, err = _validate_lead_links(
        request.form.get("portal_client_id"),
        request.form.get("partner_id"),
    )
    if err:
        return err
    opp.portal_client_id = client_id
    opp.partner_id = partner_id
    return None


def _op_form_base_ctx(opp: Opportunity | None = None) -> dict:
    projects = _commission_projects_for_form()
    return {
        "opp": opp,
        "stages": STAGES,
        "catalog_choices": _main()._crm_catalog_choices(),
        "sales_reps": _main()._crm_active_sales_reps(),
        "clients": PortalClient.query.order_by(PortalClient.name.asc()).all(),
        "partners": _partners_for_form(),
        "commission_projects": projects,
        "commission_projects_json": _commission_projects_json(projects),
        "tier_label_fn": tier_label,
        "stage_normalize_fn": normalize_stage_key,
        **_op_form_pipeline_ctx(opp),
    }


def _main():
    import app as main_app

    return main_app


def crm_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("crm_ok"):
            return redirect(url_for("crm.crm_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _parse_money(raw: str | None) -> Decimal | None:
    return _main().parse_money_brl(raw)


def _parse_catalog_lines() -> list[tuple[int, int]]:
    return _main()._parse_catalog_lines_from_form()


def _sync_pipeline(opp: Opportunity) -> None:
    _main()._sync_opportunity_pipeline_data(opp)


def _op_form_pipeline_ctx(opp: Opportunity | None) -> dict:
    stage = normalize_stage_key(opp.stage if opp else "novo")
    return {
        "pipeline_stage_field_defs": STAGE_FIELD_DEFS,
        "pipeline_stage_labels": STAGE_LABELS,
        "pipeline_visible_stages": stages_with_fields_up_to(stage),
        "pipeline_current_stage": stage,
    }


def _sync_catalog(opp: Opportunity, lines: list[tuple[int, int]] | None = None) -> None:
    if lines is None:
        lines = _parse_catalog_lines()
    _main()._sync_opportunity_catalog_lines(opp, lines)


def _apply_commission(opp: Opportunity) -> None:
    tier_raw = (request.form.get("commission_tier_id") or "").strip()
    tier: CommissionProjectTier | None = None
    if tier_raw.isdigit():
        tier = (
            CommissionProjectTier.query.options(
                selectinload(CommissionProjectTier.splits)
            )
            .filter_by(id=int(tier_raw))
            .first()
        )
    if tier is not None:
        apply_tier_to_opportunity(opp, tier)
        opp.process_ref = (request.form.get("process_ref") or "").strip() or None
        return

    opp.commission_project_id = None
    opp.commission_tier_id = None
    if opp.id:
        OpportunityCommissionSplit.query.filter_by(opportunity_id=opp.id).delete()
    raw = request.form.get("rep_commission_brl")
    if (raw or "").strip() == "":
        opp.rep_commission_brl = None
    else:
        parsed = _parse_money(raw)
        if parsed is not None:
            opp.rep_commission_brl = parsed
    opp.rep_commission_note = (request.form.get("rep_commission_note") or "").strip() or None
    opp.process_ref = (request.form.get("process_ref") or "").strip() or None


def _commission_projects_for_form() -> list[CommissionProject]:
    return (
        CommissionProject.query.filter_by(is_active=True)
        .options(
            selectinload(CommissionProject.tiers).selectinload(CommissionProjectTier.splits),
        )
        .order_by(CommissionProject.sort_order.asc(), CommissionProject.title.asc())
        .all()
    )


def _commission_projects_json(projects: list[CommissionProject]) -> list[dict]:
    rows: list[dict] = []
    for project in projects:
        tiers: list[dict] = []
        for tier in project.tiers:
            tiers.append(
                {
                    "id": tier.id,
                    "percent_total": float(tier.percent_total),
                    "with_seller": bool(tier.with_seller),
                    "label": tier_percent_label(tier),
                    "splits": [
                        {
                            "label": s.label,
                            "share_percent": float(s.share_percent),
                            "recipient_kind": s.recipient_kind,
                        }
                        for s in tier.splits
                    ],
                }
            )
        rows.append(
            {
                "id": project.id,
                "title": project.title,
                "with_seller": bool(project.with_seller),
                "tiers": tiers,
            }
        )
    return rows


def _tiers_for_project(project_id: int | None) -> list[CommissionProjectTier]:
    if not project_id:
        return []
    return (
        CommissionProjectTier.query.filter_by(project_id=project_id)
        .options(selectinload(CommissionProjectTier.splits))
        .order_by(CommissionProjectTier.sort_order.asc())
        .all()
    )


def _apply_sales_rep(opp: Opportunity) -> None:
    raw = (request.form.get("sales_rep_id") or "").strip()
    if not raw:
        opp.sales_rep_id = None
        return
    if not raw.isdigit():
        opp.sales_rep_id = None
        return
    rep = db.session.get(SalesRepresentative, int(raw))
    opp.sales_rep_id = rep.id if rep and rep.is_active else None


def _delete_lead_ok(raw: str | None) -> bool:
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    return "".join(s.split()).upper() == "EXCLUIR"


@crm_bp.route("/catalogo", defaults={"_path": ""})
@crm_bp.route("/catalogo/<path:_path>")
def legacy_crm_catalog_redirect(_path: str = ""):
    return redirect(url_for("admin_catalog_list"))


@crm_bp.route("/site")
def legacy_crm_site_redirect():
    return redirect(url_for("admin_site_edit"))


@crm_bp.route("/login", methods=["GET", "POST"], endpoint="crm_login")
def login():
    m = _main()
    if not m._crm_password_configured() and not m._portal_master_login_enabled():
        if request.method == "POST":
            flash("CRM não configurado: defina CRM_ADMIN_PASSWORD no .env.", "error")
        return render_template("crm/login.html", crm_disabled=True, login_area="crm")
    if request.method == "POST":
        email = m._normalize_rep_email(request.form.get("email"))
        password = (request.form.get("password") or "").strip()
        if email:
            rep = m._authenticate_admin_rep(email, password)
            if rep:
                session["rep_id"] = rep.id
                m._grant_staff_sessions_for_admin_rep(rep)
                session["crm_ok"] = True
                session.modified = True
                return redirect(m._safe_internal_redirect(
                    request.args.get("next"), url_for("crm.crm_dashboard"), ("/crm/login",)
                ))
        if m._password_matches(m._crm_password(), password) or m._portal_master_password_matches(password):
            session["crm_ok"] = True
            session["admin_ok"] = True
            session.modified = True
            return redirect(m._safe_internal_redirect(
                request.args.get("next"), url_for("crm.crm_dashboard"), ("/crm/login",)
            ))
        flash("Senha incorreta.", "error")
    return render_template("crm/login.html", crm_disabled=False, login_area="crm")


@crm_bp.route("/logout", endpoint="crm_logout")
def logout():
    return _main()._staff_logout()


@crm_bp.route("/", endpoint="crm_dashboard")
@crm_login_required
def dashboard():
    stage = request.args.get("stage", "").strip()
    q = Opportunity.query
    if stage:
        q = q.filter_by(stage=stage)
    leads = (
        q.options(
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.portal_client),
            selectinload(Opportunity.sales_rep),
        )
        .order_by(Opportunity.updated_at.desc())
        .limit(200)
        .all()
    )
    total_clients = PortalClient.query.count()
    total_products = CatalogItem.query.count()
    total_leads = Opportunity.query.count()
    commission_sum = (
        db.session.query(func.coalesce(func.sum(Opportunity.rep_commission_brl), 0))
        .filter(Opportunity.rep_commission_brl.isnot(None))
        .scalar()
    )
    pending_payments = RepFinancialEntry.query.filter(
        RepFinancialEntry.status.in_(("enviado", "em_analise", "aprovado"))
    ).count()
    return render_template(
        "crm/dashboard.html",
        leads=leads,
        stages=STAGES,
        filter_stage=stage,
        total_clients=total_clients,
        total_products=total_products,
        total_leads=total_leads,
        commission_sum=commission_sum,
        pending_payments=pending_payments,
        stage_normalize_fn=normalize_stage_key,
        stage_label_fn=pipeline_stage_label,
    )


@crm_bp.route("/clientes", endpoint="crm_clients_list")
@crm_login_required
def clients_list():
    q = (request.args.get("q") or "").strip()
    query = PortalClient.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PortalClient.name.ilike(like),
                PortalClient.email.ilike(like),
                PortalClient.organization.ilike(like),
                PortalClient.razao_social.ilike(like),
                PortalClient.sector.ilike(like),
                PortalClient.cnpj.ilike(like),
            )
        )
    clients = query.order_by(PortalClient.name.asc()).all()
    return render_template("crm/clientes_list.html", clients=clients, q=q)


@crm_bp.route("/clientes/novo", methods=["GET", "POST"], endpoint="crm_client_new")
@crm_login_required
def client_new():
    m = _main()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        ctx = {
            "client": None,
            "br_ufs": m.BR_UFS,
            "catalog_sphere_choices": m.CATALOG_SPHERE_CHOICES,
        }
        if not email or "@" not in email:
            flash("Informe um e-mail válido.", "error")
            return render_template("crm/cliente_form.html", **ctx)
        if not name:
            flash("Informe o nome.", "error")
            return render_template("crm/cliente_form.html", **ctx)
        if PortalClient.query.filter_by(email=email).first():
            flash("Já existe cliente com este e-mail.", "error")
            return render_template("crm/cliente_form.html", **ctx)
        if len(password) < 8:
            flash("Senha mínima de 8 caracteres (acesso área do cliente).", "error")
            return render_template("crm/cliente_form.html", **ctx)
        client = PortalClient(
            email=email,
            password_hash=generate_password_hash(password),
            name=name,
        )
        m._apply_portal_client_profile_from_form(client)
        db.session.add(client)
        db.session.commit()
        m._retro_link_opportunities_to_client(client)
        db.session.commit()
        flash("Cliente cadastrado.", "ok")
        return redirect(url_for("crm.crm_client_edit", client_id=client.id))
    return render_template(
        "crm/cliente_form.html",
        client=None,
        br_ufs=m.BR_UFS,
        catalog_sphere_choices=m.CATALOG_SPHERE_CHOICES,
    )


@crm_bp.route("/clientes/<int:client_id>", methods=["GET", "POST"], endpoint="crm_client_edit")
@crm_login_required
def client_edit(client_id):
    m = _main()
    client = PortalClient.query.get_or_404(client_id)
    ctx = {
        "client": client,
        "br_ufs": m.BR_UFS,
        "catalog_sphere_choices": m.CATALOG_SPHERE_CHOICES,
    }
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        if not email or "@" not in email:
            flash("E-mail inválido.", "error")
            return render_template("crm/cliente_form.html", **ctx)
        other = PortalClient.query.filter(
            PortalClient.email == email, PortalClient.id != client.id
        ).first()
        if other:
            flash("Outro cliente usa este e-mail.", "error")
            return render_template("crm/cliente_form.html", **ctx)
        client.email = email
        client.name = name or client.name
        m._apply_portal_client_profile_from_form(client)
        password = request.form.get("password") or ""
        if password:
            if len(password) < 8:
                flash("Nova senha: mínimo 8 caracteres.", "error")
                return render_template("crm/cliente_form.html", **ctx)
            client.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Cliente atualizado.", "ok")
        return redirect(url_for("crm.crm_clients_list"))
    leads = (
        Opportunity.query.filter_by(portal_client_id=client.id)
        .order_by(Opportunity.updated_at.desc())
        .all()
    )
    return render_template("crm/cliente_form.html", leads=leads, **ctx)


@crm_bp.route("/fornecedores", endpoint="crm_partners_list")
@crm_login_required
def partners_list():
    q = (request.args.get("q") or "").strip()
    query = Partner.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Partner.name.ilike(like),
                Partner.email.ilike(like),
                Partner.company_name.ilike(like),
                Partner.razao_social.ilike(like),
                Partner.cnpj.ilike(like),
                Partner.phone.ilike(like),
            )
        )
    partners = query.order_by(Partner.company_name.asc().nulls_last(), Partner.name.asc()).all()
    return render_template("crm/fornecedores_list.html", partners=partners, q=q)


@crm_bp.route("/fornecedores/novo", methods=["GET", "POST"], endpoint="crm_partner_new")
@crm_login_required
def partner_new():
    m = _main()
    ctx = {"partner": None, "br_ufs": m.BR_UFS}
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = m._normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        if len(password) < 8:
            flash("Senha mínima de 8 caracteres.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        if password != password2:
            flash("As senhas não coincidem.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        if Partner.query.filter_by(email=email).first():
            flash("Já existe fornecedor com este e-mail.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        partner = Partner(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            is_active=True,
        )
        m._apply_partner_profile_from_form(partner)
        db.session.add(partner)
        db.session.commit()
        flash("Fornecedor cadastrado.", "ok")
        return redirect(url_for("crm.crm_partner_edit", partner_id=partner.id))
    return render_template("crm/fornecedor_form.html", **ctx)


@crm_bp.route("/fornecedores/<int:partner_id>", methods=["GET", "POST"], endpoint="crm_partner_edit")
@crm_login_required
def partner_edit(partner_id: int):
    m = _main()
    partner = Partner.query.get_or_404(partner_id)
    ctx = {"partner": partner, "br_ufs": m.BR_UFS}
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = m._normalize_rep_email(request.form.get("email"))
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        other = Partner.query.filter(Partner.email == email, Partner.id != partner.id).first()
        if other:
            flash("Outro fornecedor usa este e-mail.", "error")
            return render_template("crm/fornecedor_form.html", **ctx)
        partner.name = name
        partner.email = email
        partner.is_active = request.form.get("is_active") == "1"
        m._apply_partner_profile_from_form(partner)
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if password:
            if len(password) < 8:
                flash("Nova senha: mínimo 8 caracteres.", "error")
                return render_template("crm/fornecedor_form.html", **ctx)
            if password != password2:
                flash("As senhas não coincidem.", "error")
                return render_template("crm/fornecedor_form.html", **ctx)
            partner.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Fornecedor atualizado.", "ok")
        return redirect(url_for("crm.crm_partner_edit", partner_id=partner.id))
    leads = (
        Opportunity.query.filter_by(partner_id=partner.id)
        .order_by(Opportunity.updated_at.desc())
        .limit(50)
        .all()
    )
    return render_template(
        "crm/fornecedor_form.html",
        leads=leads,
        stage_label_fn=pipeline_stage_label,
        br_ufs=m.BR_UFS,
        partner=partner,
    )


@crm_bp.route("/produtos", endpoint="crm_products_list")
@crm_login_required
def products_list():
    q = (request.args.get("q") or "").strip()
    query = CatalogItem.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(CatalogItem.title.ilike(like), CatalogItem.slug.ilike(like)))
    products = query.order_by(CatalogItem.title.asc()).limit(500).all()
    return render_template("crm/produtos_list.html", products=products, q=q)


@crm_bp.route("/produtos/novo", methods=["GET", "POST"], endpoint="crm_product_new")
@crm_login_required
def product_new():
    m = _main()
    if request.method == "POST":
        item, ok = m.save_catalog_item_from_request(None)
        if ok:
            flash("Produto cadastrado.", "ok")
            return redirect(url_for("crm.crm_product_edit", item_id=item.id))
        return render_template(
            "admin/catalog_form.html", **m._admin_catalog_form_ctx(item), crm_mode=True
        )
    return render_template(
        "admin/catalog_form.html", **m._admin_catalog_form_ctx(), crm_mode=True
    )


@crm_bp.route("/produtos/<int:item_id>", methods=["GET", "POST"], endpoint="crm_product_edit")
@crm_login_required
def product_edit(item_id):
    m = _main()
    item = CatalogItem.query.get_or_404(item_id)
    if request.method == "POST":
        item, ok = m.save_catalog_item_from_request(item)
        if ok:
            flash("Produto atualizado.", "ok")
            return redirect(url_for("crm.crm_products_list"))
        return render_template(
            "admin/catalog_form.html", **m._admin_catalog_form_ctx(item), crm_mode=True
        )
    return render_template(
        "admin/catalog_form.html", **m._admin_catalog_form_ctx(item), crm_mode=True
    )


@crm_bp.route("/leads/nova", methods=["GET", "POST"], endpoint="crm_op_new")
@crm_login_required
def lead_new():
    if request.method == "POST":
        client_id, partner_id, link_err = _validate_lead_links(
            request.form.get("portal_client_id"),
            request.form.get("partner_id"),
        )
        if link_err:
            flash(link_err, "error")
            return render_template(
                "crm/op_form.html",
                **_op_form_base_ctx(None),
            )
        title = (request.form.get("title") or "").strip() or "Novo lead"
        catalog_lines = _parse_catalog_lines()
        source = (request.form.get("source") or "").strip() or "CRM"
        opp = Opportunity(
            title=title,
            contact_name=(request.form.get("contact_name") or "").strip() or None,
            organization=(request.form.get("organization") or "").strip() or None,
            cnpj=_main()._normalize_cnpj_field(request.form.get("cnpj")),
            email=(request.form.get("email") or "").strip() or None,
            phone=(request.form.get("phone") or "").strip() or None,
            sphere=(request.form.get("sphere") or "").strip() or None,
            stage=normalize_stage_key(request.form.get("stage"), default="novo"),
            notes=(request.form.get("notes") or "").strip() or None,
            source=source,
            portal_client_id=client_id,
            partner_id=partner_id,
        )
        _apply_sales_rep(opp)
        db.session.add(opp)
        db.session.flush()
        _sync_catalog(opp, catalog_lines)
        _sync_pipeline(opp)
        _apply_commission(opp)
        db.session.commit()
        flash("Lead criado.", "ok")
        return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))
    return render_template(
        "crm/op_form.html",
        **_op_form_base_ctx(None),
    )


@crm_bp.route("/leads/<int:opp_id>", methods=["GET", "POST"], endpoint="crm_op_edit")
@crm_bp.route("/oportunidade/<int:opp_id>", methods=["GET", "POST"])
@crm_login_required
def lead_edit(opp_id):
    opp = (
        Opportunity.query.options(
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.commission_splits),
            selectinload(Opportunity.commission_tier),
            selectinload(Opportunity.sales_rep),
        )
        .filter_by(id=opp_id)
        .first_or_404()
    )
    legacy_stage = normalize_stage_key(opp.stage)
    if opp.stage != legacy_stage:
        opp.stage = legacy_stage
        db.session.commit()
    if request.method == "POST":
        link_err = _apply_lead_links(opp)
        if link_err:
            flash(link_err, "error")
            chat_messages = _main()._lead_chat_messages_for_opportunity(
                opp.id, _main().LEAD_CHAT_THREAD_CLIENT
            )
            internal_chat_messages = _main()._lead_chat_messages_for_opportunity(
                opp.id, _main().LEAD_CHAT_THREAD_INTERNAL
            )
            rep_name = (opp.sales_rep.name if opp.sales_rep else "") or "Vendedor"
            return render_template(
                "crm/op_form.html",
                chat_messages=chat_messages,
                chat_form_action=url_for("crm.crm_op_chat", opp_id=opp.id),
                internal_chat_messages=internal_chat_messages,
                internal_chat_form_action=url_for("crm.crm_op_internal_chat", opp_id=opp.id),
                internal_chat_viewer_is_rep=False,
                internal_chat_rep_name=rep_name,
                **_op_form_base_ctx(opp),
            )
        opp.title = (request.form.get("title") or "").strip() or opp.title
        opp.contact_name = (request.form.get("contact_name") or "").strip() or None
        opp.organization = (request.form.get("organization") or "").strip() or None
        opp.cnpj = _main()._normalize_cnpj_field(request.form.get("cnpj"))
        opp.email = (request.form.get("email") or "").strip() or None
        opp.phone = (request.form.get("phone") or "").strip() or None
        opp.sphere = (request.form.get("sphere") or "").strip() or None
        opp.stage = normalize_stage_key(request.form.get("stage"), default=opp.stage)
        opp.notes = (request.form.get("notes") or "").strip() or None
        opp.source = (request.form.get("source") or "").strip() or None
        catalog_lines = _parse_catalog_lines()
        _apply_sales_rep(opp)
        _sync_catalog(opp, catalog_lines)
        _sync_pipeline(opp)
        _apply_commission(opp)
        db.session.commit()
        flash("Lead salvo.", "ok")
        return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))
    chat_messages = _main()._lead_chat_messages_for_opportunity(
        opp.id, _main().LEAD_CHAT_THREAD_CLIENT
    )
    internal_chat_messages = _main()._lead_chat_messages_for_opportunity(
        opp.id, _main().LEAD_CHAT_THREAD_INTERNAL
    )
    rep_name = (opp.sales_rep.name if opp.sales_rep else "") or "Vendedor"
    return render_template(
        "crm/op_form.html",
        chat_messages=chat_messages,
        chat_form_action=url_for("crm.crm_op_chat", opp_id=opp.id),
        internal_chat_messages=internal_chat_messages,
        internal_chat_form_action=url_for("crm.crm_op_internal_chat", opp_id=opp.id),
        internal_chat_viewer_is_rep=False,
        internal_chat_rep_name=rep_name,
        **_op_form_base_ctx(opp),
    )


@crm_bp.route(
    "/leads/<int:opp_id>/pipeline-anexo/<stage_key>/<int:idx>",
    endpoint="crm_op_pipeline_anexo",
)
@crm_login_required
def lead_pipeline_anexo(opp_id: int, stage_key: str, idx: int):
    from flask import current_app, send_from_directory

    opp = Opportunity.query.get_or_404(opp_id)
    stage_key = normalize_stage_key(stage_key, default="")
    if stage_key not in STAGE_FIELD_DEFS:
        abort(404)
    attachments = opp.pipeline_attachments(stage_key)
    if idx < 0 or idx >= len(attachments):
        abort(404)
    att = attachments[idx]
    relpath = (att.get("relpath") or "").replace("\\", "/")
    if not relpath.startswith("uploads/lead_pipeline/"):
        abort(404)
    directory = os.path.join(current_app.root_path, "static", os.path.dirname(relpath))
    filename = os.path.basename(relpath)
    return send_from_directory(directory, filename, as_attachment=True, download_name=att.get("name") or filename)


@crm_bp.route("/leads/<int:opp_id>/estagio", methods=["POST"], endpoint="crm_op_stage")
@crm_bp.route("/oportunidade/<int:opp_id>/estagio", methods=["POST"])
@crm_login_required
def lead_stage(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    new_stage = normalize_stage_key(request.form.get("stage"), default="")
    if new_stage in dict(STAGES):
        opp.stage = new_stage
        db.session.commit()
        flash("Estágio atualizado.", "ok")
    return redirect(url_for("crm.crm_dashboard", stage=request.args.get("stage", "")))


@crm_bp.route("/leads/<int:opp_id>/excluir", methods=["GET", "POST"], endpoint="crm_op_delete")
@crm_bp.route("/oportunidade/<int:opp_id>/excluir", methods=["GET", "POST"])
@crm_login_required
def lead_delete(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    if request.method == "GET":
        return render_template("crm/op_delete_confirm.html", opp=opp)
    if not _delete_lead_ok(request.form.get("confirm")):
        flash('Digite EXCLUIR para confirmar.', "error")
        return redirect(url_for("crm.crm_op_delete", opp_id=opp_id))
    _main()._crm_delete_opportunity(opp)
    flash("Lead excluído.", "ok")
    return redirect(url_for("crm.crm_dashboard"))


@crm_bp.route("/leads/<int:opp_id>/mensagem", methods=["POST"], endpoint="crm_op_chat")
@crm_bp.route("/oportunidade/<int:opp_id>/mensagem", methods=["POST"])
@crm_login_required
def lead_chat(opp_id):
    m = _main()
    opp = Opportunity.query.get_or_404(opp_id)
    body = (request.form.get("chat_body") or "").strip()
    files = m._lead_chat_files_from_request()
    atts, att_err = m._save_lead_chat_files(files)
    if att_err:
        flash(att_err, "error")
    elif not body and not atts:
        flash("Escreva uma mensagem ou anexe ao menos um arquivo.", "error")
    elif len(body) > 12000:
        flash("Mensagem muito longa (máx. 12.000 caracteres).", "error")
    else:
        msg = LeadMessage(
            opportunity_id=opp.id,
            thread=_main().LEAD_CHAT_THREAD_CLIENT,
            sender="staff",
            body=body,
            attachments_json=json.dumps(atts, ensure_ascii=False) if atts else None,
        )
        db.session.add(msg)
        db.session.commit()
        m._notify_portal_client_crm_update(
            opp, [], body if body else None, has_attachments=bool(atts)
        )
        flash("Mensagem enviada ao cliente.", "ok")
    return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))


@crm_bp.route("/leads/<int:opp_id>/mensagem-interna", methods=["POST"], endpoint="crm_op_internal_chat")
@crm_bp.route("/oportunidade/<int:opp_id>/mensagem-interna", methods=["POST"])
@crm_login_required
def lead_internal_chat(opp_id):
    m = _main()
    opp = Opportunity.query.get_or_404(opp_id)
    body, atts, err = m._lead_chat_validate_post()
    if err:
        flash(err, "error")
    else:
        msg = LeadMessage(
            opportunity_id=opp.id,
            thread=m.LEAD_CHAT_THREAD_INTERNAL,
            sender="staff",
            body=body,
            attachments_json=json.dumps(atts, ensure_ascii=False) if atts else None,
        )
        db.session.add(msg)
        db.session.commit()
        flash("Mensagem enviada ao vendedor.", "ok")
    return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))


def _metas_page_context() -> dict:
    finance_goal = get_or_create_finance_goal()
    commission_projects = (
        CommissionProject.query.options(
            selectinload(CommissionProject.tiers).selectinload(CommissionProjectTier.splits),
        )
        .filter_by(is_active=True)
        .order_by(CommissionProject.sort_order.asc(), CommissionProject.title.asc())
        .all()
    )
    simulation_rows = (
        FinanceSimulationLine.query.options(
            selectinload(FinanceSimulationLine.commission_tier).selectinload(
                CommissionProjectTier.splits
            ),
            selectinload(FinanceSimulationLine.commission_tier).selectinload(
                CommissionProjectTier.project
            ),
        )
        .order_by(FinanceSimulationLine.sort_order.asc(), FinanceSimulationLine.id.asc())
        .all()
    )
    simulation_enriched = [enrich_simulation_line(row) for row in simulation_rows]
    goals_ctx = finance_dashboard(finance_goal, simulation_enriched)
    goal_tier = None
    goal_commission = None
    if finance_goal.commission_tier_id:
        goal_tier = CommissionProjectTier.query.options(
            selectinload(CommissionProjectTier.splits),
            selectinload(CommissionProjectTier.project),
        ).filter_by(id=finance_goal.commission_tier_id).first()
        if goal_tier:
            goal_commission = goal_commission_projection(finance_goal, goal_tier)
    edit_sim_id = request.args.get("edit_sim", type=int)
    edit_simulation = None
    if edit_sim_id:
        edit_simulation = next(
            (item for item in simulation_enriched if item["line"].id == edit_sim_id),
            None,
        )
    return {
        "finance_goal": finance_goal,
        "commission_projects": commission_projects,
        "simulation_lines": simulation_enriched,
        "goals_ctx": goals_ctx,
        "goal_commission": goal_commission,
        "goal_tier": goal_tier,
        "edit_simulation": edit_simulation,
        "tier_label_fn": tier_label,
    }


@crm_bp.route("/metas", endpoint="crm_metas_home")
@crm_login_required
def metas_home():
    return render_template("crm/metas.html", **_metas_page_context())


@crm_bp.route("/metas", methods=["POST"], endpoint="crm_metas_save")
@crm_login_required
def metas_save():
    goal = get_or_create_finance_goal()
    goal.company_label = (request.form.get("company_label") or "").strip() or None
    year_raw = (request.form.get("goal_year") or "").strip()
    try:
        goal.goal_year = int(year_raw) if year_raw else datetime.utcnow().year
    except ValueError:
        goal.goal_year = datetime.utcnow().year
    goal.goal_annual_brl = _parse_money(request.form.get("goal_annual_brl"))
    if goal.goal_annual_brl is not None:
        goal.goal_monthly_brl = (
            Decimal(str(goal.goal_annual_brl)) / Decimal("12")
        ).quantize(Decimal("0.01"))
    else:
        goal.goal_monthly_brl = None
    tier_id = request.form.get("commission_tier_id", type=int)
    if tier_id:
        tier = CommissionProjectTier.query.filter_by(id=tier_id).first()
        goal.commission_tier_id = tier.id if tier else None
    else:
        goal.commission_tier_id = None
    db.session.commit()
    flash("Metas da empresa atualizadas.", "ok")
    return redirect(url_for("crm.crm_metas_home"))


@crm_bp.route(
    "/metas/simulacao",
    methods=["POST"],
    endpoint="crm_metas_simulation_add",
)
@crm_login_required
def metas_simulation_add():
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Informe o nome da operação simulada.", "error")
        return redirect(url_for("crm.crm_metas_home"))
    value_brl = _parse_money(request.form.get("value_brl"))
    if value_brl is None or value_brl <= 0:
        flash("Informe um valor válido para a operação.", "error")
        return redirect(url_for("crm.crm_metas_home"))
    tier_id = request.form.get("commission_tier_id", type=int)
    tier = CommissionProjectTier.query.options(
        selectinload(CommissionProjectTier.splits)
    ).filter_by(id=tier_id).first()
    if tier is None:
        flash("Selecione o tipo e a faixa de comissão.", "error")
        return redirect(url_for("crm.crm_metas_home"))
    max_order = (
        db.session.query(func.max(FinanceSimulationLine.sort_order)).scalar() or 0
    )
    db.session.add(
        FinanceSimulationLine(
            title=title[:200],
            value_brl=value_brl,
            commission_tier_id=tier.id,
            sort_order=int(max_order) + 1,
            notes=(request.form.get("notes") or "").strip() or None,
        )
    )
    db.session.commit()
    flash("Operação adicionada à simulação.", "ok")
    return redirect(url_for("crm.crm_metas_home"))


@crm_bp.route(
    "/metas/simulacao/<int:line_id>/editar",
    methods=["POST"],
    endpoint="crm_metas_simulation_edit",
)
@crm_login_required
def metas_simulation_edit(line_id: int):
    line = FinanceSimulationLine.query.get_or_404(line_id)
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Informe o nome da operação simulada.", "error")
        return redirect(url_for("crm.crm_metas_home", edit_sim=line.id))
    value_brl = _parse_money(request.form.get("value_brl"))
    if value_brl is None or value_brl <= 0:
        flash("Informe um valor válido para a operação.", "error")
        return redirect(url_for("crm.crm_metas_home", edit_sim=line.id))
    tier_id = request.form.get("commission_tier_id", type=int)
    tier = CommissionProjectTier.query.filter_by(id=tier_id).first()
    if tier is None:
        flash("Selecione o tipo e a faixa de comissão.", "error")
        return redirect(url_for("crm.crm_metas_home", edit_sim=line.id))
    line.title = title[:200]
    line.value_brl = value_brl
    line.commission_tier_id = tier.id
    line.notes = (request.form.get("notes") or "").strip() or None
    db.session.commit()
    flash("Simulação atualizada.", "ok")
    return redirect(url_for("crm.crm_metas_home"))


@crm_bp.route(
    "/metas/simulacao/<int:line_id>/excluir",
    methods=["POST"],
    endpoint="crm_metas_simulation_delete",
)
@crm_login_required
def metas_simulation_delete(line_id: int):
    line = FinanceSimulationLine.query.get_or_404(line_id)
    db.session.delete(line)
    db.session.commit()
    flash("Operação removida da simulação.", "ok")
    return redirect(url_for("crm.crm_metas_home"))


@crm_bp.route("/financeiro", endpoint="crm_financeiro_home")
@crm_login_required
def finance_home():
    leads_with_commission = (
        Opportunity.query.filter(
            or_(
                Opportunity.rep_commission_brl.isnot(None),
                Opportunity.commission_tier_id.isnot(None),
            )
        )
        .options(
            selectinload(Opportunity.sales_rep),
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.commission_tier),
            selectinload(Opportunity.commission_splits),
        )
        .order_by(Opportunity.updated_at.desc())
        .limit(100)
        .all()
    )
    lead_groups_open = _finance_lead_groups(paid_only=False)
    lead_groups_paid = _finance_lead_groups(paid_only=True)
    entries = (
        RepFinancialEntry.query.options(
            selectinload(RepFinancialEntry.sales_rep),
            selectinload(RepFinancialEntry.opportunity),
        )
        .order_by(RepFinancialEntry.updated_at.desc())
        .limit(100)
        .all()
    )
    total_commission = (
        db.session.query(func.coalesce(func.sum(Opportunity.rep_commission_brl), 0)).scalar()
    )
    total_splits_open = (
        db.session.query(func.coalesce(func.sum(OpportunityCommissionSplit.amount_brl), 0))
        .filter(OpportunityCommissionSplit.payout_status != "pago")
        .scalar()
        + db.session.query(func.coalesce(func.sum(CommissionSaleSplit.amount_brl), 0))
        .filter(CommissionSaleSplit.payout_status != "pago")
        .scalar()
    )
    total_splits_paid = (
        db.session.query(func.coalesce(func.sum(OpportunityCommissionSplit.amount_brl), 0))
        .filter(OpportunityCommissionSplit.payout_status == "pago")
        .scalar()
        + db.session.query(func.coalesce(func.sum(CommissionSaleSplit.amount_brl), 0))
        .filter(CommissionSaleSplit.payout_status == "pago")
        .scalar()
    )
    total_documents_paid = (
        db.session.query(func.coalesce(func.sum(RepFinancialEntry.amount_brl), 0))
        .filter(RepFinancialEntry.status == "pago")
        .scalar()
    )
    return render_template(
        "crm/financeiro.html",
        leads_with_commission=leads_with_commission,
        lead_groups_open=lead_groups_open,
        lead_groups_paid=lead_groups_paid,
        entries=entries,
        commission_statuses=COMMISSION_STATUSES,
        payout_statuses=PAYOUT_STATUSES,
        total_commission=total_commission,
        total_splits_open=total_splits_open,
        total_splits_paid=total_splits_paid,
        total_documents_paid=total_documents_paid,
        tier_label_fn=tier_label,
    )


@crm_bp.route(
    "/financeiro/rateio/<int:split_id>/status",
    methods=["POST"],
    endpoint="crm_commission_split_status",
)
@crm_login_required
def commission_split_status(split_id):
    row = OpportunityCommissionSplit.query.get_or_404(split_id)
    new_s = (request.form.get("status") or "").strip()
    if new_s in dict(PAYOUT_STATUSES):
        row.payout_status = new_s
        db.session.commit()
        flash("Status do rateio atualizado.", "ok")
    return redirect(url_for("crm.crm_financeiro_home"))


@crm_bp.route("/comissionamento", endpoint="crm_commission_projects")
@crm_login_required
def commission_projects():
    projects = (
        CommissionProject.query.options(
            selectinload(CommissionProject.tiers).selectinload(CommissionProjectTier.splits),
        )
        .order_by(CommissionProject.sort_order.asc(), CommissionProject.title.asc())
        .all()
    )
    stakeholders = CompanyStakeholder.query.order_by(CompanyStakeholder.sort_order).all()
    stakeholders_total = sum(
        Decimal(str(s.share_percent)) for s in stakeholders if s.is_active
    )
    edit_stakeholder_id = request.args.get("edit_socio", type=int)
    edit_stakeholder = None
    if edit_stakeholder_id:
        edit_stakeholder = next(
            (s for s in stakeholders if s.id == edit_stakeholder_id),
            None,
        )
    return render_template(
        "crm/commission_projects.html",
        projects=projects,
        stakeholders=stakeholders,
        stakeholders_total=stakeholders_total,
        edit_stakeholder=edit_stakeholder,
        stakeholder_role_label_fn=_stakeholder_role_label,
        tier_label_fn=tier_label,
        rateio_mode_label_fn=rateio_mode_label,
    )


@crm_bp.route(
    "/comissionamento/socio",
    methods=["POST"],
    endpoint="crm_commission_stakeholder_add",
)
@crm_login_required
def commission_stakeholder_add():
    name, share, role, err = _parse_stakeholder_form()
    if err:
        flash(err, "error")
        return redirect(url_for("crm.crm_commission_projects"))
    max_order = (
        db.session.query(func.max(CompanyStakeholder.sort_order)).scalar() or 0
    )
    stakeholder = CompanyStakeholder(
        name=name,
        share_percent=share,
        role_key=role,
        sort_order=int(max_order) + 1,
        is_active=True,
    )
    db.session.add(stakeholder)
    db.session.flush()
    return _finalize_stakeholder_save("Sócio adicionado.")


@crm_bp.route(
    "/comissionamento/socio/<int:stakeholder_id>/editar",
    methods=["POST"],
    endpoint="crm_commission_stakeholder_edit",
)
@crm_login_required
def commission_stakeholder_edit(stakeholder_id: int):
    stakeholder = CompanyStakeholder.query.get_or_404(stakeholder_id)
    name, share, role, err = _parse_stakeholder_form()
    if err:
        flash(err, "error")
        return redirect(
            url_for(
                "crm.crm_commission_projects",
                edit_socio=stakeholder.id,
            )
        )
    stakeholder.name = name
    stakeholder.share_percent = share
    stakeholder.role_key = role
    stakeholder.is_active = True
    db.session.flush()
    return _finalize_stakeholder_save(
        "Sócio atualizado.",
        edit_socio_id=stakeholder.id,
    )


@crm_bp.route(
    "/comissionamento/socio/<int:stakeholder_id>/excluir",
    methods=["POST"],
    endpoint="crm_commission_stakeholder_delete",
)
@crm_login_required
def commission_stakeholder_delete(stakeholder_id: int):
    stakeholder = CompanyStakeholder.query.get_or_404(stakeholder_id)
    db.session.delete(stakeholder)
    db.session.flush()
    return _finalize_stakeholder_save("Sócio removido.")


@crm_bp.route("/comissionamento/novo", methods=["GET", "POST"], endpoint="crm_commission_project_new")
@crm_login_required
def commission_project_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Informe o nome do tipo de comissão.", "error")
            return render_template(
                "crm/commission_project_form.html", **_commission_project_form_ctx()
            )
        rateio_mode = _parse_rateio_mode(request.form.get("rateio_mode"))
        cat_id = fallback_catalog_item_id()
        if cat_id is None:
            flash(
                "Cadastre ao menos um produto no catálogo antes de criar tipos de comissão.",
                "error",
            )
            return render_template(
                "crm/commission_project_form.html", **_commission_project_form_ctx()
            )
        max_order = (
            db.session.query(func.max(CommissionProject.sort_order)).scalar() or 0
        )
        project = CommissionProject(
            title=title[:200],
            with_seller=rateio_mode == "with_seller",
            rateio_mode=rateio_mode,
            catalog_item_id=cat_id,
            notes=(request.form.get("notes") or "").strip() or None,
            sort_order=int(max_order) + 1,
            is_system=False,
            is_active=True,
        )
        db.session.add(project)
        db.session.commit()
        if rateio_mode == "custom":
            flash(
                "Tipo criado. Configure os participantes do rateio personalizado abaixo.",
                "ok",
            )
        else:
            flash("Tipo de comissão criado. Adicione as faixas de percentual.", "ok")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    return render_template(
        "crm/commission_project_form.html", **_commission_project_form_ctx()
    )


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/editar",
    methods=["GET", "POST"],
    endpoint="crm_commission_project_edit",
)
@crm_login_required
def commission_project_edit(project_id: int):
    project = CommissionProject.query.get_or_404(project_id)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Informe o nome do tipo de comissão.", "error")
            return render_template(
                "crm/commission_project_form.html",
                **_commission_project_form_ctx(project),
            )
        project.title = title[:200]
        if not project.is_system:
            new_mode = _parse_rateio_mode(request.form.get("rateio_mode"))
            current_mode = project_effective_rateio_mode(project)
            if new_mode != current_mode:
                if project.tiers:
                    flash(
                        "Remova as faixas antes de alterar o modelo de rateio.",
                        "error",
                    )
                    return render_template(
                        "crm/commission_project_form.html",
                        **_commission_project_form_ctx(project),
                    )
                if current_mode == "custom" and new_mode != "custom":
                    project.rateio_lines.clear()
                _apply_project_rateio_mode(project, new_mode)
        project.notes = (request.form.get("notes") or "").strip() or None
        project.is_active = request.form.get("is_active") == "1"
        db.session.commit()
        flash("Tipo de comissão atualizado.", "ok")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    return render_template(
        "crm/commission_project_form.html", **_commission_project_form_ctx(project)
    )


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/excluir",
    methods=["GET", "POST"],
    endpoint="crm_commission_project_delete",
)
@crm_login_required
def commission_project_delete(project_id: int):
    project = CommissionProject.query.get_or_404(project_id)
    if project.is_system:
        flash("Os tipos padrão do sistema não podem ser excluídos.", "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    in_use = Opportunity.query.filter_by(commission_project_id=project.id).count()
    if request.method == "POST":
        if in_use:
            flash("Há leads usando este tipo. Desative em vez de excluir.", "error")
            return redirect(
                url_for("crm.crm_commission_project_detail", project_id=project.id)
            )
        db.session.delete(project)
        db.session.commit()
        flash("Tipo de comissão removido.", "ok")
        return redirect(url_for("crm.crm_commission_projects"))
    return render_template(
        "crm/commission_project_delete.html", project=project, in_use=in_use
    )


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/faixa",
    methods=["POST"],
    endpoint="crm_commission_tier_add",
)
@crm_login_required
def commission_tier_add(project_id: int):
    project = CommissionProject.query.options(
        selectinload(CommissionProject.rateio_lines)
    ).get_or_404(project_id)
    ok, err = validate_custom_rateio(project)
    if not ok:
        flash(err or "Configure o rateio personalizado.", "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    percent = parse_tier_percent(request.form.get("percent_total"))
    if percent is None:
        flash("Informe um percentual válido (ex.: 1,5 ou 2).", "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    add_tier_to_project(project, percent)
    db.session.commit()
    pct_txt = f"{percent:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    flash(f"Faixa de {pct_txt}% adicionada.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/faixa/<int:tier_id>/excluir",
    methods=["POST"],
    endpoint="crm_commission_tier_delete",
)
@crm_login_required
def commission_tier_delete(project_id: int, tier_id: int):
    project = CommissionProject.query.get_or_404(project_id)
    tier = CommissionProjectTier.query.filter_by(
        id=tier_id, project_id=project.id
    ).first_or_404()
    in_use = Opportunity.query.filter_by(commission_tier_id=tier.id).count()
    if in_use:
        flash("Há leads usando esta faixa. Não é possível excluir.", "error")
        return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))
    if project.is_system and len(project.tiers) <= 1:
        flash("O tipo padrão precisa manter ao menos uma faixa.", "error")
        return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))
    db.session.delete(tier)
    db.session.commit()
    flash("Faixa removida.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/faixa/<int:tier_id>/atualizar",
    methods=["POST"],
    endpoint="crm_commission_tier_refresh",
)
@crm_login_required
def commission_tier_refresh(project_id: int, tier_id: int):
    project = CommissionProject.query.get_or_404(project_id)
    tier = CommissionProjectTier.query.options(
        selectinload(CommissionProjectTier.splits),
        selectinload(CommissionProjectTier.project).selectinload(
            CommissionProject.rateio_lines
        ),
    ).filter_by(id=tier_id, project_id=project.id).first_or_404()
    sync_tier_splits(tier)
    db.session.commit()
    flash("Rateio da faixa recalculado com os sócios atuais.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route("/comissionamento/vendas", endpoint="crm_commission_sales")
@crm_login_required
def commission_sales_list():
    return redirect(url_for("crm.crm_commission_projects"))


@crm_bp.route("/comissionamento/vendas/nova", methods=["GET", "POST"], endpoint="crm_commission_sale_new")
@crm_login_required
def commission_sale_new():
    return redirect(url_for("crm.crm_commission_projects"))


@crm_bp.route(
    "/comissionamento/vendas/<int:sale_id>",
    methods=["GET", "POST"],
    endpoint="crm_commission_sale_edit",
)
@crm_login_required
def commission_sale_edit(sale_id: int):
    sale = (
        CommissionSale.query.options(selectinload(CommissionSale.splits))
        .filter_by(id=sale_id)
        .first_or_404()
    )
    if request.method == "POST":
        sale.title = (request.form.get("title") or "").strip() or sale.title
        sale.organization = (request.form.get("organization") or "").strip() or None
        value_brl = _parse_money(request.form.get("value_brl"))
        sale.value_brl = value_brl
        sale.sale_date = _parse_sale_date(request.form.get("sale_date"))
        sale.process_ref = (request.form.get("process_ref") or "").strip() or None
        sale.notes = (request.form.get("notes") or "").strip() or None
        rows = _parse_commission_sale_participants(value_brl)
        if not rows:
            flash("Adicione ao menos um participante no rateio.", "error")
            return render_template(
                "crm/commission_sale_form.html", **_commission_sale_form_ctx(sale)
            )
        _apply_commission_sale_splits(sale, value_brl, rows)
        db.session.commit()
        flash("Venda atualizada.", "ok")
        return redirect(url_for("crm.crm_commission_sale_edit", sale_id=sale.id))
    return render_template(
        "crm/commission_sale_form.html", **_commission_sale_form_ctx(sale)
    )


@crm_bp.route(
    "/comissionamento/vendas/<int:sale_id>/excluir",
    methods=["GET", "POST"],
    endpoint="crm_commission_sale_delete",
)
@crm_login_required
def commission_sale_delete(sale_id: int):
    sale = CommissionSale.query.get_or_404(sale_id)
    if request.method == "POST":
        db.session.delete(sale)
        db.session.commit()
        flash("Venda avulsa removida.", "ok")
        return redirect(url_for("crm.crm_commission_sales"))
    return render_template("crm/commission_sale_delete.html", sale=sale)


@crm_bp.route(
    "/financeiro/venda-avulsa/<int:split_id>/status",
    methods=["POST"],
    endpoint="crm_commission_sale_split_status",
)
@crm_login_required
def commission_sale_split_status(split_id: int):
    row = CommissionSaleSplit.query.get_or_404(split_id)
    new_s = (request.form.get("status") or "").strip()
    if new_s in dict(PAYOUT_STATUSES):
        row.payout_status = new_s
        db.session.commit()
        flash("Status do rateio atualizado.", "ok")
    return redirect(url_for("crm.crm_financeiro_home"))


@crm_bp.route("/comissionamento/projeto/<int:project_id>", endpoint="crm_commission_project_detail")
@crm_login_required
def commission_project_detail(project_id):
    project = (
        CommissionProject.query.options(
            selectinload(CommissionProject.tiers).selectinload(CommissionProjectTier.splits),
            selectinload(CommissionProject.rateio_lines),
        )
        .filter_by(id=project_id)
        .first_or_404()
    )
    stakeholders = CompanyStakeholder.query.order_by(CompanyStakeholder.sort_order).all()
    rateio_total = _rateio_lines_total(project.rateio_lines)
    edit_line_id = request.args.get("edit_rateio", type=int)
    edit_line = None
    if edit_line_id:
        edit_line = next(
            (line for line in project.rateio_lines if line.id == edit_line_id),
            None,
        )
    return render_template(
        "crm/commission_project_detail.html",
        project=project,
        stakeholders=stakeholders,
        tier_label_fn=tier_label,
        rateio_mode_label_fn=rateio_mode_label,
        rateio_line_kind_label_fn=_rateio_line_kind_label,
        rateio_total=rateio_total,
        edit_line=edit_line,
    )


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/rateio",
    methods=["POST"],
    endpoint="crm_commission_rateio_line_add",
)
@crm_login_required
def commission_rateio_line_add(project_id: int):
    project = CommissionProject.query.options(
        selectinload(CommissionProject.rateio_lines),
        selectinload(CommissionProject.tiers),
    ).get_or_404(project_id)
    if project_effective_rateio_mode(project) != "custom":
        flash("Este tipo não usa rateio personalizado.", "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    kind, label, pool_share, err = _parse_rateio_line_form()
    if err:
        flash(err, "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    max_order = (
        db.session.query(func.max(CommissionProjectRateioLine.sort_order))
        .filter_by(project_id=project.id)
        .scalar()
        or 0
    )
    project.rateio_lines.append(
        CommissionProjectRateioLine(
            recipient_kind=kind,
            label=label,
            pool_share_percent=pool_share,
            sort_order=int(max_order) + 1,
        )
    )
    db.session.flush()
    if project.tiers:
        ok, err = validate_custom_rateio(project)
        if not ok:
            db.session.rollback()
            flash(err or "As participações devem somar 100%.", "error")
            return redirect(
                url_for("crm.crm_commission_project_detail", project_id=project.id)
            )
        refresh_project_tier_splits(project)
    db.session.commit()
    if project.tiers:
        flash("Participante adicionado ao rateio. Faixas recalculadas.", "ok")
    else:
        flash("Participante adicionado ao rateio.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/rateio/<int:line_id>/editar",
    methods=["POST"],
    endpoint="crm_commission_rateio_line_edit",
)
@crm_login_required
def commission_rateio_line_edit(project_id: int, line_id: int):
    project = CommissionProject.query.options(
        selectinload(CommissionProject.rateio_lines),
        selectinload(CommissionProject.tiers),
    ).get_or_404(project_id)
    if project_effective_rateio_mode(project) != "custom":
        flash("Este tipo não usa rateio personalizado.", "error")
        return redirect(
            url_for("crm.crm_commission_project_detail", project_id=project.id)
        )
    line = CommissionProjectRateioLine.query.filter_by(
        id=line_id, project_id=project.id
    ).first_or_404()
    kind, label, pool_share, err = _parse_rateio_line_form()
    if err:
        flash(err, "error")
        return redirect(
            url_for(
                "crm.crm_commission_project_detail",
                project_id=project.id,
                edit_rateio=line.id,
            )
        )
    line.recipient_kind = kind
    line.label = label
    line.pool_share_percent = pool_share
    db.session.flush()
    if project.tiers:
        ok, err = validate_custom_rateio(project)
        if not ok:
            db.session.rollback()
            flash(err or "As participações devem somar 100%.", "error")
            return redirect(
                url_for(
                    "crm.crm_commission_project_detail",
                    project_id=project.id,
                    edit_rateio=line.id,
                )
            )
        refresh_project_tier_splits(project)
    db.session.commit()
    if project.tiers:
        flash("Participante atualizado. Faixas recalculadas.", "ok")
    else:
        flash("Participante atualizado.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route(
    "/comissionamento/projeto/<int:project_id>/rateio/<int:line_id>/excluir",
    methods=["POST"],
    endpoint="crm_commission_rateio_line_delete",
)
@crm_login_required
def commission_rateio_line_delete(project_id: int, line_id: int):
    project = CommissionProject.query.options(
        selectinload(CommissionProject.rateio_lines),
        selectinload(CommissionProject.tiers),
    ).get_or_404(project_id)
    line = CommissionProjectRateioLine.query.filter_by(
        id=line_id, project_id=project.id
    ).first_or_404()
    db.session.delete(line)
    db.session.flush()
    if project.rateio_lines:
        ok, err = validate_custom_rateio(project)
        if not ok:
            db.session.rollback()
            flash(err or "As participações devem somar 100%.", "error")
            return redirect(
                url_for("crm.crm_commission_project_detail", project_id=project.id)
            )
    if project.tiers and project.rateio_lines:
        refresh_project_tier_splits(project)
    db.session.commit()
    if project.tiers:
        flash("Participante removido do rateio. Faixas recalculadas.", "ok")
    else:
        flash("Participante removido do rateio.", "ok")
    return redirect(url_for("crm.crm_commission_project_detail", project_id=project.id))


@crm_bp.route(
    "/financeiro/entrada/<int:entry_id>/status",
    methods=["POST"],
    endpoint="crm_finance_entry_status",
)
@crm_login_required
def finance_entry_status(entry_id):
    entry = RepFinancialEntry.query.get_or_404(entry_id)
    new_s = (request.form.get("status") or "").strip()
    if new_s in dict(COMMISSION_STATUSES):
        entry.status = new_s
        db.session.commit()
        flash("Status da comissão atualizado.", "ok")
    return redirect(url_for("crm.crm_financeiro_home"))


@crm_bp.route("/brand-kit")
def legacy_crm_brand_kit_redirect():
    return redirect(url_for("admin_brand_kit"))


# Compatibilidade: rotas antigas de financeiro redirecionam
@crm_bp.route("/financeiro/custos")
@crm_bp.route("/financeiro/representantes")
@crm_bp.route("/importar-planilha")
def legacy_finance_redirect():
    return redirect(url_for("crm.crm_financeiro_home"))
