"""Simula POST PNCP do robô e grava resultados (valida fim-a-fim)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (
    _known_contratos_arp_ids,
    _known_pncp_control_ids,
    _pncp_org_resolver_for_robo,
    app,
    db,
    ensure_contratos_gov_scan_tables,
)
from models import ContratosGovScan, ContratosGovScanResult
import arp_robot
import pncp_client as pncp


def main() -> int:
    with app.app_context():
        ensure_contratos_gov_scan_tables()
        pf = pncp.parse_portal_filters_from_form(
            {
                "pncp_vigencia_status": "vigente",
                "pncp_permite_adesao": "sim",
                "pncp_esfera": "F",
            }
        )
        scan = ContratosGovScan(
            year=2026,
            month=6,
            scan_mode="pncp",
            pncp_query_mode="vigencia",
            only_pncp_adesao=True,
            pncp_filters_json=json.dumps(pf.to_json_dict(), ensure_ascii=False),
            max_pncp_pages=3,
            status="running",
        )
        db.session.add(scan)
        db.session.commit()

        org_resolver = _pncp_org_resolver_for_robo()
        hits, stats = arp_robot.run_arp_robot(
            2026,
            month=6,
            pncp_portal_filters=pf,
            org_resolver=org_resolver,
            scan_mode="pncp",
            only_pncp_adesao=True,
            max_pncp_pages=3,
        )
        print(f"hits={len(hits)} matched={stats.pncp_rows_matched} api_rows={stats.pncp_rows_api}")

        known_arp = _known_contratos_arp_ids(exclude_scan_id=scan.id)
        known_pncp = _known_pncp_control_ids(exclude_scan_id=scan.id)
        for hit in hits:
            db.session.add(
                ContratosGovScanResult(
                    scan_id=scan.id,
                    arp_id=None,
                    pncp_control_id=(hit.get("pncp_control_id") or "")[:220] or None,
                    numero_ata=(hit.get("numero") or "")[:40] or None,
                    detail_url=hit["detail_url"],
                    was_known_before=bool(
                        hit.get("pncp_control_id") in known_pncp if hit.get("pncp_control_id") else False
                    ),
                )
            )
        scan.status = "done"
        db.session.commit()
        print(f"scan_id={scan.id} saved ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
