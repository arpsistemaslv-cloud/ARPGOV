"""
Módulos operacionais por setor (cadastros além do setor Compras).
Registrado em empresa_intranet: register_operacional_routes(empresa_bp).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import requests
from flask import flash, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from empresa_anexos import listar_anexos

from braspress_client import (
    api_base_url,
    build_tracking_url,
    default_cnpj_remetente,
    fetch_tracking_get,
    format_body_preview,
    braspress_credentials,
)

from models import (
    EmpresaChamadoTecnico,
    EmpresaContratoOrgao,
    EmpresaEmpenho,
    EmpresaLicitacao,
    EmpresaNotaFiscalEmitida,
    EmpresaPendencia,
    EmpresaProcessoJuridico,
    EmpresaProduto,
    EmpresaProjeto,
    EmpresaProjetoMarco,
    EmpresaProtocoloGarantia,
    EmpresaRemessa,
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


def _licitacao_checklist_parse(raw_json: str | None) -> list[dict]:
    try:
        data = json.loads(raw_json or "[]")
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for it in data:
            if not isinstance(it, dict):
                continue
            t = (it.get("text") or "").strip()
            if not t:
                continue
            out.append(
                {
                    "text": t,
                    "ok": bool(it.get("ok")),
                    "obs": ((it.get("obs") or "").strip() or None),
                }
            )
        return out
    except (json.JSONDecodeError, TypeError):
        return []


def _licitacao_checklist_save_from_form(row: EmpresaLicitacao) -> None:
    raw: list = []
    try:
        raw = json.loads(row.checklist_documentos_json or "[]")
    except (json.JSONDecodeError, TypeError):
        raw = []
    if not isinstance(raw, list):
        raw = []
    items: list[dict] = []
    for i, it in enumerate(raw):
        if not isinstance(it, dict):
            continue
        text = (it.get("text") or "").strip()
        if not text:
            continue
        if request.form.get(f"checklist_remover_{i}") == "1":
            continue
        ok = request.form.get(f"checklist_ok_{i}") == "1"
        obs = (request.form.get(f"checklist_obs_{i}") or "").strip()[:500] or None
        items.append({"text": text, "ok": ok, "obs": obs})
    for line in (request.form.get("checklist_novos") or "").splitlines():
        t = line.strip()
        if t:
            items.append({"text": t, "ok": False, "obs": None})
    row.checklist_documentos_json = json.dumps(items, ensure_ascii=False)


def register_operacional_routes(bp) -> None:
    from empresa_intranet import EMPRESA_SETORES, _current_employee, empresa_login_required

    setor_choices = EMPRESA_SETORES

    @bp.route("/modulos/")
    @empresa_login_required
    def modulos_hub():
        return render_template(
            "empresa/modulos/hub.html",
            staff=_current_employee(),
            setores=setor_choices,
        )

    # --- Logística ---
    @bp.route("/logistica/")
    @empresa_login_required
    def logistica_hub():
        n = EmpresaRemessa.query.count()
        return render_template(
            "empresa/logistica/hub.html",
            staff=_current_employee(),
            n_remessas=n,
        )

    @bp.route("/logistica/remessas")
    @empresa_login_required
    def logistica_remessas_list():
        q = (request.args.get("q") or "").strip()
        query = EmpresaRemessa.query.options(joinedload(EmpresaRemessa.empenho))
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    EmpresaRemessa.destino_orgao.ilike(like),
                    EmpresaRemessa.codigo_rastreio.ilike(like),
                    EmpresaRemessa.nf_referencia.ilike(like),
                )
            )
        rows = query.order_by(EmpresaRemessa.id.desc()).limit(400).all()
        return render_template(
            "empresa/logistica/remessas_list.html",
            staff=_current_employee(),
            remessas=rows,
            q=q,
        )

    @bp.route("/logistica/remessas/novo", methods=["GET", "POST"])
    @empresa_login_required
    def logistica_remessas_new():
        if request.method == "POST":
            return _save_remessa(None)
        return _remessa_form(None)

    @bp.route("/logistica/remessas/<int:rid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def logistica_remessas_edit(rid: int):
        row = EmpresaRemessa.query.get_or_404(rid)
        if request.method == "POST":
            return _save_remessa(row)
        return _remessa_form(row)

    def _remessa_form(row: EmpresaRemessa | None):
        empenhos = EmpresaEmpenho.query.order_by(EmpresaEmpenho.id.desc()).limit(200).all()
        return render_template(
            "empresa/logistica/remessa_form.html",
            staff=_current_employee(),
            remessa=row,
            empenhos=empenhos,
        )

    def _save_remessa(row: EmpresaRemessa | None):
        user = _current_employee()
        destino = (request.form.get("destino_orgao") or "").strip()
        if not destino:
            flash("Destino (órgão) é obrigatório.", "error")
            return _remessa_form(row)
        eid = request.form.get("empenho_id", type=int)
        emp = EmpresaEmpenho.query.get(eid) if eid else None
        if row is None:
            row = EmpresaRemessa(destino_orgao=destino, created_by_id=user.id if user else None)
            db.session.add(row)
        else:
            row.destino_orgao = destino
        row.codigo_rastreio = (request.form.get("codigo_rastreio") or "").strip() or None
        row.nf_referencia = (request.form.get("nf_referencia") or "").strip() or None
        row.endereco_resumo = (request.form.get("endereco_resumo") or "").strip() or None
        row.status = (request.form.get("status") or "em_transito").strip()[:40]
        row.data_prevista = _parse_date(request.form.get("data_prevista"))
        row.data_entrega = _parse_date(request.form.get("data_entrega"))
        row.empenho_id = emp.id if emp else None
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Remessa salva.", "ok")
        return redirect(url_for("empresa.logistica_remessas_list"))

    @bp.route("/logistica/rastreamento-braspress", methods=["GET", "POST"])
    @empresa_login_required
    def logistica_braspress_rastreamento():
        track_result: dict | None = None
        posted = {}
        if request.method == "POST":
            posted = {
                "cnpj": (request.form.get("cnpj") or "").strip(),
                "spec": (request.form.get("spec") or "v3_nf").strip(),
                "reference": (request.form.get("reference") or "").strip(),
                "return_type": (request.form.get("return_type") or "json").strip(),
            }
            bu, bp = braspress_credentials()
            if not bu or not bp:
                flash(
                    "Rastreamento Braspress: defina BRASPRESS_API_USER e "
                    "BRASPRESS_API_PASSWORD no .env e reinicie o servidor.",
                    "error",
                )
            else:
                try:
                    url = build_tracking_url(
                        posted["spec"],
                        posted["cnpj"],
                        posted["reference"],
                        posted["return_type"],
                    )
                    status_code, body, ct = fetch_tracking_get(url)
                    pretty = format_body_preview(body, ct)
                    track_result = {
                        "url": url,
                        "http_status": status_code,
                        "content_type": ct,
                        "body": body,
                        "pretty_json": pretty,
                    }
                except ValueError as ex:
                    flash(str(ex), "error")
                except RuntimeError as ex:
                    flash(str(ex), "error")
                except requests.RequestException as ex:
                    flash(
                        f"Erro de rede ou tempo esgotado ao falar com a API Braspress: {ex}",
                        "error",
                    )
                except Exception as ex:
                    flash(f"Falha na consulta: {ex}", "error")

        return render_template(
            "empresa/logistica/braspress_rastreamento.html",
            staff=_current_employee(),
            default_cnpj=default_cnpj_remetente() or posted.get("cnpj", ""),
            api_base=api_base_url(),
            track_result=track_result,
            posted=posted,
        )

    # --- Técnico ---
    @bp.route("/tecnico/")
    @empresa_login_required
    def tecnico_hub():
        n = EmpresaChamadoTecnico.query.count()
        return render_template("empresa/tecnico/hub.html", staff=_current_employee(), n_chamados=n)

    @bp.route("/tecnico/chamados")
    @empresa_login_required
    def tecnico_chamados_list():
        rows = EmpresaChamadoTecnico.query.order_by(EmpresaChamadoTecnico.id.desc()).limit(400).all()
        return render_template(
            "empresa/tecnico/chamados_list.html",
            staff=_current_employee(),
            chamados=rows,
        )

    @bp.route("/tecnico/chamados/novo", methods=["GET", "POST"])
    @empresa_login_required
    def tecnico_chamados_new():
        if request.method == "POST":
            return _save_chamado(None)
        return _chamado_form(None)

    @bp.route("/tecnico/chamados/<int:cid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def tecnico_chamados_edit(cid: int):
        row = EmpresaChamadoTecnico.query.get_or_404(cid)
        if request.method == "POST":
            return _save_chamado(row)
        return _chamado_form(row)

    def _chamado_form(row: EmpresaChamadoTecnico | None):
        return render_template(
            "empresa/tecnico/chamado_form.html",
            staff=_current_employee(),
            chamado=row,
        )

    def _save_chamado(row: EmpresaChamadoTecnico | None):
        user = _current_employee()
        titulo = (request.form.get("titulo") or "").strip()
        num = (request.form.get("numero_interno") or "").strip()
        if not titulo:
            flash("Título é obrigatório.", "error")
            return _chamado_form(row)
        is_new = row is None
        if is_new:
            row = EmpresaChamadoTecnico(
                numero_interno=num or "NOVO",
                titulo=titulo,
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
            db.session.flush()
            if not num:
                row.numero_interno = f"CH-{row.id:05d}"
        else:
            if num:
                row.numero_interno = num[:64]
            row.titulo = titulo
        row.orgao_cliente = (request.form.get("orgao_cliente") or "").strip() or None
        row.prioridade = (request.form.get("prioridade") or "media").strip()[:24]
        row.status = (request.form.get("status") or "aberto").strip()[:40]
        row.descricao = (request.form.get("descricao") or "").strip() or None
        row.solucao_resumo = (request.form.get("solucao_resumo") or "").strip() or None
        ae = _parse_date(request.form.get("aberto_em"))
        if is_new:
            row.aberto_em = ae or date.today()
        else:
            row.aberto_em = ae
        row.fechado_em = _parse_date(request.form.get("fechado_em"))
        db.session.commit()
        flash("Chamado salvo.", "ok")
        return redirect(url_for("empresa.tecnico_chamados_list"))

    # --- Jurídico ---
    @bp.route("/juridico/")
    @empresa_login_required
    def juridico_hub():
        n = EmpresaProcessoJuridico.query.count()
        return render_template("empresa/juridico/hub.html", staff=_current_employee(), n_processos=n)

    @bp.route("/juridico/processos")
    @empresa_login_required
    def juridico_processos_list():
        rows = EmpresaProcessoJuridico.query.order_by(EmpresaProcessoJuridico.id.desc()).limit(400).all()
        return render_template(
            "empresa/juridico/processos_list.html",
            staff=_current_employee(),
            processos=rows,
        )

    @bp.route("/juridico/processos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def juridico_processos_new():
        if request.method == "POST":
            return _save_processo(None)
        return _processo_form(None)

    @bp.route("/juridico/processos/<int:pid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def juridico_processos_edit(pid: int):
        row = EmpresaProcessoJuridico.query.get_or_404(pid)
        if request.method == "POST":
            return _save_processo(row)
        return _processo_form(row)

    def _processo_form(row: EmpresaProcessoJuridico | None):
        return render_template(
            "empresa/juridico/processo_form.html",
            staff=_current_employee(),
            processo=row,
        )

    def _save_processo(row: EmpresaProcessoJuridico | None):
        user = _current_employee()
        nproc = (request.form.get("numero_processo") or "").strip()
        if not nproc:
            flash("Número do processo é obrigatório.", "error")
            return _processo_form(row)
        if row is None:
            row = EmpresaProcessoJuridico(
                numero_processo=nproc,
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
        else:
            row.numero_processo = nproc
        row.tipo = (request.form.get("tipo") or "").strip() or None
        row.tribunal = (request.form.get("tribunal") or "").strip() or None
        row.polo = (request.form.get("polo") or "").strip() or None
        row.status = (request.form.get("status") or "ativo").strip()[:60]
        row.proximo_prazo = _parse_date(request.form.get("proximo_prazo"))
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Processo salvo.", "ok")
        return redirect(url_for("empresa.juridico_processos_list"))

    # --- Garantia ---
    @bp.route("/garantia/")
    @empresa_login_required
    def garantia_hub():
        n = EmpresaProtocoloGarantia.query.count()
        return render_template("empresa/garantia/hub.html", staff=_current_employee(), n_protocolos=n)

    @bp.route("/garantia/protocolos")
    @empresa_login_required
    def garantia_protocolos_list():
        rows = (
            EmpresaProtocoloGarantia.query.options(joinedload(EmpresaProtocoloGarantia.produto))
            .order_by(EmpresaProtocoloGarantia.id.desc())
            .limit(400)
            .all()
        )
        return render_template(
            "empresa/garantia/protocolos_list.html",
            staff=_current_employee(),
            protocolos=rows,
        )

    @bp.route("/garantia/protocolos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def garantia_protocolos_new():
        if request.method == "POST":
            return _save_protocolo_garantia(None)
        return _protocolo_garantia_form(None)

    @bp.route("/garantia/protocolos/<int:pid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def garantia_protocolos_edit(pid: int):
        row = EmpresaProtocoloGarantia.query.get_or_404(pid)
        if request.method == "POST":
            return _save_protocolo_garantia(row)
        return _protocolo_garantia_form(row)

    def _protocolo_garantia_form(row: EmpresaProtocoloGarantia | None):
        produtos = EmpresaProduto.query.filter_by(ativo=True).order_by(EmpresaProduto.part_number).limit(500).all()
        return render_template(
            "empresa/garantia/protocolo_form.html",
            staff=_current_employee(),
            protocolo=row,
            produtos=produtos,
        )

    def _save_protocolo_garantia(row: EmpresaProtocoloGarantia | None):
        user = _current_employee()
        num = (request.form.get("numero_protocolo") or "").strip()
        orgao = (request.form.get("orgao_solicitante") or "").strip()
        if not orgao:
            flash("Órgão solicitante é obrigatório.", "error")
            return _protocolo_garantia_form(row)
        pid = request.form.get("produto_id", type=int)
        prod = EmpresaProduto.query.get(pid) if pid else None
        if row is None:
            if num:
                dup = EmpresaProtocoloGarantia.query.filter_by(numero_protocolo=num[:80]).first()
                if dup:
                    flash("Já existe protocolo com este número.", "error")
                    return _protocolo_garantia_form(None)
            row = EmpresaProtocoloGarantia(
                numero_protocolo=num or "NOVO",
                orgao_solicitante=orgao,
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
            db.session.flush()
            if not num:
                row.numero_protocolo = f"PG-{row.id:05d}"
        else:
            if num:
                other = EmpresaProtocoloGarantia.query.filter(
                    EmpresaProtocoloGarantia.numero_protocolo == num,
                    EmpresaProtocoloGarantia.id != row.id,
                ).first()
                if other:
                    flash("Já existe protocolo com este número.", "error")
                    return _protocolo_garantia_form(row)
                row.numero_protocolo = num[:80]
            row.orgao_solicitante = orgao
        row.produto_id = prod.id if prod else None
        row.descricao_produto = (request.form.get("descricao_produto") or "").strip() or None
        row.defeito_relato = (request.form.get("defeito_relato") or "").strip() or None
        row.status = (request.form.get("status") or "aberto").strip()[:40]
        row.data_abertura = _parse_date(request.form.get("data_abertura"))
        row.data_conclusao = _parse_date(request.form.get("data_conclusao"))
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Protocolo salvo.", "ok")
        return redirect(url_for("empresa.garantia_protocolos_list"))

    # --- Contratos e empenhos (cadastro de contratos) ---
    @bp.route("/contratos/")
    @empresa_login_required
    def contratos_hub():
        n = EmpresaContratoOrgao.query.count()
        return render_template("empresa/contratos/hub.html", staff=_current_employee(), n_contratos=n)

    @bp.route("/contratos/lista")
    @empresa_login_required
    def contratos_lista():
        rows = EmpresaContratoOrgao.query.order_by(EmpresaContratoOrgao.id.desc()).limit(400).all()
        return render_template(
            "empresa/contratos/contratos_list.html",
            staff=_current_employee(),
            contratos=rows,
        )

    @bp.route("/contratos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def contratos_new():
        if request.method == "POST":
            return _save_contrato(None)
        return _contrato_form(None)

    @bp.route("/contratos/<int:cid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def contratos_edit(cid: int):
        row = EmpresaContratoOrgao.query.get_or_404(cid)
        if request.method == "POST":
            return _save_contrato(row)
        return _contrato_form(row)

    def _contrato_form(row: EmpresaContratoOrgao | None):
        anexos = listar_anexos("contrato", row.id) if row else []
        return render_template(
            "empresa/contratos/contrato_form.html",
            staff=_current_employee(),
            contrato=row,
            anexos=anexos,
        )

    def _save_contrato(row: EmpresaContratoOrgao | None):
        user = _current_employee()
        numero = (request.form.get("numero_contrato") or "").strip()
        orgao = (request.form.get("orgao_nome") or "").strip()
        if not numero or not orgao:
            flash("Número e órgão são obrigatórios.", "error")
            return _contrato_form(row)
        if row is None:
            other = EmpresaContratoOrgao.query.filter_by(numero_contrato=numero).first()
            if other:
                flash("Já existe contrato com este número.", "error")
                return _contrato_form(None)
            row = EmpresaContratoOrgao(
                numero_contrato=numero,
                orgao_nome=orgao,
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
        else:
            other = EmpresaContratoOrgao.query.filter(
                EmpresaContratoOrgao.numero_contrato == numero,
                EmpresaContratoOrgao.id != row.id,
            ).first()
            if other:
                flash("Já existe contrato com este número.", "error")
                return _contrato_form(row)
            row.numero_contrato = numero
            row.orgao_nome = orgao
        row.cnpj_orgao = "".join(c for c in (request.form.get("cnpj_orgao") or "") if c.isdigit())[:14] or None
        row.email_contato = (request.form.get("email_contato") or "").strip()[:120] or None
        row.telefone = (request.form.get("telefone") or "").strip()[:40] or None
        row.cliente_razao_social = (request.form.get("cliente_razao_social") or "").strip()[:400] or None
        row.cliente_cnpj = "".join(c for c in (request.form.get("cliente_cnpj") or "") if c.isdigit())[:14] or None
        row.vigencia_inicio = _parse_date(request.form.get("vigencia_inicio"))
        row.vigencia_fim = _parse_date(request.form.get("vigencia_fim"))
        row.valor_total = _parse_decimal(request.form.get("valor_total"))
        row.saldo_referencia = _parse_decimal(request.form.get("saldo_referencia"))
        row.status = (request.form.get("status") or "vigente").strip()[:40]
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Contrato salvo.", "ok")
        return redirect(url_for("empresa.contratos_detail", cid=row.id))

    # --- Faturamento ---
    @bp.route("/faturamento/")
    @empresa_login_required
    def faturamento_hub():
        n = EmpresaNotaFiscalEmitida.query.count()
        return render_template("empresa/faturamento/hub.html", staff=_current_employee(), n_notas=n)

    @bp.route("/faturamento/notas")
    @empresa_login_required
    def faturamento_notas_list():
        rows = EmpresaNotaFiscalEmitida.query.order_by(EmpresaNotaFiscalEmitida.id.desc()).limit(400).all()
        return render_template(
            "empresa/faturamento/notas_list.html",
            staff=_current_employee(),
            notas=rows,
        )

    @bp.route("/faturamento/notas/novo", methods=["GET", "POST"])
    @empresa_login_required
    def faturamento_notas_new():
        if request.method == "POST":
            return _save_nfe(None)
        return _nfe_form(None)

    @bp.route("/faturamento/notas/<int:nid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def faturamento_notas_edit(nid: int):
        row = EmpresaNotaFiscalEmitida.query.get_or_404(nid)
        if request.method == "POST":
            return _save_nfe(row)
        return _nfe_form(row)

    def _nfe_form(row: EmpresaNotaFiscalEmitida | None):
        return render_template(
            "empresa/faturamento/nota_form.html",
            staff=_current_employee(),
            nota=row,
        )

    def _save_nfe(row: EmpresaNotaFiscalEmitida | None):
        user = _current_employee()
        numero = (request.form.get("numero") or "").strip()
        if not numero:
            flash("Número da NF é obrigatório.", "error")
            return _nfe_form(row)
        if row is None:
            row = EmpresaNotaFiscalEmitida(numero=numero[:32], created_by_id=user.id if user else None)
            db.session.add(row)
        else:
            row.numero = numero[:32]
        row.serie = (request.form.get("serie") or "").strip()[:8] or None
        row.data_emissao = _parse_date(request.form.get("data_emissao"))
        row.valor_total = _parse_decimal(request.form.get("valor_total"))
        row.orgao_cliente = (request.form.get("orgao_cliente") or "").strip() or None
        row.empenho_numero_ref = (request.form.get("empenho_numero_ref") or "").strip() or None
        row.status = (request.form.get("status") or "autorizada").strip()[:40]
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Nota salva.", "ok")
        return redirect(url_for("empresa.faturamento_notas_list"))

    # --- Pendências ---
    @bp.route("/pendencias/")
    @empresa_login_required
    def pendencias_hub():
        n_abertas = EmpresaPendencia.query.filter(EmpresaPendencia.status != "resolvida").count()
        return render_template(
            "empresa/pendencias/hub.html",
            staff=_current_employee(),
            n_abertas=n_abertas,
        )

    @bp.route("/pendencias/itens")
    @empresa_login_required
    def pendencias_itens_list():
        rows = EmpresaPendencia.query.order_by(EmpresaPendencia.id.desc()).limit(500).all()
        return render_template(
            "empresa/pendencias/itens_list.html",
            staff=_current_employee(),
            itens=rows,
            setores=setor_choices,
        )

    @bp.route("/pendencias/itens/novo", methods=["GET", "POST"])
    @empresa_login_required
    def pendencias_itens_new():
        if request.method == "POST":
            return _save_pendencia(None)
        return _pendencia_form(None)

    @bp.route("/pendencias/itens/<int:pid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def pendencias_itens_edit(pid: int):
        row = EmpresaPendencia.query.get_or_404(pid)
        if request.method == "POST":
            return _save_pendencia(row)
        return _pendencia_form(row)

    def _pendencia_form(row: EmpresaPendencia | None):
        return render_template(
            "empresa/pendencias/item_form.html",
            staff=_current_employee(),
            item=row,
            setores=setor_choices,
        )

    def _save_pendencia(row: EmpresaPendencia | None):
        user = _current_employee()
        titulo = (request.form.get("titulo") or "").strip()
        if not titulo:
            flash("Título é obrigatório.", "error")
            return _pendencia_form(row)
        slugs = {s for s, _ in setor_choices}
        origem = (request.form.get("setor_origem_slug") or "").strip()
        resp = (request.form.get("setor_responsavel_slug") or "").strip()
        if origem and origem not in slugs:
            flash("Setor de origem inválido.", "error")
            return _pendencia_form(row)
        if resp and resp not in slugs:
            flash("Setor responsável inválido.", "error")
            return _pendencia_form(row)
        if row is None:
            row = EmpresaPendencia(titulo=titulo, created_by_id=user.id if user else None)
            db.session.add(row)
        else:
            row.titulo = titulo
        row.descricao = (request.form.get("descricao") or "").strip() or None
        row.setor_origem_slug = origem or None
        row.setor_responsavel_slug = resp or None
        row.prioridade = (request.form.get("prioridade") or "media").strip()[:24]
        row.status = (request.form.get("status") or "aberta").strip()[:40]
        row.data_alvo = _parse_date(request.form.get("data_alvo"))
        row.resolvida_em = _parse_date(request.form.get("resolvida_em"))
        row.observacoes_fechamento = (request.form.get("observacoes_fechamento") or "").strip() or None
        db.session.commit()
        flash("Pendência salva.", "ok")
        return redirect(url_for("empresa.pendencias_itens_list"))

    # --- Projetos ---
    @bp.route("/projetos/")
    @empresa_login_required
    def projetos_hub():
        n = EmpresaProjeto.query.count()
        return render_template("empresa/projetos/hub.html", staff=_current_employee(), n_projetos=n)

    @bp.route("/projetos/lista")
    @empresa_login_required
    def projetos_lista():
        rows = EmpresaProjeto.query.order_by(EmpresaProjeto.id.desc()).limit(400).all()
        return render_template(
            "empresa/projetos/projetos_list.html",
            staff=_current_employee(),
            projetos=rows,
        )

    @bp.route("/projetos/novo", methods=["GET", "POST"])
    @empresa_login_required
    def projetos_new():
        if request.method == "POST":
            return _save_projeto(None)
        return _projeto_form(None)

    @bp.route("/projetos/<int:pid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def projetos_edit(pid: int):
        row = EmpresaProjeto.query.get_or_404(pid)
        if request.method == "POST":
            return _save_projeto(row)
        return _projeto_form(row)

    @bp.route("/projetos/<int:pid>")
    @empresa_login_required
    def projetos_detail(pid: int):
        projeto = (
            EmpresaProjeto.query.options(joinedload(EmpresaProjeto.marcos))
            .filter_by(id=pid)
            .first_or_404()
        )
        projeto.marcos.sort(key=lambda m: (m.sort_order, m.id))
        return render_template(
            "empresa/projetos/projeto_detail.html",
            staff=_current_employee(),
            projeto=projeto,
        )

    @bp.route("/projetos/<int:pid>/marcos/adicionar", methods=["POST"])
    @empresa_login_required
    def projetos_marco_add(pid: int):
        projeto = EmpresaProjeto.query.get_or_404(pid)
        nome = (request.form.get("nome") or "").strip()
        if not nome:
            flash("Nome do marco é obrigatório.", "error")
            return redirect(url_for("empresa.projetos_detail", pid=pid))
        max_so = (
            db.session.query(db.func.max(EmpresaProjetoMarco.sort_order))
            .filter_by(projeto_id=projeto.id)
            .scalar()
        )
        m = EmpresaProjetoMarco(
            projeto_id=projeto.id,
            nome=nome[:300],
            data_prevista=_parse_date(request.form.get("data_prevista")),
            status=(request.form.get("status") or "pendente").strip()[:40],
            sort_order=(max_so or 0) + 1,
        )
        db.session.add(m)
        db.session.commit()
        flash("Marco adicionado.", "ok")
        return redirect(url_for("empresa.projetos_detail", pid=pid))

    @bp.route("/projetos/marcos/<int:mid>/excluir", methods=["POST"])
    @empresa_login_required
    def projetos_marco_delete(mid: int):
        m = EmpresaProjetoMarco.query.get_or_404(mid)
        pid = m.projeto_id
        db.session.delete(m)
        db.session.commit()
        flash("Marco removido.", "ok")
        return redirect(url_for("empresa.projetos_detail", pid=pid))

    def _projeto_form(row: EmpresaProjeto | None):
        return render_template(
            "empresa/projetos/projeto_form.html",
            staff=_current_employee(),
            projeto=row,
        )

    def _save_projeto(row: EmpresaProjeto | None):
        user = _current_employee()
        codigo = (request.form.get("codigo") or "").strip()
        nome = (request.form.get("nome") or "").strip()
        if not codigo or not nome:
            flash("Código e nome são obrigatórios.", "error")
            return _projeto_form(row)
        if row is None:
            other = EmpresaProjeto.query.filter_by(codigo=codigo).first()
            if other:
                flash("Já existe projeto com este código.", "error")
                return _projeto_form(None)
            row = EmpresaProjeto(
                codigo=codigo[:64],
                nome=nome,
                created_by_id=user.id if user else None,
            )
            db.session.add(row)
        else:
            other = EmpresaProjeto.query.filter(
                EmpresaProjeto.codigo == codigo,
                EmpresaProjeto.id != row.id,
            ).first()
            if other:
                flash("Já existe projeto com este código.", "error")
                return _projeto_form(row)
            row.codigo = codigo[:64]
            row.nome = nome
        row.orgao_cliente = (request.form.get("orgao_cliente") or "").strip() or None
        row.status = (request.form.get("status") or "planejamento").strip()[:40]
        row.data_inicio = _parse_date(request.form.get("data_inicio"))
        row.data_fim_prevista = _parse_date(request.form.get("data_fim_prevista"))
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Projeto salvo.", "ok")
        return redirect(url_for("empresa.projetos_detail", pid=row.id))

    # --- Licitações ---
    @bp.route("/licitacoes/")
    @empresa_login_required
    def licitacoes_hub():
        n = EmpresaLicitacao.query.count()
        return render_template("empresa/licitacoes/hub.html", staff=_current_employee(), n_licitacoes=n)

    @bp.route("/licitacoes/lista")
    @empresa_login_required
    def licitacoes_lista():
        rows = EmpresaLicitacao.query.order_by(EmpresaLicitacao.id.desc()).limit(400).all()
        return render_template(
            "empresa/licitacoes/licitacoes_list.html",
            staff=_current_employee(),
            licitacoes=rows,
        )

    @bp.route("/licitacoes/novo", methods=["GET", "POST"])
    @empresa_login_required
    def licitacoes_new():
        if request.method == "POST":
            return _save_licitacao(None)
        return _licitacao_form(None)

    @bp.route("/licitacoes/<int:lid>/editar", methods=["GET", "POST"])
    @empresa_login_required
    def licitacoes_edit(lid: int):
        row = EmpresaLicitacao.query.get_or_404(lid)
        if request.method == "POST":
            return _save_licitacao(row)
        return _licitacao_form(row)

    def _licitacao_form(row: EmpresaLicitacao | None):
        checklist_items = _licitacao_checklist_parse(
            row.checklist_documentos_json if row else None
        )
        anexos = listar_anexos("licitacao", row.id) if row else []
        return render_template(
            "empresa/licitacoes/licitacao_form.html",
            staff=_current_employee(),
            licitacao=row,
            checklist_items=checklist_items,
            anexos=anexos,
        )

    def _save_licitacao(row: EmpresaLicitacao | None):
        user = _current_employee()
        titulo = (request.form.get("titulo") or "").strip()
        if not titulo:
            flash("Título é obrigatório.", "error")
            return _licitacao_form(row)
        if row is None:
            row = EmpresaLicitacao(titulo=titulo, created_by_id=user.id if user else None)
            db.session.add(row)
            db.session.flush()
        else:
            row.titulo = titulo
        row.orgao = (request.form.get("orgao") or "").strip() or None
        row.modalidade = (request.form.get("modalidade") or "").strip() or None
        row.numero_edital = (request.form.get("numero_edital") or "").strip() or None
        row.data_abertura = _parse_date(request.form.get("data_abertura"))
        row.data_envio_proposta = _parse_date(request.form.get("data_envio_proposta"))
        row.data_limite_impugnacao = _parse_date(request.form.get("data_limite_impugnacao"))
        row.data_limite_esclarecimento = _parse_date(request.form.get("data_limite_esclarecimento"))
        row.prazo_entrega_objeto = _parse_date(request.form.get("prazo_entrega_objeto"))
        row.local_entrega_edital = (request.form.get("local_entrega_edital") or "").strip() or None
        row.multas_edital = (request.form.get("multas_edital") or "").strip() or None
        row.documentacao_solicitada = (request.form.get("documentacao_solicitada") or "").strip() or None
        row.esclarecimentos = (request.form.get("esclarecimentos") or "").strip() or None
        row.questionamento_impugnacao = (request.form.get("questionamento_impugnacao") or "").strip() or None
        _licitacao_checklist_save_from_form(row)
        row.status = (request.form.get("status") or "estudo").strip()[:40]
        row.valor_proposta = _parse_decimal(request.form.get("valor_proposta"))
        row.observacoes = (request.form.get("observacoes") or "").strip() or None
        db.session.commit()
        flash("Licitação salva.", "ok")
        return redirect(url_for("empresa.licitacoes_edit", lid=row.id))
