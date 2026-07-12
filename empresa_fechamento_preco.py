"""
Setor Fechamento de preço: impostos por produto+UF (setor Impostos) ou fallback NCM;
planilha pode ser fechada com snapshot dos valores.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import (
    CompanyEmployee,
    EmpresaFechamentoComissao,
    EmpresaFechamentoPreco,
    EmpresaFechamentoPrecoItem,
    EmpresaLicitacao,
    EmpresaNcmPerfil,
    EmpresaProduto,
    EmpresaProdutoImpostoUF,
    db,
)


def _parse_decimal(raw: str | None) -> Decimal | None:
    s = (raw or "").strip().replace(" ", "").replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def normalize_ncm_digits(raw: str | None) -> str:
    return "".join(c for c in (raw or "") if c.isdigit())[:8]


def buscar_perfil_ncm(ncm_raw: str | None) -> EmpresaNcmPerfil | None:
    d = normalize_ncm_digits(ncm_raw)
    if len(d) < 8:
        return None
    return EmpresaNcmPerfil.query.filter_by(ncm=d[:8]).first()


def buscar_imposto_produto_uf(produto_id: int, uf_raw: str | None) -> EmpresaProdutoImpostoUF | None:
    u = (uf_raw or "").strip().upper()[:2]
    if len(u) != 2:
        return None
    return EmpresaProdutoImpostoUF.query.filter_by(produto_id=produto_id, uf=u).first()


def soma_aliquotas(perfil: EmpresaNcmPerfil | None) -> Decimal:
    if not perfil:
        return Decimal(0)
    return Decimal(perfil.aliquota_icms or 0) + Decimal(perfil.aliquota_ipi or 0) + Decimal(
        perfil.aliquota_pis or 0
    ) + Decimal(perfil.aliquota_cofins or 0)


def soma_aliquotas_uf(imp: EmpresaProdutoImpostoUF | None) -> Decimal:
    if not imp:
        return Decimal(0)
    return Decimal(imp.aliquota_icms or 0) + Decimal(imp.aliquota_ipi or 0) + Decimal(
        imp.aliquota_pis or 0
    ) + Decimal(imp.aliquota_cofins or 0)


def calcular_linha(
    fechamento: EmpresaFechamentoPreco,
    item: EmpresaFechamentoPrecoItem,
    perfil_ncm: EmpresaNcmPerfil | None,
    imposto_uf: EmpresaProdutoImpostoUF | None,
) -> dict:
    q = Decimal(item.quantidade or 0)
    cu = Decimal(item.custo_unitario or 0)
    base = q * cu
    fp = Decimal(fechamento.percentual_frete or 0)
    finp = Decimal(fechamento.custo_financeiro_percent or 0)
    v_frete = base * (fp / Decimal(100))
    v_fin = base * (finp / Decimal(100))
    custo_ajustado = base + v_frete + v_fin
    if imposto_uf:
        tax_pct = soma_aliquotas_uf(imposto_uf)
        fonte = "produto_uf"
    elif perfil_ncm:
        tax_pct = soma_aliquotas(perfil_ncm)
        fonte = "ncm"
    else:
        tax_pct = Decimal(0)
        fonte = "—"
    v_imp = custo_ajustado * (tax_pct / Decimal(100))
    subtotal = custo_ajustado + v_imp
    mk = Decimal(fechamento.markup_final_percent or 0)
    total_sugerido = subtotal * (Decimal(1) + mk / Decimal(100))
    prod = item.produto
    ncm_show = normalize_ncm_digits(prod.ncm) if prod else ""
    return {
        "base": base,
        "frete": v_frete,
        "financeiro": v_fin,
        "custo_ajustado": custo_ajustado,
        "aliquota_total_pct": tax_pct,
        "imposto_estimado": v_imp,
        "subtotal_antes_markup": subtotal,
        "total_sugerido": total_sugerido,
        "perfil_encontrado": perfil_ncm is not None,
        "imposto_uf_encontrado": imposto_uf is not None,
        "ncm_produto": ncm_show,
        "fonte_imposto": fonte,
    }


def _parse_competencia_mes(raw: str | None):
    s = (raw or "").strip()
    if len(s) == 7 and s[4] == "-":
        try:
            y, m = s.split("-", 1)
            return date(int(y), int(m), 1)
        except (ValueError, TypeError):
            return None
    return None


def _total_geral_fechamento(fech: EmpresaFechamentoPreco) -> Decimal:
    if fech.planilha_fechada and fech.snapshot_json:
        try:
            data = json.loads(fech.snapshot_json)
            return Decimal(str(data.get("total_geral", "0")))
        except (json.JSONDecodeError, TypeError, InvalidOperation):
            pass
    _, total = _montar_linhas_calculadas(fech)
    return total


def _sync_comissoes_fechamento(fechamento_id: int) -> None:
    EmpresaFechamentoComissao.query.filter_by(fechamento_id=fechamento_id).delete(
        synchronize_session=False
    )
    emps = request.form.getlist("comissao_emp")
    pcts = request.form.getlist("comissao_pct")
    papels = request.form.getlist("comissao_papel")
    seen: set[int] = set()
    for i, eid_raw in enumerate(emps):
        if not (eid_raw and str(eid_raw).strip()):
            continue
        try:
            eid = int(eid_raw)
        except (TypeError, ValueError):
            continue
        if eid in seen:
            continue
        if not db.session.get(CompanyEmployee, eid):
            continue
        seen.add(eid)
        pct = _parse_decimal(pcts[i] if i < len(pcts) else None)
        if pct is None or pct <= 0:
            continue
        papel = (papels[i] if i < len(papels) else "") or ""
        papel = papel.strip()[:120] or None
        db.session.add(
            EmpresaFechamentoComissao(
                fechamento_id=fechamento_id,
                employee_id=eid,
                percentual_comissao=pct,
                papel=papel,
            )
        )


def _montar_linhas_calculadas(fech: EmpresaFechamentoPreco) -> tuple[list[dict], Decimal]:
    uf = (fech.uf_entrega or "").strip().upper()[:2]
    fech.itens.sort(key=lambda x: (x.sort_order, x.id))
    linhas: list[dict] = []
    total_geral = Decimal(0)
    for it in fech.itens:
        imp_uf = None
        if len(uf) == 2 and it.produto_id:
            imp_uf = buscar_imposto_produto_uf(it.produto_id, uf)
        perfil = buscar_perfil_ncm(it.produto.ncm if it.produto else None)
        calc = calcular_linha(fech, it, perfil, imp_uf)
        linhas.append({"item": it, "calc": calc, "perfil": perfil, "imposto_uf": imp_uf})
        total_geral += calc["total_sugerido"]
    return linhas, total_geral


def register_fechamento_preco_routes(bp) -> None:
    from empresa_anexos import listar_anexos
    from empresa_imposto import BR_UFS
    from empresa_intranet import _current_employee, empresa_login_required

    def _planilha_cabecalho_form(row: EmpresaFechamentoPreco | None):
        licitacoes = EmpresaLicitacao.query.order_by(EmpresaLicitacao.id.desc()).limit(200).all()
        colaboradores = CompanyEmployee.query.filter_by(is_active=True).order_by(CompanyEmployee.name).all()
        comissoes = list(row.comissoes) if row else []
        return render_template(
            "empresa/fechamento_preco/planilha_form.html",
            staff=_current_employee(),
            planilha=row,
            licitacoes=licitacoes,
            ufs=BR_UFS,
            colaboradores=colaboradores,
            comissoes=comissoes,
        )

    @bp.route("/fechamento-preco/")
    @empresa_login_required
    def fechamento_preco_hub():
        n_plan = EmpresaFechamentoPreco.query.count()
        n_ncm = EmpresaNcmPerfil.query.count()
        n_imp = EmpresaProdutoImpostoUF.query.count()
        sem_ncm = EmpresaProduto.query.filter(
            or_(EmpresaProduto.ncm.is_(None), EmpresaProduto.ncm == "")
        ).count()
        return render_template(
            "empresa/fechamento_preco/hub.html",
            staff=_current_employee(),
            n_planilhas=n_plan,
            n_ncm=n_ncm,
            n_imposto_matriz=n_imp,
            n_produtos_sem_ncm=sem_ncm,
        )

    @bp.route("/fechamento-preco/relatorio-comissoes")
    @empresa_login_required
    def fechamento_preco_relatorio_comissoes():
        mes = (request.args.get("mes") or "").strip()
        if len(mes) != 7 or mes[4] != "-":
            mes = date.today().strftime("%Y-%m")
        y_s, m_s = mes.split("-", 1)
        y, mo = int(y_s), int(m_s)
        fechamentos_mes: list[EmpresaFechamentoPreco] = []
        q = EmpresaFechamentoPreco.query.options(
            joinedload(EmpresaFechamentoPreco.comissoes).joinedload(EmpresaFechamentoComissao.employee),
            joinedload(EmpresaFechamentoPreco.account),
        ).filter(EmpresaFechamentoPreco.planilha_fechada.is_(True))
        for fech in q.all():
            comp = fech.competencia_faturamento
            fe_dt = fech.fechada_em.date() if fech.fechada_em else None
            if comp and comp.year == y and comp.month == mo:
                fechamentos_mes.append(fech)
            elif comp is None and fe_dt and fe_dt.year == y and fe_dt.month == mo:
                fechamentos_mes.append(fech)
        totais: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
        detalhes: dict[int, list[dict]] = defaultdict(list)
        for fech in fechamentos_mes:
            tot = _total_geral_fechamento(fech)
            for c in fech.comissoes:
                ve = tot * Decimal(c.percentual_comissao or 0) / Decimal(100)
                totais[c.employee_id] += ve
                detalhes[c.employee_id].append(
                    {
                        "planilha_id": fech.id,
                        "titulo": fech.titulo,
                        "percentual": c.percentual_comissao,
                        "valor": ve,
                        "papel": c.papel,
                    }
                )
        emp_ids = list(totais.keys())
        employees = {}
        if emp_ids:
            for e in CompanyEmployee.query.filter(CompanyEmployee.id.in_(emp_ids)).all():
                employees[e.id] = e
        return render_template(
            "empresa/fechamento_preco/relatorio_comissoes.html",
            staff=_current_employee(),
            mes=mes,
            totais=dict(totais),
            detalhes=dict(detalhes),
            employees=employees,
            n_planilhas=len(fechamentos_mes),
        )

    @bp.route("/fechamento-preco/planilhas")
    @empresa_login_required
    def fechamento_preco_planilhas():
        rows = (
            EmpresaFechamentoPreco.query.options(
                joinedload(EmpresaFechamentoPreco.licitacao),
                joinedload(EmpresaFechamentoPreco.account),
            )
            .order_by(EmpresaFechamentoPreco.id.desc())
            .limit(300)
            .all()
        )
        return render_template(
            "empresa/fechamento_preco/planilhas_list.html",
            staff=_current_employee(),
            planilhas=rows,
        )

    @bp.route("/fechamento-preco/planilhas/novo", methods=["GET", "POST"])
    @empresa_login_required
    def fechamento_preco_planilha_new():
        if request.method == "POST":
            return _save_planilha(None)
        return _planilha_cabecalho_form(None)

    @bp.route("/fechamento-preco/planilhas/<int:fid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def fechamento_preco_planilha_edit(fid: int):
        row = (
            EmpresaFechamentoPreco.query.options(joinedload(EmpresaFechamentoPreco.comissoes))
            .filter_by(id=fid)
            .first_or_404()
        )
        if row.planilha_fechada:
            flash("Esta planilha está fechada e não pode ser editada.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        if request.method == "POST":
            return _save_planilha(row)
        return _planilha_cabecalho_form(row)

    def _save_planilha(row: EmpresaFechamentoPreco | None):
        user = _current_employee()
        titulo = (request.form.get("titulo") or "").strip()
        if not titulo:
            flash("Título é obrigatório.", "error")
            return _planilha_cabecalho_form(row)
        if row and row.planilha_fechada:
            flash("Planilha fechada.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=row.id))
        lid = request.form.get("licitacao_id", type=int)
        lic = EmpresaLicitacao.query.get(lid) if lid else None
        if row is None:
            row = EmpresaFechamentoPreco(
                titulo=titulo[:400],
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
            db.session.flush()
        else:
            row.titulo = titulo[:400]
        row.local_entrega = (request.form.get("local_entrega") or "").strip() or None
        uf_e = (request.form.get("uf_entrega") or "").strip().upper()[:2]
        row.uf_entrega = uf_e if len(uf_e) == 2 else None
        row.licitacao_id = lic.id if lic else None
        aid = request.form.get("account_id", type=int)
        if aid:
            acc = db.session.get(CompanyEmployee, aid)
            row.account_id = acc.id if acc and acc.is_active else None
        else:
            row.account_id = None
        row.competencia_faturamento = _parse_competencia_mes(request.form.get("competencia_faturamento"))
        row.beneficio_fiscal = (request.form.get("beneficio_fiscal") or "").strip() or None
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        row.percentual_frete = _parse_decimal(request.form.get("percentual_frete"))
        row.custo_financeiro_percent = _parse_decimal(request.form.get("custo_financeiro_percent"))
        row.markup_final_percent = _parse_decimal(request.form.get("markup_final_percent"))
        row.status = (request.form.get("status") or "rascunho").strip()[:40]
        row.aprovacao_prejuizo = request.form.get("aprovacao_prejuizo") == "1"
        row.compras_aprovado = request.form.get("compras_aprovado") == "1"
        _sync_comissoes_fechamento(row.id)
        db.session.commit()
        flash("Planilha salva.", "ok")
        return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=row.id))

    @bp.route("/fechamento-preco/planilhas/<int:fid>/fechar", methods=["POST"])
    @empresa_login_required
    def fechamento_preco_planilha_fechar(fid: int):
        fech = (
            EmpresaFechamentoPreco.query.options(
                joinedload(EmpresaFechamentoPreco.itens).joinedload(EmpresaFechamentoPrecoItem.produto),
                joinedload(EmpresaFechamentoPreco.comissoes).joinedload(EmpresaFechamentoComissao.employee),
                joinedload(EmpresaFechamentoPreco.account),
            )
            .filter_by(id=fid)
            .first_or_404()
        )
        if fech.planilha_fechada:
            flash("Planilha já está fechada.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        linhas, total_geral = _montar_linhas_calculadas(fech)
        snap_linhas = []
        for row in linhas:
            c = row["calc"]
            it = row["item"]
            p = it.produto
            snap_linhas.append(
                {
                    "item_id": it.id,
                    "part_number": p.part_number if p else "",
                    "nome": p.nome if p else "",
                    "quantidade": str(it.quantidade),
                    "custo_unitario": str(it.custo_unitario),
                    "fonte_imposto": str(c["fonte_imposto"]),
                    "aliquota_total_pct": str(c["aliquota_total_pct"]),
                    "total_sugerido": str(c["total_sugerido"]),
                    "ncm": c["ncm_produto"],
                }
            )
        com_resumo = []
        for cm in fech.comissoes:
            emp = cm.employee
            pct = Decimal(cm.percentual_comissao or 0)
            ve = total_geral * (pct / Decimal(100))
            com_resumo.append(
                {
                    "employee_id": cm.employee_id,
                    "nome": emp.name if emp else "",
                    "email": emp.email if emp else "",
                    "papel": cm.papel or "",
                    "percentual": str(pct),
                    "valor_estimado_comissao": str(ve),
                }
            )
        acc = fech.account
        fech.snapshot_json = json.dumps(
            {
                "fechada_em": datetime.utcnow().isoformat() + "Z",
                "uf_entrega": fech.uf_entrega,
                "total_geral": str(total_geral),
                "linhas": snap_linhas,
                "account_id": fech.account_id,
                "account_name": acc.name if acc else None,
                "competencia_faturamento": fech.competencia_faturamento.isoformat()
                if fech.competencia_faturamento
                else None,
                "comissoes_resumo": com_resumo,
            },
            ensure_ascii=False,
        )
        fech.planilha_fechada = True
        fech.fechada_em = datetime.utcnow()
        fech.status = "fechada"
        db.session.commit()
        flash("Planilha fechada. Valores registrados no snapshot.", "ok")
        return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))

    @bp.route("/fechamento-preco/planilhas/<int:fid>")
    @empresa_login_required
    def fechamento_preco_planilha_detail(fid: int):
        fech = (
            EmpresaFechamentoPreco.query.options(
                joinedload(EmpresaFechamentoPreco.itens).joinedload(EmpresaFechamentoPrecoItem.produto),
                joinedload(EmpresaFechamentoPreco.licitacao),
                joinedload(EmpresaFechamentoPreco.comissoes).joinedload(EmpresaFechamentoComissao.employee),
                joinedload(EmpresaFechamentoPreco.account),
            )
            .filter_by(id=fid)
            .first_or_404()
        )
        snapshot = None
        if fech.planilha_fechada and fech.snapshot_json:
            try:
                snapshot = json.loads(fech.snapshot_json)
            except (json.JSONDecodeError, TypeError):
                snapshot = None
        linhas, total_geral = _montar_linhas_calculadas(fech)
        produtos = (
            EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(800).all()
        )
        anexos = listar_anexos("fechamento_preco", fech.id)
        comissoes_valores = []
        for cm in fech.comissoes:
            pct = Decimal(cm.percentual_comissao or 0)
            ve = total_geral * (pct / Decimal(100))
            comissoes_valores.append({"com": cm, "valor_estimado": ve})
        return render_template(
            "empresa/fechamento_preco/planilha_detail.html",
            staff=_current_employee(),
            planilha=fech,
            linhas=linhas,
            total_geral=total_geral,
            produtos=produtos,
            snapshot=snapshot,
            bloqueado=fech.planilha_fechada,
            anexos=anexos,
            comissoes_valores=comissoes_valores,
        )

    @bp.route("/fechamento-preco/planilhas/<int:fid>/itens/adicionar", methods=["POST"])
    @empresa_login_required
    def fechamento_preco_item_add(fid: int):
        fech = EmpresaFechamentoPreco.query.get_or_404(fid)
        if fech.planilha_fechada:
            flash("Planilha fechada — não é possível alterar linhas.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        pid = request.form.get("produto_id", type=int)
        prod = EmpresaProduto.query.get(pid) if pid else None
        if prod is None or not prod.ativo:
            flash("Selecione um produto ativo.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        uf = (fech.uf_entrega or "").strip().upper()[:2]
        imp_uf = buscar_imposto_produto_uf(prod.id, uf) if len(uf) == 2 else None
        if not imp_uf and not normalize_ncm_digits(prod.ncm):
            flash(
                "Cadastre NCM em Compras ou alíquotas por UF no setor Impostos (produto + UF de entrega).",
                "error",
            )
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        qtd = _parse_decimal(request.form.get("quantidade")) or Decimal("1")
        cu = _parse_decimal(request.form.get("custo_unitario")) or Decimal("0")
        obs = (request.form.get("observacao") or "").strip()[:500] or None
        max_so = (
            db.session.query(db.func.max(EmpresaFechamentoPrecoItem.sort_order))
            .filter_by(fechamento_id=fech.id)
            .scalar()
        )
        it = EmpresaFechamentoPrecoItem(
            fechamento_id=fech.id,
            produto_id=prod.id,
            quantidade=qtd,
            custo_unitario=cu,
            sort_order=(max_so or 0) + 1,
            observacao=obs,
        )
        db.session.add(it)
        db.session.commit()
        flash("Linha adicionada.", "ok")
        return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))

    @bp.route("/fechamento-preco/planilhas/itens/<int:iid>/excluir", methods=["POST"])
    @empresa_login_required
    def fechamento_preco_item_delete(iid: int):
        it = EmpresaFechamentoPrecoItem.query.get_or_404(iid)
        fid = it.fechamento_id
        fech = EmpresaFechamentoPreco.query.get(fid)
        if fech and fech.planilha_fechada:
            flash("Planilha fechada — não é possível excluir linhas.", "error")
            return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))
        db.session.delete(it)
        db.session.commit()
        flash("Linha removida.", "ok")
        return redirect(url_for("empresa.fechamento_preco_planilha_detail", fid=fid))

    @bp.route("/fechamento-preco/tabela-ncm")
    @empresa_login_required
    def fechamento_preco_ncm_list():
        rows = EmpresaNcmPerfil.query.order_by(EmpresaNcmPerfil.ncm).limit(2000).all()
        return render_template(
            "empresa/fechamento_preco/ncm_list.html",
            staff=_current_employee(),
            perfis=rows,
        )

    @bp.route("/fechamento-preco/tabela-ncm/novo", methods=["GET", "POST"])
    @empresa_login_required
    def fechamento_preco_ncm_new():
        if request.method == "POST":
            return _save_ncm_perfil(None)
        return render_template("empresa/fechamento_preco/ncm_form.html", staff=_current_employee(), perfil=None)

    @bp.route("/fechamento-preco/tabela-ncm/<int:nid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def fechamento_preco_ncm_edit(nid: int):
        row = EmpresaNcmPerfil.query.get_or_404(nid)
        if request.method == "POST":
            return _save_ncm_perfil(row)
        return render_template("empresa/fechamento_preco/ncm_form.html", staff=_current_employee(), perfil=row)

    def _save_ncm_perfil(row: EmpresaNcmPerfil | None):
        ncm = normalize_ncm_digits(request.form.get("ncm"))
        if len(ncm) != 8:
            flash("NCM deve ter 8 dígitos.", "error")
            return render_template(
                "empresa/fechamento_preco/ncm_form.html",
                staff=_current_employee(),
                perfil=row,
            )
        if row is None:
            ex = EmpresaNcmPerfil.query.filter_by(ncm=ncm).first()
            if ex:
                flash("Já existe perfil para este NCM.", "error")
                return render_template(
                    "empresa/fechamento_preco/ncm_form.html",
                    staff=_current_employee(),
                    perfil=None,
                )
            row = EmpresaNcmPerfil(ncm=ncm)
            db.session.add(row)
        else:
            other = EmpresaNcmPerfil.query.filter(
                EmpresaNcmPerfil.ncm == ncm,
                EmpresaNcmPerfil.id != row.id,
            ).first()
            if other:
                flash("Já existe perfil para este NCM.", "error")
                return render_template(
                    "empresa/fechamento_preco/ncm_form.html",
                    staff=_current_employee(),
                    perfil=row,
                )
            row.ncm = ncm
        row.descricao = (request.form.get("descricao") or "").strip() or None
        row.aliquota_icms = _parse_decimal(request.form.get("aliquota_icms")) or Decimal(0)
        row.aliquota_ipi = _parse_decimal(request.form.get("aliquota_ipi")) or Decimal(0)
        row.aliquota_pis = _parse_decimal(request.form.get("aliquota_pis")) or Decimal(0)
        row.aliquota_cofins = _parse_decimal(request.form.get("aliquota_cofins")) or Decimal(0)
        row.observacao = (request.form.get("observacao") or "").strip() or None
        db.session.commit()
        flash("Perfil NCM salvo.", "ok")
        return redirect(url_for("empresa.fechamento_preco_ncm_list"))
