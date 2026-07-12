"""Verifica migração arp_id nullable e insert PNCP-only."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app import app, db, ensure_contratos_gov_scan_tables
from models import ContratosGovScan, ContratosGovScanResult


def main() -> int:
    with app.app_context():
        ensure_contratos_gov_scan_tables()
        row = db.session.execute(
            text(
                "SELECT \"notnull\" FROM pragma_table_info('contratos_gov_scan_results') "
                "WHERE name='arp_id'"
            )
        ).fetchone()
        notnull = int(row[0]) if row else -1
        print(f"arp_id notnull pragma: {notnull}")
        if notnull != 0:
            print("FALHA: arp_id ainda NOT NULL")
            return 1

        scan = ContratosGovScan(year=2026, month=6, scan_mode="pncp", status="running")
        db.session.add(scan)
        db.session.flush()
        result = ContratosGovScanResult(
            scan_id=scan.id,
            arp_id=None,
            detail_url="https://example.com/test",
            pncp_control_id="test-null-arp",
        )
        db.session.add(result)
        db.session.commit()
        print(f"insert ok id={result.id}")

        db.session.delete(result)
        db.session.delete(scan)
        db.session.commit()
        print("cleanup ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
