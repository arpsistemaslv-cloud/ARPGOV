"""
Intranet da empresa: setores, chat interno e Kanban multi-setor.
Blueprint registrado em app com prefixo /empresa.
"""

from __future__ import annotations

from functools import wraps
import os

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from empresa_setores_conteudo import get_conteudo_setor

from models import (
    CompanyEmployee,
    IntranetChatMessage,
    KanbanBoard,
    KanbanCard,
    KanbanColumn,
    db,
)

empresa_bp = Blueprint("empresa", __name__, url_prefix="/empresa")

# (slug, título exibido)
EMPRESA_SETORES: tuple[tuple[str, str], ...] = (
    ("logistica", "Setor Logístico"),
    ("tecnico", "Setor Técnico"),
    ("juridico", "Setor Jurídico"),
    ("garantia", "Setor de Garantia"),
    ("compras", "Setor de Compras"),
    ("contratos_empenhos", "Setor de Contratos e Empenhos"),
    ("faturamento", "Setor de Faturamento"),
    ("pendencias", "Setor de Pendências"),
    ("projetos", "Setor de Projetos"),
    ("licitacoes", "Setor de Licitações a participar"),
    ("fechamento_preco", "Setor de Fechamento de preço"),
    ("imposto", "Setor de Impostos"),
)

_SETOR_SLUGS = frozenset(s for s, _ in EMPRESA_SETORES)


def setor_label(slug: str) -> str:
    return dict(EMPRESA_SETORES).get(slug, slug)


def _normalize_env_password(val: str | None) -> str:
    if not val:
        return ""
    s = str(val).strip()
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def _portal_master_password_matches(raw: str | None) -> bool:
    master = _normalize_env_password(os.environ.get("PORTAL_MASTER_PASSWORD"))
    if not master:
        return False
    candidate = (raw or "").strip()
    if master.startswith("pbkdf2:") or master.startswith("scrypt:"):
        return check_password_hash(master, candidate)
    return candidate == master


def empresa_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        sid = session.get("empresa_staff_id")
        if sid is None:
            return redirect(url_for("empresa.login", next=request.path))
        try:
            sid_int = int(sid)
        except (TypeError, ValueError):
            session.pop("empresa_staff_id", None)
            return redirect(url_for("empresa.login", next=request.path))
        user = db.session.get(CompanyEmployee, sid_int)
        if user is None or not user.is_active:
            session.pop("empresa_staff_id", None)
            return redirect(url_for("empresa.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _current_employee() -> CompanyEmployee | None:
    sid = session.get("empresa_staff_id")
    if sid is None:
        return None
    try:
        return db.session.get(CompanyEmployee, int(sid))
    except (TypeError, ValueError):
        return None


def seed_intranet_if_needed() -> None:
    """Cria quadro Kanban padrão e colunas se o banco estiver vazio."""
    if KanbanBoard.query.first() is not None:
        return
    board = KanbanBoard(title="Comunicação entre setores")
    db.session.add(board)
    db.session.flush()
    cols = [
        ("Backlog", 0),
        ("Em andamento", 1),
        ("Aguardando outro setor", 2),
        ("Concluído", 3),
    ]
    for title, so in cols:
        db.session.add(
            KanbanColumn(board_id=board.id, title=title, sort_order=so)
        )
    db.session.commit()


def _authenticate_admin_rep_for_empresa(email: str, password: str):
  """Representante comercial administrador pode entrar na intranet com o mesmo e-mail."""
  from models import SalesRepresentative

  em = (email or "").strip().lower()
  if not em or "@" not in em:
    return None
  rep = SalesRepresentative.query.filter_by(email=em).first()
  if rep is None or not rep.is_active or not rep.is_admin:
    return None
  if not check_password_hash(rep.password_hash, password or ""):
    return None
  return rep


def ensure_employee_for_admin_rep(rep) -> CompanyEmployee:
  """Garante colaborador da intranet para login administrador comercial."""
  emp = CompanyEmployee.query.filter_by(email=rep.email).first()
  if emp is None:
    emp = CompanyEmployee(
      email=rep.email,
      password_hash=rep.password_hash,
      name=rep.name,
      department_slug="licitacoes",
      is_active=True,
    )
    db.session.add(emp)
  else:
    emp.is_active = True
    emp.name = rep.name
    emp.password_hash = rep.password_hash
  db.session.commit()
  return emp


@empresa_bp.route("/entrar", methods=["GET", "POST"])
def login():
    if _current_employee() is not None:
        return redirect(url_for("empresa.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = None

        if _portal_master_password_matches(password):
            user = (
                CompanyEmployee.query.filter_by(is_active=True)
                .order_by(CompanyEmployee.id)
                .first()
            )
            if user is None:
                flash(
                    "Senha master válida, mas não há colaborador da intranet cadastrado.",
                    "error",
                )
        else:
            user = CompanyEmployee.query.filter_by(email=email).first()
            if user is None or not user.is_active or not check_password_hash(
                user.password_hash, password
            ):
                rep = _authenticate_admin_rep_for_empresa(email, password)
                if rep is not None:
                    user = ensure_employee_for_admin_rep(rep)
                else:
                    flash("E-mail ou senha incorretos.", "error")
                    user = None
        if user is not None:
            session["empresa_staff_id"] = user.id
            session.modified = True
            nxt = request.args.get("next") or request.form.get("next") or ""
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("empresa.dashboard"))
    return render_template("empresa/login.html")


@empresa_bp.route("/sair")
def logout():
    session.pop("empresa_staff_id", None)
    session.modified = True
    flash("Você saiu da área da empresa.", "ok")
    return redirect(url_for("empresa.login"))


@empresa_bp.route("/")
@empresa_login_required
def dashboard():
    user = _current_employee()
    return render_template(
        "empresa/dashboard.html",
        staff=user,
        setores=EMPRESA_SETORES,
    )


@empresa_bp.route("/setor/<slug>")
@empresa_login_required
def setor(slug: str):
    if slug not in _SETOR_SLUGS:
        abort(404)
    user = _current_employee()
    conteudo = get_conteudo_setor(slug)
    return render_template(
        "empresa/setor.html",
        staff=user,
        slug=slug,
        setor_label=setor_label(slug),
        setores=EMPRESA_SETORES,
        conteudo=conteudo,
    )


@empresa_bp.route("/chat")
@empresa_login_required
def chat():
    channel = (request.args.get("canal") or "geral").strip() or "geral"
    if channel != "geral" and channel not in _SETOR_SLUGS:
        channel = "geral"
    user = _current_employee()
    msgs = (
        IntranetChatMessage.query.options(joinedload(IntranetChatMessage.author))
        .filter_by(channel=channel)
        .order_by(IntranetChatMessage.created_at.desc())
        .limit(200)
        .all()
    )
    msgs = list(reversed(msgs))
    return render_template(
        "empresa/chat.html",
        staff=user,
        channel=channel,
        channel_label=setor_label(channel) if channel in _SETOR_SLUGS else "Geral",
        messages=msgs,
        setores=EMPRESA_SETORES,
    )


@empresa_bp.route("/chat/enviar", methods=["POST"])
@empresa_login_required
def chat_send():
    body = (request.form.get("body") or "").strip()
    channel = (request.form.get("channel") or "geral").strip() or "geral"
    if channel != "geral" and channel not in _SETOR_SLUGS:
        channel = "geral"
    if not body:
        flash("Escreva uma mensagem.", "error")
        return redirect(url_for("empresa.chat", canal=channel))
    if len(body) > 12000:
        flash("Mensagem muito longa (máx. 12.000 caracteres).", "error")
        return redirect(url_for("empresa.chat", canal=channel))
    user = _current_employee()
    if user is None:
        return redirect(url_for("empresa.login"))
    db.session.add(
        IntranetChatMessage(channel=channel, employee_id=user.id, body=body)
    )
    db.session.commit()
    return redirect(url_for("empresa.chat", canal=channel))


@empresa_bp.route("/api/chat/mensagens")
@empresa_login_required
def api_chat_messages():
    channel = (request.args.get("canal") or "geral").strip() or "geral"
    if channel != "geral" and channel not in _SETOR_SLUGS:
        channel = "geral"
    q = IntranetChatMessage.query.options(joinedload(IntranetChatMessage.author)).filter_by(
        channel=channel
    )
    apos_raw = request.args.get("apos")
    if apos_raw is not None and str(apos_raw).strip() != "":
        try:
            after_id = int(apos_raw)
            q = q.filter(IntranetChatMessage.id > after_id)
        except ValueError:
            pass
    msgs = q.order_by(IntranetChatMessage.created_at.asc()).limit(100).all()
    out = []
    for m in msgs:
        out.append(
            {
                "id": m.id,
                "body": m.body,
                "author": m.author.name if m.author else "?",
                "created_at": m.created_at.isoformat() + "Z",
            }
        )
    return jsonify({"messages": out})


@empresa_bp.route("/kanban")
@empresa_login_required
def kanban():
    seed_intranet_if_needed()
    user = _current_employee()
    board = KanbanBoard.query.options(
        joinedload(KanbanBoard.columns)
        .joinedload(KanbanColumn.cards)
        .joinedload(KanbanCard.created_by)
    ).first()
    if board is None:
        seed_intranet_if_needed()
        board = KanbanBoard.query.options(
            joinedload(KanbanBoard.columns)
            .joinedload(KanbanColumn.cards)
            .joinedload(KanbanCard.created_by)
        ).first()
    columns = sorted(board.columns, key=lambda c: c.sort_order)
    for col in columns:
        col.cards.sort(key=lambda x: x.sort_order)
    return render_template(
        "empresa/kanban.html",
        staff=user,
        board=board,
        columns=columns,
        setores=EMPRESA_SETORES,
    )


@empresa_bp.route("/kanban/cartao/novo", methods=["POST"])
@empresa_login_required
def kanban_card_new():
    seed_intranet_if_needed()
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    column_id = request.form.get("column_id", type=int)
    dept = (request.form.get("department_slug") or "").strip()
    if dept and dept not in _SETOR_SLUGS:
        dept = None
    user = _current_employee()
    if not title or len(title) > 300:
        flash("Título obrigatório (máx. 300 caracteres).", "error")
        return redirect(url_for("empresa.kanban"))
    col = db.session.get(KanbanColumn, column_id)
    if col is None:
        flash("Coluna inválida.", "error")
        return redirect(url_for("empresa.kanban"))
    max_so = db.session.query(db.func.max(KanbanCard.sort_order)).filter_by(
        column_id=col.id
    ).scalar()
    next_so = (max_so or 0) + 1
    card = KanbanCard(
        column_id=col.id,
        title=title,
        description=description,
        department_slug=dept,
        sort_order=next_so,
        created_by_id=user.id if user else None,
    )
    db.session.add(card)
    db.session.commit()
    flash("Cartão criado.", "ok")
    return redirect(url_for("empresa.kanban"))


@empresa_bp.route("/api/kanban/sincronizar", methods=["POST"])
@empresa_login_required
def api_kanban_sincronizar():
    """Estado completo do quadro após arrastar: [{\"id\": col_id, \"card_ids\": [..]}, ...]."""
    data = request.get_json(silent=True) or {}
    blocks = data.get("columns")
    if not isinstance(blocks, list):
        return jsonify({"ok": False, "error": "payload inválido"}), 400
    board = KanbanBoard.query.first()
    if board is None:
        return jsonify({"ok": False, "error": "sem quadro"}), 400
    valid_col_ids = {c.id for c in board.columns}
    try:
        for block in blocks:
            col_id = int(block.get("id"))
            if col_id not in valid_col_ids:
                continue
            card_ids = block.get("card_ids") or []
            for i, cid in enumerate(int(x) for x in card_ids):
                card = db.session.get(KanbanCard, cid)
                if card is None:
                    continue
                card.column_id = col_id
                card.sort_order = i
        db.session.commit()
    except (TypeError, ValueError, KeyError):
        db.session.rollback()
        return jsonify({"ok": False, "error": "dados inválidos"}), 400
    return jsonify({"ok": True})


from empresa_compras import register_compras_routes
from empresa_operacional import register_operacional_routes
from empresa_fechamento_preco import register_fechamento_preco_routes

register_compras_routes(empresa_bp)
register_operacional_routes(empresa_bp)
register_fechamento_preco_routes(empresa_bp)
