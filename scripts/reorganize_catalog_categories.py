"""
Reorganiza categorias planas em catálogos (pai) + subcategorias.

Uso: .venv\\Scripts\\python scripts\\reorganize_catalog_categories.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Catálogo (pai) -> slugs das subcategorias existentes
CATEGORY_TREE: dict[str, list[str]] = {
    "Informática e TI": [
        "desktop",
        "workstation-mobile",
        "notebook",
        "estacao-de-trabalho-workstation",
        "chromebook",
        "monitor",
        "servidor",
        "tablet",
        "smartphone",
        "armazenamento-storage",
        "impressora",
        "impressora-3d",
        "scanner",
        "webcam",
    ],
    "Redes e conectividade": [
        "ponto-de-acesso",
        "roteador",
        "switch",
        "centralizador-de-rede",
    ],
    "Segurança e vigilância": [
        "antivirus-e-seguranca-digital",
        "camera-de-seguranca",
        "sistema-de-seguranca",
        "gravador-digital",
    ],
    "Audiovisual e colaboração": [
        "projetor",
        "tela-de-projecao",
        "tela-interativa",
        "videoconferencia",
        "caixa-de-som",
        "televisor",
        "telefone",
    ],
    "Eletrodomésticos e climatização": [
        "ar-condicionado",
        "ventilador",
        "geladeira",
        "refrigerador",
        "freezer",
        "frigobar",
        "micro-ondas",
        "maquina-de-lavar",
        "bebedouro",
    ],
    "Cozinha": [
        "fogao",
        "forno",
        "itens-de-cozinha",
    ],
    "Energia e infraestrutura": [
        "nobreak",
        "estabilizador",
        "placas-solares",
    ],
    "Mobiliário": [
        "moveis-para-escritorio",
        "moveis-hospitalares",
        "persiana",
    ],
    "Saúde e hospitalar": [
        "material-hospitalar",
        "locacao-de-ambulancias",
    ],
    "Educacional": [
        "material-escolar",
    ],
    "Veículos e locação": [
        "veiculo",
        "locacao-de-veiculos",
    ],
    "Serviços": [
        "servicos-e-manutencao",
        "mao-de-obra-terceirizada",
    ],
    "Outros": [
        "drone",
        "motores",
        "maquinas-de-jardim",
        "sem-categoria",
    ],
}

# Ordem dos catálogos na loja
PARENT_SORT_ORDER: list[str] = [
    "Informática e TI",
    "Redes e conectividade",
    "Segurança e vigilância",
    "Audiovisual e colaboração",
    "Eletrodomésticos e climatização",
    "Cozinha",
    "Energia e infraestrutura",
    "Mobiliário",
    "Saúde e hospitalar",
    "Educacional",
    "Veículos e locação",
    "Serviços",
    "Outros",
]

# Subcategorias prioritárias (dentro de Informática e TI)
INFORMATICA_CHILD_PRIORITY: list[str] = [
    "desktop",
    "workstation-mobile",
    "notebook",
]


def main() -> int:
    from app import app, slugify, unique_category_slug
    from models import CatalogCategory, db

    all_slugs = {s for subs in CATEGORY_TREE.values() for s in subs}
    with app.app_context():
        by_slug: dict[str, CatalogCategory] = {
            c.slug: c for c in CatalogCategory.query.all()
        }
        parents: dict[str, CatalogCategory] = {}

        for i, parent_name in enumerate(PARENT_SORT_ORDER, start=1):
            child_slugs = CATEGORY_TREE.get(parent_name, [])
            parent_slug = slugify(parent_name)
            parent = CatalogCategory.query.filter_by(slug=parent_slug, parent_id=None).first()
            if parent is None:
                # Reaproveita categoria raiz se o slug de um filho antigo coincidir (improvável)
                parent = CatalogCategory(
                    name=parent_name[:120],
                    slug=unique_category_slug(parent_slug),
                    parent_id=None,
                    sort_order=i,
                )
                db.session.add(parent)
                db.session.flush()
                print(f"[pai novo] {parent_name}")
            else:
                parent.name = parent_name[:120]
                parent.sort_order = i
                parent.parent_id = None
                print(f"[pai ok] {parent_name}")
            parents[parent_name] = parent

        assigned: set[int] = set()
        for parent_name, child_slugs in CATEGORY_TREE.items():
            parent = parents[parent_name]
            priority = (
                INFORMATICA_CHILD_PRIORITY
                if parent_name == "Informática e TI"
                else []
            )
            ordered_slugs: list[str] = []
            for slug in priority:
                if slug in child_slugs:
                    ordered_slugs.append(slug)
            for slug in sorted(child_slugs):
                if slug not in ordered_slugs:
                    ordered_slugs.append(slug)

            for j, slug in enumerate(ordered_slugs, start=1):
                cat = by_slug.get(slug)
                if cat is None:
                    print(f"[aviso] subcategoria ausente: {slug}")
                    continue
                cat.parent_id = parent.id
                cat.sort_order = j
                assigned.add(cat.id)
                print(f"  -> {parent_name} › {cat.name}")

        orphans = [
            c
            for c in CatalogCategory.query.filter_by(parent_id=None).all()
            if c.id not in {p.id for p in parents.values()}
        ]
        outros = parents.get("Outros")
        for cat in orphans:
            if cat.slug in all_slugs:
                continue
            if outros:
                cat.parent_id = outros.id
                cat.sort_order = 900 + cat.id
                print(f"[órfão] {cat.name} -> Outros")
            assigned.add(cat.id)

        missing = all_slugs - set(by_slug.keys())
        if missing:
            print(f"\nSlugs não encontrados no banco: {sorted(missing)}")

        db.session.commit()

        roots = CatalogCategory.query.filter_by(parent_id=None).order_by(CatalogCategory.sort_order).all()
        print(f"\n=== Catálogos ({len(roots)}) ===")
        for r in roots:
            kids = (
                CatalogCategory.query.filter_by(parent_id=r.id)
                .order_by(CatalogCategory.sort_order)
                .all()
            )
            print(f"{r.sort_order:2}. {r.name} ({len(kids)} sub)")
            for k in kids[:5]:
                print(f"      - {k.name}")
            if len(kids) > 5:
                print(f"      ... +{len(kids) - 5}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
