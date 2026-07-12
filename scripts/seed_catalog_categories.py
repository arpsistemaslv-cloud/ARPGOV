"""Cadastra categorias de produto no catálogo (idempotente por slug)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Nome exibido na loja (ortografia e padrão revisados)
CATALOG_CATEGORIES: list[str] = [
    "Ponto de acesso",
    "Antivírus e segurança digital",
    "Ar-condicionado",
    "Itens de cozinha",
    "Bebedouro",
    "Caixa de som",
    "Câmera de segurança",
    "Centralizador de rede",
    "Chromebook",
    "Drone",
    "Estabilizador",
    "Fogão",
    "Forno",
    "Freezer",
    "Frigobar",
    "Geladeira",
    "Gravador digital",
    "Impressora",
    "Impressora 3D",
    "Material escolar",
    "Material hospitalar",
    "Locação de ambulâncias",
    "Mão de obra (terceirizada)",
    "Máquina de lavar",
    "Máquinas de jardim",
    "Micro-ondas",
    "Monitor",
    "Motores",
    "Móveis hospitalares",
    "Móveis para escritório",
    "Nobreak",
    "Notebook",
    "Persiana",
    "Placas solares",
    "Projetor",
    "Refrigerador",
    "Roteador",
    "Scanner",
    "Sem categoria",
    "Serviços e manutenção",
    "Servidor",
    "Sistema de segurança",
    "Smartphone",
    "Armazenamento (storage)",
    "Switch",
    "Tablet",
    "Tela de projeção",
    "Tela interativa",
    "Telefone",
    "Televisor",
    "Veículo",
    "Locação de veículos",
    "Ventilador",
    "Videoconferência",
    "Webcam",
    "Estação de trabalho (workstation)",
]

# Ordem prioritária no filtro da loja (após "Todos")
CATEGORY_PRIORITY_SLUGS: list[str] = [
    "desktop",
    "workstation-mobile",
    "notebook",
]


def apply_category_sort_order() -> None:
    """Reordena categorias raiz: prioridade fixa + demais na ordem alfabética."""
    from models import CatalogCategory, db

    roots = (
        CatalogCategory.query.filter_by(parent_id=None)
        .order_by(CatalogCategory.name.asc())
        .all()
    )
    by_slug = {c.slug: c for c in roots}
    ordered: list[CatalogCategory] = []
    used: set[int] = set()
    for slug in CATEGORY_PRIORITY_SLUGS:
        cat = by_slug.get(slug)
        if cat:
            ordered.append(cat)
            used.add(cat.id)
    for cat in sorted(roots, key=lambda c: c.name.lower()):
        if cat.id not in used:
            ordered.append(cat)
    for i, cat in enumerate(ordered, start=1):
        cat.sort_order = i


def main() -> int:
    from app import app, slugify, unique_category_slug
    from models import CatalogCategory, db

    created = 0
    skipped = 0
    with app.app_context():
        for i, name in enumerate(CATALOG_CATEGORIES, start=1):
            base_slug = slugify(name)
            existing = CatalogCategory.query.filter_by(slug=base_slug).first()
            if existing:
                if existing.name != name:
                    print(f"[atualiza] {existing.name!r} -> {name!r}")
                    existing.name = name[:120]
                    existing.sort_order = i
                else:
                    print(f"[ok] {name}")
                skipped += 1
                continue
            by_name = CatalogCategory.query.filter(
                CatalogCategory.name.ilike(name)
            ).first()
            if by_name:
                print(f"[ok] {name} (já existe como {by_name.slug})")
                skipped += 1
                continue
            slug = unique_category_slug(base_slug)
            db.session.add(
                CatalogCategory(
                    name=name[:120],
                    slug=slug,
                    parent_id=None,
                    sort_order=i,
                )
            )
            print(f"[nova] {name} ({slug})")
            created += 1
        apply_category_sort_order()
        db.session.commit()
    print(f"\nCriadas: {created} | Já existiam: {skipped} | Total na lista: {len(CATALOG_CATEGORIES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
