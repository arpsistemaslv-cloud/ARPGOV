"""Teste rápido da busca PNCP do robô (filtros portal)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pncp_client as p


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "OK" if ok else "FALHA"
    line = f"[{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    pf = p.PncpPortalFilters(
        vigencia_status="vigente",
        permite_adesao="sim",
        esfera_id="F",
    ).normalized()
    resolver = p.PncpOrgResolver()
    d0, d1 = date(2026, 6, 1), date(2026, 6, 30)

    print("=== Teste PNCP robô — Jun/2026 · Vigentes · Adesão Sim · Esfera Federal ===\n")

    hits, stats = p.scan_atas_inteligente(
        d0,
        d1,
        portal_filters=pf,
        org_resolver=resolver,
        only_possibilidade_adesao=False,
        max_pages=3,
        pause_sec=0.15,
    )

    ok = True
    ok &= _check("API respondeu", stats.pages_read > 0, f"{stats.rows_api} linhas em {stats.pages_read} pág(s)")
    ok &= _check("Sem erro fatal", not stats.errors or stats.rows_api > 0, "; ".join(stats.errors[:3]))
    ok &= _check("Encontrou atas", stats.rows_matched > 0, f"{stats.rows_matched} após filtros")

    bad_adesao = [h for h in hits if h.get("possibilidade_adesao") is not True]
    ok &= _check("Todas com adesão", len(bad_adesao) == 0, f"{len(bad_adesao)} fora do critério")

    bad_vig = []
    for h in hits[:20]:
        ctrl = h.get("pncp_control_id")
        if not ctrl:
            continue
    ok &= _check("Amostra de hits", len(hits) >= 1, hits[0].get("objeto", "")[:80] if hits else "—")

    # Esfera: amostra 5 atas
    sample = hits[:5]
    esfera_ok = 0
    for h in sample:
        ata_stub = {
            "cnpjOrgao": h.get("cnpj_orgao"),
            "codigoUnidadeOrgao": "0000",
        }
        ctx = resolver.context_for_ata(ata_stub)
        if ctx.get("esfera_id") == "F":
            esfera_ok += 1
    ok &= _check(
        "Esfera Federal na amostra",
        len(sample) == 0 or esfera_ok == len(sample),
        f"{esfera_ok}/{len(sample)} com esferaId=F",
    )

    print(f"\nTotal matched: {stats.rows_matched} | API rows: {stats.rows_api}")
    if hits:
        print("\nExemplo:")
        ex = hits[0]
        print(f"  {ex.get('numero')} | {ex.get('unidade', '')[:60]}")
        print(f"  {ex.get('objeto', '')[:100]}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
