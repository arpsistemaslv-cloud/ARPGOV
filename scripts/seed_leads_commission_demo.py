"""
Cadastra sócios, projetos globais de comissionamento, vendedor Meyre Rose e leads de demonstração.

Uso:
  .venv\\Scripts\\python scripts\\seed_leads_commission_demo.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash

from app import app, ensure_global_commission_projects, init_schema
from commission_service import apply_tier_to_opportunity
from models import (
    CatalogItem,
    CommissionProject,
    CommissionProjectTier,
    CompanyStakeholder,
    Opportunity,
    OpportunityCatalogLine,
    SalesRepresentative,
    db,
)

STAKEHOLDERS = [
    ("Victor Hugo Almeida dos Santos", Decimal("65"), "socio", 1),
    ("Luis Felipe Pimenta de Araujo", Decimal("25"), "socio", 2),
    ("Fluxo de caixa", Decimal("10"), "fluxo_caixa", 3),
]

MEYRE_EMAIL = "meyre.rose@arpgov.com.br"
MEYRE_PASSWORD = "Meyre2025!"


def _pick_catalog_item() -> CatalogItem:
    for item in CatalogItem.query.order_by(CatalogItem.id.asc()).all():
        title = (item.title or "").lower()
        if "lenovo" in title and ("thinkcentre" in title or "desktop" in title):
            return item
    item = CatalogItem.query.order_by(CatalogItem.id.asc()).first()
    if item is None:
        raise RuntimeError("Nenhum produto no catálogo. Cadastre um item ou rode seed.py.")
    return item


def ensure_stakeholders() -> list[CompanyStakeholder]:
    rows: list[CompanyStakeholder] = []
    for name, pct, role, order in STAKEHOLDERS:
        row = CompanyStakeholder.query.filter_by(name=name).first()
        if row is None:
            row = CompanyStakeholder(
                name=name,
                share_percent=pct,
                role_key=role,
                sort_order=order,
                is_active=True,
            )
            db.session.add(row)
        else:
            row.share_percent = pct
            row.role_key = role
            row.sort_order = order
            row.is_active = True
        rows.append(row)
    db.session.flush()
    return rows


def ensure_rep_meyre() -> SalesRepresentative:
    rep = SalesRepresentative.query.filter_by(email=MEYRE_EMAIL).first()
    if rep is None:
        rep = SalesRepresentative(
            name="Meyre Rose",
            email=MEYRE_EMAIL,
            phone=None,
            password_hash=generate_password_hash(MEYRE_PASSWORD),
            is_active=True,
            is_admin=False,
        )
        db.session.add(rep)
    else:
        rep.name = "Meyre Rose"
        rep.is_active = True
    db.session.flush()
    return rep


def _project(with_seller: bool) -> CommissionProject:
    project = (
        CommissionProject.query.filter_by(with_seller=with_seller, is_active=True)
        .order_by(CommissionProject.id.asc())
        .first()
    )
    if project is None:
        raise RuntimeError(
            f"Projeto {'com' if with_seller else 'sem'} vendedor não encontrado. "
            "Rode init_schema ou reinicie o servidor."
        )
    return project


def _set_catalog_line(opp: Opportunity, item_id: int, qty: int) -> None:
    for ln in opp.catalog_lines:
        if ln.catalog_item_id == item_id:
            ln.quantity = qty
            return
    opp.catalog_lines.append(
        OpportunityCatalogLine(catalog_item_id=item_id, quantity=qty)
    )


def ensure_demo_leads(item: CatalogItem, rep: SalesRepresentative) -> tuple[Opportunity, Opportunity]:
    project_sem = _project(False)
    project_com = _project(True)
    tier_sem = (
        CommissionProjectTier.query.filter_by(
            project_id=project_sem.id, percent_total=Decimal("3"), with_seller=False
        )
        .first()
    )
    tier_com = (
        CommissionProjectTier.query.filter_by(
            project_id=project_com.id, percent_total=Decimal("3"), with_seller=True
        )
        .first()
    )
    if tier_sem is None or tier_com is None:
        raise RuntimeError("Faixas de 3% não encontradas nos projetos globais.")

    value = Decimal("107730.00")

    opp_sem = Opportunity.query.filter_by(title="[Demo] Adesão — sem vendedor (3%)").first()
    if opp_sem is None:
        opp_sem = Opportunity(
            title="[Demo] Adesão — sem vendedor (3%)",
            organization="Prefeitura Municipal — demonstração",
            contact_name="Contato teste",
            email="demo-sem-vendedor@example.com",
            stage="doc_enviada",
            value_brl=value,
            source="Seed comissionamento",
        )
        db.session.add(opp_sem)
        db.session.flush()
    _set_catalog_line(opp_sem, item.id, 10)
    opp_sem.value_brl = value

    apply_tier_to_opportunity(opp_sem, tier_sem)

    opp_com = Opportunity.query.filter_by(title="[Demo] Adesão — Meyre Rose (3%)").first()
    if opp_com is None:
        opp_com = Opportunity(
            title="[Demo] Adesão — Meyre Rose (3%)",
            organization="Secretaria Estadual — demonstração",
            contact_name="Contato comercial",
            email="demo-com-vendedor@example.com",
            stage="acompanhar_faturamento",
            value_brl=value,
            source="Seed comissionamento",
            sales_rep_id=rep.id,
        )
        db.session.add(opp_com)
        db.session.flush()
    opp_com.value_brl = value
    opp_com.sales_rep_id = rep.id
    _set_catalog_line(opp_com, item.id, 10)

    apply_tier_to_opportunity(opp_com, tier_com)

    db.session.commit()
    return opp_sem, opp_com


def main() -> None:
    with app.app_context():
        init_schema()
        stakeholders = ensure_stakeholders()
        db.session.commit()
        ensure_global_commission_projects()
        rep = ensure_rep_meyre()
        item = _pick_catalog_item()
        opp_sem, opp_com = ensure_demo_leads(item, rep)

        projects = CommissionProject.query.filter_by(is_active=True).order_by(
            CommissionProject.sort_order.asc()
        ).all()

        print("=== Comissionamento configurado ===")
        for p in projects:
            print(f"  • {p.title} (id={p.id}) — {len(p.tiers)} faixas")
        print(f"Produto demo nos leads: {item.title} (id={item.id})")
        print(f"Vendedor: {rep.name} — {rep.email} / senha: {MEYRE_PASSWORD}")
        print(f"Lead sem vendedor: id={opp_sem.id}")
        print(f"Lead com vendedor: id={opp_com.id}")
        print()
        print("CRM: /crm/comissionamento")
        print("Financeiro: /crm/financeiro")


if __name__ == "__main__":
    main()
