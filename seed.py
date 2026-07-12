"""Popular catálogo de demonstração (estilo atas / esferas)."""
from app import app
from models import db, CatalogItem
from datetime import date


def seed():
    if CatalogItem.query.first():
        return
    samples = [
        CatalogItem(
            title="Van passageiros 16 lugares — Renault Master L2H2",
            section="ATA de veículos",
            sphere="Consórcio intermunicipal",
            quantity=100,
            unit_price=312072.52,
            valid_until=date(2026, 11, 21),
            slug="van-master-l2h2-16",
            highlight=True,
        ),
        CatalogItem(
            title="Renault Master Van L3H2 — 14+1 lugares",
            section="ATA de veículos",
            sphere="Estadual",
            quantity=67,
            unit_price=298719.31,
            valid_until=date(2026, 9, 1),
            slug="master-l3h2-14-1",
            highlight=True,
        ),
        CatalogItem(
            title="Desktop Lenovo M75q — Ryzen 3 Pro, 16GB, 256GB, W11 Pro + monitor",
            section="ATA em destaque",
            sphere="Federal",
            quantity=145,
            unit_price=4975.00,
            valid_until=date(2026, 11, 4),
            slug="desktop-lenovo-m75q-kit",
            highlight=True,
        ),
        CatalogItem(
            title="Ar condicionado 36.000 BTUs Midea Inverter",
            section="ATA em destaque",
            sphere="Estadual",
            quantity=1270,
            unit_price=6849.99,
            valid_until=date(2026, 11, 21),
            slug="ar-midea-36k",
            highlight=False,
        ),
        CatalogItem(
            title="Tablet Vaio TL10 — 8GB / 128GB — LCD 10,4\"",
            section="Item em destaque",
            sphere="Municipal",
            quantity=104,
            unit_price=1875.00,
            valid_until=date(2026, 6, 20),
            slug="tablet-vaio-tl10",
            highlight=True,
        ),
    ]
    for row in samples:
        db.session.add(row)
    db.session.commit()
    print("Catálogo de demonstração criado.")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed()
