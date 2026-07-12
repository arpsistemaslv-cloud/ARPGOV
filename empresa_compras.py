"""
Módulo do setor Compras: cadastro de produtos (part number, acessórios) e empenhos vinculados.
Registrado em empresa_intranet.register_compras_routes(empresa_bp).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from empresa_anexos import listar_anexos

from models import EmpresaContratoOrgao, EmpresaEmpenho, EmpresaEmpenhoItem, EmpresaProduto, db


def _parse_acessorios_text(raw: str | None) -> list[dict]:
    out: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            a, b = line.split("|", 1)
            out.append(
                {"nome": a.strip(), "part_number": (b.strip() or None)}
            )
        else:
            out.append({"nome": line, "part_number": None})
    return out


def _acessorios_to_text(items: list) -> str:
    lines = []
    for it in items:
        if isinstance(it, dict):
            n = (it.get("nome") or "").strip()
            p = (it.get("part_number") or "").strip()
            if p:
                lines.append(f"{n} | {p}")
            elif n:
                lines.append(n)
        elif isinstance(it, str) and it.strip():
            lines.append(it.strip())
    return "\n".join(lines)


def _parse_decimal(raw: str | None) -> Decimal | None:
    s = (raw or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def register_compras_routes(bp) -> None:
    from empresa_intranet import empresa_login_required, _current_employee

    @bp.route("/compras/")
    @empresa_login_required
    def compras_hub():
        n_prod = EmpresaProduto.query.filter_by(ativo=True).count()
        n_emp = EmpresaEmpenho.query.count()
        return render_template(
            "empresa/compras/hub.html",
            staff=_current_employee(),
            n_produtos=n_prod,
            n_empenhos=n_emp,
        )

    @bp.route("/compras/produtos")
    @empresa_login_required
    def compras_produtos_list():
        q = (request.args.get("q") or "").strip()
        query = EmpresaProduto.query
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    EmpresaProduto.part_number.ilike(like),
                    EmpresaProduto.nome.ilike(like),
                    EmpresaProduto.ncm.ilike(like),
                )
            )
        produtos = query.order_by(EmpresaProduto.part_number.asc()).limit(500).all()
        return render_template(
            "empresa/compras/produtos_list.html",
            staff=_current_employee(),
            produtos=produtos,
            q=q,
        )

    @bp.route("/compras/produtos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def compras_produtos_new():
        if request.method == "POST":
            return _compras_produto_save(None)
        return render_template(
            "empresa/compras/produto_form.html",
            staff=_current_employee(),
            produto=None,
        )

    @bp.route("/compras/produtos/<int:pid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def compras_produtos_edit(pid: int):
        produto = EmpresaProduto.query.get_or_404(pid)
        if request.method == "POST":
            return _compras_produto_save(produto)
        return render_template(
            "empresa/compras/produto_form.html",
            staff=_current_employee(),
            produto=produto,
            acessorios_text=_acessorios_to_text(produto.acessorios_list),
            anexos=listar_anexos("produto", produto.id),
        )

    def _compras_produto_save(produto: EmpresaProduto | None):
        user = _current_employee()
        part_number = (request.form.get("part_number") or "").strip()
        nome = (request.form.get("nome") or "").strip()
        descricao = (request.form.get("descricao") or "").strip() or None
        unidade = (request.form.get("unidade") or "UN").strip()[:20] or "UN"
        ativo = request.form.get("ativo") == "1"
        acc_raw = request.form.get("acessorios") or ""
        acessorios = _parse_acessorios_text(acc_raw)
        if not part_number or not nome:
            flash("Part number e nome são obrigatórios.", "error")
            return render_template(
                "empresa/compras/produto_form.html",
                staff=user,
                produto=produto,
                acessorios_text=acc_raw,
                anexos=listar_anexos("produto", produto.id) if produto else [],
            )
        other = EmpresaProduto.query.filter(
            EmpresaProduto.part_number == part_number,
            EmpresaProduto.id != (produto.id if produto else 0),
        ).first()
        if other:
            flash("Já existe produto com este part number.", "error")
            return render_template(
                "empresa/compras/produto_form.html",
                staff=user,
                produto=produto,
                acessorios_text=acc_raw,
                anexos=listar_anexos("produto", produto.id) if produto else [],
            )
        if produto is None:
            produto = EmpresaProduto(
                part_number=part_number,
                nome=nome,
                created_by_id=user.id if user else None,
            )
            db.session.add(produto)
        else:
            produto.part_number = part_number
            produto.nome = nome
        produto.descricao = descricao
        produto.unidade = unidade
        produto.ativo = ativo
        ncm_digits = "".join(c for c in (request.form.get("ncm") or "") if c.isdigit())[:8]
        produto.ncm = ncm_digits or None
        produto.beneficio_fiscal = (request.form.get("beneficio_fiscal") or "").strip() or None
        produto.observacoes_fiscais = (request.form.get("observacoes_fiscais") or "").strip() or None
        produto.acessorios_json = json.dumps(acessorios, ensure_ascii=False)
        db.session.commit()
        msg = "Produto salvo."
        if not ncm_digits:
            msg += " Lembrete: cadastre o NCM (8 dígitos) para o Fechamento de preço e impostos estimados."
        flash(msg, "ok")
        return redirect(url_for("empresa.compras_produtos_list"))

    @bp.route("/compras/empenhos")
    @empresa_login_required
    def compras_empenhos_list():
        empenhos = (
            EmpresaEmpenho.query.options(joinedload(EmpresaEmpenho.created_by))
            .order_by(EmpresaEmpenho.id.desc())
            .limit(300)
            .all()
        )
        return render_template(
            "empresa/compras/empenhos_list.html",
            staff=_current_employee(),
            empenhos=empenhos,
        )

    @bp.route("/compras/empenhos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def compras_empenhos_new():
        if request.method == "POST":
            return _compras_empenho_save(None)
        contratos = EmpresaContratoOrgao.query.order_by(EmpresaContratoOrgao.numero_contrato).limit(300).all()
        return render_template(
            "empresa/compras/empenho_form.html",
            staff=_current_employee(),
            empenho=None,
            contratos=contratos,
        )

    @bp.route("/compras/empenhos/<int:eid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def compras_empenhos_edit(eid: int):
        empenho = EmpresaEmpenho.query.get_or_404(eid)
        if request.method == "POST":
            return _compras_empenho_save(empenho)
        contratos = EmpresaContratoOrgao.query.order_by(EmpresaContratoOrgao.numero_contrato).limit(300).all()
        return render_template(
            "empresa/compras/empenho_form.html",
            staff=_current_employee(),
            empenho=empenho,
            contratos=contratos,
        )

    def _compras_empenho_save(empenho: EmpresaEmpenho | None):
        user = _current_employee()
        numero = (request.form.get("numero") or "").strip()
        orgao = (request.form.get("orgao_nome") or "").strip()
        cnpj = "".join(c for c in (request.form.get("cnpj_orgao") or "") if c.isdigit())[:14] or None
        valor_total = _parse_decimal(request.form.get("valor_total"))
        data_emissao = _parse_date(request.form.get("data_emissao"))
        data_proc = _parse_date(request.form.get("data_processamento"))
        status = (request.form.get("status") or "processado").strip()[:40]
        obs = (request.form.get("observacoes") or "").strip() or None
        cid = request.form.get("contrato_id", type=int)
        contrato = EmpresaContratoOrgao.query.get(cid) if cid else None
        if not numero or not orgao:
            flash("Número do empenho e órgão são obrigatórios.", "error")
            contratos = EmpresaContratoOrgao.query.order_by(EmpresaContratoOrgao.numero_contrato).limit(300).all()
            return render_template(
                "empresa/compras/empenho_form.html",
                staff=user,
                empenho=empenho,
                contratos=contratos,
            )
        if empenho is None:
            empenho = EmpresaEmpenho(
                numero=numero,
                orgao_nome=orgao,
                created_by_id=user.id if user else None,
            )
            db.session.add(empenho)
        else:
            empenho.numero = numero
            empenho.orgao_nome = orgao
        empenho.cnpj_orgao = cnpj
        empenho.valor_total = valor_total
        empenho.data_emissao = data_emissao
        empenho.data_processamento = data_proc
        empenho.status = status
        empenho.observacoes = obs
        empenho.contrato_id = contrato.id if contrato else None
        db.session.commit()
        flash("Empenho salvo.", "ok")
        return redirect(url_for("empresa.compras_empenho_detail", eid=empenho.id))

    @bp.route("/compras/empenhos/<int:eid>")
    @empresa_login_required
    def compras_empenho_detail(eid: int):
        empenho = (
            EmpresaEmpenho.query.options(
                joinedload(EmpresaEmpenho.contrato),
                joinedload(EmpresaEmpenho.itens).joinedload(EmpresaEmpenhoItem.produto),
            )
            .filter_by(id=eid)
            .first_or_404()
        )
        empenho.itens.sort(key=lambda x: (x.sort_order, x.id))
        produtos = (
            EmpresaProduto.query.filter_by(ativo=True)
            .order_by(EmpresaProduto.part_number)
            .all()
        )
        return render_template(
            "empresa/compras/empenho_detail.html",
            staff=_current_employee(),
            empenho=empenho,
            produtos=produtos,
            anexos=listar_anexos("empenho", empenho.id),
        )

    @bp.route("/compras/empenhos/<int:eid>/itens/adicionar", methods=["POST"])
    @empresa_login_required
    def compras_empenho_item_add(eid: int):
        empenho = EmpresaEmpenho.query.get_or_404(eid)
        pid = request.form.get("produto_id", type=int)
        qtd = _parse_decimal(request.form.get("quantidade")) or Decimal("1")
        vu = _parse_decimal(request.form.get("valor_unitario"))
        obs = (request.form.get("observacao") or "").strip()[:500] or None
        prod = EmpresaProduto.query.get(pid) if pid else None
        if prod is None or not prod.ativo:
            flash("Selecione um produto ativo.", "error")
            return redirect(url_for("empresa.compras_empenho_detail", eid=eid))
        max_so = (
            db.session.query(db.func.max(EmpresaEmpenhoItem.sort_order))
            .filter_by(empenho_id=empenho.id)
            .scalar()
        )
        it = EmpresaEmpenhoItem(
            empenho_id=empenho.id,
            produto_id=prod.id,
            quantidade=qtd,
            valor_unitario=vu,
            sort_order=(max_so or 0) + 1,
            observacao=obs,
        )
        db.session.add(it)
        db.session.commit()
        flash("Item vinculado ao empenho.", "ok")
        return redirect(url_for("empresa.compras_empenho_detail", eid=eid))

    @bp.route("/compras/empenhos/itens/<int:iid>/excluir", methods=["POST"])
    @empresa_login_required
    def compras_empenho_item_delete(iid: int):
        it = EmpresaEmpenhoItem.query.get_or_404(iid)
        eid = it.empenho_id
        db.session.delete(it)
        db.session.commit()
        flash("Item removido.", "ok")
        return redirect(url_for("empresa.compras_empenho_detail", eid=eid))
