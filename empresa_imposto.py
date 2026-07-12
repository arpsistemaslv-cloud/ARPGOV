"""
Setor Impostos: alíquotas e benefício por produto (catálogo Compras) e UF.
O fechamento de preço usa esta tabela quando UF de entrega está definida (prioridade sobre NCM).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy.orm import joinedload

from models import EmpresaProduto, EmpresaProdutoImpostoUF, db


def _parse_decimal(raw: str | None) -> Decimal | None:
    s = (raw or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


BR_UFS: tuple[tuple[str, str], ...] = (
    ("AC", "Acre"),
    ("AL", "Alagoas"),
    ("AP", "Amapá"),
    ("AM", "Amazonas"),
    ("BA", "Bahia"),
    ("CE", "Ceará"),
    ("DF", "Distrito Federal"),
    ("ES", "Espírito Santo"),
    ("GO", "Goiás"),
    ("MA", "Maranhão"),
    ("MT", "Mato Grosso"),
    ("MS", "Mato Grosso do Sul"),
    ("MG", "Minas Gerais"),
    ("PA", "Pará"),
    ("PB", "Paraíba"),
    ("PR", "Paraná"),
    ("PE", "Pernambuco"),
    ("PI", "Piauí"),
    ("RJ", "Rio de Janeiro"),
    ("RN", "Rio Grande do Norte"),
    ("RS", "Rio Grande do Sul"),
    ("RO", "Rondônia"),
    ("RR", "Roraima"),
    ("SC", "Santa Catarina"),
    ("SP", "São Paulo"),
    ("SE", "Sergipe"),
    ("TO", "Tocantins"),
)


def register_imposto_routes(bp) -> None:
    from empresa_anexos import listar_anexos
    from empresa_intranet import _current_employee, empresa_login_required

    @bp.route("/imposto/")
    @empresa_login_required
    def imposto_hub():
        n_prod = EmpresaProduto.query.filter_by(ativo=True).count()
        n_matriz = EmpresaProdutoImpostoUF.query.count()
        return render_template(
            "empresa/imposto/hub.html",
            staff=_current_employee(),
            n_produtos=n_prod,
            n_registros_matriz=n_matriz,
        )

    @bp.route("/imposto/matriz")
    @empresa_login_required
    def imposto_matriz():
        rows = (
            EmpresaProdutoImpostoUF.query.options(joinedload(EmpresaProdutoImpostoUF.produto))
            .order_by(EmpresaProdutoImpostoUF.uf, EmpresaProdutoImpostoUF.id.desc())
            .limit(2000)
            .all()
        )
        return render_template(
            "empresa/imposto/matriz_list.html",
            staff=_current_employee(),
            linhas=rows,
            ufs=BR_UFS,
        )

    @bp.route("/imposto/matriz/novo", methods=["GET", "POST"])
    @empresa_login_required
    def imposto_matriz_new():
        if request.method == "POST":
            return _save_imposto_uf(None)
        produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
        return render_template(
            "empresa/imposto/matriz_form.html",
            staff=_current_employee(),
            linha=None,
            produtos=produtos,
            ufs=BR_UFS,
        )

    @bp.route("/imposto/matriz/<int:iid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def imposto_matriz_edit(iid: int):
        row = EmpresaProdutoImpostoUF.query.get_or_404(iid)
        if request.method == "POST":
            return _save_imposto_uf(row)
        produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
        anexos = listar_anexos("imposto_uf", row.id)
        return render_template(
            "empresa/imposto/matriz_form.html",
            staff=_current_employee(),
            linha=row,
            produtos=produtos,
            ufs=BR_UFS,
            anexos=anexos,
        )

    def _save_imposto_uf(row: EmpresaProdutoImpostoUF | None):
        user = _current_employee()
        pid = request.form.get("produto_id", type=int)
        uf = (request.form.get("uf") or "").strip().upper()[:2]
        prod = EmpresaProduto.query.get(pid) if pid else None
        if not prod or not uf or len(uf) != 2:
            flash("Produto e UF válidos são obrigatórios.", "error")
            produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
            return render_template(
                "empresa/imposto/matriz_form.html",
                staff=user,
                linha=row,
                produtos=produtos,
                ufs=BR_UFS,
                anexos=listar_anexos("imposto_uf", row.id) if row else [],
            )
        if row is None:
            ex = EmpresaProdutoImpostoUF.query.filter_by(produto_id=prod.id, uf=uf).first()
            if ex:
                flash("Já existe registro para este produto e UF.", "error")
                produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
                return render_template(
                    "empresa/imposto/matriz_form.html",
                    staff=user,
                    linha=None,
                    produtos=produtos,
                    ufs=BR_UFS,
                    anexos=[],
                )
            row = EmpresaProdutoImpostoUF(produto_id=prod.id, uf=uf)
            db.session.add(row)
        else:
            other = EmpresaProdutoImpostoUF.query.filter(
                EmpresaProdutoImpostoUF.produto_id == prod.id,
                EmpresaProdutoImpostoUF.uf == uf,
                EmpresaProdutoImpostoUF.id != row.id,
            ).first()
            if other:
                flash("Já existe registro para este produto e UF.", "error")
                produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
                return render_template(
                    "empresa/imposto/matriz_form.html",
                    staff=user,
                    linha=row,
                    produtos=produtos,
                    ufs=BR_UFS,
                    anexos=listar_anexos("imposto_uf", row.id),
                )
            row.produto_id = prod.id
            row.uf = uf
        row.beneficio_fiscal = (request.form.get("beneficio_fiscal") or "").strip() or None
        row.aliquota_icms = _parse_decimal(request.form.get("aliquota_icms")) or Decimal(0)
        row.aliquota_ipi = _parse_decimal(request.form.get("aliquota_ipi")) or Decimal(0)
        row.aliquota_pis = _parse_decimal(request.form.get("aliquota_pis")) or Decimal(0)
        row.aliquota_cofins = _parse_decimal(request.form.get("aliquota_cofins")) or Decimal(0)
        row.observacao = (request.form.get("observacao") or "").strip() or None
        db.session.commit()
        flash("Registro salvo.", "ok")
        return redirect(url_for("empresa.imposto_matriz_edit", iid=row.id))
