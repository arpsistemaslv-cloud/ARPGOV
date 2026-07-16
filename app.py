import base64
import csv
import io
import json
import os
import re
import secrets
import smtplib
import string
import sys
import threading
from email.message import EmailMessage
from email.utils import formataddr
import unicodedata
import uuid
import webbrowser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps

from dotenv import load_dotenv
import click
import requests

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import delete, false, func, or_, select, text, update
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import brasil_geo
import br_ibge_sync
import br_org_import
import pncp_mercado_stats
from br_extenso import (
    format_inteiro_pt_br,
    frase_contagem_fem,
    frase_contagem_masc,
    moeda_extenso_brl,
)

from models import (
    ArpAnalysis,
    BrOrgaoPublico,
    CatalogCategory,
    CatalogItem,
    CompanyExpense,
    ContratosGovScan,
    ContratosGovScanResult,
    LeadMessage,
    LicitacaoWatch,
    Opportunity,
    OpportunityCatalogLine,
    OpportunityCommissionSplit,
    Partner,
    PartnerProduct,
    PartnerProductArpCommission,
    PncpMercadoSnapshot,
    PncpOrgaoUnidade,
    PortalClient,
    RepFinancialEntry,
    SalesRepresentative,
    SitePage,
    SiteSettings,
    db,
    opportunity_catalog_items,
)

from portal_client_news import get_client_news_items
from social_post_generator import (
    SOCIAL_FORMATS,
    SOCIAL_LAYOUTS,
    SocialPostInput,
    generate_social_post_image,
    social_post_filename,
)

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path, override=True, encoding="utf-8")


def _refresh_dotenv() -> None:
    """Relê o .env (útil após alterar chaves sem reiniciar o servidor)."""
    load_dotenv(_env_path, override=True, encoding="utf-8")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


app = Flask(__name__)
_flask_production = _env_bool("FLASK_PRODUCTION", False)
_secret_key = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
if _flask_production:
    if (
        not _secret_key
        or _secret_key == "dev-only-change-me"
        or len(_secret_key) < 32
    ):
        raise RuntimeError(
            "FLASK_SECRET_KEY obrigatória em produção (mín. 32 caracteres, "
            "diferente do valor de desenvolvimento)."
        )
    app.config["SECRET_KEY"] = _secret_key
else:
    app.config["SECRET_KEY"] = _secret_key or "dev-only-change-me"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Chat: vários anexos de até 15 MB (margem para multipart)
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024 * 1024
if not _flask_production:
    app.config["TEMPLATES_AUTO_RELOAD"] = True

# Sessão: em produção (HTTPS) use SESSION_COOKIE_SECURE=1; em http://127.0.0.1 deixe desligado.
_cookie_secure = _env_bool("SESSION_COOKIE_SECURE", _flask_production)
app.config["SESSION_COOKIE_SECURE"] = _cookie_secure
app.config["SESSION_COOKIE_HTTPONLY"] = True
_ss = (os.environ.get("SESSION_COOKIE_SAMESITE") or "Lax").strip().capitalize()
if _ss not in ("Lax", "Strict", "None"):
    _ss = "Lax"
if _ss == "None" and not _cookie_secure:
    _ss = "Lax"
app.config["SESSION_COOKIE_SAMESITE"] = _ss
app.config["WTF_CSRF_TIME_LIMIT"] = None
app.config["WTF_CSRF_HEADERS"] = ["X-CSRFToken", "X-CSRF-Token"]

# Proxy reverso (Nginx/Caddy): repassa X-Forwarded-Proto/Host para url_for e cookies seguros.
if _env_bool("TRUST_PROXY", _flask_production):
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1
    )

instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
os.makedirs(instance_path, exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(instance_path, 'portal.db')}"

db.init_app(app)

from flask_wtf.csrf import CSRFProtect, generate_csrf

csrf = CSRFProtect(app)


@app.context_processor
def _inject_csrf_token():
    return {"csrf_token": generate_csrf}


from crm_v2 import crm_bp

app.register_blueprint(crm_bp)


@app.before_request
def enforce_portal_public_only():
    """Em produção pública, /admin, /crm e /comercial ficam no servidor interno."""
    if not portal_public_only():
        return
    path = request.path or ""
    if not (
        path.startswith("/admin")
        or path.startswith("/crm")
        or path.startswith("/comercial")
    ):
        return
    staff = staff_portal_base_url()
    if staff:
        return redirect(staff + path, code=302)
    from flask import abort

    abort(404)


def _register_crm_endpoint_aliases() -> None:
    """Permite url_for('crm_dashboard') além de url_for('crm.crm_dashboard')."""
    known = {rule.endpoint for rule in app.url_map.iter_rules()}
    for rule in list(app.url_map.iter_rules()):
        ep = rule.endpoint
        if not ep.startswith("crm."):
            continue
        short = ep.split(".", 1)[1]
        if short in known:
            continue
        view = app.view_functions.get(ep)
        if view is None:
            continue
        app.add_url_rule(
            rule.rule,
            endpoint=short,
            view_func=view,
            methods=rule.methods,
        )
        known.add(short)


_register_crm_endpoint_aliases()

PATH_ADMIN_HOME = "/admin"
PATH_ADMIN_LOGIN = "/admin/login"


def portal_public_only() -> bool:
    """Site público sem painel/CRM/comercial (deploy separado)."""
    return _env_bool("PORTAL_PUBLIC_ONLY", False)


def staff_portal_base_url() -> str | None:
    """URL base do servidor interno (painel + CRM), se configurado."""
    raw = (os.environ.get("STAFF_PORTAL_URL") or "").strip().rstrip("/")
    return raw or None


def _env_api_key(name: str) -> str:
    """Remove aspas, BOM, CR e prefixo Bearer comuns em chaves coladas no .env."""
    v = (os.environ.get(name) or "").strip().replace("\r", "").lstrip("\ufeff")
    if len(v) >= 2 and v[0] in "'\"" and v[-1] == v[0]:
        v = v[1:-1].strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    return v


def _openai_api_key_usable(key: str) -> bool:
    """Evita chamar a API da OpenAI com valor vazio ou placeholder (deixa passar para o Pexels)."""
    k = (key or "").strip()
    return len(k) >= 20 and k.startswith("sk-")


def _safe_internal_redirect(
    target: str | None, default: str, forbidden_paths: tuple[str, ...]
) -> str:
    if not target or not isinstance(target, str):
        return default
    t = target.strip()
    if not t.startswith("/") or t.startswith("//"):
        return default
    path_only = t.split("?", 1)[0]
    for fp in forbidden_paths:
        if path_only == fp or path_only.startswith(fp + "/"):
            return default
    return t


SECTION_SUGGESTIONS = [
    "ATA de veículos",
    "ATA em destaque",
    "Item em destaque",
]

CATALOG_SPHERE_CHOICES = (
    "Federal",
    "Estadual",
    "Municipal",
    "Sistema-S",
    "Autarquia",
)

from pipeline_stages import (
    LEGACY_STAGE_MAP,
    STAGES,
    normalize_stage_key,
    stage_label as pipeline_stage_label,
    stages_with_fields_up_to,
)

# UFs para parceiros indicarem onde o produto pode ser vendido (exceto opção “Todo o Brasil”).
BR_UFS = (
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

PNCP_ESFERA_LABELS = {
    "F": "Federal",
    "E": "Estadual",
    "M": "Municipal",
    "D": "Distrital",
}
PNCP_PODER_LABELS = {
    "E": "Executivo",
    "L": "Legislativo",
    "J": "Judiciário",
}

# Filtro do diretório nacional (área comercial)
TIPOS_ORGAO_PUBLICO = (
    ("prefeitura", "Prefeituras (municípios)"),
    ("orgao_estadual", "Administração estadual"),
    ("autarquia_estadual", "Autarquias estaduais (DETRAN, demais)"),
    ("sistema_s", "Sistema S (SESI, SENAI, SESC, SENAC)"),
    ("servico_aprendizagem", "SENAR, SEBRAE, SENAT, SESCOOP"),
    ("federal_executivo", "Presidência e ministérios (União)"),
    ("autarquia_federal", "Autarquias e agências federais"),
    ("orgao_juridico", "Judiciário, MP, Defensoria, tribunais"),
    ("justica_trabalho", "Justiça do Trabalho (TRT) e MPT"),
    ("orgao_legislativo", "Congresso e assembleias legislativas"),
    ("seguranca_publica", "Polícias, bombeiros e segurança"),
    (
        "educacao_instituicoes",
        "Educação (IFs, universidades federais, órgãos MEC)",
    ),
    ("pncp", "Órgãos PNCP"),
    ("federal", "Federal (legado)"),
    ("consorcio", "Consórcios"),
)

CATALOG_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
CATALOG_IMAGES_MAX = 20
CLIENT_CART_MAX = 20
CLIENT_CART_SESSION_KEY = "client_cart"
PENDING_CART_SLUG_KEY = "pending_cart_slug"
PORTAL_CLIENT_PHOTO_PREFIX = "uploads/portal_clients"
PORTAL_CLIENT_PHOTO_MAX_BYTES = 3 * 1024 * 1024
PORTAL_CLIENT_PHOTO_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_PORTAL_CLIENT_PHOTO_REL_RE = re.compile(
    r"^uploads/portal_clients/[0-9a-f]{32}\.(png|jpg|jpeg|webp)$",
    re.I,
)
PORTAL_CLIENT_PHOTO_SIZE = (400, 400)
CATALOG_IMAGE_FRAME_SIZE = (1200, 900)
CATALOG_IMAGE_FRAME_BG = (245, 247, 250)
MANUFACTURER_IMG_FETCH_MAX = 16
MANUFACTURER_IMG_MIN_BYTES = 3_500
_MANUFACTURER_IMG_SKIP_PARTS = (
    "icon",
    "logo",
    "sprite",
    "pixel",
    "tracking",
    "avatar",
    "badge",
    "1x1",
    "spinner",
    "loading",
    "placeholder",
    "banner-ad",
)
CATALOG_DOC_EXT = frozenset({".pdf", ".doc", ".docx"})
CATALOG_ATTACHMENTS_MAX = 8
ATA_COMPANY_DOCS_MAX = 12
MAX_DOC_UPLOAD_BYTES = 15 * 1024 * 1024
CATALOG_ATTACHMENTS_PREFIX = "uploads/catalog_attachments"
ATA_COMPANY_DOCS_PREFIX = "uploads/ata_company_docs"
_PREFETCH_CATALOG_IMG_RE = re.compile(
    r"^uploads/catalog/[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif)$",
    re.I,
)
_CATALOG_DOC_FILENAME_RE = re.compile(r"^[a-f0-9]{32}\.(pdf|doc|docx)$", re.I)

LEAD_CHAT_PREFIX = "uploads/lead_chat"
LEAD_CHAT_ALLOWED_EXT = frozenset(CATALOG_IMAGE_EXT | CATALOG_DOC_EXT)
LEAD_CHAT_MAX_FILES = 8
LEAD_CHAT_MAX_BYTES = MAX_DOC_UPLOAD_BYTES
_LEAD_CHAT_REL_RE = re.compile(
    r"^uploads/lead_chat/[0-9a-f]{32}\.(png|jpg|jpeg|webp|gif|pdf|doc|docx)$",
    re.I,
)

FINANCE_COMPANY_PREFIX = "uploads/finance_company"
FINANCE_REP_PREFIX = "uploads/finance_rep"
LEAD_PIPELINE_PREFIX = "uploads/lead_pipeline"
FINANCE_ALLOWED_EXT = frozenset(
    {".pdf", ".xml", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".doc", ".docx"}
)
FINANCE_MAX_FILES = 15
FINANCE_MAX_BYTES = MAX_DOC_UPLOAD_BYTES
_FINANCE_COMPANY_REL_RE = re.compile(
    r"^uploads/finance_company/[0-9a-f]{32}\.(pdf|xml|zip|png|jpg|jpeg|webp|gif|doc|docx)$",
    re.I,
)
_FINANCE_REP_REL_RE = re.compile(
    r"^uploads/finance_rep/[0-9a-f]{32}\.(pdf|xml|zip|png|jpg|jpeg|webp|gif|doc|docx)$",
    re.I,
)

REP_FINANCE_STATUSES = [
    ("enviado", "Enviado"),
    ("em_analise", "Em análise"),
    ("aprovado", "Aprovado"),
    ("pago", "Pago"),
    ("recusado", "Recusado"),
]


def crm_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("crm_ok"):
            return redirect(url_for("crm.crm_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_ok"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def catalog_staff_required(view):
    """Painel admin ou CRM — edição de catálogo e sugestão de imagens."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("admin_ok") or session.get("crm_ok"):
            return view(*args, **kwargs)
        if (request.path or "").startswith("/crm"):
            return redirect(url_for("crm.crm_login", next=request.path))
        return redirect(url_for("admin_login", next=request.path))

    return wrapped


def client_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        cid = session.get("client_id")
        if cid is None:
            return redirect(url_for("cliente_entrar", next=request.path))
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            session.pop("client_id", None)
            return redirect(url_for("cliente_entrar", next=request.path))
        if db.session.get(PortalClient, cid_int) is None:
            session.pop("client_id", None)
            return redirect(url_for("cliente_entrar", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def rep_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        rid = session.get("rep_id")
        if rid is None:
            return redirect(url_for("comercial_login", next=request.path))
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            session.pop("rep_id", None)
            return redirect(url_for("comercial_login", next=request.path))
        rep = db.session.get(SalesRepresentative, rid_int)
        if rep is None or not rep.is_active:
            session.pop("rep_id", None)
            return redirect(url_for("comercial_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def partner_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        pid = session.get("partner_id")
        if pid is None:
            return redirect(url_for("parceiro_login", next=request.path))
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            session.pop("partner_id", None)
            return redirect(url_for("parceiro_login", next=request.path))
        partner = db.session.get(Partner, pid_int)
        if partner is None or not partner.is_active:
            session.pop("partner_id", None)
            return redirect(url_for("parceiro_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def _normalize_rep_email(raw: str | None) -> str:
    return (raw or "").strip().lower()


def _session_sales_rep() -> SalesRepresentative | None:
    rid = session.get("rep_id")
    if rid is None:
        return None
    try:
        rep = db.session.get(SalesRepresentative, int(rid))
    except (TypeError, ValueError):
        return None
    if rep is None or not rep.is_active:
        return None
    return rep


def _rep_is_admin(rep: SalesRepresentative | None) -> bool:
    return bool(rep and rep.is_active and rep.is_admin)


def _rep_has_comercial_access(rep: SalesRepresentative | None) -> bool:
    return bool(rep and rep.is_active and getattr(rep, "access_comercial", True))


def _rep_has_crm_access(rep: SalesRepresentative | None) -> bool:
    return bool(rep and rep.is_active and rep.access_crm)


def _rep_has_painel_access(rep: SalesRepresentative | None) -> bool:
    return bool(rep and rep.is_active and rep.access_painel)


def _grant_staff_sessions_for_rep(rep: SalesRepresentative) -> None:
    session.pop("admin_ok", None)
    session.pop("crm_ok", None)
    if _rep_has_painel_access(rep):
        session["admin_ok"] = True
    if _rep_has_crm_access(rep):
        session["crm_ok"] = True
    session.modified = True


def _grant_staff_sessions_for_admin_rep(rep: SalesRepresentative) -> None:
    """Compatibilidade — delega para permissões granulares."""
    _grant_staff_sessions_for_rep(rep)


def _authenticate_rep_email_login(
    email: str,
    password: str,
    *,
    require_comercial: bool = False,
    require_crm: bool = False,
    require_painel: bool = False,
) -> SalesRepresentative | None:
    if not email or "@" not in email:
        return None
    rep = SalesRepresentative.query.filter_by(email=email).first()
    if rep is None or not rep.is_active:
        return None
    if require_comercial and not _rep_has_comercial_access(rep):
        return None
    if require_crm and not _rep_has_crm_access(rep):
        return None
    if require_painel and not _rep_has_painel_access(rep):
        return None
    if not check_password_hash(rep.password_hash, password):
        return None
    return rep


def _authenticate_admin_rep(email: str, password: str) -> SalesRepresentative | None:
    return _authenticate_rep_email_login(
        email, password, require_painel=True
    )


def _apply_rep_permissions_from_form(rep: SalesRepresentative, *, is_new: bool) -> None:
    preset = (request.form.get("role_preset") or "custom").strip()
    if preset == "vendor":
        rep.access_comercial = True
        rep.access_crm = False
        rep.access_painel = False
        rep.is_admin = False
    elif preset == "admin":
        rep.access_comercial = True
        rep.access_crm = True
        rep.access_painel = True
        rep.is_admin = True
    else:
        rep.access_comercial = request.form.get("access_comercial") == "1"
        rep.access_crm = request.form.get("access_crm") == "1"
        rep.access_painel = request.form.get("access_painel") == "1"
        rep.is_admin = request.form.get("is_admin") == "1"
    if is_new:
        rep.is_active = True
    else:
        rep.is_active = request.form.get("is_active") == "1"


def _delete_sales_rep(rep: SalesRepresentative) -> str | None:
    rid = session.get("rep_id")
    try:
        if rid is not None and int(rid) == rep.id:
            return "Não é possível excluir o usuário com o qual você está logado."
    except (TypeError, ValueError):
        pass
    fin_count = RepFinancialEntry.query.filter_by(sales_rep_id=rep.id).count()
    if fin_count:
        return (
            f"Este usuário tem {fin_count} lançamento(s) financeiro(s). "
            "Desative o acesso em vez de excluir."
        )
    Opportunity.query.filter_by(sales_rep_id=rep.id).update(
        {Opportunity.sales_rep_id: None},
        synchronize_session=False,
    )
    db.session.delete(rep)
    return None


def _comercial_opportunities_query(rep: SalesRepresentative):
    q = Opportunity.query
    if not _rep_is_admin(rep):
        q = q.filter_by(sales_rep_id=rep.id)
    return q


def _comercial_get_opportunity(rep: SalesRepresentative, opp_id: int) -> Opportunity | None:
    q = Opportunity.query.filter_by(id=opp_id)
    if not _rep_is_admin(rep):
        q = q.filter_by(sales_rep_id=rep.id)
    return q.first()


def _comercial_finance_opportunities_query(rep: SalesRepresentative):
    """Leads visíveis no financeiro comercial (atribuídos ou com comissão do vendedor)."""
    if _rep_is_admin(rep):
        return _comercial_opportunities_query(rep)
    split_opp_ids = (
        select(OpportunityCommissionSplit.opportunity_id)
        .where(OpportunityCommissionSplit.sales_rep_id == rep.id)
        .distinct()
    )
    return Opportunity.query.filter(
        or_(
            Opportunity.sales_rep_id == rep.id,
            Opportunity.id.in_(split_opp_ids),
        )
    )


def _comercial_get_finance_opportunity(
    rep: SalesRepresentative, opp_id: int
) -> Opportunity | None:
    return _comercial_finance_opportunities_query(rep).filter_by(id=opp_id).first()


def _stage_label(stage_key: str) -> str:
    return pipeline_stage_label(stage_key)


def _public_base_url() -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    return "http://127.0.0.1:5001"


def _url_cliente_meus_leads() -> str:
    return f"{_public_base_url()}{url_for('cliente_meus_leads')}"


def _url_cliente_lead(opp_id: int) -> str:
    return f"{_public_base_url()}{url_for('cliente_lead_detail', oid=opp_id)}"


def _url_crm_oportunidade(opp_id: int) -> str:
    return f"{_public_base_url()}{url_for('crm.crm_op_edit', opp_id=opp_id)}"


def _smtp_config() -> dict | None:
    host = (os.environ.get("SMTP_HOST") or "").strip()
    port_s = (os.environ.get("SMTP_PORT") or "587").strip()
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (
        os.environ.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASS") or ""
    ).strip()
    from_addr = (os.environ.get("SMTP_FROM") or user or "").strip()
    tls_raw = (os.environ.get("SMTP_USE_TLS") or "true").strip().lower()
    use_tls = tls_raw not in ("0", "false", "no", "off")
    if not host or not from_addr:
        return None
    try:
        port = int(port_s)
    except ValueError:
        port = 587
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_addr": from_addr,
        "use_tls": use_tls,
    }


def _format_email_sender(from_addr: str, from_name: str | None = None) -> str:
    addr = (from_addr or "").strip()
    if not addr:
        return ""
    name = (from_name or "").strip()
    return formataddr((name, addr)) if name else addr


def _email_sender_from_rep(rep: SalesRepresentative | None) -> dict[str, str]:
    """Remetente do e-mail = cadastro do vendedor (área comercial)."""
    if not rep or not rep.is_active:
        return {}
    em = (rep.email or "").strip()
    if not em or "@" not in em:
        return {}
    out: dict[str, str] = {"from_addr": em, "reply_to": em}
    nm = (rep.name or "").strip()
    if nm:
        out["from_name"] = nm
    return out


def _send_email_smtp(
    to_addr: str,
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
) -> None:
    cfg = _smtp_config()
    if not cfg or not to_addr or "@" not in to_addr:
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _format_email_sender(
        (from_addr or cfg["from_addr"]).strip(),
        from_name,
    )
    msg["To"] = to_addr
    rt = (reply_to or "").strip()
    if rt and "@" in rt:
        msg["Reply-To"] = rt
    msg.set_content(body, charset="utf-8")
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
        if cfg["use_tls"]:
            smtp.starttls()
        if cfg["user"] and cfg["password"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


def _send_email_background(
    to_addr: str,
    subject: str,
    body: str,
    *,
    from_addr: str | None = None,
    from_name: str | None = None,
    reply_to: str | None = None,
) -> None:
    if not _smtp_config():
        app.logger.warning(
            "SMTP não configurado; e-mail não enviado para %s (assunto: %s)",
            to_addr,
            subject[:80],
        )
        return

    def run():
        try:
            _send_email_smtp(
                to_addr,
                subject,
                body,
                from_addr=from_addr,
                from_name=from_name,
                reply_to=reply_to,
            )
        except Exception:
            app.logger.exception("Falha ao enviar e-mail para %s", to_addr)

    threading.Thread(target=run, daemon=True).start()


def _resolve_notify_client(opp: Opportunity) -> tuple[str, str] | None:
    """Nome e e-mail do cliente para notificações do lead."""
    client = opp.portal_client
    if client and (client.email or "").strip():
        return (client.name or opp.contact_name or "cliente", client.email.strip())
    em = _normalize_portal_client_email(opp.email)
    if em:
        if client is None:
            client = PortalClient.query.filter_by(email=em).first()
        name = (
            (client.name if client else None)
            or opp.contact_name
            or "cliente"
        )
        return (name, em)
    return None


def _notify_portal_client_lead_chat_reply(
    opp: Opportunity,
    staff_message: str | None,
    *,
    has_attachments: bool = False,
    sender_rep: SalesRepresentative | None = None,
) -> None:
    """E-mail automático ao cliente quando a equipe responde no chat do lead."""
    resolved = _resolve_notify_client(opp)
    if resolved is None:
        return
    name, to_addr = resolved
    sm = (staff_message or "").strip()
    if not sm and not has_attachments:
        return
    sender = sender_rep or opp.sales_rep
    sender_name = (sender.name if sender else "").strip() or "Equipe ARPGOV"
    title = (opp.title or f"Lead #{opp.id}").strip()
    parts = [
        f"Olá, {name},",
        f"{sender_name} respondeu no chat do seu lead «{title}».",
    ]
    if sm:
        parts.append(f"Mensagem:\n\n{sm}")
    if has_attachments:
        parts.append(
            "Foram enviado(s) arquivo(s) na conversa. Acesse o lead para visualizar os anexos."
        )
    parts.append(f"Ver conversa: {_url_cliente_lead(opp.id)}")
    if sender and (sender.email or "").strip():
        parts.append(
            f"Responda este e-mail para falar diretamente com {sender.name or 'seu representante'}."
        )
    parts.append(f"Todos os seus leads: {_url_cliente_meus_leads()}")
    body = "\n\n".join(parts)
    subj = f"Nova resposta no seu lead — {title[:55]}"
    _send_email_background(to_addr, subj, body, **_email_sender_from_rep(sender))


def _marketing_recipients_query(
    sphere: str | None = None,
    uf: str | None = None,
    q: str | None = None,
):
    query = PortalClient.query.filter(
        PortalClient.email.isnot(None),
        PortalClient.email != "",
    )
    if sphere:
        query = query.filter(PortalClient.sphere == sphere)
    if uf:
        query = query.filter(PortalClient.address_state == uf)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PortalClient.name.ilike(like),
                PortalClient.email.ilike(like),
                PortalClient.organization.ilike(like),
                PortalClient.razao_social.ilike(like),
                PortalClient.address_city.ilike(like),
            )
        )
    return query.order_by(PortalClient.name.asc(), PortalClient.id.asc())


def _marketing_personalize_body(template: str, client: PortalClient) -> str:
    org = (client.organization or client.razao_social or "").strip()
    return (
        template.replace("{nome}", (client.name or "").strip())
        .replace("{orgao}", org)
        .replace("{esfera}", (client.sphere or "").strip())
    )


def _marketing_arp_block(items: list[CatalogItem], base_url: str) -> str:
    if not items:
        return ""
    lines = ["", "— ARPs em destaque —", ""]
    for it in items:
        url = f"{base_url.rstrip('/')}{url_for('produto', slug=it.slug)}"
        lines.append(f"• {it.title}")
        lines.append(f"  Esfera: {it.sphere} | Valor unit.: {_format_currency_brl(it.unit_price)}")
        if it.valid_until:
            lines.append(f"  Válida até: {it.valid_until.strftime('%d/%m/%Y')}")
        lines.append(f"  Ver no portal: {url}")
        lines.append("")
    lines.append(f"Catálogo completo: {base_url.rstrip('/')}{url_for('arps')}")
    return "\n".join(lines)


def _marketing_catalog_for_filters(sphere: str | None) -> list[CatalogItem]:
    q = CatalogItem.query.order_by(CatalogItem.section.asc(), CatalogItem.title.asc())
    if sphere:
        q = q.filter(CatalogItem.sphere == sphere)
    return q.limit(250).all()


def _email_marketing_filter_params() -> dict[str, str]:
    sphere = _normalize_sphere_field(
        request.values.get("sphere") or request.values.get("sphere_filter")
    )
    uf = _normalize_uf_field(request.values.get("uf") or request.values.get("uf_filter"))
    q = (request.values.get("q") or "").strip()
    params: dict[str, str] = {}
    if sphere:
        params["sphere"] = sphere
    if uf:
        params["uf"] = uf
    if q:
        params["q"] = q
    return params


def _email_marketing_page_ctx(*, area: str, form_action: str, cancel_url: str) -> dict:
    params = _email_marketing_filter_params()
    sphere = params.get("sphere")
    uf = params.get("uf")
    q = params.get("q", "")
    recipients_q = _marketing_recipients_query(sphere, uf, q or None)
    total = recipients_q.count()
    preview = recipients_q.limit(50).all()
    catalog_items = _marketing_catalog_for_filters(sphere)
    return {
        "area": area,
        "form_action": form_action,
        "cancel_url": cancel_url,
        "comercial_subnav": area == "comercial",
        "sphere_filter": sphere or "",
        "uf_filter": uf or "",
        "q": q,
        "total_recipients": total,
        "preview_recipients": preview,
        "catalog_items": catalog_items,
        "sphere_choices": CATALOG_SPHERE_CHOICES,
        "br_ufs": BR_UFS,
        "smtp_ok": bool(_smtp_config()),
        "filter_url_endpoint": (
            "comercial_email_marketing" if area == "comercial" else "crm.crm_email_marketing"
        ),
    }


def _email_marketing_handle_send(*, area: str, redirect_endpoint: str):
    if request.form.get("action") != "send":
        return None
    subject = (request.form.get("subject") or "").strip()
    body_tpl = (request.form.get("body") or "").strip()
    if not subject:
        flash("Informe o assunto do e-mail.", "error")
        return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))
    if not body_tpl:
        flash("Escreva a mensagem do e-mail.", "error")
        return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))
    if request.form.get("confirm_send") != "1":
        flash("Marque a confirmação para enviar a campanha.", "error")
        return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))
    if not _smtp_config():
        flash("SMTP não configurado no servidor (variáveis SMTP_HOST / SMTP_FROM).", "error")
        return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))
    sphere = _normalize_sphere_field(request.form.get("sphere_filter"))
    uf = _normalize_uf_field(request.form.get("uf_filter"))
    q = (request.form.get("q") or "").strip()
    catalog_ids: list[int] = []
    for raw in request.form.getlist("catalog_ids"):
        try:
            catalog_ids.append(int(raw))
        except (TypeError, ValueError):
            pass
    items = (
        CatalogItem.query.filter(CatalogItem.id.in_(catalog_ids)).all()
        if catalog_ids
        else []
    )
    arp_block = _marketing_arp_block(items, _public_base_url())
    recipients = _marketing_recipients_query(sphere, uf, q or None).all()
    if not recipients:
        flash("Nenhum cliente com e-mail corresponde aos filtros.", "error")
        return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))
    _send_marketing_campaign_background(recipients, subject, body_tpl, arp_block)
    flash(
        f"Envio iniciado para {len(recipients)} destinatário(s). Os e-mails saem em segundo plano.",
        "ok",
    )
    return redirect(url_for(redirect_endpoint, **_email_marketing_filter_params()))


def _send_marketing_campaign_background(
    recipients: list[PortalClient],
    subject: str,
    body_tpl: str,
    arp_block: str,
) -> None:
    def run():
        import time

        for client in recipients:
            to_addr = (client.email or "").strip()
            if not to_addr or "@" not in to_addr:
                continue
            try:
                body = _marketing_personalize_body(body_tpl, client)
                if arp_block:
                    body = body.rstrip() + "\n" + arp_block
                _send_email_smtp(to_addr, subject, body)
            except Exception:
                app.logger.exception("Falha no e-mail marketing para %s", to_addr)
            time.sleep(0.45)

    threading.Thread(target=run, daemon=True).start()


def _opp_snapshot_for_notify(opp: Opportunity) -> dict:
    cat_lines = tuple(
        sorted((ln.catalog_item_id, int(ln.quantity)) for ln in opp.catalog_lines)
    )
    return {
        "stage": opp.stage,
        "notes": opp.notes or "",
        "title": opp.title,
        "value_brl": opp.value_brl,
        "catalog_lines": cat_lines,
    }


def _opp_notify_change_lines(before: dict, opp: Opportunity) -> list[str]:
    lines: list[str] = []
    if before["stage"] != opp.stage:
        lines.append(
            f"Estágio: {_stage_label(before['stage'])} → {_stage_label(opp.stage)}"
        )
    if (before["notes"] or "") != (opp.notes or ""):
        lines.append("As observações do lead foram atualizadas.")
    if before["title"] != opp.title:
        lines.append("O título do lead foi atualizado.")
    if before["value_brl"] != opp.value_brl:
        lines.append("O valor estimado foi atualizado.")
    new_cat = tuple(
        sorted((ln.catalog_item_id, int(ln.quantity)) for ln in opp.catalog_lines)
    )
    if before["catalog_lines"] != new_cat:
        old_map = {cid: qty for cid, qty in before["catalog_lines"]}
        new_map = {cid: qty for cid, qty in new_cat}
        if set(old_map) != set(new_map):
            lines.append("Os produtos de interesse foram atualizados.")
        else:
            for cid in new_map:
                if old_map.get(cid) != new_map.get(cid):
                    lines.append("As quantidades de adesão foram atualizadas.")
                    break
    return lines


def _notify_portal_client_crm_update(
    opp: Opportunity,
    change_lines: list[str],
    staff_message: str | None,
    has_attachments: bool = False,
) -> None:
    sm = (staff_message or "").strip()
    if not change_lines and not sm and not has_attachments:
        return
    resolved = _resolve_notify_client(opp)
    if resolved is None:
        return
    name, to_addr = resolved
    parts: list[str] = [f"Olá, {name},"]
    if change_lines:
        parts.append(
            "Há novidades no seu lead:\n\n"
            + "\n".join(f"• {line}" for line in change_lines)
        )
    if sm:
        parts.append(f"Mensagem da equipe:\n\n{sm}")
    if has_attachments:
        parts.append(
            "A equipe enviou arquivo(s) na conversa. Acesse o lead para ver os anexos."
        )
    parts.append(f"\nAcompanhe em: {_url_cliente_lead(opp.id)}")
    body = "\n\n".join(parts)
    subj = f"Atualização no seu lead — {(opp.title or 'Lead')[:55]}"
    _send_email_background(to_addr, subj, body)


def _notify_staff_client_chat_message(
    opp: Opportunity,
    portal_client: PortalClient,
    message_body: str,
    attachment_count: int = 0,
) -> None:
    row = db.session.get(SiteSettings, 1)
    to_addr = (row.contact_email or "").strip() if row else ""
    if not to_addr:
        return
    preview = (message_body or "").strip()[:2500]
    if attachment_count > 0:
        extra = f"\n\n[{attachment_count} arquivo(s) anexo(s) — veja no CRM]"
        preview = (preview + extra) if preview else extra.strip()
    body = (
        f"O cliente {portal_client.name} ({portal_client.email}) enviou uma mensagem "
        f"no lead #{opp.id}: {opp.title}\n\n{preview}\n\n"
        f"Abrir no CRM: {_url_crm_oportunidade(opp.id)}"
    )
    subj = f"[ARPGOV] Nova mensagem do cliente — lead #{opp.id}"
    _send_email_background(to_addr, subj, body)


def _normalize_env_password(val: str | None) -> str:
    if not val:
        return ""
    s = str(val).strip()
    if len(s) >= 2 and s[0] in "'\"" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def _password_matches(stored: str, password: str) -> bool:
    if stored.startswith("pbkdf2:") or stored.startswith("scrypt:"):
        return check_password_hash(stored, password)
    return password == stored


def _painel_password() -> str:
    p = _normalize_env_password(os.environ.get("PAINEL_ADMIN_PASSWORD"))
    if not p:
        p = _normalize_env_password(os.environ.get("PORTAL_ADMIN_PASSWORD"))
    return p


def _crm_password() -> str:
    return _normalize_env_password(os.environ.get("CRM_ADMIN_PASSWORD"))


def _crm_password_configured() -> bool:
    return bool(_crm_password())


def _rotate_auth_session() -> None:
    """Evita session fixation: limpa a sessão antes de gravar novo login."""
    session.clear()
    session.modified = True


def _sanitize_public_html(raw: str | None) -> str:
    """HTML público editável no painel — allowlist (mitiga XSS armazenado)."""
    import bleach

    allowed_tags = [
        "a", "abbr", "b", "blockquote", "br", "code", "div", "em", "h1", "h2",
        "h3", "h4", "hr", "i", "li", "ol", "p", "pre", "span", "strong", "u",
        "ul", "table", "thead", "tbody", "tr", "th", "td",
    ]
    allowed_attrs = {
        "*": ["class"],
        "a": ["href", "title", "rel", "target"],
        "td": ["colspan", "rowspan"],
        "th": ["colspan", "rowspan"],
    }
    return bleach.clean(
        raw or "",
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def _sanitize_custom_css(raw: str | None) -> str | None:
    """Remove construções perigosas de CSS injetável no <style> do site."""
    css = (raw or "").strip()
    if not css:
        return None
    # Quebra fechamento de style / tags / javascript
    css = re.sub(r"</\s*style", r"<\\/style", css, flags=re.I)
    css = re.sub(r"<\s*script", "", css, flags=re.I)
    css = re.sub(r"@import\b", "/*blocked-import*/", css, flags=re.I)
    css = re.sub(r"expression\s*\(", "/*blocked*/(", css, flags=re.I)
    css = re.sub(r"url\s*\(\s*['\"]?\s*javascript:", "url(/*blocked*/", css, flags=re.I)
    css = re.sub(r"-moz-binding\s*:", "/*blocked*/:", css, flags=re.I)
    css = re.sub(r"behavior\s*:", "/*blocked*/:", css, flags=re.I)
    return css


@app.template_filter("safe_html")
def _filter_safe_html(value):
    from markupsafe import Markup

    return Markup(_sanitize_public_html(value))


@app.template_filter("safe_css")
def _filter_safe_css(value):
    from markupsafe import Markup

    return Markup(_sanitize_custom_css(value) or "")


def _portal_master_password() -> str:
    """Senha opcional no .env que vale em todas as telas de login (painel, CRM, comercial, cliente, parceiro, intranet)."""
    return _normalize_env_password(os.environ.get("PORTAL_MASTER_PASSWORD"))


def _portal_master_login_enabled() -> bool:
    return bool(_portal_master_password())


def _portal_master_password_matches(raw: str | None) -> bool:
    m = _portal_master_password()
    if not m:
        return False
    return _password_matches(m, (raw or "").strip())


def slugify(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = t.strip("-")
    return t or "item"


def unique_slug(base: str, exclude_id: int | None = None) -> str:
    slug = base
    n = 2
    while True:
        q = CatalogItem.query.filter_by(slug=slug)
        if exclude_id is not None:
            q = q.filter(CatalogItem.id != exclude_id)
        if q.first() is None:
            return slug
        slug = f"{base}-{n}"
        n += 1


RESERVED_PAGE_SLUGS = frozenset(
    {
        "admin",
        "crm",
        "arps",
        "contato",
        "produto",
        "static",
        "p",
        "api",
        "pagina",
        "cliente",
        "comercial",
        "empresa",
        "institucional",
        "como-aderir",
    }
)


def unique_page_slug(base: str, exclude_id: int | None = None) -> str:
    b = (base or "pagina").strip() or "pagina"
    slug = b
    n = 2
    while True:
        if slug not in RESERVED_PAGE_SLUGS:
            q = SitePage.query.filter_by(slug=slug)
            if exclude_id is not None:
                q = q.filter(SitePage.id != exclude_id)
            if q.first() is None:
                return slug
        slug = f"{b}-{n}"
        n += 1


def unique_category_slug(base: str, exclude_id: int | None = None) -> str:
    slug = (base or "categoria").strip() or "categoria"
    b = slug
    n = 2
    while True:
        q = CatalogCategory.query.filter_by(slug=slug)
        if exclude_id is not None:
            q = q.filter(CatalogCategory.id != exclude_id)
        if q.first() is None:
            return slug
        slug = f"{b}-{n}"
        n += 1


def ensure_source_pncp_column():
    try:
        db.session.execute(text("SELECT source_pncp_id FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "ALTER TABLE catalog_items ADD COLUMN source_pncp_id VARCHAR(220)"
            )


def ensure_opportunity_process_ref_column():
    try:
        db.session.execute(text("SELECT process_ref FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE opportunities ADD COLUMN process_ref VARCHAR(120)"
                )
        except Exception:
            pass


def ensure_opportunity_cnpj_column():
    try:
        db.session.execute(text("SELECT cnpj FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE opportunities ADD COLUMN cnpj VARCHAR(22)")
        except Exception:
            pass


def ensure_site_settings_extra_columns():
    for name, ddl in (
        ("site_brand_primary", "VARCHAR(120)"),
        ("site_brand_accent", "VARCHAR(120)"),
        ("contact_intro", "TEXT"),
        ("contact_address", "VARCHAR(500)"),
        ("social_whatsapp", "VARCHAR(120)"),
        ("social_instagram", "VARCHAR(200)"),
        ("social_tiktok", "VARCHAR(200)"),
        ("custom_css", "TEXT"),
        ("meta_description", "VARCHAR(320)"),
    ):
        try:
            db.session.execute(text(f"SELECT {name} FROM site_settings LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE site_settings ADD COLUMN {name} {ddl}")
            except Exception:
                pass


def ensure_sales_rep_is_admin_column():
    try:
        db.session.execute(text("SELECT is_admin FROM sales_reps LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE sales_reps ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
                )
        except Exception:
            pass


def ensure_sales_rep_access_columns():
    cols = (
        ("access_comercial", "BOOLEAN NOT NULL DEFAULT 1"),
        ("access_crm", "BOOLEAN NOT NULL DEFAULT 0"),
        ("access_painel", "BOOLEAN NOT NULL DEFAULT 0"),
    )
    for col, ddl in cols:
        try:
            db.session.execute(text(f"SELECT {col} FROM sales_reps LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE sales_reps ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass
    try:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE sales_reps SET access_crm = 1, access_painel = 1 "
                "WHERE is_admin = 1 AND (access_crm = 0 OR access_painel = 0)"
            )
    except Exception:
        pass


def ensure_arpgov_brand_defaults():
    """Garante marca ARPGOV quando ainda não há personalização no banco."""
    row = db.session.get(SiteSettings, 1)
    if row is None:
        db.session.add(
            SiteSettings(
                id=1,
                site_brand_primary="ARP",
                site_brand_accent="GOV",
                meta_description=(
                    "ARPGOV — atas de registro de preço e adesão simplificada para o setor público."
                ),
            )
        )
        db.session.commit()
        return
    changed = False
    primary = (row.site_brand_primary or "").strip()
    accent = (row.site_brand_accent or "").strip()
    legacy_pairs = {
        ("Portal", "Gov"),
        ("Portal", "GOV"),
        ("Portal", " Gov"),
        ("portalgov", ""),
        ("PortalGov", ""),
    }
    if not primary and not accent:
        row.site_brand_primary = "ARP"
        row.site_brand_accent = "GOV"
        changed = True
    elif (primary, accent) in legacy_pairs or primary.lower() in ("portal", "portalgov"):
        row.site_brand_primary = "ARP"
        row.site_brand_accent = "GOV"
        changed = True
    if not (row.meta_description or "").strip():
        row.meta_description = (
            "ARPGOV — atas de registro de preço e adesão simplificada para o setor público."
        )
        changed = True
    if changed:
        db.session.commit()


def ensure_catalog_item_category_id_column():
    try:
        db.session.execute(text("SELECT category_id FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE catalog_items ADD COLUMN category_id INTEGER REFERENCES catalog_categories(id)"
                )
        except Exception:
            pass


def ensure_catalog_item_manufacturer_column():
    try:
        db.session.execute(text("SELECT manufacturer FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE catalog_items ADD COLUMN manufacturer VARCHAR(200)"
                )
        except Exception:
            pass


def ensure_catalog_item_source_product_url_column():
    try:
        db.session.execute(text("SELECT source_product_url FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE catalog_items ADD COLUMN source_product_url VARCHAR(500)"
                )
        except Exception:
            pass


def ensure_catalog_item_arp_link_columns():
    for col, ddl in (
        ("pncp_url", "VARCHAR(700)"),
        ("contract_page_url", "VARCHAR(700)"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM catalog_items LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE catalog_items ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_catalog_item_warranty_and_technical_columns():
    for col, ddl in (
        ("warranty", "VARCHAR(300)"),
        ("technical_description", "TEXT"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM catalog_items LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE catalog_items ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_catalog_item_ata_owner_company_column():
    try:
        db.session.execute(text("SELECT ata_owner_company FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE catalog_items ADD COLUMN ata_owner_company VARCHAR(200)"
                )
        except Exception:
            pass


def ensure_catalog_item_attachment_columns():
    for col in ("catalog_attachments_json", "ata_company_docs_json"):
        try:
            db.session.execute(text(f"SELECT {col} FROM catalog_items LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE catalog_items ADD COLUMN {col} TEXT")
            except Exception:
                pass


def ensure_catalog_images_json_column():
    try:
        db.session.execute(text("SELECT images_json FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE catalog_items ADD COLUMN images_json TEXT")
        except Exception:
            pass


def ensure_catalog_stock_on_hand_column():
    try:
        db.session.execute(text("SELECT stock_on_hand FROM catalog_items LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE catalog_items ADD COLUMN stock_on_hand INTEGER NOT NULL DEFAULT 0"
                )
        except Exception:
            pass


def ensure_catalog_upload_dir():
    d = os.path.join(app.root_path, "static", "uploads", "catalog")
    os.makedirs(d, exist_ok=True)


def ensure_catalog_attachments_dir():
    d = os.path.join(app.root_path, "static", "uploads", "catalog_attachments")
    os.makedirs(d, exist_ok=True)


def ensure_ata_company_docs_dir():
    d = os.path.join(app.root_path, "static", "uploads", "ata_company_docs")
    os.makedirs(d, exist_ok=True)


def ensure_lead_chat_upload_dir():
    d = os.path.join(app.root_path, "static", "uploads", "lead_chat")
    os.makedirs(d, exist_ok=True)


def ensure_social_post_upload_dir():
    d = os.path.join(app.root_path, "static", "uploads", "social_posts")
    os.makedirs(d, exist_ok=True)


def ensure_lead_message_attachments_json_column():
    try:
        db.session.execute(text("SELECT attachments_json FROM lead_messages LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE lead_messages ADD COLUMN attachments_json TEXT"
                )
        except Exception:
            pass


def ensure_lead_message_thread_column():
    try:
        db.session.execute(text("SELECT thread FROM lead_messages LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE lead_messages ADD COLUMN thread VARCHAR(20) NOT NULL DEFAULT 'client'"
                )
        except Exception:
            pass


def ensure_finance_upload_dirs():
    for sub in ("finance_company", "finance_rep", "lead_pipeline"):
        d = os.path.join(app.root_path, "static", "uploads", sub)
        os.makedirs(d, exist_ok=True)


def ensure_partner_product_approval_columns():
    """SQLite: colunas de proposta de catálogo e aprovação em partner_products."""
    cols = (
        ("approval_status", "VARCHAR(24)"),
        ("catalog_item_id", "INTEGER"),
        ("rejection_note", "TEXT"),
        ("draft_category_id", "INTEGER"),
        ("draft_section", "VARCHAR(80)"),
        ("draft_sphere", "VARCHAR(80)"),
        ("draft_quantity", "INTEGER"),
        ("draft_stock_on_hand", "INTEGER"),
        ("draft_unit_price", "NUMERIC(14,2)"),
        ("draft_valid_until", "DATE"),
        ("draft_slug", "VARCHAR(200)"),
        ("draft_highlight", "INTEGER NOT NULL DEFAULT 0"),
        ("draft_images_json", "TEXT"),
        ("draft_catalog_attachments_json", "TEXT"),
        ("draft_ata_company_docs_json", "TEXT"),
        ("draft_ata_owner_company", "VARCHAR(200)"),
        ("draft_manufacturer", "VARCHAR(200)"),
        ("draft_source_product_url", "VARCHAR(500)"),
        ("draft_pncp_url", "VARCHAR(700)"),
        ("draft_contract_page_url", "VARCHAR(700)"),
        ("draft_warranty", "VARCHAR(300)"),
        ("draft_technical_description", "TEXT"),
    )
    for col, ddl in cols:
        try:
            db.session.execute(text(f"SELECT {col} FROM partner_products LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE partner_products ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass
    try:
        db.session.execute(
            text(
                "UPDATE partner_products SET approval_status = 'legacy' "
                "WHERE approval_status IS NULL"
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def ensure_partner_product_deletion_request_columns():
    """SQLite: pedido de exclusão pelo parceiro."""
    for col, ddl in (
        ("deletion_requested_at", "DATETIME"),
        ("deletion_request_note", "TEXT"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM partner_products LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE partner_products ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_br_orgaos_demografia_columns():
    for col, ddl in (
        ("populacao_ibge", "INTEGER"),
        ("ano_referencia_pop_ibge", "INTEGER"),
        ("potencial_orcamento_anual_brl", "NUMERIC(18,2)"),
        ("orcamento_metodo", "VARCHAR(60)"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM br_orgaos_publicos LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE br_orgaos_publicos ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_empresa_intranet_module_columns():
    """SQLite: colunas novas em tabelas já existentes (create_all não faz ALTER)."""
    for col, ddl in (
        ("ncm", "VARCHAR(10)"),
        ("beneficio_fiscal", "TEXT"),
        ("observacoes_fiscais", "TEXT"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM empresa_produtos LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE empresa_produtos ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass
    try:
        db.session.execute(text("SELECT contrato_id FROM empresa_empenhos LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE empresa_empenhos ADD COLUMN contrato_id INTEGER"
                )
        except Exception:
            pass


def ensure_empresa_contrato_fechamento_cols():
    """Colunas de contrato (contato/cliente) e fechamento de preço (UF / snapshot)."""
    for table, cols in (
        (
            "empresa_contratos_orgao",
            (
                ("email_contato", "VARCHAR(120)"),
                ("telefone", "VARCHAR(40)"),
                ("cliente_razao_social", "VARCHAR(400)"),
                ("cliente_cnpj", "VARCHAR(14)"),
            ),
        ),
        (
            "empresa_fechamento_precos",
            (
                ("uf_entrega", "VARCHAR(2)"),
                ("planilha_fechada", "INTEGER"),
                ("fechada_em", "DATETIME"),
                ("snapshot_json", "TEXT"),
            ),
        ),
    ):
        for col, ddl in cols:
            try:
                db.session.execute(text(f"SELECT {col} FROM {table} LIMIT 1"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                try:
                    with db.engine.begin() as conn:
                        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                except Exception:
                    pass


def ensure_empresa_fechamento_account_cols():
    """Account (vendedor) e competência de faturamento na planilha de fechamento."""
    cols = (
        ("account_id", "INTEGER"),
        ("competencia_faturamento", "DATE"),
    )
    for col, ddl in cols:
        try:
            db.session.execute(text(f"SELECT {col} FROM empresa_fechamento_precos LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE empresa_fechamento_precos ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_empresa_licitacao_edital_cols():
    """Análise de edital: esclarecimentos, impugnação, prazos, multas, checklist JSON."""
    cols = (
        ("data_limite_impugnacao", "DATE"),
        ("data_limite_esclarecimento", "DATE"),
        ("prazo_entrega_objeto", "DATE"),
        ("local_entrega_edital", "TEXT"),
        ("multas_edital", "TEXT"),
        ("documentacao_solicitada", "TEXT"),
        ("esclarecimentos", "TEXT"),
        ("questionamento_impugnacao", "TEXT"),
        ("checklist_documentos_json", "TEXT"),
    )
    for col, ddl in cols:
        try:
            db.session.execute(text(f"SELECT {col} FROM empresa_licitacoes LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE empresa_licitacoes ADD COLUMN {col} {ddl}")
            except Exception:
                pass


def init_schema():
    with app.app_context():
        db.create_all()
        ensure_empresa_intranet_module_columns()
        ensure_empresa_contrato_fechamento_cols()
        ensure_empresa_fechamento_account_cols()
        ensure_empresa_licitacao_edital_cols()
        ensure_br_orgaos_demografia_columns()
        ensure_source_pncp_column()
        ensure_opportunity_cnpj_column()
        ensure_opportunity_process_ref_column()
        ensure_site_settings_extra_columns()
        ensure_sales_rep_is_admin_column()
        ensure_sales_rep_access_columns()
        ensure_arpgov_brand_defaults()
        ensure_catalog_images_json_column()
        ensure_catalog_item_category_id_column()
        ensure_catalog_item_ata_owner_company_column()
        ensure_catalog_item_manufacturer_column()
        ensure_catalog_item_source_product_url_column()
        ensure_catalog_item_arp_link_columns()
        ensure_catalog_item_warranty_and_technical_columns()
        ensure_catalog_item_attachment_columns()
        ensure_catalog_stock_on_hand_column()
        ensure_catalog_upload_dir()
        ensure_catalog_attachments_dir()
        ensure_ata_company_docs_dir()
        ensure_lead_chat_upload_dir()
        ensure_social_post_upload_dir()
        ensure_lead_message_attachments_json_column()
        ensure_lead_message_thread_column()
        ensure_partner_product_approval_columns()
        ensure_partner_product_deletion_request_columns()
        ensure_finance_upload_dirs()
        ensure_opportunity_portal_client_id_column()
        ensure_opportunity_partner_id_column()
        ensure_opportunity_sales_rep_id_column()
        ensure_opportunity_rep_commission_columns()
        ensure_opportunity_commission_project_columns()
        ensure_opportunity_catalog_lines_table()
        ensure_opportunity_pipeline_data_column()
        ensure_opportunity_stage_migration()
        ensure_commission_sales_tables()
        ensure_commission_project_global_columns()
        ensure_commission_project_system_column()
        ensure_commission_project_rateio_columns()
        ensure_commission_projects_catalog_item()
        ensure_global_commission_projects()
        ensure_finance_goals_tables()
        ensure_company_expenses_table()
        ensure_portal_client_profile_columns()
        ensure_partner_profile_columns()
        ensure_portal_client_upload_dir()
        ensure_contratos_gov_scan_tables()
        ensure_arp_pipeline_tables()


def ensure_finance_goals_tables():
    db.create_all()
    try:
        db.session.execute(text("SELECT commission_tier_id FROM company_finance_goals LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE company_finance_goals ADD COLUMN commission_tier_id INTEGER"
                )
        except Exception:
            pass
    for col, ddl in (
        ("goal_start_month", "INTEGER NOT NULL DEFAULT 1"),
        ("goal_end_month", "INTEGER NOT NULL DEFAULT 12"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM company_finance_goals LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE company_finance_goals ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_company_expenses_table():
    db.create_all()
    try:
        db.session.execute(text("SELECT id FROM company_expenses LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    for col, ddl in (
        ("installment_group_id", "VARCHAR(32)"),
        ("installment_index", "INTEGER"),
        ("installment_count", "INTEGER"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM company_expenses LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE company_expenses ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_opportunity_rep_commission_columns():
    for col, ddl in (
        ("rep_commission_brl", "NUMERIC(14,2)"),
        ("rep_commission_note", "TEXT"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM opportunities LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE opportunities ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_opportunity_partner_id_column():
    try:
        db.session.execute(text("SELECT partner_id FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE opportunities ADD COLUMN partner_id INTEGER")
        except Exception:
            pass


def ensure_opportunity_portal_client_id_column():
    try:
        db.session.execute(text("SELECT portal_client_id FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE opportunities ADD COLUMN portal_client_id INTEGER")
        except Exception:
            pass


def ensure_opportunity_sales_rep_id_column():
    try:
        db.session.execute(text("SELECT sales_rep_id FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE opportunities ADD COLUMN sales_rep_id INTEGER"
                )
        except Exception:
            pass


def ensure_opportunity_commission_project_columns():
    for col, ddl in (
        ("commission_project_id", "INTEGER"),
        ("commission_tier_id", "INTEGER"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM opportunities LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE opportunities ADD COLUMN {col} {ddl}")
            except Exception:
                pass


def ensure_opportunity_catalog_lines_table():
    """Tabela de linhas catálogo×lead com quantidade; migra dados do M2M legado."""
    db.create_all()
    try:
        db.session.execute(text("SELECT quantity FROM opportunity_catalog_lines LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        legacy = db.session.execute(
            text(
                "SELECT opportunity_id, catalog_item_id "
                "FROM opportunity_catalog_items"
            )
        ).fetchall()
    except Exception:
        db.session.rollback()
        return
    if not legacy:
        return
    existing = {
        (int(r[0]), int(r[1]))
        for r in db.session.execute(
            text("SELECT opportunity_id, catalog_item_id FROM opportunity_catalog_lines")
        ).fetchall()
    }
    for opp_id, cat_id in legacy:
        key = (int(opp_id), int(cat_id))
        if key in existing:
            continue
        db.session.add(
            OpportunityCatalogLine(
                opportunity_id=key[0],
                catalog_item_id=key[1],
                quantity=1,
            )
        )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()


def ensure_opportunity_pipeline_data_column():
    try:
        db.session.execute(text("SELECT pipeline_data_json FROM opportunities LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE opportunities ADD COLUMN pipeline_data_json TEXT"
                )
        except Exception:
            pass


def ensure_opportunity_stage_migration():
    """Migra chaves de estágio legadas para o funil atual."""
    for old, new in LEGACY_STAGE_MAP.items():
        try:
            db.session.execute(
                text("UPDATE opportunities SET stage = :new WHERE stage = :old"),
                {"new": new, "old": old},
            )
            db.session.commit()
        except Exception:
            db.session.rollback()


def ensure_commission_sales_tables():
    db.create_all()


def ensure_commission_project_system_column():
    try:
        db.session.execute(text("SELECT is_system FROM commission_projects LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql(
                    "ALTER TABLE commission_projects ADD COLUMN is_system INTEGER NOT NULL DEFAULT 0"
                )
        except Exception:
            pass
    try:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE commission_projects SET is_system = 1 "
                "WHERE title IN ('Comissão sem vendedor', 'Comissão com vendedor')"
            )
    except Exception:
        pass


def ensure_commission_project_rateio_columns():
    for col, ddl in (("rateio_mode", "VARCHAR(20) NOT NULL DEFAULT 'no_seller'"),):
        try:
            db.session.execute(text(f"SELECT {col} FROM commission_projects LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE commission_projects ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass
    try:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE commission_projects SET rateio_mode = 'with_seller' "
                "WHERE (rateio_mode IS NULL OR rateio_mode = '') AND with_seller = 1"
            )
            conn.exec_driver_sql(
                "UPDATE commission_projects SET rateio_mode = 'no_seller' "
                "WHERE rateio_mode IS NULL OR rateio_mode = ''"
            )
    except Exception:
        pass
    db.create_all()


def ensure_commission_project_global_columns():
    for col, ddl in (
        ("with_seller", "INTEGER NOT NULL DEFAULT 0"),
        ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM commission_projects LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE commission_projects ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass


def ensure_commission_projects_catalog_item():
    """Garante catalog_item_id em projetos legados (coluna NOT NULL no SQLite antigo)."""
    try:
        cat_id = (
            db.session.query(CatalogItem.id)
            .order_by(CatalogItem.id.asc())
            .limit(1)
            .scalar()
        )
        if cat_id is None:
            return
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE commission_projects SET catalog_item_id = ? "
                "WHERE catalog_item_id IS NULL",
                (int(cat_id),),
            )
    except Exception:
        pass


def ensure_global_commission_projects():
    """Dois projetos globais (com/sem vendedor), sem vínculo a produto."""
    from commission_service import (
        reapply_all_opportunity_commissions,
        refresh_all_commission_tier_splits,
        sync_project_tiers,
    )

    from models import (
        CommissionProject,
        CommissionProjectTier,
        CompanyStakeholder,
        Opportunity,
    )

    db.create_all()
    stakeholders = (
        CompanyStakeholder.query.filter_by(is_active=True)
        .order_by(CompanyStakeholder.sort_order.asc(), CompanyStakeholder.id.asc())
        .all()
    )
    fallback_cat_id = (
        db.session.query(CatalogItem.id)
        .order_by(CatalogItem.id.asc())
        .limit(1)
        .scalar()
    )

    configs = (
        ("Comissão sem vendedor", False, 1, "100% rateado entre sócios (65% / 25% / 10%)."),
        (
            "Comissão com vendedor",
            True,
            2,
            "30% para o vendedor e 70% rateado entre sócios (65% / 25% / 10%).",
        ),
    )
    canonical: dict[bool, CommissionProject] = {}
    for title, with_seller, sort_order, notes in configs:
        project = (
            CommissionProject.query.filter_by(with_seller=with_seller, is_active=True)
            .order_by(CommissionProject.id.asc())
            .first()
        )
        if project is None:
            project = CommissionProject.query.filter_by(title=title).first()
        if project is None:
            project = CommissionProject(
                title=title,
                with_seller=with_seller,
                rateio_mode="with_seller" if with_seller else "no_seller",
                sort_order=sort_order,
                notes=notes,
                is_active=True,
                is_system=True,
            )
            if fallback_cat_id is not None:
                project.catalog_item_id = fallback_cat_id
            db.session.add(project)
        else:
            project.title = title
            project.with_seller = with_seller
            project.rateio_mode = "with_seller" if with_seller else "no_seller"
            project.sort_order = sort_order
            project.notes = notes
            project.is_active = True
            project.is_system = True
        db.session.flush()
        sync_project_tiers(project, stakeholders)
        canonical[with_seller] = project

    tier_map: dict[tuple[bool, Decimal], int] = {}
    for ws, project in canonical.items():
        for tier in project.tiers:
            tier_map[(ws, Decimal(str(tier.percent_total)))] = tier.id

    for opp in Opportunity.query.filter(Opportunity.commission_tier_id.isnot(None)).all():
        old_tier = db.session.get(CommissionProjectTier, opp.commission_tier_id)
        if old_tier is None:
            continue
        key = (bool(old_tier.with_seller), Decimal(str(old_tier.percent_total)))
        new_id = tier_map.get(key)
        if new_id and new_id != opp.commission_tier_id:
            opp.commission_tier_id = new_id
            opp.commission_project_id = canonical[key[0]].id

    canonical_ids = {p.id for p in canonical.values()}
    for project in CommissionProject.query.filter_by(is_system=True, is_active=True).all():
        if project.id not in canonical_ids:
            project.is_active = False

    try:
        refresh_all_commission_tier_splits(stakeholders)
        reapply_all_opportunity_commissions(preserve_payout_status=True)
        db.session.commit()
    except Exception:
        db.session.rollback()


def ensure_partner_profile_columns():
    for col, ddl in (
        ("razao_social", "VARCHAR(300)"),
        ("cnpj", "VARCHAR(22)"),
        ("cpf", "VARCHAR(14)"),
        ("job_title", "VARCHAR(120)"),
        ("sector", "VARCHAR(120)"),
        ("website", "VARCHAR(300)"),
        ("address_street", "VARCHAR(200)"),
        ("address_number", "VARCHAR(20)"),
        ("address_complement", "VARCHAR(120)"),
        ("address_neighborhood", "VARCHAR(120)"),
        ("address_city", "VARCHAR(120)"),
        ("address_state", "VARCHAR(2)"),
        ("address_zip", "VARCHAR(10)"),
        ("updated_at", "DATETIME"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM partners LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(f"ALTER TABLE partners ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    try:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE partners SET updated_at = created_at "
                "WHERE updated_at IS NULL AND created_at IS NOT NULL"
            )
    except Exception:
        pass


def ensure_portal_client_profile_columns():
    for col, ddl in (
        ("razao_social", "VARCHAR(300)"),
        ("cpf", "VARCHAR(14)"),
        ("job_title", "VARCHAR(120)"),
        ("sector", "VARCHAR(120)"),
        ("sphere", "VARCHAR(80)"),
        ("address_street", "VARCHAR(200)"),
        ("address_number", "VARCHAR(20)"),
        ("address_complement", "VARCHAR(120)"),
        ("address_neighborhood", "VARCHAR(120)"),
        ("address_city", "VARCHAR(120)"),
        ("address_state", "VARCHAR(2)"),
        ("address_zip", "VARCHAR(10)"),
        ("photo_path", "VARCHAR(256)"),
        ("updated_at", "DATETIME"),
        ("created_by_sales_rep_id", "INTEGER"),
    ):
        try:
            db.session.execute(text(f"SELECT {col} FROM portal_clients LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                with db.engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE portal_clients ADD COLUMN {col} {ddl}"
                    )
            except Exception:
                pass
    try:
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE portal_clients SET updated_at = created_at "
                "WHERE updated_at IS NULL AND created_at IS NOT NULL"
            )
    except Exception:
        pass


def ensure_portal_client_upload_dir():
    d = os.path.join(app.root_path, "static", "uploads", "portal_clients")
    os.makedirs(d, exist_ok=True)


def ensure_arp_pipeline_tables():
    for table in ("arp_analyses", "licitacao_watches"):
        try:
            db.session.execute(text(f"SELECT id FROM {table} LIMIT 1"))
            db.session.commit()
        except Exception:
            db.session.rollback()
            db.create_all()


def ensure_contratos_gov_scan_tables():
    try:
        db.session.execute(text("SELECT id FROM contratos_gov_scans LIMIT 1"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        db.create_all()
    _ensure_contratos_gov_scan_columns()
    _ensure_scan_result_arp_id_nullable()


def _ensure_scan_result_arp_id_nullable():
    """SQLite legado exige arp_id NOT NULL; resultados só PNCP usam arp_id NULL."""
    try:
        row = db.session.execute(
            text("SELECT \"notnull\" FROM pragma_table_info('contratos_gov_scan_results') WHERE name='arp_id'")
        ).fetchone()
        if not row or int(row[0]) == 0:
            return
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE contratos_gov_scan_results__new (
                    id INTEGER PRIMARY KEY,
                    scan_id INTEGER NOT NULL,
                    arp_id INTEGER,
                    numero_ata VARCHAR(40),
                    unidade VARCHAR(300),
                    compra_ano VARCHAR(40),
                    status_ata VARCHAR(40),
                    valor_total VARCHAR(80),
                    vigencia_inicial VARCHAR(20),
                    vigencia_final VARCHAR(20),
                    detail_url VARCHAR(300) NOT NULL,
                    items_json TEXT,
                    created_at DATETIME NOT NULL,
                    modalidade VARCHAR(120),
                    pncp_ata_url VARCHAR(300),
                    pncp_compra_url VARCHAR(300),
                    suppliers_json TEXT,
                    was_known_before BOOLEAN DEFAULT 0,
                    catalog_item_id INTEGER,
                    opportunity_id INTEGER,
                    pncp_control_id VARCHAR(220),
                    objeto TEXT,
                    verification_level VARCHAR(20) DEFAULT 'item'
                )
                """
            )
            conn.exec_driver_sql(
                """
                INSERT INTO contratos_gov_scan_results__new (
                    id, scan_id, arp_id, numero_ata, unidade, compra_ano, status_ata,
                    valor_total, vigencia_inicial, vigencia_final, detail_url, items_json,
                    created_at, modalidade, pncp_ata_url, pncp_compra_url, suppliers_json,
                    was_known_before, catalog_item_id, opportunity_id, pncp_control_id,
                    objeto, verification_level
                )
                SELECT
                    id, scan_id, arp_id, numero_ata, unidade, compra_ano, status_ata,
                    valor_total, vigencia_inicial, vigencia_final, detail_url, items_json,
                    created_at, modalidade, pncp_ata_url, pncp_compra_url, suppliers_json,
                    was_known_before, catalog_item_id, opportunity_id, pncp_control_id,
                    objeto, verification_level
                FROM contratos_gov_scan_results
                """
            )
            conn.exec_driver_sql("DROP TABLE contratos_gov_scan_results")
            conn.exec_driver_sql(
                "ALTER TABLE contratos_gov_scan_results__new RENAME TO contratos_gov_scan_results"
            )
    except Exception:
        db.session.rollback()


def _ensure_contratos_gov_scan_columns():
    for table, cols in (
        (
            "contratos_gov_scans",
            (
                ("list_pages_total", "INTEGER"),
                ("duplicates_skipped", "INTEGER DEFAULT 0"),
                ("item_details_fetched", "INTEGER DEFAULT 0"),
                ("list_scan_complete", "BOOLEAN DEFAULT 0"),
                ("detail_limit_hit", "BOOLEAN DEFAULT 0"),
                ("enrich_suppliers", "BOOLEAN DEFAULT 1"),
                ("month", "INTEGER"),
                ("keyword", "VARCHAR(120)"),
                ("supplier_cnpj", "VARCHAR(22)"),
                ("orgao_cnpj", "VARCHAR(22)"),
                ("pncp_query_mode", "VARCHAR(20) DEFAULT 'vigencia'"),
                ("pncp_ano_ata", "INTEGER"),
                ("only_pncp_adesao", "BOOLEAN DEFAULT 1"),
                ("pncp_filters_json", "TEXT"),
                ("scan_mode", "VARCHAR(20) DEFAULT 'contratos'"),
                ("max_pncp_pages", "INTEGER DEFAULT 30"),
                ("pncp_pages_read", "INTEGER DEFAULT 0"),
                ("pncp_total_pages", "INTEGER DEFAULT 0"),
                ("pncp_rows_api", "INTEGER DEFAULT 0"),
                ("pncp_rows_matched", "INTEGER DEFAULT 0"),
            ),
        ),
        (
            "contratos_gov_scan_results",
            (
                ("modalidade", "VARCHAR(120)"),
                ("pncp_ata_url", "VARCHAR(300)"),
                ("pncp_compra_url", "VARCHAR(300)"),
                ("suppliers_json", "TEXT"),
                ("was_known_before", "BOOLEAN DEFAULT 0"),
                ("catalog_item_id", "INTEGER"),
                ("opportunity_id", "INTEGER"),
                ("pncp_control_id", "VARCHAR(220)"),
                ("objeto", "TEXT"),
                ("verification_level", "VARCHAR(20) DEFAULT 'item'"),
            ),
        ),
    ):
        for name, ddl in cols:
            try:
                db.session.execute(text(f"SELECT {name} FROM {table} LIMIT 1"))
                db.session.commit()
            except Exception:
                db.session.rollback()
                try:
                    with db.engine.begin() as conn:
                        conn.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                        )
                except Exception:
                    pass


def _robo_pncp_filter_context():
    """Opções de filtros do portal PNCP para o formulário do robô."""
    import pncp_client as pncp

    orgaos: dict[str, str] = {}
    unidades: list[dict[str, str | None]] = []
    municipios: dict[str, dict[str, str]] = {}
    try:
        rows = (
            PncpOrgaoUnidade.query.order_by(
                PncpOrgaoUnidade.razao_social.asc(),
                PncpOrgaoUnidade.nome_unidade.asc(),
            )
            .limit(8000)
            .all()
        )
        for row in rows:
            cnpj = re.sub(r"\D", "", row.cnpj or "")[:14]
            if len(cnpj) != 14:
                continue
            if cnpj not in orgaos:
                orgaos[cnpj] = (row.razao_social or cnpj)[:320]
            cod = (row.codigo_unidade or "0000")[:24]
            unidades.append(
                {
                    "cnpj": cnpj,
                    "codigo": cod,
                    "nome": ((row.nome_unidade or row.razao_social) or "")[:320],
                    "uf": (row.uf_sigla or "")[:2] or None,
                    "ibge": (row.codigo_municipio_ibge or "")[:12] or None,
                }
            )
            ibge = (row.codigo_municipio_ibge or "").strip()[:12]
            if ibge and row.municipio_nome:
                municipios[ibge] = {
                    "nome": row.municipio_nome[:220],
                    "uf": (row.uf_sigla or "")[:2],
                }
    except Exception:
        pass
    orgao_list = sorted(
        ((cnpj, nome) for cnpj, nome in orgaos.items()),
        key=lambda x: x[1].lower(),
    )
    municipio_list = sorted(
        (
            (ibge, data["nome"][:220], data.get("uf") or "")
            for ibge, data in municipios.items()
        ),
        key=lambda x: x[1].lower(),
    )
    return {
        "pncp_esferas": pncp.PNCP_ESFERA_CHOICES,
        "pncp_poderes": pncp.PNCP_PODER_CHOICES,
        "pncp_vigencia_status": pncp.PNCP_VIGENCIA_STATUS,
        "pncp_permite_adesao": pncp.PNCP_PERMITE_ADESAO,
        "pncp_orgaos": orgao_list,
        "pncp_unidades_data": unidades,
        "pncp_municipios_data": municipio_list,
        "pncp_org_sync_count": len(orgaos),
        "br_ufs": BR_UFS,
        "pncp_api_filters": pncp.PNCP_ATAS_API_FILTERS,
        "pncp_ata_field_filters": pncp.PNCP_ATAS_FIELD_FILTERS,
    }


def _pncp_org_resolver_for_robo():
    import pncp_client as pncp

    try:
        rows = PncpOrgaoUnidade.query.limit(12000).all()
        if rows:
            return pncp.PncpOrgResolver.from_unit_rows(rows)
    except Exception:
        pass
    return pncp.PncpOrgResolver()


CONTRATOS_CATALOG_SOURCE_PREFIX = "cgov:"


def _sphere_from_contratos_unidade(unidade: str | None) -> str:
    u = (unidade or "").upper()
    if "MUN" in u:
        return "Municipal"
    if "EST" in u or "ESP" in u:
        return "Estadual"
    return "Federal"


def _parse_contratos_br_date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip()[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def _contratos_collect_suppliers(items_adesao: list) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for it in items_adesao:
        for f in it.get("fornecedores") or []:
            key = (str(f.get("cnpj") or ""), str(f.get("fornecedor") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out


def _parse_contratos_unit_price(items_adesao: list) -> Decimal:
    for it in items_adesao:
        for f in it.get("fornecedores") or []:
            v = parse_money_brl(f.get("valor_unitario"))
            if v is not None and v > 0:
                return v
    return Decimal("0")


def _contratos_catalog_source_id(result: ContratosGovScanResult) -> str:
    if result.pncp_control_id:
        return (result.pncp_control_id or "")[:220]
    if result.arp_id:
        return f"{CONTRATOS_CATALOG_SOURCE_PREFIX}{result.arp_id}"
    return f"{CONTRATOS_CATALOG_SOURCE_PREFIX}unknown-{result.id}"


def _catalog_title_from_contratos_result(result: ContratosGovScanResult) -> str:
    if result.objeto:
        t = result.objeto.strip()
        if t:
            return t[:300]
    parts: list[str] = []
    for it in result.items_adesao[:3]:
        d = (it.get("descricao_detalhada") or it.get("descricao") or "").strip()
        if d:
            parts.append(d)
    if parts:
        return " — ".join(parts)[:300]
    return f"ARP {result.numero_ata or result.arp_id}"[:300]


def _technical_notes_from_contratos_result(result: ContratosGovScanResult) -> str:
    lines: list[str] = []
    if result.objeto:
        lines.append(f"Objeto: {result.objeto}")
    if result.verification_level == "pncp_only":
        lines.append(
            "Fonte: PNCP (possibilidade de adesão na ata — confirme itens no órgão gerenciador)."
        )
    elif result.verification_level == "pncp":
        lines.append("Fonte: PNCP (nível ata).")
    if result.unidade:
        lines.append(f"Unidade gerenciadora: {result.unidade}")
    if result.compra_ano:
        lines.append(f"Compra/ano: {result.compra_ano}")
    if result.modalidade:
        lines.append(f"Modalidade: {result.modalidade}")
    for it in result.items_adesao:
        lines.append(f"Item {it.get('numero')}: {it.get('descricao')}")
        if it.get("descricao_detalhada"):
            lines.append(f"  Detalhe: {it['descricao_detalhada']}")
        for f in it.get("fornecedores") or []:
            lines.append(
                f"  Fornecedor: {f.get('fornecedor')} — CNPJ {f.get('cnpj')} — "
                f"{f.get('valor_unitario')}"
            )
    if result.detail_url:
        lines.append(f"Contratos.gov.br: {result.detail_url}")
    if result.pncp_ata_url:
        lines.append(f"PNCP ata: {result.pncp_ata_url}")
    if result.pncp_compra_url:
        lines.append(f"PNCP compra: {result.pncp_compra_url}")
    return "\n".join(lines)


def create_catalog_from_contratos_result(
    result: ContratosGovScanResult,
    *,
    section: str = "Contratos.gov.br — robô",
) -> CatalogItem:
    ensure_source_pncp_column()
    source_id = _contratos_catalog_source_id(result)
    existing = CatalogItem.query.filter_by(source_pncp_id=source_id).first()
    if existing:
        result.catalog_item_id = existing.id
        return existing
    if result.catalog_item_id:
        linked = db.session.get(CatalogItem, result.catalog_item_id)
        if linked:
            return linked

    supplier = result.primary_supplier
    item = CatalogItem(
        title=_catalog_title_from_contratos_result(result),
        section=section[:80],
        sphere=_sphere_from_contratos_unidade(result.unidade),
        quantity=1,
        unit_price=_parse_contratos_unit_price(result.items_adesao),
        valid_until=_parse_contratos_br_date(result.vigencia_final),
        slug=unique_slug(
            slugify(
                f"ata-{result.numero_ata or result.pncp_control_id or result.arp_id or result.id}"
            )
        ),
        highlight=False,
        source_pncp_id=source_id,
        ata_owner_company=(supplier.get("fornecedor")[:200] if supplier else None),
        technical_description=_technical_notes_from_contratos_result(result),
    )
    db.session.add(item)
    db.session.flush()
    result.catalog_item_id = item.id
    return item


def create_opportunity_from_contratos_result(
    result: ContratosGovScanResult,
) -> Opportunity:
    if result.opportunity_id:
        linked = db.session.get(Opportunity, result.opportunity_id)
        if linked:
            return linked

    supplier = result.primary_supplier
    title = f"Adesão ARP {result.numero_ata or result.arp_id}"
    opp = Opportunity(
        title=title[:200],
        organization=(result.unidade or "")[:200] or None,
        cnpj=_normalize_cnpj_field(supplier.get("cnpj") if supplier else None),
        sphere=_sphere_from_contratos_unidade(result.unidade),
        stage="novo",
        source="Robô Contratos.gov.br",
        process_ref=(result.compra_ano or "")[:120] or None,
        notes=_technical_notes_from_contratos_result(result),
    )
    val = _parse_contratos_unit_price(result.items_adesao)
    if val > 0:
        opp.value_brl = val
    db.session.add(opp)
    db.session.flush()
    if result.catalog_item_id:
        cat = db.session.get(CatalogItem, result.catalog_item_id)
        if cat and not any(
            ln.catalog_item_id == cat.id for ln in opp.catalog_lines
        ):
            opp.catalog_lines.append(
                OpportunityCatalogLine(catalog_item_id=cat.id, quantity=1)
            )
    result.opportunity_id = opp.id
    return opp


def _known_contratos_arp_ids(exclude_scan_id: int | None = None) -> set[int]:
    q = db.session.query(ContratosGovScanResult.arp_id).filter(
        ContratosGovScanResult.arp_id.isnot(None)
    )
    if exclude_scan_id is not None:
        q = q.filter(ContratosGovScanResult.scan_id != exclude_scan_id)
    return {int(row[0]) for row in q.distinct().all()}


def _known_pncp_control_ids(exclude_scan_id: int | None = None) -> set[str]:
    q = db.session.query(ContratosGovScanResult.pncp_control_id).filter(
        ContratosGovScanResult.pncp_control_id.isnot(None),
        ContratosGovScanResult.pncp_control_id != "",
    )
    if exclude_scan_id is not None:
        q = q.filter(ContratosGovScanResult.scan_id != exclude_scan_id)
    return {str(row[0]) for row in q.distinct().all()}


def _normalize_cnpj_field(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    return s[:22]


def _normalize_cpf_field(raw: str | None) -> str | None:
    s = re.sub(r"\D", "", (raw or "").strip())
    return s[:11] if s else None


def _normalize_cep_field(raw: str | None) -> str | None:
    s = re.sub(r"\D", "", (raw or "").strip())
    if not s:
        return None
    if len(s) == 8:
        return f"{s[:5]}-{s[5:]}"
    return s[:10]


def _normalize_uf_field(raw: str | None) -> str | None:
    uf = (raw or "").strip().upper()
    if not uf:
        return None
    valid = {x[0] for x in BR_UFS}
    return uf if uf in valid else None


def _normalize_sphere_field(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s in CATALOG_SPHERE_CHOICES:
        return s
    return s[:80]


def _strip_form_field(name: str, max_len: int | None = None) -> str | None:
    s = (request.form.get(name) or "").strip()
    if not s:
        return None
    if max_len is not None:
        s = s[:max_len]
    return s


def _apply_portal_client_profile_from_form(client: PortalClient) -> str | None:
    client.name = _strip_form_field("name", 120) or client.name
    client.organization = _strip_form_field("organization", 200)
    client.razao_social = _strip_form_field("razao_social", 300)
    client.phone = _strip_form_field("phone", 40)
    client.cnpj = _normalize_cnpj_field(request.form.get("cnpj"))
    client.job_title = _strip_form_field("job_title", 120)
    client.sector = _strip_form_field("sector", 120)
    client.sphere = _normalize_sphere_field(request.form.get("sphere"))
    client.address_street = _strip_form_field("address_street", 200)
    client.address_number = _strip_form_field("address_number", 20)
    client.address_complement = _strip_form_field("address_complement", 120)
    client.address_neighborhood = _strip_form_field("address_neighborhood", 120)
    client.address_city = _strip_form_field("address_city", 120)
    client.address_state = _normalize_uf_field(request.form.get("address_state"))
    client.address_zip = _normalize_cep_field(request.form.get("address_zip"))
    return None


def _apply_partner_profile_from_form(partner: Partner) -> None:
    partner.name = _strip_form_field("name", 120) or partner.name
    partner.razao_social = _strip_form_field("razao_social", 300)
    partner.company_name = _strip_form_field("company_name", 200) or _strip_form_field(
        "organization", 200
    )
    if not partner.company_name and partner.razao_social:
        partner.company_name = partner.razao_social[:200]
    partner.phone = _strip_form_field("phone", 40)
    partner.cnpj = _normalize_cnpj_field(request.form.get("cnpj"))
    partner.job_title = _strip_form_field("job_title", 120)
    partner.sector = _strip_form_field("sector", 120)
    partner.website = _strip_form_field("website", 300)
    partner.address_street = _strip_form_field("address_street", 200)
    partner.address_number = _strip_form_field("address_number", 20)
    partner.address_complement = _strip_form_field("address_complement", 120)
    partner.address_neighborhood = _strip_form_field("address_neighborhood", 120)
    partner.address_city = _strip_form_field("address_city", 120)
    partner.address_state = _normalize_uf_field(request.form.get("address_state"))
    partner.address_zip = _normalize_cep_field(request.form.get("address_zip"))
    partner.updated_at = datetime.utcnow()


def _get_logged_portal_client() -> PortalClient | None:
    cid = session.get("client_id")
    if not cid:
        return None
    try:
        return db.session.get(PortalClient, int(cid))
    except (TypeError, ValueError):
        return None


def _delete_portal_client_photo(rel_path: str | None) -> None:
    if not rel_path or not _PORTAL_CLIENT_PHOTO_REL_RE.match(rel_path):
        return
    fname = rel_path.rsplit("/", 1)[-1]
    fp = os.path.join(app.root_path, "static", "uploads", "portal_clients", fname)
    try:
        if os.path.isfile(fp):
            os.remove(fp)
    except OSError:
        pass


def _prepare_portal_client_photo_bytes(raw: bytes) -> tuple[bytes, str] | None:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        ext = _image_ext_from_bytes(raw) or ".jpg"
        return raw, ext
    try:
        src = Image.open(io.BytesIO(raw))
        if getattr(src, "is_animated", False):
            src.seek(0)
        has_alpha = "A" in src.getbands()
        if has_alpha:
            src = src.convert("RGBA")
            bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
            bbox = ImageChops.difference(src, bg).getbbox()
        else:
            src = src.convert("RGB")
            bg = Image.new("RGB", src.size, (255, 255, 255))
            bbox = ImageChops.difference(src, bg).getbbox()
        if bbox:
            src = src.crop(bbox)

        size = PORTAL_CLIENT_PHOTO_SIZE[0]
        src.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (size, size), CATALOG_IMAGE_FRAME_BG)
        x = (size - src.width) // 2
        y = (size - src.height) // 2
        if has_alpha and src.mode == "RGBA":
            canvas.paste(src, (x, y), src)
        else:
            if src.mode != "RGB":
                src = src.convert("RGB")
            canvas.paste(src, (x, y))

        out = io.BytesIO()
        canvas.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), ".jpg"
    except Exception:
        ext = _image_ext_from_bytes(raw) or ".jpg"
        return raw, ext


def _save_portal_client_photo(file_storage, client: PortalClient) -> str | None:
    if not file_storage or not getattr(file_storage, "filename", None):
        return None
    raw_name = secure_filename(file_storage.filename)
    if not raw_name:
        return None
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in PORTAL_CLIENT_PHOTO_EXT:
        return "Use uma foto em JPG, PNG ou WebP."
    try:
        file_storage.seek(0, os.SEEK_END)
        sz = file_storage.tell()
        file_storage.seek(0)
    except OSError:
        return "Não foi possível ler a foto enviada."
    if sz > PORTAL_CLIENT_PHOTO_MAX_BYTES:
        return "A foto deve ter no máximo 3 MB."
    try:
        raw = file_storage.read()
    except OSError:
        return "Não foi possível ler a foto enviada."
    prepared = _prepare_portal_client_photo_bytes(raw)
    if not prepared:
        return "Formato de imagem inválido."
    new_raw, out_ext = prepared
    ensure_portal_client_upload_dir()
    name = f"{uuid.uuid4().hex}{out_ext}"
    path = os.path.join(app.root_path, "static", "uploads", "portal_clients", name)
    try:
        with open(path, "wb") as handle:
            handle.write(new_raw)
    except OSError:
        return "Não foi possível salvar a foto."
    rel = f"{PORTAL_CLIENT_PHOTO_PREFIX}/{name}"
    _delete_portal_client_photo(client.photo_path)
    client.photo_path = rel
    return None


def _apply_rep_commission_from_form(opp: Opportunity) -> None:
    """Grava comissão usando o mesmo parser de moeda BRL do catálogo (ex.: 1.234,56)."""
    raw = request.form.get("rep_commission_brl")
    if (raw or "").strip() == "":
        opp.rep_commission_brl = None
    else:
        parsed = parse_money_brl(raw)
        if parsed is not None:
            opp.rep_commission_brl = parsed
        # Se o texto for inválido, mantém o valor já carregado no objeto (evita apagar silenciosamente).
    note = (request.form.get("rep_commission_note") or "").strip()
    opp.rep_commission_note = note or None


def _normalize_portal_client_email(raw: str | None) -> str:
    return (raw or "").strip().lower()


def _retro_link_opportunities_to_client(client: PortalClient) -> int:
    """Associa oportunidades antigas (mesmo e-mail, sem vínculo) à conta do cliente."""
    em = _normalize_portal_client_email(client.email)
    if not em or "@" not in em:
        return 0
    n = 0
    for opp in Opportunity.query.filter(
        Opportunity.portal_client_id.is_(None),
        Opportunity.email.isnot(None),
    ).all():
        if _normalize_portal_client_email(opp.email) == em:
            opp.portal_client_id = client.id
            n += 1
    return n


def _parse_stock_on_hand_from_form(field: str = "stock_on_hand") -> int:
    raw = (request.form.get(field) or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _parse_catalog_lines_from_form() -> list[tuple[int, int]]:
    raw_json = (request.form.get("catalog_lines_json") or "").strip()
    if raw_json:
        try:
            import json as _json

            data = _json.loads(raw_json)
            if isinstance(data, list):
                seen: set[int] = set()
                out: list[tuple[int, int]] = []
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    raw_id = row.get("id")
                    if raw_id is None:
                        continue
                    try:
                        cid = int(raw_id)
                    except (TypeError, ValueError):
                        continue
                    if cid <= 0 or cid in seen:
                        continue
                    seen.add(cid)
                    try:
                        qty = max(1, int(row.get("qty", 1)))
                    except (TypeError, ValueError):
                        qty = 1
                    out.append((cid, qty))
                return out
        except (ValueError, TypeError):
            pass

    ids = request.form.getlist("catalog_item_id")
    qtys = request.form.getlist("catalog_qty")
    seen: set[int] = set()
    out: list[tuple[int, int]] = []
    for i, raw_id in enumerate(ids):
        part = (raw_id or "").strip()
        if not part.isdigit():
            continue
        cid = int(part)
        if cid in seen:
            continue
        seen.add(cid)
        qty = 1
        if i < len(qtys):
            try:
                qty = max(1, int((qtys[i] or "1").strip()))
            except ValueError:
                qty = 1
        out.append((cid, qty))
    return out


def _parse_catalog_item_ids_from_form() -> list[int]:
    return [cid for cid, _ in _parse_catalog_lines_from_form()]


def _recompute_opportunity_value_from_lines(opp: Opportunity) -> None:
    total = Decimal(0)
    has_price = False
    for line in opp.catalog_lines:
        item = line.catalog_item
        if item is None and line.catalog_item_id:
            item = db.session.get(CatalogItem, line.catalog_item_id)
        if item is not None and item.unit_price is not None:
            has_price = True
            total += Decimal(str(item.unit_price)) * int(line.quantity)
    opp.value_brl = total if has_price else None


def _sync_opportunity_catalog_lines(
    opp: Opportunity, lines: list[tuple[int, int]]
) -> None:
    opp.catalog_lines.clear()
    db.session.flush()

    for cid, qty in lines:
        item = db.session.get(CatalogItem, cid)
        if item is not None:
            opp.catalog_lines.append(
                OpportunityCatalogLine(
                    catalog_item_id=cid,
                    quantity=max(1, int(qty)),
                )
            )

    db.session.flush()
    if opp.catalog_lines:
        _recompute_opportunity_value_from_lines(opp)
    else:
        opp.value_brl = None


def _sync_opportunity_catalog_items(opp: Opportunity, ids: list[int]) -> None:
    _sync_opportunity_catalog_lines(opp, [(i, 1) for i in ids])


def _catalog_lines_for_picker(opp: Opportunity | None) -> list[dict]:
    if opp is None:
        return []
    return [
        {"id": ln.catalog_item_id, "qty": int(ln.quantity)}
        for ln in opp.catalog_lines
    ]


def _parse_pipeline_date(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _pipeline_files_from_request(stage_key: str) -> list:
    field = f"pipeline_files_{stage_key}"
    out = request.files.getlist(field)
    if not out:
        out = request.files.getlist(f"{field}[]")
    return [f for f in out if f and (getattr(f, "filename", None) or "").strip()]


def _sync_opportunity_pipeline_data(opp: Opportunity) -> None:
    from pipeline_stages import STAGE_FIELD_DEFS, normalize_stage_key

    data = dict(opp.pipeline_data)

    raw_json = (request.form.get("pipeline_data_json") or "").strip()
    if raw_json:
        try:
            posted = json.loads(raw_json)
            if isinstance(posted, dict):
                for stage_key, block in posted.items():
                    if stage_key not in STAGE_FIELD_DEFS or not isinstance(block, dict):
                        continue
                    merged = dict(data.get(stage_key) or {})
                    if "date" in block:
                        merged["date"] = _parse_pipeline_date(block.get("date")) if block.get("date") else None
                    if "forecast_date" in block:
                        merged["forecast_date"] = (
                            _parse_pipeline_date(block.get("forecast_date"))
                            if block.get("forecast_date")
                            else None
                        )
                    if "notes" in block:
                        note = (block.get("notes") or "").strip() if block.get("notes") else ""
                        merged["notes"] = note or None
                    if isinstance(block.get("attachments"), list):
                        merged["attachments"] = [
                            a
                            for a in block["attachments"]
                            if isinstance(a, dict) and a.get("relpath")
                        ]
                    has_content = any(
                        [
                            merged.get("attachments"),
                            merged.get("date"),
                            merged.get("forecast_date"),
                            merged.get("notes"),
                        ]
                    )
                    if has_content:
                        data[stage_key] = merged
                    elif stage_key in data:
                        del data[stage_key]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    current_stage = normalize_stage_key(
        request.form.get("stage"), default=normalize_stage_key(opp.stage)
    )
    if current_stage not in STAGE_FIELD_DEFS:
        opp.pipeline_data_json = json.dumps(data, ensure_ascii=False) if data else None
        return

    stage_key = current_stage
    defs = STAGE_FIELD_DEFS[stage_key]
    block = dict(data.get(stage_key) or {})

    if defs.get("attachments"):
        attachments = [
            x
            for x in block.get("attachments") or []
            if isinstance(x, dict) and x.get("relpath")
        ]
        remove_raw = request.form.getlist(f"pipeline_remove_{stage_key}")
        remove_idx = {int(x) for x in remove_raw if str(x).isdigit()}
        if remove_idx:
            attachments = [
                att for i, att in enumerate(attachments) if i not in remove_idx
            ]
        new_files = _pipeline_files_from_request(stage_key)
        if new_files:
            new_atts, err = _save_finance_upload_files(new_files, LEAD_PIPELINE_PREFIX)
            if err:
                flash(err, "error")
            else:
                attachments.extend(new_atts)
        block["attachments"] = attachments

    if defs.get("date"):
        block["date"] = _parse_pipeline_date(
            request.form.get(f"pipeline_date_{stage_key}")
        )

    if defs.get("forecast_date"):
        block["forecast_date"] = _parse_pipeline_date(
            request.form.get(f"pipeline_forecast_{stage_key}")
        )

    if defs.get("notes"):
        note = (request.form.get(f"pipeline_notes_{stage_key}") or "").strip()
        block["notes"] = note or None

    has_content = any(
        [
            block.get("attachments"),
            block.get("date"),
            block.get("forecast_date"),
            block.get("notes"),
        ]
    )
    if has_content:
        data[stage_key] = block
    elif stage_key in data:
        del data[stage_key]

    opp.pipeline_data_json = json.dumps(data, ensure_ascii=False) if data else None


def _catalog_lines_from_cart() -> list[dict]:
    return [{"id": row["id"], "qty": row.get("qty", 1)} for row in _get_client_cart_lines()]


_CRM_SHEETS_HEADER_ALIASES: dict[str, set[str]] = {
    "title": {
        "titulo",
        "title",
        "assunto",
        "oportunidade",
        "negocio",
        "nome_da_oportunidade",
    },
    "contact_name": {
        "contato",
        "nome_contato",
        "contact_name",
        "pessoa",
        "responsavel",
        "responsavel_pela_compra",
    },
    "organization": {
        "organizacao",
        "orgao",
        "empresa",
        "organization",
        "orgao_publico",
        "cliente",
        "razao_social",
    },
    "cnpj": {"cnpj"},
    "email": {"email", "e_mail"},
    "phone": {"telefone", "phone", "tel", "celular"},
    "sphere": {"esfera", "sphere"},
    "stage": {"estagio", "stage", "fase", "status_pipeline"},
    "value_brl": {"valor", "value", "valor_r", "valor_brl", "valor_rs"},
    "notes": {
        "observacoes",
        "observacao",
        "notes",
        "notas",
        "comentarios",
        "descricao",
    },
    "source": {"origem", "source", "fonte", "canal"},
    "rep_email": {
        "representante_email",
        "email_representante",
        "rep_email",
        "vendedor_email",
    },
    "catalog_slugs": {
        "slugs_produtos",
        "produtos",
        "catalogo_slugs",
        "itens_catalogo",
        "sku_slug",
    },
    "catalog_ids": {"ids_catalogo", "ids_produtos", "catalog_ids"},
}

_CRM_IMPORT_MAX_ROWS = 5000


def _crm_sheets_header_key(cell: str) -> str:
    s = (cell or "").strip().lstrip("\ufeff")
    s = "".join(
        c
        for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _crm_sheets_column_map(header_row: list[str]) -> dict[str, int]:
    inv: dict[str, str] = {}
    for canon, aliases in _CRM_SHEETS_HEADER_ALIASES.items():
        for a in aliases:
            inv[a] = canon
    out: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        k = _crm_sheets_header_key(cell)
        if not k:
            continue
        canon = inv.get(k)
        if canon and canon not in out:
            out[canon] = i
    return out


def _crm_import_stage(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "novo"
    if s in LEGACY_STAGE_MAP:
        return LEGACY_STAGE_MAP[s]
    stage_keys = {k for k, _ in STAGES}
    if s in stage_keys:
        return s
    rev = {label.lower(): k for k, label in STAGES}
    if s in rev:
        return rev[s]
    folded = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    rev_fold = {
        "".join(
            c for c in unicodedata.normalize("NFD", label.lower())
            if unicodedata.category(c) != "Mn"
        ): k
        for k, label in STAGES
    }
    if folded in rev_fold:
        return rev_fold[folded]
    return "novo"


def _crm_resolve_rep_id_from_email(email_raw: str | None) -> int | None:
    em = (email_raw or "").strip().lower()
    if not em or "@" not in em:
        return None
    rep = SalesRepresentative.query.filter(
        func.lower(SalesRepresentative.email) == em,
        SalesRepresentative.is_active.is_(True),
    ).first()
    return rep.id if rep else None


def _crm_resolve_catalog_ids_from_slugs(slugs_raw: str | None) -> tuple[list[int], list[str]]:
    if not (slugs_raw or "").strip():
        return [], []
    parts = re.split(r"[,;\n|]+", slugs_raw)
    ids: list[int] = []
    missing: list[str] = []
    seen: set[int] = set()
    for p in parts:
        s = (p or "").strip()
        if not s:
            continue
        item = None
        if s.isdigit():
            item = db.session.get(CatalogItem, int(s))
        if item is None:
            item = CatalogItem.query.filter_by(slug=s).first()
        if item is None:
            missing.append(s)
        elif item.id not in seen:
            seen.add(item.id)
            ids.append(item.id)
    return ids, missing


def _crm_resolve_catalog_ids_from_ids_cell(raw: str | None) -> tuple[list[int], list[str]]:
    if not (raw or "").strip():
        return [], []
    parts = re.split(r"[,;\s]+", raw)
    ids: list[int] = []
    missing: list[str] = []
    seen: set[int] = set()
    for p in parts:
        s = (p or "").strip()
        if not s or not s.isdigit():
            if s:
                missing.append(s)
            continue
        cid = int(s)
        item = db.session.get(CatalogItem, cid)
        if item is None:
            missing.append(s)
        elif cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids, missing


def _crm_import_opportunities_from_csv(text: str) -> tuple[int, list[str], list[str]]:
    """Retorna (criados, erros, avisos)."""
    errors: list[str] = []
    warnings: list[str] = []
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return 0, ["Arquivo vazio."], []
    col = _crm_sheets_column_map(header)
    if "title" not in col and "organization" not in col and "contact_name" not in col:
        errors.append(
            "Cabeçalho não reconhecido. Inclua ao menos uma coluna: título, organização ou contato "
            "(ou baixe o modelo CSV nesta página)."
        )
        return 0, errors, warnings

    created = 0
    line_no = 1
    for row in reader:
        line_no += 1
        if line_no > _CRM_IMPORT_MAX_ROWS + 1:
            warnings.append(f"Limite de {_CRM_IMPORT_MAX_ROWS} linhas; importação interrompida.")
            break

        def cell(field: str) -> str:
            idx = col.get(field)
            if idx is None or idx >= len(row):
                return ""
            return (row[idx] or "").strip()

        title = cell("title") or None
        organization = cell("organization") or None
        contact_name = cell("contact_name") or None
        if not title and not organization and not contact_name:
            continue

        final_title = (title or organization or contact_name or f"Importação linha {line_no}")[:200]
        src = cell("source") or "Google Sheets / CSV"
        opp = Opportunity(
            title=final_title,
            contact_name=contact_name,
            organization=organization,
            cnpj=_normalize_cnpj_field(cell("cnpj")),
            email=cell("email") or None,
            phone=cell("phone") or None,
            sphere=cell("sphere") or None,
            stage=_crm_import_stage(cell("stage")),
            notes=cell("notes") or None,
            source=src[:80],
        )
        raw_val = cell("value_brl")
        if raw_val:
            parsed = parse_money_brl(raw_val)
            if parsed is not None:
                opp.value_brl = parsed
            else:
                warnings.append(f"Linha {line_no}: valor ignorado ({raw_val!r}).")

        rid = _crm_resolve_rep_id_from_email(cell("rep_email"))
        if cell("rep_email") and rid is None:
            warnings.append(
                f"Linha {line_no}: representante não encontrado ou inativo ({cell('rep_email')!r})."
            )
        opp.sales_rep_id = rid

        cat_ids: list[int] = []
        slug_cell = cell("catalog_slugs")
        id_cell = cell("catalog_ids")
        if slug_cell:
            s_ids, miss_s = _crm_resolve_catalog_ids_from_slugs(slug_cell)
            cat_ids.extend(s_ids)
            for m in miss_s:
                warnings.append(f"Linha {line_no}: produto/slug não encontrado ({m!r}).")
        if id_cell:
            i_ids, miss_i = _crm_resolve_catalog_ids_from_ids_cell(id_cell)
            for cid in i_ids:
                if cid not in cat_ids:
                    cat_ids.append(cid)
            for m in miss_i:
                warnings.append(f"Linha {line_no}: id de catálogo inválido ({m!r}).")

        try:
            db.session.add(opp)
            db.session.flush()
            _sync_opportunity_catalog_items(opp, cat_ids)
            db.session.commit()
            created += 1
        except Exception as ex:
            db.session.rollback()
            errors.append(f"Linha {line_no}: {ex}")

    return created, errors, warnings


LEAD_CHAT_THREAD_CLIENT = "client"
LEAD_CHAT_THREAD_INTERNAL = "internal"


def _lead_chat_messages_for_opportunity(
    opp_id: int, thread: str = LEAD_CHAT_THREAD_CLIENT
) -> list[LeadMessage]:
    return (
        LeadMessage.query.filter_by(opportunity_id=opp_id, thread=thread)
        .order_by(LeadMessage.created_at.asc())
        .all()
    )


def _lead_chat_internal_access_allowed(opp: Opportunity) -> bool:
    if session.get("crm_ok"):
        return True
    rid = session.get("rep_id")
    if rid is None:
        return False
    try:
        rid_int = int(rid)
    except (TypeError, ValueError):
        return False
    rep = db.session.get(SalesRepresentative, rid_int)
    if not rep or not rep.is_active:
        return False
    if _rep_is_admin(rep):
        return True
    return opp.sales_rep_id == rid_int


_LEAD_CHAT_MIME_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _lead_chat_ext_from_filename(filename: str | None) -> str | None:
    raw = secure_filename((filename or "").strip())
    if not raw or "." not in raw:
        return None
    ext = os.path.splitext(raw)[1].lower()
    return ext if ext in LEAD_CHAT_ALLOWED_EXT else None


def _lead_chat_ext_from_mimetype(file_storage) -> str | None:
    raw = (getattr(file_storage, "mimetype", None) or "").split(";")[0].strip().lower()
    ext = _LEAD_CHAT_MIME_EXT.get(raw)
    return ext if ext in LEAD_CHAT_ALLOWED_EXT else None


def _lead_chat_sniff_pdf_prefix(file_storage) -> bool:
    try:
        file_storage.stream.seek(0)
        head = file_storage.stream.read(5)
        file_storage.stream.seek(0)
        return bool(head.startswith(b"%PDF"))
    except OSError:
        return False


def _lead_chat_files_from_request() -> list:
    """Coleta uploads do chat (vários browsers / nomes de campo)."""
    lst = request.files.getlist("attachments")
    out = [f for f in lst if f and (getattr(f, "filename", None) or "").strip()]
    if not out:
        lst = request.files.getlist("attachments[]")
        out = [f for f in lst if f and (getattr(f, "filename", None) or "").strip()]
    return out


def _save_lead_chat_files(file_list) -> tuple[list[dict], str | None]:
    """Grava anexos do chat; retorna (lista {relpath, name}, erro)."""
    ensure_lead_chat_upload_dir()
    out: list[dict] = []
    base = os.path.join(app.root_path, "static", "uploads", "lead_chat")
    wanted = sum(
        1 for f in file_list if f and (getattr(f, "filename", None) or "").strip()
    )
    for f in file_list:
        if len(out) >= LEAD_CHAT_MAX_FILES:
            return [], f"Máximo de {LEAD_CHAT_MAX_FILES} arquivos por mensagem."
        if not f or not (getattr(f, "filename", None) or "").strip():
            continue
        ext = _lead_chat_ext_from_filename(f.filename)
        if not ext:
            ext = _lead_chat_ext_from_mimetype(f)
        if not ext and _lead_chat_sniff_pdf_prefix(f):
            ext = ".pdf"
        if not ext:
            return (
                [],
                "Tipo não permitido. Use imagens (JPG, PNG, GIF, WebP) ou PDF/DOC/DOCX.",
            )
        try:
            f.stream.seek(0, os.SEEK_END)
            sz = f.stream.tell()
            f.stream.seek(0)
        except OSError:
            sz = -1
        if sz < 0 or sz > LEAD_CHAT_MAX_BYTES:
            return [], "Cada arquivo deve ter no máximo 15 MB."
        raw_name = (f.filename or "").strip()
        orig = secure_filename(raw_name) or f"anexo{ext}"
        name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(base, name)
        try:
            f.save(path)
        except OSError:
            return [], "Não foi possível salvar o arquivo. Tente novamente."
        out.append({"relpath": f"{LEAD_CHAT_PREFIX}/{name}", "name": orig})
    if wanted > 0 and not out:
        return (
            [],
            "Nenhum arquivo foi salvo. Tente outro PDF ou renomeie o arquivo (ex.: documento.pdf).",
        )
    return out, None


def _lead_chat_relpath_ok(rel: str | None) -> bool:
    if not rel:
        return False
    return bool(_LEAD_CHAT_REL_RE.match(rel.replace("\\", "/").strip()))


def _lead_chat_access_allowed(msg: LeadMessage) -> bool:
    opp = msg.opportunity
    if opp is None:
        return False
    if msg.chat_thread == LEAD_CHAT_THREAD_INTERNAL:
        return _lead_chat_internal_access_allowed(opp)
    if session.get("crm_ok"):
        return True
    rid = session.get("rep_id")
    if rid is not None:
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            return False
        rep = db.session.get(SalesRepresentative, rid_int)
        if rep and rep.is_active and opp.sales_rep_id == rid_int:
            return True
    cid = session.get("client_id")
    if cid is None:
        return False
    try:
        return opp.portal_client_id == int(cid)
    except (TypeError, ValueError):
        return False


def _lead_chat_validate_post() -> tuple[str, list[dict], str | None]:
    """Valida corpo e anexos do chat; retorna (body, attachments, erro)."""
    body = (request.form.get("chat_body") or "").strip()
    files = _lead_chat_files_from_request()
    atts, att_err = _save_lead_chat_files(files)
    if att_err:
        return body, atts, att_err
    if not body and not atts:
        return body, atts, "Escreva uma mensagem ou anexe ao menos um arquivo."
    if len(body) > 12000:
        return body, atts, "Mensagem muito longa (máx. 12.000 caracteres)."
    return body, atts, None


def _lead_chat_delete_disk_files_for_entries(entries: list[dict]) -> None:
    static_root = os.path.normpath(os.path.join(app.root_path, "static"))
    for removed in entries:
        rel = (removed.get("relpath") or "").strip()
        if not _lead_chat_relpath_ok(rel):
            continue
        parts = [p for p in rel.split("/") if p and p not in (".", "..")]
        abs_path = os.path.normpath(os.path.join(app.root_path, "static", *parts))
        if abs_path.startswith(static_root + os.sep) and os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except OSError:
                pass


def _delete_lead_confirmation_ok(raw: str | None) -> bool:
    """Aceita EXCLUIR / excluir; ignora espaços e normaliza Unicode (evita falha por cópia/colagem)."""
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    s = "".join(s.split()).upper()
    return s == "EXCLUIR"


def _crm_delete_opportunity(opp: Opportunity) -> None:
    """Remove oportunidade (lead): anexos do chat no disco, vínculos financeiros e catálogo."""
    oid = opp.id
    for msg in LeadMessage.query.filter_by(opportunity_id=oid).all():
        _lead_chat_delete_disk_files_for_entries(msg.attachment_list)
    db.session.execute(
        update(RepFinancialEntry)
        .where(RepFinancialEntry.opportunity_id == oid)
        .values(opportunity_id=None)
    )
    opp.catalog_lines.clear()
    db.session.delete(opp)
    db.session.commit()


def _finance_ext_from_filename(filename: str | None) -> str | None:
    raw = secure_filename((filename or "").strip())
    if not raw or "." not in raw:
        return None
    ext = os.path.splitext(raw)[1].lower()
    return ext if ext in FINANCE_ALLOWED_EXT else None


_FINANCE_MIME_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/zip": ".zip",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _finance_ext_from_mimetype(file_storage) -> str | None:
    raw = (getattr(file_storage, "mimetype", None) or "").split(";")[0].strip().lower()
    ext = _FINANCE_MIME_EXT.get(raw)
    return ext if ext in FINANCE_ALLOWED_EXT else None


def _save_finance_upload_files(
    file_list, relpath_prefix: str
) -> tuple[list[dict], str | None]:
    ensure_finance_upload_dirs()
    subdir = relpath_prefix.split("/")[-1]
    base = os.path.join(app.root_path, "static", "uploads", subdir)
    out: list[dict] = []
    wanted = sum(
        1 for f in file_list if f and (getattr(f, "filename", None) or "").strip()
    )
    for f in file_list:
        if len(out) >= FINANCE_MAX_FILES:
            return [], f"Máximo de {FINANCE_MAX_FILES} arquivos."
        if not f or not (getattr(f, "filename", None) or "").strip():
            continue
        ext = _finance_ext_from_filename(f.filename)
        if not ext:
            ext = _finance_ext_from_mimetype(f)
        if not ext:
            return (
                [],
                "Tipo não permitido. Use PDF, XML, ZIP, imagens ou Word.",
            )
        try:
            f.stream.seek(0, os.SEEK_END)
            sz = f.stream.tell()
            f.stream.seek(0)
        except OSError:
            sz = -1
        if sz < 0 or sz > FINANCE_MAX_BYTES:
            return [], "Cada arquivo deve ter no máximo 15 MB."
        raw_name = (f.filename or "").strip()
        orig = secure_filename(raw_name) or f"anexo{ext}"
        name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(base, name)
        try:
            f.save(path)
        except OSError:
            return [], "Não foi possível salvar o arquivo."
        out.append({"relpath": f"{relpath_prefix}/{name}", "name": orig})
    if wanted > 0 and not out:
        return [], "Nenhum arquivo foi salvo. Verifique o formato."
    return out, None


def _finance_relpath_ok(rel: str, *, company: bool) -> bool:
    if not rel:
        return False
    r = rel.replace("\\", "/").strip()
    return bool(
        (_FINANCE_COMPANY_REL_RE if company else _FINANCE_REP_REL_RE).match(r)
    )


def _finance_abs_path(rel: str) -> str | None:
    parts = [p for p in rel.split("/") if p and p not in (".", "..")]
    abs_path = os.path.normpath(os.path.join(app.root_path, "static", *parts))
    static_root = os.path.normpath(os.path.join(app.root_path, "static"))
    if not abs_path.startswith(static_root + os.sep) or not os.path.isfile(abs_path):
        return None
    return abs_path


def _finance_delete_disk_attachments(entries: list[dict]) -> None:
    for e in entries:
        rel = (e.get("relpath") or "").strip()
        if not rel:
            continue
        p = _finance_abs_path(rel)
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _finance_send_attachment_download(rel: str, download_name: str, *, company: bool):
    if not _finance_relpath_ok(rel, company=company):
        abort(404)
    abs_path = _finance_abs_path(rel)
    if not abs_path:
        abort(404)
    return send_file(
        abs_path,
        as_attachment=True,
        download_name=download_name or os.path.basename(rel),
        max_age=0,
        conditional=True,
    )


def _crm_active_sales_reps() -> list[SalesRepresentative]:
    return (
        SalesRepresentative.query.filter_by(is_active=True)
        .order_by(SalesRepresentative.name.asc(), SalesRepresentative.id.asc())
        .all()
    )


def _apply_sales_rep_from_form(opp: Opportunity) -> None:
    raw = (request.form.get("sales_rep_id") or "").strip()
    if not raw:
        opp.sales_rep_id = None
        return
    if not raw.isdigit():
        opp.sales_rep_id = None
        return
    rep = db.session.get(SalesRepresentative, int(raw))
    if rep is not None and rep.is_active:
        opp.sales_rep_id = rep.id
    else:
        opp.sales_rep_id = None


def _crm_catalog_choices():
    return CatalogItem.query.order_by(CatalogItem.section, CatalogItem.title).all()


def _catalog_picker_item_json(item: CatalogItem) -> dict:
    img = item.image_paths[0] if item.image_paths else None
    unit = float(item.unit_price) if item.unit_price is not None else None
    return {
        "id": item.id,
        "title": item.title,
        "slug": item.slug,
        "sphere": item.sphere,
        "manufacturer": item.manufacturer or "",
        "section": item.section,
        "unit_price": unit,
        "unit_price_label": _format_currency_brl(item.unit_price),
        "valid_until": item.valid_until.strftime("%d/%m/%Y") if item.valid_until else "",
        "image_url": url_for("static", filename=img) if img else "",
        "ata_owner_company": item.ata_owner_company or "",
        "highlight": bool(item.highlight),
    }


@app.route("/api/lookup/cep/<cep>")
def api_lookup_cep(cep: str):
    from br_lookup import lookup_cep

    data = lookup_cep(cep)
    if not data:
        return jsonify({"ok": False, "error": "CEP não encontrado."}), 404
    return jsonify({"ok": True, "data": data})


@app.route("/api/lookup/cnpj/<cnpj>")
def api_lookup_cnpj(cnpj: str):
    from br_lookup import lookup_cnpj

    data = lookup_cnpj(cnpj)
    if not data:
        return jsonify({"ok": False, "error": "CNPJ não encontrado ou indisponível."}), 404
    return jsonify({"ok": True, "data": data})


@app.route("/api/catalog-picker")
def api_catalog_picker():
    ids_raw = (request.args.get("ids") or "").strip()
    if ids_raw:
        id_list: list[int] = []
        for part in ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                id_list.append(int(part))
        if not id_list:
            return jsonify({"items": [], "total": 0, "page": 1, "pages": 1, "per_page": 0})
        by_id = {
            row.id: row
            for row in CatalogItem.query.filter(CatalogItem.id.in_(id_list)).all()
        }
        items = [_catalog_picker_item_json(by_id[i]) for i in id_list if i in by_id]
        return jsonify(
            {
                "items": items,
                "total": len(items),
                "page": 1,
                "pages": 1,
                "per_page": len(items),
            }
        )

    q = (request.args.get("q") or "").strip()
    sphere = (request.args.get("sphere") or "").strip()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    try:
        per_page = min(30, max(5, int(request.args.get("per_page") or 15)))
    except ValueError:
        per_page = 15

    if len(q) < 2 and not sphere:
        return jsonify(
            {
                "items": [],
                "total": 0,
                "page": 1,
                "pages": 0,
                "per_page": per_page,
                "hint": "Digite ao menos 2 caracteres ou escolha uma esfera para buscar.",
            }
        )

    query = CatalogItem.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                CatalogItem.title.ilike(like),
                CatalogItem.manufacturer.ilike(like),
                CatalogItem.slug.ilike(like),
                CatalogItem.section.ilike(like),
            )
        )
    if sphere:
        query = query.filter(CatalogItem.sphere == sphere)

    pagination = (
        query.order_by(CatalogItem.highlight.desc(), CatalogItem.title.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return jsonify(
        {
            "items": [_catalog_picker_item_json(row) for row in pagination.items],
            "total": pagination.total,
            "page": pagination.page,
            "pages": pagination.pages,
            "per_page": per_page,
        }
    )


def _portal_client_picker_json(client: PortalClient) -> dict:
    org = (client.organization or client.razao_social or "").strip()
    cnpj_display = client.cnpj or ""
    if cnpj_display and len(re.sub(r"\D", "", cnpj_display)) == 14:
        cnpj_display = _format_cnpj_display(re.sub(r"\D", "", cnpj_display))
    return {
        "id": client.id,
        "name": client.name,
        "email": client.email or "",
        "organization": org,
        "cnpj": cnpj_display,
        "phone": client.phone or "",
        "sphere": client.sphere or "",
    }


@app.route("/api/portal-client-picker")
def api_portal_client_picker():
    if not session.get("rep_id") and not session.get("crm_ok"):
        return jsonify({"error": "Não autorizado"}), 403

    ids_raw = (request.args.get("ids") or "").strip()
    if ids_raw:
        id_list: list[int] = []
        for part in ids_raw.split(","):
            part = part.strip()
            if part.isdigit():
                id_list.append(int(part))
        if not id_list:
            return jsonify({"items": [], "total": 0, "page": 1, "pages": 1, "per_page": 0})
        by_id = {
            row.id: row
            for row in PortalClient.query.filter(PortalClient.id.in_(id_list)).all()
        }
        items = [_portal_client_picker_json(by_id[i]) for i in id_list if i in by_id]
        return jsonify(
            {
                "items": items,
                "total": len(items),
                "page": 1,
                "pages": 1,
                "per_page": len(items),
            }
        )

    q = (request.args.get("q") or "").strip()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    try:
        per_page = min(30, max(5, int(request.args.get("per_page") or 15)))
    except ValueError:
        per_page = 15

    if len(q) < 2:
        return jsonify(
            {
                "items": [],
                "total": 0,
                "page": 1,
                "pages": 0,
                "per_page": per_page,
                "hint": "Digite ao menos 2 caracteres para buscar.",
            }
        )

    rep_for_q = None
    if not (session.get("crm_ok") or session.get("admin_ok")):
        rep_for_q = _session_sales_rep()
    pagination = _comercial_portal_clients_query(q, rep=rep_for_q).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return jsonify(
        {
            "items": [_portal_client_picker_json(row) for row in pagination.items],
            "total": pagination.total,
            "page": pagination.page,
            "pages": pagination.pages,
            "per_page": per_page,
        }
    )


def _partner_allowed_ufs_from_form() -> list[str]:
    if request.form.get("venda_nacional") == "1":
        return ["BR"]
    valid = {x[0] for x in BR_UFS}
    seen: set[str] = set()
    out: list[str] = []
    for u in request.form.getlist("allowed_uf"):
        u = (u or "").strip().upper()
        if u in valid and u not in seen:
            seen.add(u)
            out.append(u)
    return sorted(out)


def _parse_partner_commission_rows_from_form() -> tuple[list[dict], str | None]:
    ids = request.form.getlist("commission_catalog_id")
    brls = request.form.getlist("commission_brl")
    pcts = request.form.getlist("commission_percent")
    notes = request.form.getlist("commission_note")
    seen_cat: set[int] = set()
    rows: list[dict] = []
    for i, cid in enumerate(ids):
        cid = (cid or "").strip()
        if not cid.isdigit():
            continue
        cat_id = int(cid)
        if cat_id in seen_cat:
            return [], "Cada ARP (item do catálogo) só pode aparecer uma vez na lista de comissões."
        seen_cat.add(cat_id)
        brl_raw = brls[i] if i < len(brls) else ""
        pct_raw = pcts[i] if i < len(pcts) else ""
        note_raw = (notes[i] if i < len(notes) else "") or ""
        note = note_raw.strip() or None
        brl = parse_money_brl(brl_raw)
        pct_val = None
        ps = (pct_raw or "").strip().replace(",", ".")
        if ps:
            try:
                p = Decimal(ps)
                if p < 0 or p > 100:
                    return [], "Percentual de comissão deve estar entre 0 e 100."
                pct_val = p
            except Exception:
                return [], "Percentual de comissão inválido."
        if brl is None and pct_val is None and not note:
            continue
        if brl is not None and pct_val is not None:
            return (
                [],
                "Para cada ARP, use apenas valor em R$ ou percentual — não os dois ao mesmo tempo.",
            )
        rows.append(
            {
                "catalog_item_id": cat_id,
                "commission_brl": brl,
                "commission_percent": pct_val,
                "note": note,
            }
        )
    return rows, None


def _replace_partner_product_commissions(
    product: PartnerProduct, rows: list[dict]
) -> None:
    PartnerProductArpCommission.query.filter_by(
        partner_product_id=product.id
    ).delete(synchronize_session=False)
    for r in rows:
        if db.session.get(CatalogItem, r["catalog_item_id"]) is None:
            continue
        db.session.add(
            PartnerProductArpCommission(
                partner_product_id=product.id,
                catalog_item_id=r["catalog_item_id"],
                commission_brl=r["commission_brl"],
                commission_percent=r["commission_percent"],
                note=r["note"],
            )
        )


def _merge_pncp_fontes(existing: str | None, new_fonte: str) -> str:
    parts = set((existing or "").split(";"))
    parts.discard("")
    parts.add(new_fonte)
    return ";".join(sorted(parts))


def _upsert_pncp_org_row(payload: dict) -> str:
    """Insere ou mescla registro de órgão PNCP. Retorna 'insert' ou 'update'."""
    row = PncpOrgaoUnidade.query.filter_by(
        cnpj=payload["cnpj"], codigo_unidade=payload["codigo_unidade"]
    ).first()
    fonte = (payload.get("fonte") or "api").strip()
    if row is None:
        db.session.add(
            PncpOrgaoUnidade(
                cnpj=payload["cnpj"],
                codigo_unidade=payload["codigo_unidade"],
                razao_social=(payload.get("razao_social") or "Órgão")[:320],
                nome_unidade=payload.get("nome_unidade"),
                uf_sigla=payload.get("uf_sigla"),
                municipio_nome=payload.get("municipio_nome"),
                codigo_municipio_ibge=payload.get("codigo_municipio_ibge"),
                esfera_id=payload.get("esfera_id"),
                poder_id=payload.get("poder_id"),
                fontes=fonte,
            )
        )
        return "insert"
    rz = payload.get("razao_social") or ""
    if rz and len(rz) > len(row.razao_social or ""):
        row.razao_social = rz[:320]
    for fld in (
        "nome_unidade",
        "uf_sigla",
        "municipio_nome",
        "codigo_municipio_ibge",
        "esfera_id",
        "poder_id",
    ):
        v = payload.get(fld)
        if v and not getattr(row, fld):
            setattr(row, fld, v)
    row.fontes = _merge_pncp_fontes(row.fontes, fonte)
    return "update"


def _format_cnpj_display(digits14: str) -> str:
    d = re.sub(r"\D", "", digits14 or "")
    if len(d) != 14:
        return digits14 or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"


def _format_decimal_brl(val, empty: str = "—") -> str:
    if val is None:
        return empty
    try:
        d = Decimal(str(val))
        s = f"{d:,.2f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return empty


def _format_currency_brl(val) -> str:
    num = _format_decimal_brl(val)
    if num == "—":
        return num
    return f"R$ {num}"


def _unit_price_form_value(val) -> str:
    return _format_decimal_brl(val, "")


@app.template_filter("brl")
def _jinja_filter_brl(val):
    return _format_currency_brl(val)


@app.template_filter("brl_decimal")
def _jinja_filter_brl_decimal(val):
    return _format_decimal_brl(val, "")


def _mercado_chart_payload(snap: PncpMercadoSnapshot | None) -> dict:
    """JSON para gráficos na área do cliente (Chart.js)."""
    if snap is None:
        return {}
    try:
        return {
            "categorias": json.loads(snap.json_categorias or "[]"),
            "tipos_contrato": json.loads(snap.json_tipos_contrato or "[]"),
            "esfera": json.loads(snap.json_esfera or "[]"),
            "keywords": json.loads(snap.json_keywords_objeto or "[]"),
        }
    except json.JSONDecodeError:
        return {}


def _dedupe_org_payloads(payloads: list[dict]) -> list[dict]:
    """Uma entrada por (CNPJ, código unidade); mantém a última ocorrência."""
    od: dict[tuple[str, str], dict] = {}
    for p in payloads:
        od[(p["cnpj"], p["codigo_unidade"])] = p
    return list(od.values())


def parse_optional_date(s: str) -> date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_money_brl(raw: str | None) -> Decimal | None:
    """Aceita 1234,56 · 1.234,56 · R$ 100,00 · 1234.56 (ponto decimal)."""
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"^\s*R\$\s*", "", s, flags=re.I).strip()
    s = s.replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    if "," in s:
        last = s.rfind(",")
        int_part = s[:last].replace(".", "")
        dec_part = s[last + 1 :].replace(".", "")
        if not int_part.isdigit() or not dec_part.isdigit():
            return None
        dec_part = (dec_part + "00")[:2]
        try:
            return Decimal(f"{int_part}.{dec_part}")
        except Exception:
            return None
    if s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except Exception:
        return None


def _delete_catalog_upload_file(rel_path: str) -> None:
    if not rel_path or not rel_path.startswith("uploads/catalog/"):
        return
    fname = rel_path.split("/")[-1]
    if not re.match(r"^[a-f0-9]{32}\.(png|jpg|jpeg|webp|gif)$", fname, re.I):
        return
    fp = os.path.join(app.root_path, "static", "uploads", "catalog", fname)
    if os.path.isfile(fp):
        try:
            os.remove(fp)
        except OSError:
            pass


def _delete_catalog_attachment_file(rel_path: str) -> None:
    if not rel_path or not rel_path.startswith(f"{CATALOG_ATTACHMENTS_PREFIX}/"):
        return
    fname = rel_path.split("/")[-1]
    if not _CATALOG_DOC_FILENAME_RE.match(fname):
        return
    fp = os.path.join(app.root_path, "static", *rel_path.split("/"))
    if os.path.isfile(fp):
        try:
            os.remove(fp)
        except OSError:
            pass


def _delete_ata_company_doc_file(rel_path: str) -> None:
    if not rel_path or not rel_path.startswith(f"{ATA_COMPANY_DOCS_PREFIX}/"):
        return
    fname = rel_path.split("/")[-1]
    if not _CATALOG_DOC_FILENAME_RE.match(fname):
        return
    fp = os.path.join(app.root_path, "static", *rel_path.split("/"))
    if os.path.isfile(fp):
        try:
            os.remove(fp)
        except OSError:
            pass


def _delete_catalog_item_disk_files(item: CatalogItem) -> None:
    for p in item.image_paths:
        _delete_catalog_upload_file(p)
    for p in item.catalog_attachment_paths:
        _delete_catalog_attachment_file(p)
    for p in item.ata_company_doc_paths:
        _delete_ata_company_doc_file(p)


def _delete_partner_product_draft_files(pp: PartnerProduct) -> None:
    for p in pp.draft_image_paths():
        _delete_catalog_upload_file(p)
    for p in pp.draft_catalog_attachment_paths():
        _delete_catalog_attachment_file(p)
    for p in pp.draft_ata_company_doc_paths():
        _delete_ata_company_doc_file(p)


def _unlink_opportunities_for_catalog_ids(catalog_ids: list[int]) -> None:
    if not catalog_ids:
        return
    db.session.execute(
        delete(opportunity_catalog_items).where(
            opportunity_catalog_items.c.catalog_item_id.in_(catalog_ids)
        )
    )
    OpportunityCatalogLine.query.filter(
        OpportunityCatalogLine.catalog_item_id.in_(catalog_ids)
    ).delete(synchronize_session=False)


def _save_catalog_document_uploads(
    file_list,
    static_subdir: str,
    url_prefix: str,
    max_total: int,
    already: int,
) -> list[str]:
    if static_subdir == "catalog_attachments":
        ensure_catalog_attachments_dir()
    else:
        ensure_ata_company_docs_dir()
    out: list[str] = []
    base = os.path.join(app.root_path, "static", "uploads", static_subdir)
    for f in file_list:
        if already + len(out) >= max_total:
            flash("Limite de anexos atingido neste campo.", "error")
            break
        if not f or not getattr(f, "filename", None):
            continue
        raw = secure_filename(f.filename)
        if not raw:
            continue
        ext = os.path.splitext(raw)[1].lower()
        if ext not in CATALOG_DOC_EXT:
            continue
        try:
            f.seek(0, os.SEEK_END)
            sz = f.tell()
            f.seek(0)
        except OSError:
            continue
        if sz > MAX_DOC_UPLOAD_BYTES:
            flash("Arquivo acima de 15 MB foi ignorado.", "error")
            continue
        name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(base, name)
        try:
            f.save(path)
            out.append(f"{url_prefix}/{name}")
        except OSError:
            pass
    return out


def _save_catalog_uploads(file_list) -> list[str]:
    ensure_catalog_upload_dir()
    out: list[str] = []
    base = os.path.join(app.root_path, "static", "uploads", "catalog")
    for f in file_list:
        if len(out) >= CATALOG_IMAGES_MAX:
            break
        if not f or not getattr(f, "filename", None):
            continue
        raw = secure_filename(f.filename)
        if not raw:
            continue
        ext = os.path.splitext(raw)[1].lower()
        if ext not in CATALOG_IMAGE_EXT:
            continue
        name = f"{uuid.uuid4().hex}{ext}"
        path = os.path.join(base, name)
        try:
            f.save(path)
            rel = _normalize_catalog_image_file_on_disk(path) or f"uploads/catalog/{name}"
            out.append(rel)
        except OSError:
            pass
    return out


def _finalize_catalog_images_json(item: CatalogItem | None) -> str | None:
    old = list(item.image_paths) if item else []
    remove = set(request.form.getlist("remove_image"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_catalog_upload_file(p)
    new_paths.extend(_save_catalog_uploads(request.files.getlist("images")))
    for raw in request.form.getlist("prefetch_image"):
        p = (raw or "").strip()
        if (
            p
            and _PREFETCH_CATALOG_IMG_RE.match(p)
            and p not in new_paths
            and len(new_paths) < CATALOG_IMAGES_MAX
        ):
            disk = os.path.join(app.root_path, "static", *p.split("/"))
            if os.path.isfile(disk):
                new_paths.append(p)
    if len(new_paths) > CATALOG_IMAGES_MAX:
        new_paths = new_paths[:CATALOG_IMAGES_MAX]
        flash(f"No máximo {CATALOG_IMAGES_MAX} imagens por produto.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def _finalize_catalog_attachments_json(item: CatalogItem | None) -> str | None:
    old = list(item.catalog_attachment_paths) if item else []
    remove = set(request.form.getlist("remove_catalog_attachment"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_catalog_attachment_file(p)
    new_paths.extend(
        _save_catalog_document_uploads(
            request.files.getlist("catalog_attachments"),
            "catalog_attachments",
            CATALOG_ATTACHMENTS_PREFIX,
            CATALOG_ATTACHMENTS_MAX,
            len(new_paths),
        )
    )
    if len(new_paths) > CATALOG_ATTACHMENTS_MAX:
        new_paths = new_paths[:CATALOG_ATTACHMENTS_MAX]
        flash(f"No máximo {CATALOG_ATTACHMENTS_MAX} anexos de catálogo.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def _finalize_ata_company_docs_json(item: CatalogItem | None) -> str | None:
    old = list(item.ata_company_doc_paths) if item else []
    remove = set(request.form.getlist("remove_ata_company_doc"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_ata_company_doc_file(p)
    new_paths.extend(
        _save_catalog_document_uploads(
            request.files.getlist("ata_company_docs"),
            "ata_company_docs",
            ATA_COMPANY_DOCS_PREFIX,
            ATA_COMPANY_DOCS_MAX,
            len(new_paths),
        )
    )
    if len(new_paths) > ATA_COMPANY_DOCS_MAX:
        new_paths = new_paths[:ATA_COMPANY_DOCS_MAX]
        flash(f"No máximo {ATA_COMPANY_DOCS_MAX} documentos da empresa.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def save_catalog_item_from_request(
    item: CatalogItem | None = None,
) -> tuple[CatalogItem | None, bool]:
    """Cria ou atualiza produto do catálogo a partir do POST (inclui imagens)."""
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Informe o título do produto.", "error")
        return item, False

    section = (request.form.get("section") or "").strip() or "ATA em destaque"
    sphere = _parse_sphere_from_form()
    if not sphere:
        flash("Selecione a esfera (Federal, Estadual, Municipal, Sistema-S ou Autarquia).", "error")
        return item, False
    raw_slug = (request.form.get("slug") or "").strip()
    base_slug = slugify(raw_slug or title)

    qty_raw = (request.form.get("quantity") or "1").strip()
    try:
        quantity = max(1, int(qty_raw))
    except ValueError:
        quantity = 1
    stock_on_hand = _parse_stock_on_hand_from_form()
    raw_price = (request.form.get("unit_price") or "").strip()
    if item is None:
        unit_price = parse_money_brl(raw_price)
        if unit_price is None:
            flash(
                "Preço inválido. Exemplos válidos: 1234,56 · 1.234,56 · R$ 100,00 · 1234.56",
                "error",
            )
            return item, False
    elif raw_price:
        unit_price = parse_money_brl(raw_price)
        if unit_price is None:
            flash(
                "Preço inválido. Exemplos: 1234,56 · 1.234,56 · R$ 100,00 · 1234.56",
                "error",
            )
            return item, False
    else:
        unit_price = item.unit_price

    valid_until = parse_optional_date(request.form.get("valid_until") or "")
    highlight = request.form.get("highlight") == "1"
    ata_owner = _ata_owner_from_partner_form()
    manufacturer = _manufacturer_from_form()
    source_product_url = _source_product_url_from_form()
    pncp_url = _pncp_url_from_form()
    contract_page_url = _contract_page_url_from_form()
    warranty = _warranty_from_form()
    technical_description = _technical_description_from_form()
    category_id = _parse_optional_category_id(request.form.get("category_id"))

    if item is None:
        item = CatalogItem(
            category_id=category_id,
            title=title,
            section=section,
            sphere=sphere,
            quantity=quantity,
            stock_on_hand=stock_on_hand,
            unit_price=unit_price,
            valid_until=valid_until,
            slug=unique_slug(base_slug),
            highlight=highlight,
            ata_owner_company=ata_owner,
            manufacturer=manufacturer,
            source_product_url=source_product_url,
            pncp_url=pncp_url,
            contract_page_url=contract_page_url,
            warranty=warranty,
            technical_description=technical_description,
        )
        db.session.add(item)
        db.session.flush()
    else:
        item.title = title
        item.section = section
        item.sphere = sphere
        if base_slug != item.slug:
            item.slug = unique_slug(base_slug, exclude_id=item.id)
        item.quantity = quantity
        item.stock_on_hand = stock_on_hand
        item.unit_price = unit_price
        item.valid_until = valid_until
        item.highlight = highlight
        item.category_id = category_id
        item.ata_owner_company = ata_owner
        item.manufacturer = manufacturer
        item.source_product_url = source_product_url
        item.pncp_url = pncp_url
        item.contract_page_url = contract_page_url
        item.warranty = warranty
        item.technical_description = technical_description

    item.images_json = _finalize_catalog_images_json(item)
    item.catalog_attachments_json = _finalize_catalog_attachments_json(item)
    item.ata_company_docs_json = _finalize_ata_company_docs_json(item)
    db.session.commit()
    return item, True


def _prepare_catalog_image_bytes(raw: bytes) -> tuple[bytes, str] | None:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        ext = _image_ext_from_bytes(raw) or ".jpg"
        return raw, ext
    try:
        src = Image.open(io.BytesIO(raw))
        if getattr(src, "is_animated", False):
            src.seek(0)
        has_alpha = "A" in src.getbands()
        if has_alpha:
            src = src.convert("RGBA")
            bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
            bbox = ImageChops.difference(src, bg).getbbox()
        else:
            src = src.convert("RGB")
            bg = Image.new("RGB", src.size, (255, 255, 255))
            bbox = ImageChops.difference(src, bg).getbbox()
        if bbox:
            src = src.crop(bbox)

        frame_w, frame_h = CATALOG_IMAGE_FRAME_SIZE
        src.thumbnail((frame_w, frame_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (frame_w, frame_h), CATALOG_IMAGE_FRAME_BG)
        x = (frame_w - src.width) // 2
        y = (frame_h - src.height) // 2
        if has_alpha and src.mode == "RGBA":
            canvas.paste(src, (x, y), src)
        else:
            if src.mode != "RGB":
                src = src.convert("RGB")
            canvas.paste(src, (x, y))

        out = io.BytesIO()
        canvas.save(out, format="JPEG", quality=88, optimize=True)
        return out.getvalue(), ".jpg"
    except Exception:
        ext = _image_ext_from_bytes(raw) or ".jpg"
        return raw, ext


def _normalize_catalog_image_file_on_disk(fp: str) -> str | None:
    try:
        with open(fp, "rb") as handle:
            raw = handle.read()
        prepared = _prepare_catalog_image_bytes(raw)
        if not prepared:
            return None
        new_raw, ext = prepared
        new_name = f"{uuid.uuid4().hex}{ext}"
        new_fp = os.path.join(os.path.dirname(fp), new_name)
        with open(new_fp, "wb") as handle:
            handle.write(new_raw)
        try:
            os.remove(fp)
        except OSError:
            pass
        return f"uploads/catalog/{new_name}"
    except OSError:
        return None


def _save_bytes_to_catalog_image(raw: bytes, ext: str = ".png") -> str | None:
    prepared = _prepare_catalog_image_bytes(raw)
    if prepared:
        raw, ext = prepared
    ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    if ext not in CATALOG_IMAGE_EXT:
        ext = ".png"
    ensure_catalog_upload_dir()
    name = f"{uuid.uuid4().hex}{ext}"
    rel = f"uploads/catalog/{name}"
    fp = os.path.join(app.root_path, "static", "uploads", "catalog", name)
    try:
        with open(fp, "wb") as out:
            out.write(raw)
        return rel
    except OSError:
        return None


def _browser_headers(
    *,
    referer: str | None = None,
    accept_images: bool = False,
    image_url: str | None = None,
) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if accept_images:
        headers["Accept"] = "image/avif,image/webp,image/apng,image/png,image/jpeg,*/*;q=0.8"
    else:
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        )
    if referer:
        headers["Referer"] = referer
        try:
            p = urlparse(referer)
            if p.scheme and p.netloc:
                headers["Origin"] = f"{p.scheme}://{p.netloc}"
        except Exception:
            pass
    if accept_images:
        headers["Sec-Fetch-Dest"] = "image"
        headers["Sec-Fetch-Mode"] = "no-cors"
        site = "cross-site"
        if referer and image_url:
            try:
                ref_h = (urlparse(referer).hostname or "").lower()
                img_h = (urlparse(image_url).hostname or "").lower()
                if ref_h and img_h:
                    if ref_h == img_h:
                        site = "same-origin"
                    elif img_h.endswith(ref_h) or ref_h.endswith(img_h):
                        site = "same-site"
            except Exception:
                pass
        headers["Sec-Fetch-Site"] = site
    return headers


def _image_ext_from_bytes(raw: bytes) -> str | None:
    if len(raw) < 12:
        return None
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return None


def _image_ext_from_url(url: str) -> str | None:
    path = (urlparse(url).path or "").lower()
    for ext in (".jpeg", ".jpg", ".png", ".webp", ".gif"):
        if path.endswith(ext) or f"{ext}?" in path:
            return ".jpg" if ext == ".jpeg" else ext
    return None


def _largest_url_from_srcset(srcset: str, base_url: str) -> str | None:
    best_url = None
    best_w = -1
    for part in (srcset or "").split(","):
        chunk = part.strip().split()
        if not chunk:
            continue
        u = urljoin(base_url, chunk[0].strip())
        w = 0
        if len(chunk) > 1 and chunk[1].endswith("w"):
            try:
                w = int(chunk[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best_w = w
            best_url = u
    return best_url


def _download_url_to_catalog_image(
    url: str,
    referer: str | None = None,
    session: requests.Session | None = None,
) -> str | None:
    ensure_catalog_upload_dir()
    url = _upgrade_manufacturer_image_url(url)
    client = session or requests
    referers: list[str | None] = []
    if referer:
        referers.append(referer)
    try:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}/"
        if origin not in referers:
            referers.append(origin)
    except Exception:
        pass
    referers.append(None)

    for ref in referers:
        headers = _browser_headers(referer=ref, accept_images=True, image_url=url)
        try:
            r = client.get(url, timeout=60, headers=headers, allow_redirects=True)
            if r.status_code in (401, 403, 406) and ref is not None:
                continue
            r.raise_for_status()
            raw = r.content or b""
            if len(raw) < 200:
                continue
            ct = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
            ext = None
            if "jpeg" in ct or "jpg" in ct:
                ext = ".jpg"
            elif "png" in ct:
                ext = ".png"
            elif "webp" in ct:
                ext = ".webp"
            elif "gif" in ct:
                ext = ".gif"
            if not ext:
                ext = _image_ext_from_bytes(raw)
            if not ext:
                ext = _image_ext_from_url(url)
            magic = _image_ext_from_bytes(raw)
            if not magic and "image" not in ct and ct not in (
                "application/octet-stream",
                "binary/octet-stream",
                "",
            ):
                continue
            if not ext and magic:
                ext = magic
            if not ext:
                continue
            return _save_bytes_to_catalog_image(raw, ext)
        except Exception:
            continue
    return None


class _ProductPageImageParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_d = {k.lower(): v for k, v in attrs if v}
        if tag == "meta":
            prop = (attrs_d.get("property") or attrs_d.get("name") or "").lower()
            if prop in ("og:image", "twitter:image", "og:image:url"):
                content = (attrs_d.get("content") or "").strip()
                if content:
                    self.urls.append(urljoin(self.base_url, content))
        elif tag == "img":
            for key in (
                "src",
                "data-src",
                "data-lazy-src",
                "data-original",
                "data-zoom-image",
                "data-large-image",
                "data-full",
            ):
                val = (attrs_d.get(key) or "").strip()
                if val:
                    self.urls.append(urljoin(self.base_url, val))
            for key in ("srcset", "data-srcset"):
                srcset = (attrs_d.get(key) or "").strip()
                if srcset:
                    largest = _largest_url_from_srcset(srcset, self.base_url)
                    if largest:
                        self.urls.append(largest)
        elif tag == "link":
            rel = (attrs_d.get("rel") or "").lower()
            if "image_src" in rel or (rel == "preload" and attrs_d.get("as") == "image"):
                href = (attrs_d.get("href") or "").strip()
                if href:
                    self.urls.append(urljoin(self.base_url, href))
        elif tag == "source":
            srcset = (attrs_d.get("srcset") or "").strip()
            if srcset:
                largest = _largest_url_from_srcset(srcset, self.base_url)
                if largest:
                    self.urls.append(largest)


def _is_public_http_url(url: str) -> bool:
    try:
        p = urlparse((url or "").strip())
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        if not host:
            return False
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"):
            return False
        if host.endswith(".local") or host.endswith(".internal"):
            return False
        if host.startswith("192.168.") or host.startswith("10.") or host.startswith("169.254."):
            return False
        if host.startswith("172."):
            parts = host.split(".")
            if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
                return False
        # Resolve DNS e bloqueia IPs privados / link-local / loopback
        try:
            import ipaddress
            import socket

            for info in socket.getaddrinfo(host, None):
                ip = ipaddress.ip_address(info[4][0])
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return False
        except OSError:
            return False
        return True
    except Exception:
        return False


def _normalize_product_image_candidate(url: str) -> str | None:
    u = (url or "").strip().strip("'\"")
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        u = "https:" + u
    low = u.lower()
    if low.endswith(".svg") or ".svg?" in low:
        return None
    if any(low.endswith(ext) for ext in (".ico", ".bmp")):
        return None
    path = (urlparse(u).path or "").lower()
    name = path.rsplit("/", 1)[-1]
    if any(tok in name for tok in ("logo", "icon", "sprite", "favicon", "avatar", "pixel", "spinner")):
        return None
    if any(tok in path for tok in ("/flags/", "/plugins/polylang/", "/header/", "/footer/")):
        return None
    if any(tok in path for tok in ("/shareresource/", "/processor", "/badge/", "/metodo-de-pago/")):
        return None
    if "1x1" in low or "tracking" in low:
        return None
    return u


def _coerce_remote_image_url(raw: str, page_url: str) -> str | None:
    u = (raw or "").strip().strip("'\"")
    if not u or u.startswith("data:"):
        return None
    if u.startswith("//"):
        u = "https:" + u
    elif not u.startswith("http"):
        u = urljoin(page_url, u)
    try:
        parts = urlparse(u)
        clean_path = re.sub(r"/{2,}", "/", parts.path or "")
        u = parts._replace(path=clean_path).geturl()
    except Exception:
        pass
    return u


def _upgrade_manufacturer_image_url(url: str) -> str:
    low = (url or "").lower()
    if "static.pub" in low and "/fes/cms/" in low:
        return f"{url.split('?')[0]}?width=1200&height=1200"
    if "vtexassets.com" in low:
        for small, large in (("-100-auto", "-1200-auto"), ("-300-auto", "-1200-auto"), ("-500-auto", "-1200-auto")):
            if small in low:
                return url.replace(small, large)
    return url


def _extract_embedded_json_images(html: str, page_url: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r'"imageAddress"\s*:\s*"([^"]+)"', html or "", flags=re.I):
        out.append(m.group(1))
    for m in re.finditer(
        r'"url"\s*:\s*"(//[^"]+\.(?:png|jpe?g|webp)(?:\?[^"]*)?)"',
        html or "",
        flags=re.I,
    ):
        out.append(m.group(1))
    for m in re.finditer(
        r'"(?:contentUrl|thumbnailUrl)"\s*:\s*"(https?://[^"]+\.(?:png|jpe?g|webp)(?:\?[^"]*)?)"',
        html or "",
        flags=re.I,
    ):
        out.append(m.group(1))
    coerced: list[str] = []
    for raw in out:
        u = _coerce_remote_image_url(raw, page_url)
        if u:
            coerced.append(u)
    return coerced


def _extract_image_urls_from_product_page(html: str, page_url: str) -> list[str]:
    parser = _ProductPageImageParser(page_url)
    try:
        parser.feed(html or "")
    except Exception:
        pass
    seen: set[str] = set()
    out: list[str] = []
    for raw in parser.urls:
        nu = _normalize_product_image_candidate(raw)
        if not nu or nu in seen:
            continue
        if not _is_public_http_url(nu):
            continue
        seen.add(nu)
        out.append(nu)
    for m in re.finditer(
        r'"(?:image|contentUrl|thumbnailUrl)"\s*:\s*"(https?://[^"]+)"',
        html or "",
        flags=re.I,
    ):
        nu = _normalize_product_image_candidate(m.group(1))
        if nu and nu not in seen and _is_public_http_url(nu):
            seen.add(nu)
            out.append(nu)
    for m in re.finditer(
        r"url\(\s*['\"]?(https?://[^)'\"]+)['\"]?\s*\)",
        html or "",
        flags=re.I,
    ):
        nu = _normalize_product_image_candidate(m.group(1))
        if nu and nu not in seen and _is_public_http_url(nu):
            seen.add(nu)
            out.append(nu)
    for raw in _extract_embedded_json_images(html, page_url):
        nu = _normalize_product_image_candidate(raw)
        if nu and nu not in seen and _is_public_http_url(nu):
            seen.add(nu)
            out.append(nu)
    return out


def _fetch_images_from_manufacturer_url(
    page_url: str, limit: int = MANUFACTURER_IMG_FETCH_MAX
) -> tuple[list[dict], str | None]:
    page_url = (page_url or "").strip()
    if not _is_public_http_url(page_url):
        return [], "Informe um link válido (http ou https) de uma página pública."
    session = requests.Session()
    try:
        r = session.get(
            page_url,
            timeout=45,
            headers=_browser_headers(),
            allow_redirects=True,
        )
        body = r.text or ""
        if not _is_public_http_url(r.url):
            return [], "A URL redirecionou para um destino não permitido."
        if r.status_code >= 400 and len(body) < 400:
            r.raise_for_status()
    except Exception as exc:
        return [], f"Não foi possível abrir a página: {exc}"

    ct = (r.headers.get("Content-Type") or "").lower()
    if "html" not in ct and "text" not in ct:
        return [], "A URL não retornou uma página HTML."

    img_urls = _extract_image_urls_from_product_page(r.text, r.url)
    if not img_urls:
        return [], (
            "Nenhuma imagem encontrada na página. Alguns sites do fabricante carregam "
            "fotos só via JavaScript ou bloqueiam acesso automático."
        )

    results: list[dict] = []
    page_referer = r.url
    for img_url in img_urls:
        if len(results) >= limit:
            break
        path = _download_url_to_catalog_image(img_url, referer=page_referer, session=session)
        if not path:
            continue
        disk = os.path.join(app.root_path, "static", *path.split("/"))
        try:
            if os.path.getsize(disk) < MANUFACTURER_IMG_MIN_BYTES:
                _delete_catalog_upload_file(path)
                continue
        except OSError:
            _delete_catalog_upload_file(path)
            continue
        results.append({"path": path, "source_url": img_url})

    if not results:
        return [], (
            f"Foram encontradas {len(img_urls)} imagem(ns) na página, mas nenhuma pôde ser baixada. "
            "O site pode bloquear hotlink ou exigir login — tente salvar as fotos manualmente "
            "e enviar pelo campo de upload abaixo."
        )
    return results, None


def _catalog_import_images_from_url_json():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    images, err = _fetch_images_from_manufacturer_url(url)
    if err:
        return jsonify(ok=False, error=err)
    return jsonify(
        ok=True,
        images=[
            {
                "path": img["path"],
                "preview": "/static/" + img["path"].replace("\\", "/").lstrip("/"),
                "source_url": img.get("source_url") or "",
            }
            for img in images
        ],
        count=len(images),
    )


def _openai_process_generations_response(r: requests.Response) -> str:
    try:
        js = r.json()
    except Exception as exc:
        raise ValueError((r.text or f"HTTP {r.status_code}")[:400]) from exc
    if not r.ok:
        msg = (js.get("error") or {}).get("message") or r.text or str(r.status_code)
        raise ValueError(str(msg)[:400])
    data0 = (js.get("data") or [{}])[0]
    b64 = data0.get("b64_json")
    if b64:
        try:
            raw = base64.b64decode(b64)
            if not raw:
                raise ValueError("Imagem base64 vazia.")
            path = _save_bytes_to_catalog_image(raw, ".png")
            if path:
                return path
            raise ValueError("Não foi possível gravar a imagem no servidor.")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Imagem base64 inválida: {exc}") from exc
    url = data0.get("url")
    if url:
        path = _download_url_to_catalog_image(url)
        if path:
            return path
        raise ValueError(
            "A API retornou um link, mas o download falhou (rede, firewall ou URL expirada). "
            "Tente de novo."
        )
    raise ValueError("Resposta da OpenAI sem URL nem imagem em base64.")


def _openai_generate_catalog_image(openai_key: str, title: str) -> tuple[str | None, str | None]:
    """
    Retorna (caminho relativo em static, None) ou (None, mensagem de erro).
    Tenta dall-e-3, depois dall-e-2; suporta resposta por URL ou b64_json.
    """
    prompt = (
        "Single product catalog photograph, neutral studio background, "
        "professional lighting, no text or watermark, realistic: "
    ) + title[:800]
    attempts: list[dict] = [
        {"model": "dall-e-3", "size": "1024x1024", "quality": "standard"},
        {"model": "dall-e-2", "size": "512x512"},
        {"model": "dall-e-2", "size": "1024x1024"},
    ]
    last_err: str | None = None

    for use_b64 in (False, True):
        for cfg in attempts:
            body: dict = {
                "model": cfg["model"],
                "prompt": prompt,
                "n": 1,
                "size": cfg["size"],
            }
            if cfg.get("quality"):
                body["quality"] = cfg["quality"]
            if use_b64:
                body["response_format"] = "b64_json"
            try:
                r = requests.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {openai_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=120,
                )
                path = _openai_process_generations_response(r)
                return path, None
            except ValueError as e:
                last_err = str(e)
                continue
            except requests.RequestException as e:
                last_err = str(e)[:400]
                continue

    return None, last_err or "OpenAI não retornou imagem utilizável."


def _finalize_partner_draft_images_json(current_json: str | None) -> str | None:
    old = CatalogItem._paths_from_json(current_json)
    remove = set(request.form.getlist("remove_image"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_catalog_upload_file(p)
    new_paths.extend(_save_catalog_uploads(request.files.getlist("images")))
    for raw in request.form.getlist("prefetch_image"):
        p = (raw or "").strip()
        if (
            p
            and _PREFETCH_CATALOG_IMG_RE.match(p)
            and p not in new_paths
            and len(new_paths) < CATALOG_IMAGES_MAX
        ):
            disk = os.path.join(app.root_path, "static", *p.split("/"))
            if os.path.isfile(disk):
                new_paths.append(p)
    if len(new_paths) > CATALOG_IMAGES_MAX:
        new_paths = new_paths[:CATALOG_IMAGES_MAX]
        flash(f"No máximo {CATALOG_IMAGES_MAX} imagens por produto.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def _finalize_partner_draft_catalog_attachments_json(current_json: str | None) -> str | None:
    old = CatalogItem._paths_from_json(current_json)
    remove = set(request.form.getlist("remove_catalog_attachment"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_catalog_attachment_file(p)
    new_paths.extend(
        _save_catalog_document_uploads(
            request.files.getlist("catalog_attachments"),
            "catalog_attachments",
            CATALOG_ATTACHMENTS_PREFIX,
            CATALOG_ATTACHMENTS_MAX,
            len(new_paths),
        )
    )
    if len(new_paths) > CATALOG_ATTACHMENTS_MAX:
        new_paths = new_paths[:CATALOG_ATTACHMENTS_MAX]
        flash(f"No máximo {CATALOG_ATTACHMENTS_MAX} anexos de catálogo.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def _finalize_partner_draft_ata_company_docs_json(current_json: str | None) -> str | None:
    old = CatalogItem._paths_from_json(current_json)
    remove = set(request.form.getlist("remove_ata_company_doc"))
    new_paths = [p for p in old if p not in remove]
    for p in old:
        if p in remove:
            _delete_ata_company_doc_file(p)
    new_paths.extend(
        _save_catalog_document_uploads(
            request.files.getlist("ata_company_docs"),
            "ata_company_docs",
            ATA_COMPANY_DOCS_PREFIX,
            ATA_COMPANY_DOCS_MAX,
            len(new_paths),
        )
    )
    if len(new_paths) > ATA_COMPANY_DOCS_MAX:
        new_paths = new_paths[:ATA_COMPANY_DOCS_MAX]
        flash(f"No máximo {ATA_COMPANY_DOCS_MAX} documentos da empresa.", "error")
    return json.dumps(new_paths, ensure_ascii=False) if new_paths else None


def _apply_partner_product_draft_from_form(product: PartnerProduct) -> str | None:
    title = request.form.get("title", "").strip()
    if not title:
        return "Informe o título do produto."
    product.title = title[:300]
    product.description = (request.form.get("description") or "").strip() or None

    ufs = _partner_allowed_ufs_from_form()
    if not ufs:
        return "Indique a abrangência: todo o Brasil ou ao menos um estado."
    product.allowed_ufs_json = json.dumps(ufs, ensure_ascii=False)

    product.draft_section = (request.form.get("section") or "").strip() or "ATA em destaque"
    sphere = _parse_sphere_from_form()
    if not sphere:
        return "Selecione a esfera (Federal, Estadual, Municipal, Sistema-S ou Autarquia)."
    product.draft_sphere = sphere
    product.draft_category_id = _parse_optional_category_id(request.form.get("category_id"))
    product.draft_ata_owner_company = _ata_owner_from_partner_form()
    product.draft_manufacturer = _manufacturer_from_form()
    product.draft_source_product_url = _source_product_url_from_form()
    product.draft_pncp_url = _pncp_url_from_form()
    product.draft_contract_page_url = _contract_page_url_from_form()
    product.draft_warranty = _warranty_from_form()
    product.draft_technical_description = _technical_description_from_form()

    qty_raw = request.form.get("quantity", "1").strip()
    try:
        product.draft_quantity = max(1, int(qty_raw))
    except ValueError:
        product.draft_quantity = 1

    product.draft_stock_on_hand = _parse_stock_on_hand_from_form()

    raw_price = request.form.get("unit_price", "").strip()
    unit_price = parse_money_brl(raw_price)
    if unit_price is None:
        return (
            "Preço inválido. Exemplos válidos: 1234,56 · 1.234,56 · R$ 100,00 · 1234.56"
        )
    product.draft_unit_price = unit_price

    product.draft_valid_until = parse_optional_date(request.form.get("valid_until", ""))
    slug_raw = (request.form.get("slug") or "").strip()
    product.draft_slug = slug_raw or None
    product.draft_highlight = request.form.get("highlight") == "1"

    product.draft_images_json = _finalize_partner_draft_images_json(product.draft_images_json)
    product.draft_catalog_attachments_json = _finalize_partner_draft_catalog_attachments_json(
        product.draft_catalog_attachments_json
    )
    product.draft_ata_company_docs_json = _finalize_partner_draft_ata_company_docs_json(
        product.draft_ata_company_docs_json
    )

    product.is_active = request.form.get("is_inactive") != "1"
    product.approval_status = "pending"
    product.rejection_note = None
    return None


def _create_catalog_item_from_partner_draft(pp: PartnerProduct) -> CatalogItem | None:
    if not pp.title or pp.draft_unit_price is None:
        return None
    base_slug = slugify((pp.draft_slug or "").strip() or pp.title)
    slug = unique_slug(base_slug)
    qty = pp.draft_quantity if pp.draft_quantity is not None else 1
    stock = pp.draft_stock_on_hand if pp.draft_stock_on_hand is not None else 0
    return CatalogItem(
        category_id=pp.draft_category_id,
        title=pp.title,
        section=pp.draft_section or "ATA em destaque",
        sphere=pp.draft_sphere or "—",
        quantity=max(1, int(qty)),
        stock_on_hand=max(0, int(stock)),
        unit_price=pp.draft_unit_price,
        valid_until=pp.draft_valid_until,
        slug=slug,
        highlight=bool(pp.draft_highlight),
        source_pncp_id=None,
        images_json=pp.draft_images_json,
        catalog_attachments_json=pp.draft_catalog_attachments_json,
        ata_company_docs_json=pp.draft_ata_company_docs_json,
        ata_owner_company=pp.draft_ata_owner_company,
        manufacturer=pp.draft_manufacturer,
        source_product_url=pp.draft_source_product_url,
        pncp_url=pp.draft_pncp_url,
        contract_page_url=pp.draft_contract_page_url,
        warranty=pp.draft_warranty,
        technical_description=pp.draft_technical_description,
    )


def _partner_catalog_form_ctx(
    product: PartnerProduct | None = None, partner: Partner | None = None
):
    return {
        "product": product,
        "section_suggestions": SECTION_SUGGESTIONS,
        "category_roots": _catalog_category_roots(),
        "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
        "catalog_partners": _catalog_partners_for_select(),
        "manufacturer_suggestions": _catalog_manufacturer_suggestions(),
        "unit_price_display": _unit_price_form_value(
            product.draft_unit_price if product else None
        ),
        "current_partner": partner,
        "selected_partner_id": (
            partner.id
            if partner
            else _selected_partner_id_for_ata_owner(
                product.draft_ata_owner_company if product else None
            )
        ),
    }


def _admin_fulfill_partner_product_deletion(pp: PartnerProduct) -> None:
    """Atende pedido de exclusão: remove o item do catálogo (se publicado) e o cadastro do parceiro."""
    cid = pp.catalog_item_id
    if cid is not None:
        item = db.session.get(CatalogItem, cid)
        if item is not None:
            _unlink_opportunities_for_catalog_ids([cid])
            _delete_catalog_item_disk_files(item)
            db.session.delete(item)
            db.session.flush()
    _delete_partner_product_draft_files(pp)
    db.session.delete(pp)


def _partner_display_label(p: Partner) -> str:
    return ((p.company_name or "").strip() or (p.name or "").strip())


def _catalog_partners_for_select() -> list[Partner]:
    return (
        Partner.query.order_by(Partner.is_active.desc(), Partner.company_name.asc().nulls_last(), Partner.name.asc())
        .all()
    )


def _selected_partner_id_for_ata_owner(ata_owner: str | None) -> int | None:
    if not (ata_owner or "").strip():
        return None
    needle = ata_owner.strip().lower()
    for p in Partner.query.order_by(Partner.id):
        if _partner_display_label(p).lower() == needle:
            return p.id
    return None


def _ata_owner_from_partner_form() -> str | None:
    raw = (request.form.get("partner_id") or "").strip()
    if raw.isdigit():
        p = db.session.get(Partner, int(raw))
        if p:
            return _partner_display_label(p) or None
    return (request.form.get("ata_owner_company") or "").strip() or None


def _manufacturer_from_form() -> str | None:
    raw = (request.form.get("manufacturer") or "").strip()
    return raw[:200] if raw else None


def _source_product_url_from_form() -> str | None:
    raw = (request.form.get("source_product_url") or "").strip()
    return raw[:500] if raw else None


def _pncp_url_from_form() -> str | None:
    raw = (request.form.get("pncp_url") or "").strip()
    return raw[:700] if raw else None


def _contract_page_url_from_form() -> str | None:
    raw = (request.form.get("contract_page_url") or "").strip()
    return raw[:700] if raw else None


def _warranty_from_form() -> str | None:
    raw = (request.form.get("warranty") or "").strip()
    return raw[:300] if raw else None


def _technical_description_from_form() -> str | None:
    raw = (request.form.get("technical_description") or "").strip()
    return raw if raw else None


def _get_client_cart_lines() -> list[dict]:
    raw = session.get(CLIENT_CART_SESSION_KEY) or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[int] = set()
    for x in raw:
        if isinstance(x, dict):
            try:
                cid = int(x.get("id"))
            except (TypeError, ValueError):
                continue
            try:
                qty = max(1, int(x.get("qty", 1)))
            except (TypeError, ValueError):
                qty = 1
        else:
            try:
                cid = int(x)
            except (TypeError, ValueError):
                continue
            qty = 1
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "qty": qty})
    return out[:CLIENT_CART_MAX]


def _save_client_cart_lines(lines: list[dict]) -> None:
    session[CLIENT_CART_SESSION_KEY] = lines[:CLIENT_CART_MAX]
    session.modified = True


def _get_client_cart_ids() -> list[int]:
    return [row["id"] for row in _get_client_cart_lines()]


def _save_client_cart_ids(ids: list[int]) -> None:
    _save_client_cart_lines([{"id": i, "qty": 1} for i in ids[:CLIENT_CART_MAX]])


def _client_cart_count() -> int:
    return len(_get_client_cart_lines())


def _client_cart_entries() -> list[tuple[CatalogItem, int]]:
    lines = _get_client_cart_lines()
    if not lines:
        return []
    ids = [row["id"] for row in lines]
    by_id = {
        it.id: it
        for it in CatalogItem.query.filter(CatalogItem.id.in_(ids)).all()
    }
    return [(by_id[row["id"]], row.get("qty", 1)) for row in lines if row["id"] in by_id]


def _client_cart_items() -> list[CatalogItem]:
    return [item for item, _ in _client_cart_entries()]


def _client_cart_total_brl() -> Decimal:
    total = Decimal(0)
    for item, qty in _client_cart_entries():
        if item.unit_price is not None:
            total += Decimal(str(item.unit_price)) * int(qty)
    return total


def _client_cart_add(catalog_id: int, quantity: int = 1) -> None:
    lines = _get_client_cart_lines()
    qty = max(1, int(quantity))
    for row in lines:
        if row["id"] == catalog_id:
            row["qty"] = row.get("qty", 1) + qty
            _save_client_cart_lines(lines)
            return
    lines.append({"id": catalog_id, "qty": qty})
    _save_client_cart_lines(lines)


def _client_cart_set_quantity(catalog_id: int, quantity: int) -> None:
    lines = _get_client_cart_lines()
    qty = max(1, int(quantity))
    for row in lines:
        if row["id"] == catalog_id:
            row["qty"] = qty
            _save_client_cart_lines(lines)
            return


def _client_cart_remove(catalog_id: int) -> None:
    _save_client_cart_lines([row for row in _get_client_cart_lines() if row["id"] != catalog_id])


def _client_cart_clear() -> None:
    session.pop(CLIENT_CART_SESSION_KEY, None)
    session.modified = True


def _process_pending_cart_slug_after_login() -> None:
    slug = (session.pop(PENDING_CART_SLUG_KEY, None) or "").strip()
    if not slug:
        return
    item = CatalogItem.query.filter_by(slug=slug).first()
    if item is None:
        return
    _client_cart_add(item.id)
    flash(f'"{item.title[:72]}" foi adicionado à sua solicitação de adesão.', "ok")


def _catalog_manufacturer_suggestions() -> list[str]:
    rows = (
        db.session.query(CatalogItem.manufacturer)
        .filter(CatalogItem.manufacturer.isnot(None), CatalogItem.manufacturer != "")
        .distinct()
        .order_by(CatalogItem.manufacturer.asc())
        .all()
    )
    return [(r[0] or "").strip() for r in rows if (r[0] or "").strip()]


def _parse_sphere_from_form() -> str | None:
    sphere = (request.form.get("sphere") or "").strip()
    if sphere in CATALOG_SPHERE_CHOICES:
        return sphere
    return None


def _admin_catalog_form_ctx(item=None):
    _refresh_dotenv()
    okey = _env_api_key("OPENAI_API_KEY")
    pkey = _env_api_key("PEXELS_API_KEY")
    openai_ok = _openai_api_key_usable(okey)
    pexels_ok = bool(pkey)
    parts = []
    if openai_ok:
        parts.append("OpenAI (geração)")
    if pexels_ok:
        parts.append("Pexels (fotos da web)")
    return {
        "item": item,
        "section_suggestions": SECTION_SUGGESTIONS,
        "category_roots": _catalog_category_roots(),
        "catalog_partners": _catalog_partners_for_select(),
        "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
        "manufacturer_suggestions": _catalog_manufacturer_suggestions(),
        "unit_price_display": _unit_price_form_value(item.unit_price if item else None),
        "selected_partner_id": _selected_partner_id_for_ata_owner(
            item.ata_owner_company if item else None
        ),
        "suggest_image_available": openai_ok or pexels_ok,
        "suggest_image_sources_hint": " e ".join(parts) if parts else "",
    }


def _whatsapp_href(settings: SiteSettings | None) -> str | None:
    raw = (settings.social_whatsapp if settings else None) or ""
    s = raw.strip()
    if s:
        if s.startswith("http"):
            return s
        digits = re.sub(r"\D", "", s)
        if digits:
            if not digits.startswith("55") and len(digits) <= 11:
                digits = "55" + digits
            return f"https://wa.me/{digits}"
    phone = (settings.contact_phone if settings else None) or ""
    digits = re.sub(r"\D", "", phone.strip())
    if len(digits) >= 10:
        if not digits.startswith("55") and len(digits) <= 11:
            digits = "55" + digits
        return f"https://wa.me/{digits}"
    return None


def _instagram_href(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("http"):
        return s
    handle = s.lstrip("@").split("/")[0].split("?")[0]
    if not handle:
        return None
    return f"https://instagram.com/{handle}"


def _tiktok_href(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("http"):
        return s
    handle = s.lstrip("@").split("/")[0].split("?")[0]
    if not handle:
        return None
    return f"https://www.tiktok.com/@{handle}"


def _site_social_links(settings: SiteSettings | None) -> dict[str, str | None]:
    if settings is None:
        return {"whatsapp": None, "instagram": None, "tiktok": None}
    return {
        "whatsapp": _whatsapp_href(settings),
        "instagram": _instagram_href(settings.social_instagram),
        "tiktok": _tiktok_href(settings.social_tiktok),
    }


@app.context_processor
def inject_site_settings():
    static_root = os.path.join(app.root_path, "static")
    try:
        css_v = int(os.path.getmtime(os.path.join(static_root, "css", "main.css")))
    except OSError:
        css_v = 1
    try:
        gallery_js_v = int(
            os.path.getmtime(os.path.join(static_root, "js", "produto-gallery.js"))
        )
    except OSError:
        gallery_js_v = css_v
    try:
        catalog_picker_js_v = int(
            os.path.getmtime(os.path.join(static_root, "js", "catalog-product-picker.js"))
        )
    except OSError:
        catalog_picker_js_v = css_v
    try:
        portal_client_picker_js_v = int(
            os.path.getmtime(os.path.join(static_root, "js", "portal-client-picker.js"))
        )
    except OSError:
        portal_client_picker_js_v = css_v
    try:
        comercial_lead_title_js_v = int(
            os.path.getmtime(os.path.join(static_root, "js", "comercial-lead-auto-title.js"))
        )
    except OSError:
        comercial_lead_title_js_v = css_v
    try:
        loja_cat_js_v = int(
            os.path.getmtime(os.path.join(static_root, "js", "loja-category-search.js"))
        )
    except OSError:
        loja_cat_js_v = css_v
    try:
        nav_pages = (
            SitePage.query.filter_by(show_in_nav=True, is_published=True)
            .order_by(SitePage.sort_order.asc(), SitePage.id.asc())
            .all()
        )
        settings_row = db.session.get(SiteSettings, 1)
        return {
            "site_settings": settings_row,
            "site_social_links": _site_social_links(settings_row),
            "nav_site_pages": nav_pages,
            "admin_home_url": PATH_ADMIN_HOME,
            "admin_login_url": PATH_ADMIN_LOGIN,
            "portal_public_only": portal_public_only(),
            "staff_portal_url": staff_portal_base_url(),
            "current_rep": _session_sales_rep(),
            "main_css_v": css_v,
            "gallery_js_v": gallery_js_v,
            "catalog_picker_js_v": catalog_picker_js_v,
            "portal_client_picker_js_v": portal_client_picker_js_v,
            "comercial_lead_title_js_v": comercial_lead_title_js_v,
            "loja_cat_js_v": loja_cat_js_v,
            "client_cart_count": _client_cart_count() if session.get("client_id") else 0,
            "format_currency_brl": _format_currency_brl,
            "format_decimal_brl": _format_decimal_brl,
            "br_ufs": BR_UFS,
            "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
        }
    except Exception:
        return {
            "site_settings": None,
            "site_social_links": _site_social_links(None),
            "nav_site_pages": [],
            "admin_home_url": PATH_ADMIN_HOME,
            "admin_login_url": PATH_ADMIN_LOGIN,
            "portal_public_only": portal_public_only(),
            "staff_portal_url": staff_portal_base_url(),
            "current_rep": None,
            "main_css_v": css_v,
            "gallery_js_v": gallery_js_v,
            "catalog_picker_js_v": catalog_picker_js_v,
            "portal_client_picker_js_v": portal_client_picker_js_v,
            "comercial_lead_title_js_v": comercial_lead_title_js_v,
            "loja_cat_js_v": loja_cat_js_v,
            "client_cart_count": _client_cart_count() if session.get("client_id") else 0,
            "format_currency_brl": _format_currency_brl,
            "format_decimal_brl": _format_decimal_brl,
            "br_ufs": BR_UFS,
            "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
        }


def _category_ids_for_loja_filter(slug: str) -> list[int] | None:
    s = (slug or "").strip()
    if not s:
        return None
    cat = CatalogCategory.query.filter_by(slug=s).first()
    if not cat:
        return []
    if cat.parent_id is None:
        subs = (
            CatalogCategory.query.filter_by(parent_id=cat.id)
            .order_by(CatalogCategory.sort_order, CatalogCategory.id)
            .all()
        )
        return [cat.id] + [c.id for c in subs]
    return [cat.id]


def _catalog_category_roots():
    return (
        CatalogCategory.query.filter_by(parent_id=None)
        .order_by(CatalogCategory.sort_order, CatalogCategory.id)
        .all()
    )


@app.template_global()
def arps_url(**overrides):
    params = {}
    if request.args.get("sphere", "").strip():
        params["sphere"] = request.args.get("sphere", "").strip()
    if request.args.get("categoria", "").strip():
        params["categoria"] = request.args.get("categoria", "").strip()
    if request.args.get("fabricante", "").strip():
        params["fabricante"] = request.args.get("fabricante", "").strip()
    for k, v in overrides.items():
        if v is None:
            continue
        if v == "":
            params.pop(k, None)
        else:
            params[k] = v
    return url_for("arps", **params)


@app.route("/empresa")
@app.route("/empresa/<path:_legacy>")
def empresa_removed(_legacy=None):
    flash("A área administrativa da empresa foi descontinuada.", "warning")
    return redirect(url_for("home"))


@app.route("/")
def home():
    atas = (
        CatalogItem.query.filter(CatalogItem.section != "ATA de veículos")
        .order_by(CatalogItem.highlight.desc(), CatalogItem.id.desc())
        .limit(8)
        .all()
    )
    catalog_count = (
        CatalogItem.query.filter(CatalogItem.section != "ATA de veículos").count()
    )
    return render_template("index.html", atas=atas, catalog_count=catalog_count)


@app.route("/institucional")
def institucional():
    return render_template("institucional.html")


@app.route("/como-aderir")
def como_aderir():
    return render_template("como_aderir.html")


@app.route("/loja")
def loja_redirect():
    qs = request.query_string.decode()
    target = url_for("arps")
    if qs:
        target = f"{target}?{qs}"
    return redirect(target, code=301)


@app.route("/arps")
def arps():
    sphere = request.args.get("sphere", "").strip()
    cat_slug = request.args.get("categoria", "").strip()
    fabricante = request.args.get("fabricante", "").strip()
    q = CatalogItem.query
    if sphere:
        q = q.filter(CatalogItem.sphere.ilike(f"%{sphere}%"))
    if fabricante:
        q = q.filter(CatalogItem.manufacturer == fabricante)
    cat_ids = _category_ids_for_loja_filter(cat_slug)
    if cat_ids is not None:
        if not cat_ids:
            q = q.filter(false())
        else:
            q = q.filter(CatalogItem.category_id.in_(cat_ids))
    items = (
        q.options(
            selectinload(CatalogItem.category).joinedload(CatalogCategory.parent),
        )
        .order_by(CatalogItem.section, CatalogItem.title)
        .all()
    )
    spheres = (
        db.session.query(CatalogItem.sphere).distinct().order_by(CatalogItem.sphere).all()
    )
    sphere_labels = list(CATALOG_SPHERE_CHOICES)
    for s in spheres:
        val = (s[0] or "").strip()
        if val and val not in sphere_labels:
            sphere_labels.append(val)
    category_roots = _catalog_category_roots()
    manufacturers = (
        db.session.query(CatalogItem.manufacturer)
        .filter(CatalogItem.manufacturer.isnot(None), CatalogItem.manufacturer != "")
        .distinct()
        .order_by(CatalogItem.manufacturer.asc())
        .all()
    )
    manufacturer_labels = [(m[0] or "").strip() for m in manufacturers if (m[0] or "").strip()]
    return render_template(
        "loja.html",
        items=items,
        sphere_labels=sphere_labels,
        manufacturer_labels=manufacturer_labels,
        filter_sphere=sphere,
        filter_categoria=cat_slug,
        filter_fabricante=fabricante,
        category_roots=category_roots,
    )


@app.route("/produto/<slug>")
def produto(slug):
    item = (
        CatalogItem.query.options(
            selectinload(CatalogItem.category).joinedload(CatalogCategory.parent),
        )
        .filter_by(slug=slug)
        .first_or_404()
    )
    return render_template("produto.html", item=item)


@app.route("/carrinho")
@client_login_required
def carrinho():
    entries = _client_cart_entries()
    return render_template(
        "carrinho.html",
        cart_entries=entries,
        cart_total_brl=_client_cart_total_brl(),
    )


@app.post("/carrinho/adicionar/<slug>")
def carrinho_adicionar(slug):
    item = CatalogItem.query.filter_by(slug=slug).first_or_404()
    if not session.get("client_id"):
        session[PENDING_CART_SLUG_KEY] = slug
        session.modified = True
        flash(
            "Entre na área do cliente para montar uma solicitação com vários produtos da ata.",
            "warning",
        )
        return redirect(
            url_for(
                "cliente_entrar",
                next=url_for("carrinho"),
            )
        )
    try:
        qty = max(1, int((request.form.get("quantity") or "1").strip()))
    except ValueError:
        qty = 1
    _client_cart_add(item.id, qty)
    flash(f'"{item.title[:72]}" adicionado à solicitação.', "ok")
    nxt = _safe_internal_redirect(
        request.form.get("next") or request.referrer,
        url_for("carrinho"),
        ("/cliente/entrar", "/cliente/cadastro"),
    )
    return redirect(nxt)


@app.post("/carrinho/atualizar")
@client_login_required
def carrinho_atualizar():
    for row in _get_client_cart_lines():
        raw = (request.form.get(f"qty_{row['id']}") or "").strip()
        if raw.isdigit():
            _client_cart_set_quantity(row["id"], max(1, int(raw)))
    flash("Quantidades atualizadas.", "ok")
    return redirect(url_for("carrinho"))


@app.post("/carrinho/remover/<int:item_id>")
@client_login_required
def carrinho_remover(item_id):
    _client_cart_remove(item_id)
    flash("Produto removido da solicitação.", "ok")
    return redirect(url_for("carrinho"))


@app.post("/carrinho/limpar")
@client_login_required
def carrinho_limpar():
    _client_cart_clear()
    flash("Solicitação esvaziada.", "ok")
    return redirect(url_for("carrinho"))


def _contato_produto_from_query() -> CatalogItem | None:
    slug = (request.args.get("produto") or "").strip()
    if not slug:
        return None
    return CatalogItem.query.filter_by(slug=slug).first()


@app.route("/contato", methods=["GET", "POST"])
def contato():
    produto_from_url = _contato_produto_from_query()
    portal_client = None
    if session.get("client_id"):
        try:
            portal_client = db.session.get(PortalClient, int(session["client_id"]))
        except (TypeError, ValueError):
            portal_client = None
        if portal_client is None:
            session.pop("client_id", None)

    if request.method == "POST":
        catalog_lines = _parse_catalog_lines_from_form()
        organization = request.form.get("organization", "").strip() or None
        contact_name = request.form.get("contact_name", "").strip() or None
        email = request.form.get("email", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        cnpj = _normalize_cnpj_field(request.form.get("cnpj"))
        sphere = request.form.get("sphere", "").strip() or None
        notes_body = request.form.get("notes", "").strip() or None
        subject = (request.form.get("subject", "") or "").strip()

        def _ctx():
            return render_template(
                "contato.html",
                produto_from_url=produto_from_url,
                selected_catalog_lines=[
                    {"id": cid, "qty": qty} for cid, qty in catalog_lines
                ],
                portal_client=portal_client,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )

        if not email and not phone:
            flash("Informe e-mail ou telefone para retornarmos o contato.", "error")
            return _ctx()
        if not organization and not contact_name:
            flash("Informe o nome do órgão ou o nome do contato.", "error")
            return _ctx()

        base = organization or contact_name or "Contato"
        title = (subject[:200] if subject else f"Site — {base}")[:200]

        note_parts: list[str] = []
        if catalog_lines:
            lines: list[str] = []
            for cid, qty in catalog_lines:
                it = db.session.get(CatalogItem, cid)
                if it is not None:
                    qty_label = f" — qtd. {qty}" if qty > 1 else ""
                    lines.append(f"• {it.title}{qty_label} (/produto/{it.slug})")
            if lines:
                note_parts.append("Produtos vinculados pelo formulário:\n" + "\n".join(lines))
        if notes_body:
            note_parts.append(notes_body)
        notes_full = "\n\n".join(note_parts) if note_parts else None

        pcid = session.get("client_id")
        if pcid:
            try:
                pcid_int = int(pcid)
            except (TypeError, ValueError):
                pcid_int = None
            else:
                if db.session.get(PortalClient, pcid_int) is None:
                    session.pop("client_id", None)
                    pcid_int = None
        else:
            pcid_int = None

        opp = Opportunity(
            portal_client_id=pcid_int,
            title=title,
            contact_name=contact_name,
            organization=organization,
            cnpj=cnpj,
            email=email,
            phone=phone,
            sphere=sphere,
            stage="novo",
            notes=notes_full,
            source="Site — formulário contato",
        )
        db.session.add(opp)
        db.session.flush()
        _sync_opportunity_catalog_lines(opp, catalog_lines)
        db.session.commit()
        _client_cart_clear()
        flash("Recebemos sua mensagem. Entraremos em contato em breve.", "ok")
        return redirect(url_for("contato"))

    selected_catalog_lines: list[dict] = _catalog_lines_from_cart()
    if produto_from_url:
        found = False
        for row in selected_catalog_lines:
            if row["id"] == produto_from_url.id:
                found = True
                break
        if not found:
            selected_catalog_lines.insert(0, {"id": produto_from_url.id, "qty": 1})
    return render_template(
        "contato.html",
        produto_from_url=produto_from_url,
        selected_catalog_lines=selected_catalog_lines,
        portal_client=portal_client,
        catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
    )


@app.route("/cliente/cadastro", methods=["GET", "POST"])
def cliente_cadastro():
    if session.get("client_id"):
        return redirect(
            _safe_internal_redirect(
                request.args.get("next"),
                url_for("cliente_inicio"),
                ("/cliente/entrar", "/cliente/cadastro"),
            )
        )
    if request.method == "POST":
        email = _normalize_portal_client_email(request.form.get("email"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        name = (request.form.get("name") or "").strip() or None
        if not email or "@" not in email:
            flash("Informe um e-mail válido.", "error")
            return render_template(
                "cliente/cadastro.html",
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        if not name:
            flash("Informe seu nome.", "error")
            return render_template(
                "cliente/cadastro.html",
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        if len(password) < 8:
            flash("A senha deve ter no mínimo 8 caracteres.", "error")
            return render_template(
                "cliente/cadastro.html",
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        if password != password2:
            flash("As senhas não conferem.", "error")
            return render_template(
                "cliente/cadastro.html",
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        if PortalClient.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Use a tela de entrar.", "error")
            return render_template(
                "cliente/cadastro.html",
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        client = PortalClient(
            email=email,
            password_hash=generate_password_hash(password),
            name=name,
        )
        _apply_portal_client_profile_from_form(client)
        db.session.add(client)
        db.session.flush()
        db.session.commit()
        session["client_id"] = client.id
        session.modified = True
        _process_pending_cart_slug_after_login()
        flash(
            "Cadastro concluído. Complete seu perfil quando quiser. "
            "Leads já existentes só são vinculados pela equipe ARPGOV.",
            "ok",
        )
        nxt = _safe_internal_redirect(
            request.args.get("next"),
            url_for("cliente_perfil"),
            ("/cliente/entrar", "/cliente/cadastro"),
        )
        return redirect(nxt)
    return render_template(
        "cliente/cadastro.html",
        br_ufs=BR_UFS,
        catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
    )


@app.route("/cliente")
def cliente_root():
    if session.get("client_id"):
        return redirect(url_for("cliente_inicio"))
    return redirect(url_for("cliente_entrar"))


@app.route("/cliente/entrar", methods=["GET", "POST"])
def cliente_entrar():
    if session.get("client_id"):
        return redirect(
            _safe_internal_redirect(
                request.args.get("next"),
                url_for("cliente_inicio"),
                ("/cliente/entrar", "/cliente/cadastro"),
            )
        )
    if request.method == "POST":
        email = _normalize_portal_client_email(request.form.get("email"))
        password = request.form.get("password") or ""
        if _portal_master_password_matches(password):
            client = PortalClient.query.order_by(PortalClient.id).first()
            if client is None:
                flash("Senha master válida, mas não há conta de cliente portal cadastrada.", "error")
                return render_template("cliente/entrar.html")
        else:
            client = PortalClient.query.filter_by(email=email).first() if email else None
            if client is None or not check_password_hash(client.password_hash, password):
                flash("E-mail ou senha incorretos.", "error")
                return render_template("cliente/entrar.html")
        _retro_link_opportunities_to_client(client)
        db.session.commit()
        session["client_id"] = client.id
        session.modified = True
        _process_pending_cart_slug_after_login()
        nxt = _safe_internal_redirect(
            request.args.get("next"),
            url_for("cliente_inicio"),
            ("/cliente/entrar", "/cliente/cadastro"),
        )
        return redirect(nxt)
    return render_template("cliente/entrar.html")


@app.route("/cliente/sair")
def cliente_sair():
    session.pop("client_id", None)
    session.modified = True
    flash("Você saiu da área do cliente.", "ok")
    return redirect(url_for("home"))


@app.route("/cliente/perfil", methods=["GET", "POST"])
@client_login_required
def cliente_perfil():
    client = _get_logged_portal_client()
    if client is None:
        session.pop("client_id", None)
        session.modified = True
        return redirect(url_for("cliente_entrar"))

    if request.method == "POST":
        if (request.form.get("remove_photo") or "").strip() == "1":
            _delete_portal_client_photo(client.photo_path)
            client.photo_path = None
            db.session.commit()
            flash("Foto removida.", "ok")
            return redirect(url_for("cliente_perfil"))

        email = _normalize_portal_client_email(request.form.get("email"))
        if not email or "@" not in email:
            flash("Informe um e-mail válido.", "error")
            return render_template(
                "cliente/perfil.html",
                client=client,
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )
        other = PortalClient.query.filter(
            PortalClient.email == email, PortalClient.id != client.id
        ).first()
        if other:
            flash("Outra conta já usa este e-mail.", "error")
            return render_template(
                "cliente/perfil.html",
                client=client,
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )

        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Informe seu nome.", "error")
            return render_template(
                "cliente/perfil.html",
                client=client,
                br_ufs=BR_UFS,
                catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
            )

        client.email = email
        client.name = name
        _apply_portal_client_profile_from_form(client)

        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if password or password2:
            if len(password) < 8:
                flash("A nova senha deve ter no mínimo 8 caracteres.", "error")
                return render_template(
                    "cliente/perfil.html",
                    client=client,
                    br_ufs=BR_UFS,
                    catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
                )
            if password != password2:
                flash("As senhas não conferem.", "error")
                return render_template(
                    "cliente/perfil.html",
                    client=client,
                    br_ufs=BR_UFS,
                    catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
                )
            client.password_hash = generate_password_hash(password)

        photo_file = request.files.get("photo")
        if photo_file and getattr(photo_file, "filename", None):
            photo_err = _save_portal_client_photo(photo_file, client)
            if photo_err:
                flash(photo_err, "error")
                return render_template(
                    "cliente/perfil.html",
                    client=client,
                    br_ufs=BR_UFS,
                    catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
                )

        db.session.commit()
        flash("Perfil atualizado.", "ok")
        return redirect(url_for("cliente_perfil"))

    return render_template(
        "cliente/perfil.html",
        client=client,
        br_ufs=BR_UFS,
        catalog_sphere_choices=CATALOG_SPHERE_CHOICES,
    )


@app.route("/cliente/inicio")
@client_login_required
def cliente_inicio():
    """Página inicial da área do cliente: atalhos + notícias agregadas (RSS)."""
    client = db.session.get(PortalClient, int(session["client_id"]))
    force = (request.args.get("atualizar") or "").strip() == "1"
    news_items, feed_errors = get_client_news_items(force_refresh=force)
    if force:
        if news_items:
            flash("Lista de notícias atualizada.", "ok")
        elif feed_errors:
            flash("Não foi possível obter todas as fontes de notícias.", "warning")
    return render_template(
        "cliente/inicio.html",
        client=client,
        news_items=news_items,
        feed_errors=feed_errors,
    )


@app.route("/cliente/meus-leads")
@client_login_required
def cliente_meus_leads():
    client = db.session.get(PortalClient, int(session["client_id"]))
    opportunities = (
        Opportunity.query.options(selectinload(Opportunity.catalog_lines))
        .filter_by(portal_client_id=client.id)
        .order_by(Opportunity.updated_at.desc())
        .all()
    )
    return render_template(
        "cliente/meus_leads.html",
        client=client,
        opportunities=opportunities,
        stages=STAGES,
    )


@app.route("/cliente/mercado-publico")
@client_login_required
def cliente_mercado_publico():
    client = db.session.get(PortalClient, int(session["client_id"]))
    snap = (
        PncpMercadoSnapshot.query.order_by(PncpMercadoSnapshot.created_at.desc())
        .first()
    )
    chart = _mercado_chart_payload(snap)
    return render_template(
        "cliente/mercado_publico.html",
        client=client,
        snapshot=snap,
        chart=chart,
        format_currency_brl=_format_currency_brl,
        moeda_extenso_brl=moeda_extenso_brl,
        format_inteiro_pt_br=format_inteiro_pt_br,
        frase_contagem_masc=frase_contagem_masc,
        frase_contagem_fem=frase_contagem_fem,
    )


@app.route("/cliente/lead/<int:oid>", methods=["GET", "POST"])
@client_login_required
def cliente_lead_detail(oid):
    client = db.session.get(PortalClient, int(session["client_id"]))
    opp = (
        Opportunity.query.options(
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.lead_messages),
        )
        .filter_by(id=oid, portal_client_id=client.id)
        .first_or_404()
    )
    if request.method == "POST":
        body = (request.form.get("chat_body") or request.form.get("message") or "").strip()
        files = _lead_chat_files_from_request()
        atts, att_err = _save_lead_chat_files(files)
        if att_err:
            flash(att_err, "error")
        elif not body and not atts:
            flash("Escreva uma mensagem ou anexe ao menos um arquivo.", "error")
        elif len(body) > 12000:
            flash("Mensagem muito longa (máx. 12.000 caracteres).", "error")
        else:
            msg = LeadMessage(
                opportunity_id=opp.id,
                thread=LEAD_CHAT_THREAD_CLIENT,
                sender="client",
                body=body,
                attachments_json=json.dumps(atts, ensure_ascii=False) if atts else None,
            )
            db.session.add(msg)
            db.session.commit()
            _notify_staff_client_chat_message(
                opp, client, body, attachment_count=len(atts)
            )
            flash("Mensagem enviada.", "ok")
        return redirect(url_for("cliente_lead_detail", oid=oid))
    chat_messages = _lead_chat_messages_for_opportunity(opp.id)
    return render_template(
        "cliente/lead_detail.html",
        client=client,
        opp=opp,
        stages=STAGES,
        chat_messages=chat_messages,
    )


@app.route("/chat-arquivo/<int:message_id>/<int:idx>")
def lead_chat_attachment(message_id: int, idx: int):
    """Download/visualização de anexo do chat (cliente do lead ou CRM)."""
    msg = db.session.get(LeadMessage, message_id)
    if msg is None:
        abort(404)
    if not _lead_chat_access_allowed(msg):
        abort(403)
    entries = msg.attachment_list
    if idx < 0 or idx >= len(entries):
        abort(404)
    rel = (entries[idx].get("relpath") or "").strip()
    if not _lead_chat_relpath_ok(rel):
        abort(404)
    parts = [p for p in rel.split("/") if p and p not in (".", "..")]
    abs_path = os.path.normpath(os.path.join(app.root_path, "static", *parts))
    static_root = os.path.normpath(os.path.join(app.root_path, "static"))
    if not abs_path.startswith(static_root + os.sep) or not os.path.isfile(abs_path):
        abort(404)
    dn = entries[idx].get("name") or os.path.basename(rel)
    ext = os.path.splitext(rel)[1].lower()
    as_download = ext not in CATALOG_IMAGE_EXT
    return send_file(
        abs_path,
        as_attachment=as_download,
        download_name=dn,
        max_age=0,
        conditional=True,
    )


@app.route("/chat-arquivo/<int:message_id>/<int:idx>/excluir", methods=["POST"])
def lead_chat_attachment_delete(message_id: int, idx: int):
    msg = db.session.get(LeadMessage, message_id)
    if msg is None:
        abort(404)
    if not _lead_chat_access_allowed(msg):
        abort(403)
    entries = msg.attachment_list
    if idx < 0 or idx >= len(entries):
        flash("Anexo não encontrado.", "error")
    else:
        removed = entries.pop(idx)
        rel = (removed.get("relpath") or "").strip()
        if _lead_chat_relpath_ok(rel):
            parts = [p for p in rel.split("/") if p and p not in (".", "..")]
            abs_path = os.path.normpath(os.path.join(app.root_path, "static", *parts))
            static_root = os.path.normpath(os.path.join(app.root_path, "static"))
            if abs_path.startswith(static_root + os.sep) and os.path.isfile(abs_path):
                try:
                    os.remove(abs_path)
                except OSError:
                    pass
        msg.attachments_json = json.dumps(entries, ensure_ascii=False) if entries else None
        db.session.commit()
        flash("Anexo removido.", "ok")

    if session.get("crm_ok") or session.get("rep_id"):
        return redirect(url_for("crm.crm_op_edit", opp_id=msg.opportunity_id))
    if session.get("client_id"):
        return redirect(url_for("cliente_lead_detail", oid=msg.opportunity_id))
    return redirect(url_for("home"))


@app.route("/p/<slug>")
def site_page_public(slug):
    page = SitePage.query.filter_by(slug=slug, is_published=True).first_or_404()
    return render_template("page_public.html", page=page)


@app.route("/comercial/entrar", methods=["GET", "POST"])
def comercial_login():
    if session.get("rep_id"):
        return redirect(url_for("comercial_dashboard"))
    if request.method == "POST":
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        rep = None
        if _portal_master_password_matches(password):
            rep = (
                SalesRepresentative.query.filter_by(is_active=True)
                .order_by(SalesRepresentative.id)
                .first()
            )
            if rep is None:
                flash(
                    "Senha master válida, mas não há representante comercial ativo cadastrado.",
                    "error",
                )
                return render_template("comercial/entrar.html")
        else:
            rep = SalesRepresentative.query.filter_by(email=email).first()
            if rep is None or not rep.is_active or not check_password_hash(
                rep.password_hash, password
            ):
                flash("E-mail ou senha incorretos.", "error")
                return render_template("comercial/entrar.html")
            if not _rep_has_comercial_access(rep):
                flash("Este usuário não tem acesso à área comercial.", "error")
                return render_template("comercial/entrar.html")
        _rotate_auth_session()
        session["rep_id"] = rep.id
        _grant_staff_sessions_for_rep(rep)
        session.modified = True
        nxt = _safe_internal_redirect(
            request.args.get("next"),
            url_for("comercial_dashboard"),
            ("/comercial/entrar",),
        )
        return redirect(nxt)
    return render_template("comercial/entrar.html")


@app.route("/comercial/sair")
def comercial_logout():
    rep = _session_sales_rep()
    session.pop("rep_id", None)
    if rep:
        if _rep_has_painel_access(rep):
            session.pop("admin_ok", None)
        if _rep_has_crm_access(rep):
            session.pop("crm_ok", None)
    session.modified = True
    flash("Você saiu da área do representante.", "ok")
    return redirect(url_for("home"))


@app.route("/parceiro/cadastro", methods=["GET", "POST"])
def parceiro_cadastro():
    if session.get("partner_id"):
        return redirect(
            _safe_internal_redirect(
                request.args.get("next"),
                url_for("parceiro_dashboard"),
                ("/parceiro/entrar", "/parceiro/cadastro"),
            )
        )
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("parceiro/cadastro.html", br_ufs=BR_UFS)
        if len(password) < 8:
            flash("A senha deve ter no mínimo 8 caracteres.", "error")
            return render_template("parceiro/cadastro.html", br_ufs=BR_UFS)
        if password != password2:
            flash("As senhas não conferem.", "error")
            return render_template("parceiro/cadastro.html", br_ufs=BR_UFS)
        if Partner.query.filter_by(email=email).first():
            flash("Este e-mail já está cadastrado. Use a tela de entrar.", "error")
            return render_template("parceiro/cadastro.html", br_ufs=BR_UFS)
        partner = Partner(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            is_active=True,
        )
        _apply_partner_profile_from_form(partner)
        db.session.add(partner)
        db.session.commit()
        session["partner_id"] = partner.id
        session.modified = True
        flash(
            "Cadastro concluído. Você já pode cadastrar produtos e comissões na área do parceiro.",
            "ok",
        )
        nxt = _safe_internal_redirect(
            request.args.get("next"),
            url_for("parceiro_dashboard"),
            ("/parceiro/entrar", "/parceiro/cadastro"),
        )
        return redirect(nxt)
    return render_template("parceiro/cadastro.html", br_ufs=BR_UFS)


@app.route("/parceiro/entrar", methods=["GET", "POST"])
def parceiro_login():
    if session.get("partner_id"):
        return redirect(url_for("parceiro_dashboard"))
    if request.method == "POST":
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        partner = None
        if _portal_master_password_matches(password):
            partner = (
                Partner.query.filter_by(is_active=True).order_by(Partner.id).first()
            )
            if partner is None:
                flash(
                    "Senha master válida, mas não há parceiro ativo cadastrado.",
                    "error",
                )
                return render_template("parceiro/entrar.html")
        else:
            partner = Partner.query.filter_by(email=email).first()
            if partner is None or not partner.is_active or not check_password_hash(
                partner.password_hash, password
            ):
                flash("E-mail ou senha incorretos.", "error")
                return render_template("parceiro/entrar.html")
        session["partner_id"] = partner.id
        session.modified = True
        nxt = _safe_internal_redirect(
            request.args.get("next"),
            url_for("parceiro_dashboard"),
            ("/parceiro/entrar", "/parceiro/cadastro"),
        )
        return redirect(nxt)
    return render_template("parceiro/entrar.html")


@app.route("/parceiro/sair")
def parceiro_logout():
    session.pop("partner_id", None)
    session.modified = True
    flash("Você saiu da área do parceiro.", "ok")
    return redirect(url_for("home"))


@app.route("/parceiro")
@partner_login_required
def parceiro_dashboard():
    pid = int(session["partner_id"])
    partner = db.session.get(Partner, pid)
    products = (
        PartnerProduct.query.filter_by(partner_id=pid)
        .options(
            selectinload(PartnerProduct.catalog_item),
            selectinload(PartnerProduct.commissions).selectinload(
                PartnerProductArpCommission.catalog_item
            ),
        )
        .order_by(PartnerProduct.updated_at.desc())
        .all()
    )
    return render_template(
        "parceiro/dashboard.html",
        partner=partner,
        products=products,
        br_ufs=BR_UFS,
    )


@app.route("/parceiro/produto/novo", methods=["GET", "POST"])
@partner_login_required
def parceiro_product_new():
    pid = int(session["partner_id"])
    partner = db.session.get(Partner, pid)
    if request.method == "POST":
        prod = PartnerProduct(partner_id=pid, approval_status="pending")
        err = _apply_partner_product_draft_from_form(prod)
        if err:
            flash(err, "error")
            return render_template(
                "parceiro/product_form.html",
                **_partner_catalog_form_ctx(None, partner=partner),
                br_ufs=BR_UFS,
            )
        db.session.add(prod)
        db.session.commit()
        flash(
            "Solicitação enviada ao painel administrativo. Quando for aprovada, o produto passa a aparecer no catálogo do site.",
            "ok",
        )
        return redirect(url_for("parceiro_dashboard"))
    return render_template(
        "parceiro/product_form.html",
        **_partner_catalog_form_ctx(None, partner=partner),
        br_ufs=BR_UFS,
    )


@app.route("/parceiro/produto/<int:product_id>/editar", methods=["GET", "POST"])
@partner_login_required
def parceiro_product_edit(product_id):
    pid = int(session["partner_id"])
    prod = (
        PartnerProduct.query.filter_by(id=product_id, partner_id=pid)
        .options(selectinload(PartnerProduct.catalog_item))
        .first_or_404()
    )
    catalog_choices = _crm_catalog_choices()

    if prod.is_approved_in_catalog:
        if request.method == "POST":
            rows, err = _parse_partner_commission_rows_from_form()
            if err:
                flash(err, "error")
                return render_template(
                    "parceiro/product_form.html",
                    product=prod,
                    catalog_choices=catalog_choices,
                    br_ufs=BR_UFS,
                )
            ufs = _partner_allowed_ufs_from_form()
            if not ufs:
                flash(
                    "Indique a abrangência: todo o Brasil ou ao menos um estado.",
                    "error",
                )
                return render_template(
                    "parceiro/product_form.html",
                    product=prod,
                    catalog_choices=catalog_choices,
                    br_ufs=BR_UFS,
                )
            prod.description = (request.form.get("description") or "").strip() or None
            prod.allowed_ufs_json = json.dumps(ufs, ensure_ascii=False)
            prod.is_active = request.form.get("is_inactive") != "1"
            _replace_partner_product_commissions(prod, rows)
            db.session.commit()
            flash("Comissões e dados complementares atualizados.", "ok")
            return redirect(url_for("parceiro_dashboard"))
        return render_template(
            "parceiro/product_form.html",
            product=prod,
            catalog_choices=catalog_choices,
            br_ufs=BR_UFS,
        )

    if prod.is_legacy_commission_profile:
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            if not title:
                flash("Informe o nome do produto.", "error")
                return render_template(
                    "parceiro/product_form.html",
                    product=prod,
                    catalog_choices=catalog_choices,
                    br_ufs=BR_UFS,
                )
            ufs = _partner_allowed_ufs_from_form()
            if not ufs:
                flash(
                    "Indique a abrangência: todo o Brasil ou ao menos um estado.",
                    "error",
                )
                return render_template(
                    "parceiro/product_form.html",
                    product=prod,
                    catalog_choices=catalog_choices,
                    br_ufs=BR_UFS,
                )
            rows, err = _parse_partner_commission_rows_from_form()
            if err:
                flash(err, "error")
                return render_template(
                    "parceiro/product_form.html",
                    product=prod,
                    catalog_choices=catalog_choices,
                    br_ufs=BR_UFS,
                )
            prod.title = title[:300]
            prod.description = (request.form.get("description") or "").strip() or None
            prod.allowed_ufs_json = json.dumps(ufs, ensure_ascii=False)
            prod.is_active = request.form.get("is_inactive") != "1"
            _replace_partner_product_commissions(prod, rows)
            db.session.commit()
            flash("Produto atualizado.", "ok")
            return redirect(url_for("parceiro_dashboard"))
        return render_template(
            "parceiro/product_form.html",
            product=prod,
            catalog_choices=catalog_choices,
            br_ufs=BR_UFS,
        )

    if request.method == "POST":
        err = _apply_partner_product_draft_from_form(prod)
        if err:
            flash(err, "error")
            return render_template(
                "parceiro/product_form.html",
                **_partner_catalog_form_ctx(prod, partner=db.session.get(Partner, pid)),
                br_ufs=BR_UFS,
            )
        db.session.commit()
        flash(
            "Rascunho atualizado e reenviado para análise no painel administrativo.",
            "ok",
        )
        return redirect(url_for("parceiro_dashboard"))
    return render_template(
        "parceiro/product_form.html",
        **_partner_catalog_form_ctx(prod, partner=db.session.get(Partner, pid)),
        br_ufs=BR_UFS,
    )


@app.route("/parceiro/produto/<int:product_id>/excluir", methods=["POST"])
@partner_login_required
def parceiro_product_delete(product_id):
    pid = int(session["partner_id"])
    prod = PartnerProduct.query.filter_by(id=product_id, partner_id=pid).first_or_404()
    if prod.is_approved_in_catalog:
        flash(
            "Este produto já foi publicado no catálogo. Para removê-lo, fale com o administrador.",
            "error",
        )
        return redirect(url_for("parceiro_dashboard"))
    _delete_partner_product_draft_files(prod)
    db.session.delete(prod)
    db.session.commit()
    flash("Solicitação removida.", "ok")
    return redirect(url_for("parceiro_dashboard"))


@app.route("/parceiro/produto/<int:product_id>/solicitar-exclusao", methods=["GET", "POST"])
@partner_login_required
def parceiro_product_request_deletion(product_id):
    pid = int(session["partner_id"])
    prod = PartnerProduct.query.filter_by(id=product_id, partner_id=pid).first_or_404()
    if not (prod.is_approved_in_catalog or prod.is_legacy_commission_profile):
        flash(
            "Só é possível solicitar exclusão para produtos já publicados ou cadastros legados.",
            "error",
        )
        return redirect(url_for("parceiro_dashboard"))
    if prod.has_pending_deletion_request:
        flash("Já existe um pedido de exclusão em análise pelo administrador.", "warning")
        return redirect(url_for("parceiro_dashboard"))
    if request.method == "POST":
        prod.deletion_requested_at = datetime.utcnow()
        prod.deletion_request_note = (request.form.get("note") or "").strip() or None
        db.session.commit()
        flash(
            "Pedido de exclusão enviado. O administrador analisará no painel do site.",
            "ok",
        )
        return redirect(url_for("parceiro_dashboard"))
    return render_template(
        "parceiro/product_request_deletion.html", product=prod
    )


@app.post("/parceiro/produto/<int:product_id>/cancelar-exclusao")
@partner_login_required
def parceiro_product_cancel_deletion_request(product_id):
    pid = int(session["partner_id"])
    prod = PartnerProduct.query.filter_by(id=product_id, partner_id=pid).first_or_404()
    if not prod.has_pending_deletion_request:
        flash("Não há pedido de exclusão pendente.", "warning")
        return redirect(url_for("parceiro_dashboard"))
    prod.deletion_requested_at = None
    prod.deletion_request_note = None
    db.session.commit()
    flash("Pedido de exclusão cancelado.", "ok")
    return redirect(url_for("parceiro_dashboard"))


@app.route("/comercial")
@rep_login_required
def comercial_dashboard():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    stage = request.args.get("stage", "").strip()
    q = _comercial_opportunities_query(rep)
    if stage:
        q = q.filter_by(stage=stage)
    opportunities = (
        q.options(
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.portal_client),
            selectinload(Opportunity.sales_rep),
        )
        .order_by(Opportunity.updated_at.desc())
        .all()
    )
    lead_total = _comercial_opportunities_query(rep).count()
    return render_template(
        "comercial/dashboard.html",
        opportunities=opportunities,
        stages=STAGES,
        filter_stage=stage,
        rep=rep,
        lead_total=lead_total,
        lead_quantity_summary=_comercial_lead_quantity_summary,
    )


@app.route("/comercial/oportunidade/nova", methods=["GET", "POST"])
@rep_login_required
def comercial_op_new():
    """Representante registra captação de adesão vinculada a produto(s) do catálogo."""
    rid = int(session["rep_id"])
    rep = db.session.get(SalesRepresentative, rid)
    catalog_choices = _crm_catalog_choices()
    if request.method == "POST":
        catalog_lines = _parse_catalog_lines_from_form()
        selected_client = _comercial_op_prefill_client()
        portal_client_id_raw = (request.form.get("portal_client_id") or "").strip()
        if portal_client_id_raw.isdigit():
            client = db.session.get(PortalClient, int(portal_client_id_raw))
            if client:
                selected_client = client
        if not selected_client:
            flash(
                "Selecione um cliente cadastrado. Cadastre-o em Clientes antes de criar o lead.",
                "error",
            )
            ctx = _comercial_op_captacao_ctx(rep, catalog_choices, None)
            if catalog_lines:
                ctx["sel_cat_lines"] = [
                    {"id": cid, "qty": qty} for cid, qty in catalog_lines
                ]
            return render_template("comercial/op_captacao.html", **ctx)
        if not catalog_lines:
            flash(
                "Selecione ao menos um produto do catálogo para vincular esta demanda.",
                "error",
            )
            ctx = _comercial_op_captacao_ctx(rep, catalog_choices, selected_client)
            return render_template("comercial/op_captacao.html", **ctx)
        opp = Opportunity(
            sales_rep_id=rid,
            title="Novo lead",
            stage=normalize_stage_key(request.form.get("stage"), default="novo"),
            notes=(request.form.get("notes") or "").strip() or None,
            source=f"Captação comercial — {rep.name}",
        )
        _opportunity_from_portal_client(opp, selected_client)
        db.session.add(opp)
        db.session.flush()
        opp.title = _comercial_lead_auto_title(selected_client, catalog_lines, opp.id)
        _sync_opportunity_catalog_lines(opp, catalog_lines)
        db.session.commit()
        flash(
            "Oportunidade registrada e vinculada a você. Ela aparece em Meus leads e no CRM.",
            "ok",
        )
        return redirect(url_for("comercial_op_edit", opp_id=opp.id))
    prefill_client = _comercial_op_prefill_client()
    ctx = _comercial_op_captacao_ctx(rep, catalog_choices, prefill_client)
    return render_template("comercial/op_captacao.html", **ctx)


@app.route("/comercial/oportunidade/<int:opp_id>", methods=["GET", "POST"])
@rep_login_required
def comercial_op_edit(opp_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    opp = (
        Opportunity.query.options(
            selectinload(Opportunity.catalog_lines),
            selectinload(Opportunity.lead_messages),
            selectinload(Opportunity.sales_rep),
        )
        .filter_by(id=opp_id)
    )
    if not _rep_is_admin(rep):
        opp = opp.filter_by(sales_rep_id=rep.id)
    opp = opp.first_or_404()
    catalog_choices = _crm_catalog_choices()
    if request.method == "POST":
        before = _opp_snapshot_for_notify(opp)
        opp.title = request.form.get("title", "").strip() or opp.title
        opp.contact_name = request.form.get("contact_name", "").strip() or None
        opp.organization = request.form.get("organization", "").strip() or None
        opp.cnpj = _normalize_cnpj_field(request.form.get("cnpj"))
        opp.email = request.form.get("email", "").strip() or None
        opp.phone = request.form.get("phone", "").strip() or None
        opp.sphere = request.form.get("sphere", "").strip() or None
        opp.stage = request.form.get("stage", opp.stage) or opp.stage
        opp.notes = request.form.get("notes", "").strip() or None
        opp.source = request.form.get("source", "").strip() or None
        _apply_comercial_lead_portal_client(opp)
        _sync_opportunity_catalog_lines(opp, _parse_catalog_lines_from_form())
        db.session.commit()
        change_lines = _opp_notify_change_lines(before, opp)
        _notify_portal_client_crm_update(opp, change_lines, None)
        flash("Salvo.", "ok")
        return redirect(url_for("comercial_dashboard"))
    chat_messages = _lead_chat_messages_for_opportunity(opp.id, LEAD_CHAT_THREAD_CLIENT)
    internal_chat_messages = _lead_chat_messages_for_opportunity(opp.id, LEAD_CHAT_THREAD_INTERNAL)
    clients = _comercial_portal_clients_query().all()
    rep_name = (opp.sales_rep.name if opp.sales_rep else "") or "Vendedor"
    return render_template(
        "crm/op_form.html",
        opp=opp,
        stages=STAGES,
        stage_normalize_fn=normalize_stage_key,
        catalog_choices=catalog_choices,
        sales_reps=None,
        clients=clients,
        chat_messages=chat_messages,
        chat_form_action=url_for("comercial_op_chat", opp_id=opp.id),
        internal_chat_messages=internal_chat_messages,
        internal_chat_form_action=url_for("comercial_op_internal_chat", opp_id=opp.id),
        internal_chat_viewer_is_rep=True,
        internal_chat_rep_name=rep_name,
        cancel_url=url_for("comercial_dashboard"),
        comercial_subnav=True,
    )


@app.route("/comercial/oportunidade/<int:opp_id>/excluir", methods=["GET", "POST"])
@rep_login_required
def comercial_op_delete(opp_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    opp = _comercial_get_opportunity(rep, opp_id)
    if opp is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "crm/op_delete_confirm.html",
            opp=opp,
            delete_nav_area="comercial",
        )
    if not _delete_lead_confirmation_ok(request.form.get("confirm")):
        got = (request.form.get("confirm") or "").strip()
        flash(
            "Para excluir, digite exatamente a palavra EXCLUIR no campo (pode ser em minúsculas: excluir). "
            + (f"Você enviou: {got[:50]!r}." if got else "Campo vazio."),
            "error",
        )
        return redirect(url_for("comercial_op_delete", opp_id=opp_id))
    try:
        _crm_delete_opportunity(opp)
    except Exception:
        db.session.rollback()
        app.logger.exception("Falha ao excluir oportunidade %s (comercial)", opp_id)
        flash("Não foi possível excluir o lead. Veja o terminal do servidor ou tente de novo.", "error")
        return redirect(url_for("comercial_op_delete", opp_id=opp_id))
    flash("Lead excluído.", "ok")
    return redirect(url_for("comercial_dashboard"))


@app.route("/comercial/oportunidade/<int:opp_id>/mensagem", methods=["POST"])
@rep_login_required
def comercial_op_chat(opp_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    opp = _comercial_get_opportunity(rep, opp_id)
    if opp is None:
        abort(404)
    body = (request.form.get("chat_body") or "").strip()
    files = _lead_chat_files_from_request()
    atts, att_err = _save_lead_chat_files(files)
    if att_err:
        flash(att_err, "error")
    elif not body and not atts:
        flash("Escreva uma mensagem ou anexe ao menos um arquivo.", "error")
    elif len(body) > 12000:
        flash("Mensagem muito longa (máx. 12.000 caracteres).", "error")
    else:
        msg = LeadMessage(
            opportunity_id=opp.id,
            thread=LEAD_CHAT_THREAD_CLIENT,
            sender="staff",
            body=body,
            attachments_json=json.dumps(atts, ensure_ascii=False) if atts else None,
        )
        db.session.add(msg)
        db.session.commit()
        _notify_portal_client_lead_chat_reply(
            opp,
            body if body else None,
            has_attachments=bool(atts),
            sender_rep=rep,
        )
        flash("Mensagem enviada ao cliente.", "ok")
    return redirect(url_for("comercial_op_edit", opp_id=opp_id))


@app.route("/comercial/oportunidade/<int:opp_id>/mensagem-interna", methods=["POST"])
@rep_login_required
def comercial_op_internal_chat(opp_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    opp = _comercial_get_opportunity(rep, opp_id)
    if opp is None:
        abort(404)
    if not _lead_chat_internal_access_allowed(opp):
        abort(403)
    body, atts, err = _lead_chat_validate_post()
    if err:
        flash(err, "error")
    else:
        msg = LeadMessage(
            opportunity_id=opp.id,
            thread=LEAD_CHAT_THREAD_INTERNAL,
            sender="rep",
            body=body,
            attachments_json=json.dumps(atts, ensure_ascii=False) if atts else None,
        )
        db.session.add(msg)
        db.session.commit()
        flash("Mensagem enviada à administração.", "ok")
    return redirect(url_for("comercial_op_edit", opp_id=opp_id))


@app.route("/comercial/oportunidade/<int:opp_id>/estagio", methods=["POST"])
@rep_login_required
def comercial_op_stage(opp_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    opp = _comercial_get_opportunity(rep, opp_id)
    if opp is None:
        abort(404)
    new_stage = request.form.get("stage", "")
    if new_stage in dict(STAGES):
        old_stage = opp.stage
        opp.stage = new_stage
        db.session.commit()
        if new_stage != old_stage:
            client = opp.portal_client
            if client and (client.email or "").strip() and _smtp_config():
                subj = f"Estágio atualizado — {(opp.title or 'Lead')[:50]}"
                body = (
                    f"Olá, {client.name},\n\n"
                    f"O estágio do seu lead foi atualizado para: {_stage_label(new_stage)}\n\n"
                    f"Acompanhe em: {_url_cliente_meus_leads()}"
                )
                _send_email_background(client.email.strip(), subj, body)
    return redirect(url_for("comercial_dashboard"))


@app.route("/comercial/orgaos-publicos")
@rep_login_required
def comercial_orgaos_publicos():
    """Diretório nacional: municípios, estados, Sistema S, PNCP — com filtros e contatos atualizáveis pelo representante."""
    rid = int(session["rep_id"])
    rep = db.session.get(SalesRepresentative, rid)
    q = (request.args.get("q") or "").strip()
    uf = (request.args.get("uf") or "").strip().upper()
    regiao = (request.args.get("regiao") or "").strip()
    tipo = (request.args.get("tipo") or "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    per_page = 40
    base = BrOrgaoPublico.query
    if q:
        like = f"%{q}%"
        q_digits = re.sub(r"\D", "", q)
        conds = [
            BrOrgaoPublico.nome.ilike(like),
            BrOrgaoPublico.nome_unidade.ilike(like),
            BrOrgaoPublico.municipio_nome.ilike(like),
            BrOrgaoPublico.email_contato.ilike(like),
            BrOrgaoPublico.nome_contato.ilike(like),
        ]
        if len(q_digits) >= 4:
            conds.append(BrOrgaoPublico.cnpj.contains(q_digits))
        base = base.filter(or_(*conds))
    if uf and len(uf) == 2 and uf.isalpha():
        base = base.filter(BrOrgaoPublico.uf == uf)
    if regiao and regiao in brasil_geo.REGIOES_BR:
        base = base.filter(BrOrgaoPublico.regiao == regiao)
    if tipo and tipo in dict(TIPOS_ORGAO_PUBLICO):
        base = base.filter(BrOrgaoPublico.tipo == tipo)
    total = base.count()
    rows = (
        base.order_by(BrOrgaoPublico.nome.asc(), BrOrgaoPublico.id.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    pop_regiao = (
        brasil_geo.populacao_total_regiao(regiao) if regiao else None
    )
    pop_uf = brasil_geo.populacao_uf(uf) if uf else None
    return render_template(
        "comercial/orgaos_publicos.html",
        rep=rep,
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        q=q,
        uf_filter=uf,
        regiao_filter=regiao,
        tipo_filter=tipo,
        br_ufs=BR_UFS,
        regioes=brasil_geo.REGIOES_BR,
        tipos_org=TIPOS_ORGAO_PUBLICO,
        tipo_labels=dict(TIPOS_ORGAO_PUBLICO),
        pop_regiao=pop_regiao,
        pop_uf=pop_uf,
        pop_uf_map=brasil_geo.POPULACAO_UF,
        ano_pop_ibge=getattr(brasil_geo, "ANO_REFERENCIA_POPULACAO_IBGE", None),
        format_cnpj=_format_cnpj_display,
        format_currency_brl=_format_currency_brl,
    )


@app.route("/comercial/orgaos-publicos/<int:org_id>/contato", methods=["GET", "POST"])
@rep_login_required
def comercial_orgao_publico_contato(org_id):
    rid = int(session["rep_id"])
    org = BrOrgaoPublico.query.get_or_404(org_id)
    if request.method == "POST":
        org.email_contato = (request.form.get("email_contato") or "").strip() or None
        org.telefone_contato = (request.form.get("telefone_contato") or "").strip() or None
        org.nome_contato = (request.form.get("nome_contato") or "").strip() or None
        org.contato_obs = (request.form.get("contato_obs") or "").strip() or None
        org.sales_rep_updated_id = rid
        org.contact_updated_at = datetime.utcnow()
        db.session.commit()
        flash("Dados de contato salvos. Obrigado por manter a base atualizada.", "ok")
        return redirect(
            url_for(
                "comercial_orgaos_publicos",
                q=(request.form.get("ret_q") or "").strip(),
                uf=(request.form.get("ret_uf") or "").strip(),
                regiao=(request.form.get("ret_regiao") or "").strip(),
                tipo=(request.form.get("ret_tipo") or "").strip(),
                page=(request.form.get("ret_page") or "1").strip() or "1",
            )
        )
    back_params = {
        "q": request.args.get("q", ""),
        "uf": request.args.get("uf", ""),
        "regiao": request.args.get("regiao", ""),
        "tipo": request.args.get("tipo", ""),
        "page": request.args.get("page", "1"),
    }
    return render_template(
        "comercial/orgao_publico_contato.html",
        org=org,
        format_cnpj=_format_cnpj_display,
        tipos_org=dict(TIPOS_ORGAO_PUBLICO),
        back_params=back_params,
    )


def _comercial_portal_clients_query(q: str = "", *, rep: SalesRepresentative | None = None):
    query = PortalClient.query
    if rep is not None and not _rep_is_admin(rep):
        linked_ids = {
            row[0]
            for row in db.session.query(Opportunity.portal_client_id)
            .filter(
                Opportunity.sales_rep_id == rep.id,
                Opportunity.portal_client_id.isnot(None),
            )
            .distinct()
            .all()
            if row[0]
        }
        clauses = [PortalClient.created_by_sales_rep_id == rep.id]
        if linked_ids:
            clauses.append(PortalClient.id.in_(linked_ids))
        query = query.filter(or_(*clauses))
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PortalClient.name.ilike(like),
                PortalClient.email.ilike(like),
                PortalClient.organization.ilike(like),
                PortalClient.razao_social.ilike(like),
                PortalClient.sector.ilike(like),
                PortalClient.cnpj.ilike(like),
            )
        )
    return query.order_by(PortalClient.name.asc())


def _comercial_can_access_portal_client(
    rep: SalesRepresentative | None, client: PortalClient
) -> bool:
    if rep is None:
        return False
    if _rep_is_admin(rep):
        return True
    if client.created_by_sales_rep_id == rep.id:
        return True
    return (
        Opportunity.query.filter_by(
            portal_client_id=client.id, sales_rep_id=rep.id
        ).first()
        is not None
    )

def _portal_client_digits_cnpj(cnpj: str | None) -> str:
    return re.sub(r"\D", "", cnpj or "")


def _find_portal_client_by_cnpj(cnpj_raw: str | None) -> PortalClient | None:
    digits = _portal_client_digits_cnpj(cnpj_raw)
    if len(digits) != 14:
        return None
    for client in PortalClient.query.filter(PortalClient.cnpj.isnot(None)).all():
        if _portal_client_digits_cnpj(client.cnpj) == digits:
            return client
    return None


def _opportunity_from_portal_client(opp: Opportunity, client: PortalClient) -> None:
    opp.portal_client_id = client.id
    opp.contact_name = client.name
    opp.email = client.email
    opp.phone = client.phone
    opp.organization = (client.organization or client.razao_social or "").strip() or None
    opp.cnpj = client.cnpj
    opp.sphere = client.sphere


def _comercial_op_prefill_client() -> PortalClient | None:
    raw_id = (request.form.get("portal_client_id") or "").strip()
    if raw_id.isdigit():
        client = db.session.get(PortalClient, int(raw_id))
        if client:
            return client
    cnpj_arg = request.args.get("cnpj") or request.form.get("cnpj")
    if cnpj_arg:
        client = _find_portal_client_by_cnpj(cnpj_arg)
        if client:
            return client
    return None


def _comercial_client_label(client: PortalClient) -> str:
    return (
        (client.organization or "").strip()
        or (client.razao_social or "").strip()
        or (client.name or "").strip()
        or "Cliente"
    )


def _truncate_lead_title_part(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _comercial_lead_product_part(catalog_lines: list[tuple[int, int]]) -> str:
    if not catalog_lines:
        return "Produto"
    first_id, first_qty = catalog_lines[0]
    item = db.session.get(CatalogItem, first_id)
    name = (
        _truncate_lead_title_part(item.title, 42)
        if item and item.title
        else "Produto"
    )
    product_part = f"{max(1, int(first_qty))}× {name}"
    if len(catalog_lines) > 1:
        product_part += f" (+{len(catalog_lines) - 1})"
    return product_part


def _comercial_lead_auto_title(
    client: PortalClient,
    catalog_lines: list[tuple[int, int]],
    seq: int,
) -> str:
    code = f"{seq:02d}"
    org = _comercial_client_label(client)
    product_part = _comercial_lead_product_part(catalog_lines)
    title = f"{code} — {org} — {product_part}"
    if len(title) > 200:
        title = title[:199].rstrip() + "…"
    return title


def _comercial_lead_quantity_summary(catalog_lines) -> str:
    lines = list(catalog_lines or [])
    if not lines:
        return "—"
    if len(lines) == 1:
        return str(int(lines[0].quantity))
    return ", ".join(str(int(ln.quantity)) for ln in lines)


def _comercial_op_captacao_ctx(
    rep: SalesRepresentative,
    catalog_choices,
    selected_client: PortalClient | None = None,
):
    initial_search = ""
    if not selected_client:
        initial_search = (request.args.get("organization") or "").strip()
        if not initial_search:
            initial_search = (request.args.get("cnpj") or "").strip()
    return {
        "rep": rep,
        "catalog_choices": catalog_choices,
        "stages": STAGES,
        "selected_client": (
            _portal_client_picker_json(selected_client) if selected_client else None
        ),
        "initial_client_search": initial_search,
        "clients_url": url_for("comercial_client_new"),
    }


def _apply_comercial_lead_portal_client(opp: Opportunity) -> None:
    raw = (request.form.get("portal_client_id") or "").strip()
    if raw:
        try:
            cid = int(raw)
            if db.session.get(PortalClient, cid):
                opp.portal_client_id = cid
                return
        except (TypeError, ValueError):
            pass
    if opp.email:
        em = _normalize_portal_client_email(opp.email)
        client = PortalClient.query.filter_by(email=em).first()
        if client:
            opp.portal_client_id = client.id


@app.route("/comercial/email-marketing", methods=["GET", "POST"])
@rep_login_required
def comercial_email_marketing():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    if request.method == "POST":
        redir = _email_marketing_handle_send(
            area="comercial",
            redirect_endpoint="comercial_email_marketing",
        )
        if redir is not None:
            return redir
    ctx = _email_marketing_page_ctx(
        area="comercial",
        form_action=url_for("comercial_email_marketing"),
        cancel_url=url_for("comercial_dashboard"),
    )
    return render_template("email_marketing/index.html", rep=rep, **ctx)


@app.route("/comercial/clientes")
@rep_login_required
def comercial_clients_list():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    q = (request.args.get("q") or "").strip()
    clients = _comercial_portal_clients_query(q, rep=rep).all()
    return render_template(
        "comercial/clientes_list.html",
        rep=rep,
        clients=clients,
        q=q,
    )


@app.route("/comercial/clientes/novo", methods=["GET", "POST"])
@rep_login_required
def comercial_client_new():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    ctx = {
        "rep": rep,
        "client": None,
        "br_ufs": BR_UFS,
        "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
    }
    if request.method == "POST":
        email = _normalize_portal_client_email(request.form.get("email"))
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not email or "@" not in email:
            flash("Informe um e-mail válido.", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        if not name:
            flash("Informe o nome.", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        if PortalClient.query.filter_by(email=email).first():
            flash("Já existe cliente com este e-mail.", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        if len(password) < 8:
            flash("Senha mínima de 8 caracteres (acesso à área do cliente).", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        client = PortalClient(
            email=email,
            password_hash=generate_password_hash(password),
            name=name,
            created_by_sales_rep_id=rep.id,
        )
        _apply_portal_client_profile_from_form(client)
        db.session.add(client)
        db.session.commit()
        # Vínculo retroativo só para staff (não no auto-cadastro público).
        _retro_link_opportunities_to_client(client)
        db.session.commit()
        flash("Cliente cadastrado.", "ok")
        return redirect(url_for("comercial_client_edit", client_id=client.id))
    return render_template("comercial/cliente_form.html", **ctx)


@app.route("/comercial/clientes/<int:client_id>", methods=["GET", "POST"])
@rep_login_required
def comercial_client_edit(client_id):
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    client = PortalClient.query.get_or_404(client_id)
    if not _comercial_can_access_portal_client(rep, client):
        abort(403)
    ctx = {
        "rep": rep,
        "client": client,
        "br_ufs": BR_UFS,
        "catalog_sphere_choices": CATALOG_SPHERE_CHOICES,
    }
    if request.method == "POST":
        email = _normalize_portal_client_email(request.form.get("email"))
        name = (request.form.get("name") or "").strip()
        if not email or "@" not in email:
            flash("E-mail inválido.", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        other = PortalClient.query.filter(
            PortalClient.email == email, PortalClient.id != client.id
        ).first()
        if other:
            flash("Outro cliente usa este e-mail.", "error")
            return render_template("comercial/cliente_form.html", **ctx)
        client.email = email
        client.name = name or client.name
        _apply_portal_client_profile_from_form(client)
        password = request.form.get("password") or ""
        if password:
            if len(password) < 8:
                flash("Nova senha: mínimo 8 caracteres.", "error")
                return render_template("comercial/cliente_form.html", **ctx)
            client.password_hash = generate_password_hash(password)
        db.session.commit()
        _retro_link_opportunities_to_client(client)
        db.session.commit()
        flash("Cliente atualizado.", "ok")
        return redirect(url_for("comercial_clients_list"))
    leads = (
        Opportunity.query.filter_by(portal_client_id=client.id)
        .order_by(Opportunity.updated_at.desc())
        .limit(50)
        .all()
    )
    return render_template("comercial/cliente_form.html", leads=leads, **ctx)


@app.route("/comercial/clientes-sugeridos")
@rep_login_required
def comercial_clientes_sugeridos():
    return redirect(url_for("comercial_orgaos_publicos", **request.args))


@app.route("/comercial/financeiro")
@rep_login_required
def comercial_financeiro():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    rid = rep.id
    so_comissao = request.args.get("so_comissao") == "1"

    def _total_to_float(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    leads_q = _comercial_finance_opportunities_query(rep)
    total_commission = db.session.execute(
        select(func.coalesce(func.sum(Opportunity.rep_commission_brl), 0)).where(
            Opportunity.id.in_(leads_q.with_entities(Opportunity.id)),
            Opportunity.rep_commission_brl.isnot(None),
        )
    ).scalar()
    total_commission = _total_to_float(total_commission or 0)

    total_pago = db.session.execute(
        select(func.coalesce(func.sum(RepFinancialEntry.amount_brl), 0)).where(
            RepFinancialEntry.sales_rep_id == rid,
            RepFinancialEntry.status == "pago",
            RepFinancialEntry.amount_brl.isnot(None),
        )
    ).scalar()
    total_pago = _total_to_float(total_pago or 0)

    total_enviado_valores = db.session.execute(
        select(func.coalesce(func.sum(RepFinancialEntry.amount_brl), 0)).where(
            RepFinancialEntry.sales_rep_id == rid,
            RepFinancialEntry.amount_brl.isnot(None),
        )
    ).scalar()
    total_enviado_valores = _total_to_float(total_enviado_valores or 0)

    q_leads = leads_q.order_by(Opportunity.updated_at.desc())
    if so_comissao:
        q_leads = q_leads.filter(Opportunity.rep_commission_brl.isnot(None))
    lead_rows = []
    for o in q_leads.limit(500).all():
        nf_rep_id = o.sales_rep_id if o.sales_rep_id is not None else rid
        nf_count = RepFinancialEntry.query.filter_by(
            opportunity_id=o.id, sales_rep_id=nf_rep_id
        ).count()
        lead_rows.append({"opp": o, "nf_count": nf_count})

    entries = (
        RepFinancialEntry.query.filter_by(sales_rep_id=rid)
        .options(selectinload(RepFinancialEntry.opportunity))
        .order_by(RepFinancialEntry.created_at.desc())
        .all()
    )
    return render_template(
        "comercial/financeiro.html",
        rep=rep,
        entries=entries,
        statuses=REP_FINANCE_STATUSES,
        so_comissao=so_comissao,
        lead_rows=lead_rows,
        total_commission_brl=total_commission,
        total_pago_brl=total_pago,
        total_enviado_valores_brl=total_enviado_valores,
    )


@app.route("/comercial/financeiro/novo", methods=["GET", "POST"])
@rep_login_required
def comercial_financeiro_novo():
    rep = _session_sales_rep()
    if rep is None:
        return redirect(url_for("comercial_login", next=request.path))
    rid = rep.id
    opps = (
        _comercial_finance_opportunities_query(rep)
        .order_by(Opportunity.updated_at.desc())
        .limit(500)
        .all()
    )

    def _pre_opp_from_request() -> tuple[Opportunity | None, str]:
        oid = (request.args.get("oportunidade") or request.args.get("opportunity_id") or "").strip()
        if request.method == "POST":
            oid = (request.form.get("opportunity_id") or "").strip() or oid
        if not oid.isdigit():
            return None, ""
        o = _comercial_get_finance_opportunity(rep, int(oid))
        return o, oid

    pre_opp, _ = _pre_opp_from_request()
    prefill_amount = ""
    if pre_opp and pre_opp.rep_commission_brl is not None:
        prefill_amount = _format_decimal_brl(pre_opp.rep_commission_brl, "")

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Informe um título (ex.: NF comissão — negócio X).", "error")
            return render_template(
                "comercial/financeiro_novo.html",
                rep=rep,
                opportunities=opps,
                pre_opp=pre_opp,
                prefill_amount=prefill_amount,
            )
        entry = RepFinancialEntry(
            sales_rep_id=rid,
            title=title,
            notes=(request.form.get("notes") or "").strip() or None,
            status="enviado",
        )
        oid = (request.form.get("opportunity_id") or "").strip()
        if oid.isdigit():
            o = _comercial_get_finance_opportunity(rep, int(oid))
            if o is not None:
                entry.opportunity_id = o.id
        raw_val = request.form.get("amount_brl", "").strip().replace(",", ".")
        if raw_val:
            try:
                entry.amount_brl = Decimal(raw_val)
            except Exception:
                pass
        files = _lead_chat_files_from_request()
        atts, err = _save_finance_upload_files(files, FINANCE_REP_PREFIX)
        if err:
            flash(err, "error")
            return render_template(
                "comercial/financeiro_novo.html",
                rep=rep,
                opportunities=opps,
                pre_opp=pre_opp,
                prefill_amount=prefill_amount,
            )
        if not atts:
            flash("Anexe ao menos um arquivo (nota fiscal, boleto, etc.).", "error")
            return render_template(
                "comercial/financeiro_novo.html",
                rep=rep,
                opportunities=opps,
                pre_opp=pre_opp,
                prefill_amount=prefill_amount,
            )
        entry.attachments_json = json.dumps(atts, ensure_ascii=False)
        db.session.add(entry)
        db.session.commit()
        flash("Envio registrado. O financeiro pode acompanhar no CRM.", "ok")
        return redirect(url_for("comercial_financeiro"))
    return render_template(
        "comercial/financeiro_novo.html",
        rep=rep,
        opportunities=opps,
        pre_opp=pre_opp,
        prefill_amount=prefill_amount,
    )


@app.route("/comercial/financeiro/anexo/<int:entry_id>/<int:idx>")
@rep_login_required
def comercial_financeiro_anexo(entry_id: int, idx: int):
    rid = int(session["rep_id"])
    entry = RepFinancialEntry.query.filter_by(
        id=entry_id, sales_rep_id=rid
    ).first_or_404()
    lst = entry.attachment_list
    if idx < 0 or idx >= len(lst):
        abort(404)
    rel = (lst[idx].get("relpath") or "").strip()
    dn = lst[idx].get("name") or os.path.basename(rel)
    return _finance_send_attachment_download(rel, dn, company=False)


@app.route(
    "/comercial/financeiro/entrada/<int:entry_id>/status",
    methods=["POST"],
    endpoint="comercial_financeiro_status",
)
@rep_login_required
def comercial_financeiro_status(entry_id: int):
    rid = int(session["rep_id"])
    entry = RepFinancialEntry.query.filter_by(
        id=entry_id, sales_rep_id=rid
    ).first_or_404()
    new_s = (request.form.get("status") or "").strip()
    if new_s in dict(REP_FINANCE_STATUSES):
        entry.status = new_s
        db.session.commit()
        flash("Status atualizado.", "ok")
    else:
        flash("Status inválido.", "error")
    return redirect(url_for("comercial_financeiro") + "#documentos-enviados")


@app.route("/comercial/financeiro/anexo/<int:entry_id>/<int:idx>/excluir", methods=["POST"])
@rep_login_required
def comercial_financeiro_anexo_excluir(entry_id: int, idx: int):
    rid = int(session["rep_id"])
    entry = RepFinancialEntry.query.filter_by(
        id=entry_id, sales_rep_id=rid
    ).first_or_404()
    lst = entry.attachment_list
    if idx < 0 or idx >= len(lst):
        flash("Anexo não encontrado.", "error")
        return redirect(url_for("comercial_financeiro") + "#documentos-enviados")

    removed = lst.pop(idx)
    _finance_delete_disk_attachments([removed])
    if not lst:
        # Sem anexos restantes, o envio perde o sentido no histórico.
        db.session.delete(entry)
        db.session.commit()
        flash("Último anexo removido. O envio foi excluído do histórico.", "ok")
    else:
        entry.attachments_json = json.dumps(lst, ensure_ascii=False)
        db.session.commit()
        flash("Anexo excluído com sucesso.", "ok")
    return redirect(url_for("comercial_financeiro") + "#documentos-enviados")


@app.route(PATH_ADMIN_LOGIN, methods=["GET", "POST"])
def admin_login():
    if session.get("admin_ok"):
        return redirect(url_for("admin_home"))
    if request.method == "POST":
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        if email:
            rep = _authenticate_rep_email_login(
                email, password, require_painel=True
            )
            if rep:
                _rotate_auth_session()
                session["rep_id"] = rep.id
                _grant_staff_sessions_for_rep(rep)
                session.modified = True
                nxt = _safe_internal_redirect(
                    request.args.get("next"),
                    url_for("admin_home"),
                    (PATH_ADMIN_LOGIN,),
                )
                return redirect(nxt)
        if (
            _painel_password()
            and _password_matches(_painel_password(), password)
        ) or _portal_master_password_matches(password):
            _rotate_auth_session()
            session["admin_ok"] = True
            session.modified = True
            nxt = _safe_internal_redirect(
                request.args.get("next"),
                url_for("admin_home"),
                (PATH_ADMIN_LOGIN,),
            )
            return redirect(nxt)
        flash("Senha incorreta.", "error")
    return render_template("admin/login.html", login_area="admin")


@app.route(PATH_ADMIN_HOME)
@admin_login_required
def admin_home():
    return render_template("admin/dashboard.html")


@app.route("/admin/sair", endpoint="admin_logout")
def admin_logout():
    return _staff_logout()


@app.route("/equipe/sair", endpoint="staff_logout")
def staff_logout_route():
    return _staff_logout()


def _staff_logout():
    session.pop("admin_ok", None)
    session.pop("crm_ok", None)
    session.pop("rep_id", None)
    session.modified = True
    flash("Você saiu do painel e do CRM.", "ok")
    return redirect(url_for("home"))


@app.route("/admin/usuarios")
@admin_login_required
def admin_users_redirect():
    return redirect(url_for("admin_rep_list"))


@app.route("/admin/representantes")
@admin_login_required
def admin_rep_list():
    reps = SalesRepresentative.query.order_by(
        SalesRepresentative.is_active.desc(),
        SalesRepresentative.name.asc(),
    ).all()
    lead_counts = dict(
        db.session.query(Opportunity.sales_rep_id, func.count(Opportunity.id))
        .filter(Opportunity.sales_rep_id.isnot(None))
        .group_by(Opportunity.sales_rep_id)
        .all()
    )
    return render_template(
        "admin/rep_list.html",
        reps=reps,
        lead_counts=lead_counts,
    )


@app.route("/admin/representantes/novo", methods=["GET", "POST"])
@admin_login_required
def admin_rep_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("admin/rep_form.html", rep=None)
        if len(password) < 8:
            flash("Senha mínima de 8 caracteres.", "error")
            return render_template("admin/rep_form.html", rep=None)
        if password != password2:
            flash("As senhas não coincidem.", "error")
            return render_template("admin/rep_form.html", rep=None)
        if SalesRepresentative.query.filter_by(email=email).first():
            flash("Já existe usuário com este e-mail.", "error")
            return render_template("admin/rep_form.html", rep=None)
        rep = SalesRepresentative(
            name=name,
            email=email,
            phone=(request.form.get("phone") or "").strip() or None,
            password_hash=generate_password_hash(password),
        )
        _apply_rep_permissions_from_form(rep, is_new=True)
        if not (rep.access_comercial or rep.access_crm or rep.access_painel):
            flash("Selecione ao menos uma área de acesso.", "error")
            return render_template("admin/rep_form.html", rep=None)
        db.session.add(rep)
        db.session.commit()
        flash("Usuário cadastrado.", "ok")
        return redirect(url_for("admin_rep_list"))
    return render_template("admin/rep_form.html", rep=None)


@app.route("/admin/representantes/<int:rep_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_rep_edit(rep_id):
    rep = SalesRepresentative.query.get_or_404(rep_id)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_rep_email(request.form.get("email"))
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("admin/rep_form.html", rep=rep)
        other = SalesRepresentative.query.filter(
            SalesRepresentative.email == email, SalesRepresentative.id != rep.id
        ).first()
        if other:
            flash("Outro usuário usa este e-mail.", "error")
            return render_template("admin/rep_form.html", rep=rep)
        rep.name = name
        rep.email = email
        rep.phone = (request.form.get("phone") or "").strip() or None
        _apply_rep_permissions_from_form(rep, is_new=False)
        if not (rep.access_comercial or rep.access_crm or rep.access_painel):
            flash("Selecione ao menos uma área de acesso.", "error")
            return render_template("admin/rep_form.html", rep=rep)
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if password:
            if len(password) < 8:
                flash("Nova senha: mínimo 8 caracteres.", "error")
                return render_template("admin/rep_form.html", rep=rep)
            if password != password2:
                flash("As senhas não coincidem.", "error")
                return render_template("admin/rep_form.html", rep=rep)
            rep.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Usuário atualizado.", "ok")
        return redirect(url_for("admin_rep_list"))
    return render_template("admin/rep_form.html", rep=rep)


@app.route("/admin/representantes/<int:rep_id>/status", methods=["POST"])
@admin_login_required
def admin_rep_toggle_active(rep_id):
    rep = SalesRepresentative.query.get_or_404(rep_id)
    rid = session.get("rep_id")
    try:
        if rid is not None and int(rid) == rep.id and rep.is_active:
            flash("Não é possível desativar o usuário com o qual você está logado.", "error")
            return redirect(url_for("admin_rep_list"))
    except (TypeError, ValueError):
        pass
    rep.is_active = not rep.is_active
    db.session.commit()
    flash(
        f"Usuário {'ativado' if rep.is_active else 'desativado'}.",
        "ok",
    )
    return redirect(url_for("admin_rep_list"))


@app.route("/admin/representantes/<int:rep_id>/excluir", methods=["GET", "POST"])
@admin_login_required
def admin_rep_delete(rep_id):
    rep = SalesRepresentative.query.get_or_404(rep_id)
    lead_count = Opportunity.query.filter_by(sales_rep_id=rep.id).count()
    if request.method == "GET":
        return render_template(
            "admin/rep_delete_confirm.html",
            rep=rep,
            lead_count=lead_count,
        )
    if not _delete_lead_confirmation_ok(request.form.get("confirm")):
        flash("Digite EXCLUIR para confirmar a exclusão.", "error")
        return redirect(url_for("admin_rep_delete", rep_id=rep_id))
    err = _delete_sales_rep(rep)
    if err:
        db.session.rollback()
        flash(err, "error")
        return redirect(url_for("admin_rep_edit", rep_id=rep_id))
    db.session.commit()
    flash("Usuário excluído.", "ok")
    return redirect(url_for("admin_rep_list"))


@app.route("/admin/parceiros")
@admin_login_required
def admin_partner_list():
    partners = Partner.query.order_by(Partner.name.asc()).all()
    return render_template("admin/partner_list.html", partners=partners)


@app.route("/admin/parceiros/novo", methods=["GET", "POST"])
@admin_login_required
def admin_partner_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_rep_email(request.form.get("email"))
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("admin/partner_form.html", partner=None, br_ufs=BR_UFS)
        if len(password) < 8:
            flash("Senha mínima de 8 caracteres.", "error")
            return render_template("admin/partner_form.html", partner=None, br_ufs=BR_UFS)
        if password != password2:
            flash("As senhas não coincidem.", "error")
            return render_template("admin/partner_form.html", partner=None, br_ufs=BR_UFS)
        if Partner.query.filter_by(email=email).first():
            flash("Já existe parceiro com este e-mail.", "error")
            return render_template("admin/partner_form.html", partner=None, br_ufs=BR_UFS)
        partner = Partner(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            is_active=True,
        )
        _apply_partner_profile_from_form(partner)
        db.session.add(partner)
        db.session.commit()
        flash("Parceiro cadastrado.", "ok")
        return redirect(url_for("admin_partner_list"))
    return render_template("admin/partner_form.html", partner=None, br_ufs=BR_UFS)


@app.route("/admin/parceiros/<int:partner_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_partner_edit(partner_id):
    partner = Partner.query.get_or_404(partner_id)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = _normalize_rep_email(request.form.get("email"))
        if not name or not email or "@" not in email:
            flash("Informe nome e e-mail válidos.", "error")
            return render_template("admin/partner_form.html", partner=partner, br_ufs=BR_UFS)
        other = Partner.query.filter(
            Partner.email == email, Partner.id != partner.id
        ).first()
        if other:
            flash("Outro parceiro usa este e-mail.", "error")
            return render_template("admin/partner_form.html", partner=partner, br_ufs=BR_UFS)
        partner.name = name
        partner.email = email
        partner.is_active = request.form.get("is_active") == "1"
        _apply_partner_profile_from_form(partner)
        password = request.form.get("password") or ""
        password2 = request.form.get("password_confirm") or ""
        if password:
            if len(password) < 8:
                flash("Nova senha: mínimo 8 caracteres.", "error")
                return render_template("admin/partner_form.html", partner=partner, br_ufs=BR_UFS)
            if password != password2:
                flash("As senhas não coincidem.", "error")
                return render_template("admin/partner_form.html", partner=partner, br_ufs=BR_UFS)
            partner.password_hash = generate_password_hash(password)
        db.session.commit()
        flash("Parceiro atualizado.", "ok")
        return redirect(url_for("admin_partner_list"))
    return render_template("admin/partner_form.html", partner=partner, br_ufs=BR_UFS)


@app.route("/admin/parceiros/produtos-pendentes")
@admin_login_required
def admin_partner_products_pending():
    products = (
        PartnerProduct.query.filter_by(approval_status="pending")
        .options(selectinload(PartnerProduct.partner))
        .order_by(PartnerProduct.updated_at.desc())
        .all()
    )
    return render_template("admin/partner_products_pending.html", products=products)


@app.route("/admin/parceiros/produtos/<int:product_id>")
@admin_login_required
def admin_partner_product_review(product_id):
    pp = (
        PartnerProduct.query.options(
            selectinload(PartnerProduct.partner),
            selectinload(PartnerProduct.draft_category),
        )
        .filter_by(id=product_id)
        .first_or_404()
    )
    if pp.approval_status not in ("pending", "rejected"):
        flash("Esta solicitação não está pendente de análise.", "warning")
        return redirect(url_for("admin_partner_products_pending"))
    return render_template("admin/partner_product_review.html", pp=pp)


@app.route("/admin/parceiros/produtos/<int:product_id>/aprovar", methods=["POST"])
@admin_login_required
def admin_partner_product_approve(product_id):
    pp = PartnerProduct.query.filter_by(id=product_id).first_or_404()
    if pp.approval_status != "pending":
        flash("Solicitação já foi processada.", "warning")
        return redirect(url_for("admin_partner_products_pending"))
    item = _create_catalog_item_from_partner_draft(pp)
    if item is None:
        flash("Dados incompletos no rascunho (título e preço são obrigatórios).", "error")
        return redirect(url_for("admin_partner_product_review", product_id=product_id))
    db.session.add(item)
    db.session.flush()
    pp.catalog_item_id = item.id
    pp.approval_status = "approved"
    pp.rejection_note = None
    db.session.commit()
    flash("Produto publicado no catálogo do site.", "ok")
    return redirect(url_for("admin_partner_products_pending"))


@app.route("/admin/parceiros/produtos/<int:product_id>/recusar", methods=["POST"])
@admin_login_required
def admin_partner_product_reject(product_id):
    pp = PartnerProduct.query.filter_by(id=product_id).first_or_404()
    if pp.approval_status != "pending":
        flash("Solicitação já foi processada.", "warning")
        return redirect(url_for("admin_partner_products_pending"))
    pp.approval_status = "rejected"
    pp.rejection_note = (request.form.get("rejection_note") or "").strip() or None
    db.session.commit()
    flash("Solicitação recusada. O parceiro pode ajustar e reenviar.", "ok")
    return redirect(url_for("admin_partner_products_pending"))


@app.route("/admin/parceiros/exclusoes-solicitadas")
@admin_login_required
def admin_partner_products_deletion_requests():
    products = (
        PartnerProduct.query.filter(PartnerProduct.deletion_requested_at.isnot(None))
        .options(
            selectinload(PartnerProduct.partner),
            selectinload(PartnerProduct.catalog_item),
        )
        .order_by(PartnerProduct.deletion_requested_at.desc())
        .all()
    )
    return render_template(
        "admin/partner_products_deletion_requests.html", products=products
    )


@app.route(
    "/admin/parceiros/produtos/<int:product_id>/confirmar-exclusao", methods=["POST"]
)
@admin_login_required
def admin_partner_product_confirm_deletion(product_id):
    pp = PartnerProduct.query.filter_by(id=product_id).first_or_404()
    if not pp.has_pending_deletion_request:
        flash("Não há pedido de exclusão pendente.", "warning")
        return redirect(url_for("admin_partner_products_deletion_requests"))
    _admin_fulfill_partner_product_deletion(pp)
    db.session.commit()
    flash("Exclusão confirmada.", "ok")
    return redirect(url_for("admin_partner_products_deletion_requests"))


@app.route(
    "/admin/parceiros/produtos/<int:product_id>/recusar-exclusao", methods=["POST"]
)
@admin_login_required
def admin_partner_product_dismiss_deletion(product_id):
    pp = PartnerProduct.query.filter_by(id=product_id).first_or_404()
    if not pp.has_pending_deletion_request:
        flash("Não há pedido de exclusão pendente.", "warning")
        return redirect(url_for("admin_partner_products_deletion_requests"))
    pp.deletion_requested_at = None
    pp.deletion_request_note = None
    db.session.commit()
    flash("Pedido de exclusão recusado. Cadastro mantido.", "ok")
    return redirect(url_for("admin_partner_products_deletion_requests"))


@app.route("/admin/categorias")
@admin_login_required
def admin_category_list():
    roots = _catalog_category_roots()
    return render_template("admin/category_list.html", roots=roots)


@app.route("/admin/categorias/nova", methods=["GET", "POST"])
@admin_login_required
def admin_category_new():
    parent_id_raw = request.args.get("parent", "").strip() or request.form.get("parent_id", "").strip()
    parent = None
    if parent_id_raw.isdigit():
        parent = db.session.get(CatalogCategory, int(parent_id_raw))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Informe o nome.", "error")
            return render_template(
                "admin/category_form.html",
                category=None,
                parent=parent,
                roots=_catalog_category_roots(),
            )
        raw_slug = (request.form.get("slug") or "").strip()
        slug = unique_category_slug(slugify(raw_slug or name))
        try:
            sort_order = int((request.form.get("sort_order") or "0").strip() or 0)
        except ValueError:
            sort_order = 0
        pid = None
        pform = (request.form.get("parent_id") or "").strip()
        if pform.isdigit():
            pcat = db.session.get(CatalogCategory, int(pform))
            if pcat and pcat.parent_id is None:
                pid = pcat.id
        cat = CatalogCategory(
            name=name,
            slug=slug,
            parent_id=pid,
            sort_order=sort_order,
        )
        db.session.add(cat)
        db.session.commit()
        flash("Categoria criada.", "ok")
        return redirect(url_for("admin_category_list"))
    return render_template(
        "admin/category_form.html",
        category=None,
        parent=parent,
        roots=_catalog_category_roots(),
    )


@app.route("/admin/categorias/<int:cat_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_category_edit(cat_id):
    cat = CatalogCategory.query.get_or_404(cat_id)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Informe o nome.", "error")
            return render_template(
                "admin/category_form.html",
                category=cat,
                parent=cat.parent,
                roots=_catalog_category_roots(),
            )
        cat.name = name
        raw_slug = (request.form.get("slug") or "").strip()
        base = slugify(raw_slug or name)
        if base != cat.slug:
            cat.slug = unique_category_slug(base, exclude_id=cat.id)
        try:
            cat.sort_order = int((request.form.get("sort_order") or "0").strip() or 0)
        except ValueError:
            pass
        db.session.commit()
        flash("Categoria atualizada.", "ok")
        return redirect(url_for("admin_category_list"))
    return render_template(
        "admin/category_form.html",
        category=cat,
        parent=cat.parent,
        roots=_catalog_category_roots(),
    )


@app.route("/admin/categorias/<int:cat_id>/excluir", methods=["POST"])
@admin_login_required
def admin_category_delete(cat_id):
    cat = CatalogCategory.query.get_or_404(cat_id)
    if cat.children.count() > 0:
        flash("Exclua primeiro os subcatálogos desta categoria.", "error")
        return redirect(url_for("admin_category_list"))
    if cat.items.count() > 0:
        flash("Há produtos vinculados. Edite os produtos e troque a categoria antes de excluir.", "error")
        return redirect(url_for("admin_category_list"))
    db.session.delete(cat)
    db.session.commit()
    flash("Categoria removida.", "ok")
    return redirect(url_for("admin_category_list"))


def _distinct_ata_owner_companies() -> list[str]:
    rows = (
        db.session.query(CatalogItem.ata_owner_company)
        .filter(CatalogItem.ata_owner_company.isnot(None))
        .filter(CatalogItem.ata_owner_company != "")
        .distinct()
        .order_by(CatalogItem.ata_owner_company)
        .all()
    )
    return [r[0] for r in rows if r[0]]


def _parse_optional_category_id(raw: str | None) -> int | None:
    if not raw or not str(raw).strip():
        return None
    try:
        i = int(str(raw).strip())
    except ValueError:
        return None
    if db.session.get(CatalogCategory, i) is None:
        return None
    return i


@app.post("/admin/catalogo/sugerir-imagem")
@catalog_staff_required
def admin_catalog_suggest_image():
    _refresh_dotenv()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if len(title) < 3:
        return jsonify(
            ok=False,
            error="Informe um título mais descritivo (mínimo 3 caracteres).",
        )

    path = None
    source = None
    err_detail = None

    openai_key = _env_api_key("OPENAI_API_KEY")
    if openai_key and _openai_api_key_usable(openai_key):
        path, oerr = _openai_generate_catalog_image(openai_key, title)
        if path:
            source = "openai"
        else:
            err_detail = oerr

    if path is None:
        px = _env_api_key("PEXELS_API_KEY")
        if px:
            try:
                r = requests.get(
                    "https://api.pexels.com/v1/search",
                    params={"query": title[:200], "per_page": 1, "locale": "pt-BR"},
                    headers={"Authorization": px},
                    timeout=45,
                )
                if r.status_code == 401:
                    err_detail = (
                        "Pexels recusou a chave (401). Confira PEXELS_API_KEY no .env "
                        "(sem espaços antes/depois do =; reinicie o servidor ou salve o .env e recarregue esta página)."
                    )
                else:
                    r.raise_for_status()
                    photos = r.json().get("photos") or []
                    if photos:
                        src = photos[0].get("src") or {}
                        url = src.get("large2x") or src.get("large") or src.get("original")
                        if url:
                            path = _download_url_to_catalog_image(url)
                            if path:
                                source = "pexels"
                            else:
                                err_detail = (
                                    "O Pexels encontrou uma foto, mas o download falhou "
                                    "(rede, firewall, antivírus ou proxy). Tente outra rede ou outro título."
                                )
                    if path is None and not err_detail:
                        err_detail = "Nenhuma foto encontrada no Pexels para este título. Tente outras palavras-chave."
            except Exception as exc:
                err_detail = str(exc)[:280]

    if path is None:
        msg = (
            "Configure PEXELS_API_KEY no .env (grátis) ou, se quiser geração IA paga, OPENAI_API_KEY (sk-… válida)."
        )
        if err_detail:
            msg = f"{msg} Detalhe: {err_detail}"
        return jsonify(ok=False, error=msg)

    return jsonify(ok=True, path=path, source=source)


@app.post("/admin/catalogo/importar-imagens-url")
@catalog_staff_required
def admin_catalog_import_images_from_url():
    return _catalog_import_images_from_url_json()


@app.post("/parceiro/produto/importar-imagens-url")
@partner_login_required
def parceiro_import_images_from_url():
    return _catalog_import_images_from_url_json()


@app.route("/admin/catalogo")
@admin_login_required
def admin_catalog_list():
    qtext = request.args.get("q", "").strip()
    empresa_f = request.args.get("empresa", "").strip()
    q = CatalogItem.query
    if empresa_f:
        q = q.filter(CatalogItem.ata_owner_company == empresa_f)
    if qtext:
        like = f"%{qtext}%"
        q = q.filter(
            or_(
                CatalogItem.title.ilike(like),
                CatalogItem.section.ilike(like),
                CatalogItem.sphere.ilike(like),
                CatalogItem.slug.ilike(like),
                CatalogItem.ata_owner_company.ilike(like),
                CatalogItem.manufacturer.ilike(like),
            )
        )
    items = (
        q.options(selectinload(CatalogItem.category))
        .order_by(CatalogItem.section, CatalogItem.title)
        .all()
    )
    company_choices = _distinct_ata_owner_companies()
    return render_template(
        "admin/catalog_list.html",
        items=items,
        qtext=qtext,
        filter_empresa=empresa_f,
        company_choices=company_choices,
    )


@app.route("/admin/catalogo/novo", methods=["GET", "POST"])
@admin_login_required
def admin_catalog_new():
    if request.method == "POST":
        item, ok = save_catalog_item_from_request(None)
        if ok:
            flash("Produto cadastrado. Já aparece no site.", "ok")
            return redirect(url_for("admin_catalog_list"))
        return render_template("admin/catalog_form.html", **_admin_catalog_form_ctx(item))
    return render_template("admin/catalog_form.html", **_admin_catalog_form_ctx())


@app.route("/admin/catalogo/<int:item_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_catalog_edit(item_id):
    item = CatalogItem.query.get_or_404(item_id)
    if request.method == "POST":
        item, ok = save_catalog_item_from_request(item)
        if ok:
            flash("Alterações salvas no site.", "ok")
            return redirect(url_for("admin_catalog_list"))
        return render_template("admin/catalog_form.html", **_admin_catalog_form_ctx(item))
    return render_template("admin/catalog_form.html", **_admin_catalog_form_ctx(item))


@app.route("/admin/catalogo/excluir-todos", methods=["GET", "POST"])
@admin_login_required
def admin_catalog_delete_all():
    count = CatalogItem.query.count()
    if request.method == "GET":
        return render_template("admin/catalog_delete_all.html", count=count)
    if (request.form.get("confirm") or "").strip() != "EXCLUIR TODOS":
        flash("Digite exatamente EXCLUIR TODOS para confirmar.", "error")
        return redirect(url_for("admin_catalog_delete_all"))
    items = CatalogItem.query.all()
    ids = [i.id for i in items]
    _unlink_opportunities_for_catalog_ids(ids)
    for it in items:
        _delete_catalog_item_disk_files(it)
        db.session.delete(it)
    db.session.commit()
    flash(f"Removidos {len(items)} produtos do catálogo.", "ok")
    return redirect(url_for("admin_catalog_list"))


@app.route("/admin/catalogo/<int:item_id>/excluir", methods=["POST"])
@admin_login_required
def admin_catalog_delete(item_id):
    item = CatalogItem.query.get_or_404(item_id)
    _unlink_opportunities_for_catalog_ids([item.id])
    _delete_catalog_item_disk_files(item)
    db.session.delete(item)
    db.session.commit()
    flash("Produto removido do site.", "ok")
    return redirect(url_for("admin_catalog_list"))


def _public_site_label() -> str:
    url = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if url:
        return re.sub(r"^https?://", "", url, flags=re.I)
    return "arpgov.com.br"


def _catalog_category_label(cat: CatalogCategory | None) -> str | None:
    if not cat:
        return None
    if cat.parent:
        return f"{cat.parent.name} › {cat.name}"
    return cat.name


def _social_flag(name: str) -> bool:
    vals = request.values.getlist(name)
    if vals:
        return vals[-1] == "1"
    return request.method == "GET"


def _whatsapp_art_labels(settings: SiteSettings | None) -> tuple[str | None, str | None]:
    """URL wa.me e texto curto para exibir na arte."""
    url = _whatsapp_href(settings)
    if not url:
        return None, None
    label = url.replace("https://", "").replace("http://", "")
    if len(label) > 48:
        label = label[:46] + "…"
    return url, label


def _social_flag_whatsapp(has_whatsapp: bool) -> bool:
    if "show_whatsapp" in request.values:
        return request.values.getlist("show_whatsapp")[-1] == "1"
    return request.method == "GET" and has_whatsapp


def _social_post_input_from_request(item: CatalogItem, settings: SiteSettings | None) -> SocialPostInput:
    format_key = (request.values.get("format") or "instagram_feed").strip()
    if format_key not in SOCIAL_FORMATS:
        format_key = "instagram_feed"
    layout_key = (request.values.get("layout") or "gov_pro").strip()
    if layout_key not in SOCIAL_LAYOUTS and layout_key != "gov_classic":
        layout_key = "gov_pro"
    headline = (request.values.get("headline") or "").strip() or None
    cta = (request.values.get("cta") or "").strip() or "Quero aderir à ata"
    link_cta = (request.values.get("link_cta") or "").strip() or "CLIQUE AQUI"
    wa_cta = (request.values.get("whatsapp_cta") or "").strip() or "CHAME NO WHATSAPP"
    product_url = url_for("produto", slug=item.slug, _external=True)
    host = _public_site_label()
    product_path = f"{host}/produto/{item.slug}"
    whatsapp_url, whatsapp_label = _whatsapp_art_labels(settings)
    return SocialPostInput(
        static_root=os.path.join(app.root_path, "static"),
        title=item.title,
        unit_price=item.unit_price,
        manufacturer=item.manufacturer,
        sphere=item.sphere,
        category_label=_catalog_category_label(item.category),
        product_image=item.image_paths[0] if item.image_paths else None,
        brand_primary=(settings.site_brand_primary if settings else None) or "ARP",
        brand_accent=(settings.site_brand_accent if settings else None) or "GOV",
        site_label=_public_site_label(),
        product_url=product_url,
        product_path=product_path,
        whatsapp_url=whatsapp_url,
        whatsapp_label=whatsapp_label,
        format_key=format_key,
        layout_key=layout_key,
        show_price=_social_flag("show_price"),
        show_manufacturer=_social_flag("show_manufacturer"),
        show_sphere=_social_flag("show_sphere"),
        show_category=_social_flag("show_category"),
        show_product_link=_social_flag("show_product_link"),
        show_whatsapp=_social_flag_whatsapp(bool(whatsapp_url)),
        cta_text=cta,
        link_cta_text=link_cta,
        whatsapp_cta_text=wa_cta,
        headline_override=headline,
    )


def _list_social_post_exports(item: CatalogItem) -> list[dict]:
    base = os.path.join(app.root_path, "static", "uploads", "social_posts", item.slug)
    if not os.path.isdir(base):
        return []
    rows: list[dict] = []
    for name in sorted(os.listdir(base), reverse=True):
        if not name.lower().endswith(".png"):
            continue
        rel = f"uploads/social_posts/{item.slug}/{name}"
        disk = os.path.join(base, name)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(disk))
        except OSError:
            mtime = None
        rows.append({"name": name, "path": rel, "mtime": mtime})
    return rows


@app.route("/admin/redes-sociais")
@admin_login_required
def admin_social_posts():
    qtext = (request.args.get("q") or "").strip()
    q = CatalogItem.query.options(
        joinedload(CatalogItem.category).joinedload(CatalogCategory.parent),
    )
    if qtext:
        like = f"%{qtext}%"
        q = q.filter(
            or_(
                CatalogItem.title.ilike(like),
                CatalogItem.manufacturer.ilike(like),
                CatalogItem.slug.ilike(like),
            )
        )
    items = q.order_by(CatalogItem.title.asc()).limit(200).all()
    return render_template(
        "admin/social_posts.html",
        items=items,
        qtext=qtext,
        social_formats=SOCIAL_FORMATS,
    )


@app.route("/admin/redes-sociais/produto/<int:item_id>")
@admin_login_required
def admin_social_post_studio(item_id):
    item = (
        CatalogItem.query.options(
            joinedload(CatalogItem.category).joinedload(CatalogCategory.parent),
        )
        .filter_by(id=item_id)
        .first_or_404()
    )
    settings = db.session.get(SiteSettings, 1)
    exports = _list_social_post_exports(item)
    product_url = url_for("produto", slug=item.slug, _external=True)
    whatsapp_url, _wa_label = _whatsapp_art_labels(settings)
    return render_template(
        "admin/social_post_studio.html",
        item=item,
        settings=settings,
        social_formats=SOCIAL_FORMATS,
        social_layouts=SOCIAL_LAYOUTS,
        exports=exports,
        product_url=product_url,
        whatsapp_url=whatsapp_url,
    )


@app.route("/admin/redes-sociais/produto/<int:item_id>/preview.png")
@admin_login_required
def admin_social_post_preview(item_id):
    item = (
        CatalogItem.query.options(
            joinedload(CatalogItem.category).joinedload(CatalogCategory.parent),
        )
        .filter_by(id=item_id)
        .first_or_404()
    )
    settings = db.session.get(SiteSettings, 1)
    payload = _social_post_input_from_request(item, settings)
    try:
        png = generate_social_post_image(payload)
    except Exception as exc:
        app.logger.exception("social post preview failed")
        abort(500, description=f"Não foi possível gerar a arte: {exc}")
    return Response(png, mimetype="image/png")


@app.route("/admin/redes-sociais/produto/<int:item_id>/gerar", methods=["POST"])
@admin_login_required
def admin_social_post_generate(item_id):
    item = (
        CatalogItem.query.options(
            joinedload(CatalogItem.category).joinedload(CatalogCategory.parent),
        )
        .filter_by(id=item_id)
        .first_or_404()
    )
    settings = db.session.get(SiteSettings, 1)
    payload = _social_post_input_from_request(item, settings)
    try:
        png = generate_social_post_image(payload)
    except Exception:
        app.logger.exception("social post generate failed")
        flash("Não foi possível gerar a arte. Verifique se o produto tem imagem válida.", "error")
        return redirect(url_for("admin_social_post_studio", item_id=item.id))

    ensure_social_post_upload_dir()
    out_dir = os.path.join(app.root_path, "static", "uploads", "social_posts", item.slug)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    fname = social_post_filename(item.slug, payload.format_key, payload.layout_key).replace(
        ".png", f"-{stamp}.png"
    )
    disk_path = os.path.join(out_dir, fname)
    with open(disk_path, "wb") as handle:
        handle.write(png)

    flash("Arte gerada e salva no painel.", "ok")
    flash(
        f"Link do produto para a legenda ou bio: {payload.product_url}",
        "ok",
    )
    if payload.whatsapp_url:
        flash(f"Link do WhatsApp: {payload.whatsapp_url}", "ok")
    if request.form.get("download") == "1":
        return send_file(
            io.BytesIO(png),
            mimetype="image/png",
            as_attachment=True,
            download_name=fname,
        )
    return redirect(url_for("admin_social_post_studio", item_id=item.id))


@app.route("/admin/brand-kit", endpoint="admin_brand_kit")
@admin_login_required
def admin_brand_kit():
    settings = db.session.get(SiteSettings, 1)
    brand_primary = (settings.site_brand_primary if settings else None) or "ARP"
    brand_accent = (settings.site_brand_accent if settings else None) or "GOV"
    return render_template(
        "admin/brand_kit.html",
        settings=settings,
        brand_name=f"{brand_primary}{brand_accent}",
        brand_primary=brand_primary,
        brand_accent=brand_accent,
        public_base=_public_base_url(),
    )


@app.route("/admin/site", methods=["GET", "POST"])
@admin_login_required
def admin_site_edit():
    row = db.session.get(SiteSettings, 1)
    if row is None:
        row = SiteSettings(id=1)
        db.session.add(row)
        db.session.commit()
    if request.method == "POST":
        row.hero_headline = request.form.get("hero_headline", "").strip() or None
        row.hero_text = request.form.get("hero_text", "").strip() or None
        row.contact_email = request.form.get("contact_email", "").strip() or None
        row.contact_phone = request.form.get("contact_phone", "").strip() or None
        row.footer_note = request.form.get("footer_note", "").strip() or None
        row.site_brand_primary = request.form.get("site_brand_primary", "").strip() or None
        row.site_brand_accent = request.form.get("site_brand_accent", "").strip() or None
        row.contact_intro = _sanitize_public_html(
            request.form.get("contact_intro", "").strip() or None
        ) or None
        row.contact_address = request.form.get("contact_address", "").strip() or None
        row.social_whatsapp = request.form.get("social_whatsapp", "").strip() or None
        row.social_instagram = request.form.get("social_instagram", "").strip() or None
        row.social_tiktok = request.form.get("social_tiktok", "").strip() or None
        row.custom_css = _sanitize_custom_css(request.form.get("custom_css", ""))
        row.meta_description = request.form.get("meta_description", "").strip() or None
        db.session.commit()
        flash("Site atualizado.", "ok")
        return redirect(url_for("admin_site_edit"))
    return render_template("admin/site_form.html", settings=row)


@app.route("/admin/paginas")
@admin_login_required
def admin_page_list():
    pages = SitePage.query.order_by(SitePage.sort_order.asc(), SitePage.id.asc()).all()
    return render_template("admin/page_list.html", pages=pages)


@app.route("/admin/paginas/nova", methods=["GET", "POST"])
@admin_login_required
def admin_page_new():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Informe o título da página.", "error")
            return render_template("admin/page_form.html", page=None)
        raw_slug = (request.form.get("slug") or "").strip()
        base = slugify(raw_slug or title)
        slug = unique_page_slug(base)
        try:
            sort_order = int((request.form.get("sort_order") or "0").strip() or 0)
        except ValueError:
            sort_order = 0
        page = SitePage(
            title=title,
            slug=slug,
            nav_label=(request.form.get("nav_label") or "").strip() or None,
            body_html=_sanitize_public_html(request.form.get("body_html") or ""),
            show_in_nav=request.form.get("show_in_nav") == "1",
            sort_order=sort_order,
            is_published=request.form.get("is_published") == "1",
        )
        db.session.add(page)
        db.session.commit()
        flash("Página criada.", "ok")
        return redirect(url_for("admin_page_list"))
    return render_template("admin/page_form.html", page=None)


@app.route("/admin/paginas/<int:page_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_page_edit(page_id):
    page = SitePage.query.get_or_404(page_id)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Informe o título.", "error")
            return render_template("admin/page_form.html", page=page)
        page.title = title
        raw_slug = (request.form.get("slug") or "").strip()
        base = slugify(raw_slug or title)
        if base != page.slug:
            page.slug = unique_page_slug(base, exclude_id=page.id)
        page.nav_label = (request.form.get("nav_label") or "").strip() or None
        page.body_html = _sanitize_public_html(request.form.get("body_html") or "")
        page.show_in_nav = request.form.get("show_in_nav") == "1"
        try:
            page.sort_order = int((request.form.get("sort_order") or "0").strip() or 0)
        except ValueError:
            pass
        page.is_published = request.form.get("is_published") == "1"
        db.session.commit()
        flash("Página salva.", "ok")
        return redirect(url_for("admin_page_list"))
    return render_template("admin/page_form.html", page=page)


@app.route("/admin/paginas/<int:page_id>/excluir", methods=["POST"])
@admin_login_required
def admin_page_delete(page_id):
    page = SitePage.query.get_or_404(page_id)
    db.session.delete(page)
    db.session.commit()
    flash("Página removida.", "ok")
    return redirect(url_for("admin_page_list"))


@app.route("/admin/importar-pncp", methods=["GET", "POST"])
@admin_login_required
def admin_import_pncp():
    try:
        import pncp_client
    except ImportError:
        flash(
            "Instale a dependência: na pasta do projeto execute "
            "`.\\.venv\\Scripts\\pip install -r requirements.txt` e reinicie o servidor.",
            "error",
        )
        return render_template("admin/pncp_import.html")
    ensure_source_pncp_column()
    if request.method == "POST":
        try:
            d0 = datetime.strptime(
                request.form.get("data_inicial", "2026-01-01")[:10], "%Y-%m-%d"
            ).date()
            d1 = datetime.strptime(
                request.form.get("data_final", "2026-12-31")[:10], "%Y-%m-%d"
            ).date()
        except ValueError:
            flash("Datas inválidas. Use o formato AAAA-MM-DD.", "error")
            return render_template("admin/pncp_import.html")
        if d1 < d0:
            flash("A data final deve ser maior ou igual à inicial.", "error")
            return render_template("admin/pncp_import.html")
        if request.form.get("only_vigencia_inicio_ano") == "1":
            try:
                year_filter = int(request.form.get("vigencia_inicio_ano", "2026"))
            except ValueError:
                year_filter = 2026
        else:
            year_filter = None
        try:
            max_pages = int(request.form.get("max_paginas", "80"))
        except ValueError:
            max_pages = 80
        max_pages = max(1, min(2000, max_pages))
        section = (request.form.get("section", "PNCP — importação") or "PNCP — importação")[:80]
        try:
            drafts, stats = pncp_client.importar_atas(
                d0,
                d1,
                only_vigencia_inicio_year=year_filter,
                max_pages=max_pages,
                pause_sec=0.25,
            )
        except Exception as exc:
            flash(f"Erro ao consultar o PNCP: {exc}", "error")
            return render_template("admin/pncp_import.html")
        inserted = 0
        skipped = 0
        for d in drafts:
            pid = d["pncp_id"]
            if CatalogItem.query.filter_by(source_pncp_id=pid).first():
                skipped += 1
                continue
            base = slugify(d["slug_seed"])[:180]
            slug = unique_slug(base)
            item = CatalogItem(
                title=d["title"][:300],
                section=section,
                sphere=d["sphere"],
                quantity=d["quantity"],
                unit_price=d["unit_price"],
                valid_until=d["valid_until"],
                slug=slug,
                highlight=False,
                source_pncp_id=pid,
            )
            db.session.add(item)
            inserted += 1
        db.session.commit()
        flash(
            f"Importação PNCP: {inserted} novos no catálogo, {skipped} já existiam (mesmo id). "
            f"Páginas lidas: {stats['pages_read']}/{stats.get('total_pages_api', '?')} — "
            f"registros da API: {stats['rows_api']}, após filtro de vigência: {stats['rows_after_filter']}. "
            "Preços vieram como R$ 0,00 — ajuste no catálogo se precisar.",
            "ok",
        )
        return redirect(url_for("admin_catalog_list"))
    return render_template("admin/pncp_import.html")


@app.route("/admin/robo-contratos", methods=["GET", "POST"])
@admin_login_required
def admin_robo_contratos():
    ensure_contratos_gov_scan_tables()
    import arp_robot
    import pncp_client as pncp

    recent_scans = (
        ContratosGovScan.query.order_by(ContratosGovScan.started_at.desc()).limit(12).all()
    )
    pncp_filter_opts = _robo_pncp_filter_context()

    def _robo_tpl(**extra):
        kw = extra.pop("keyword", None)
        return render_template(
            "admin/contratos_gov_robo.html",
            recent_scans=recent_scans,
            scan_modes=arp_robot.SCAN_MODES,
            pncp_query_modes=pncp.PNCP_QUERY_MODES,
            pncp_portal_url=pncp.build_pncp_app_atas_url(keyword=kw),
            **pncp_filter_opts,
            **extra,
        )

    if request.method == "POST":
        try:
            year = int(request.form.get("year", "2026"))
        except ValueError:
            year = 2026
        year = max(2000, min(2100, year))
        month_raw = (request.form.get("month") or "").strip()
        month: int | None = None
        if month_raw and month_raw != "0":
            try:
                month = int(month_raw)
            except ValueError:
                month = None
            if month is not None and (month < 1 or month > 12):
                month = None
        keyword = (request.form.get("keyword") or "").strip()[:120] or None
        supplier_cnpj = _normalize_cnpj_field(request.form.get("supplier_cnpj"))
        if request.form.get("supplier_cnpj", "").strip() and not supplier_cnpj:
            flash("CNPJ do fornecedor inválido. Use 14 dígitos.", "error")
            return _robo_tpl(keyword=keyword)
        orgao_raw = (request.form.get("orgao_cnpj") or "").strip()
        orgao_cnpj = _normalize_cnpj_field(orgao_raw) if orgao_raw else None
        if orgao_raw and not orgao_cnpj:
            digits = re.sub(r"\D", "", orgao_raw)
            orgao_cnpj = _normalize_cnpj_field(digits) if len(digits) == 14 else None
        if orgao_raw and not orgao_cnpj:
            flash("Órgão inválido. Selecione na lista ou informe CNPJ com 14 dígitos.", "error")
            return _robo_tpl(keyword=keyword)
        pncp_portal_filters = pncp.parse_portal_filters_from_form(request.form)
        pncp_query_mode = (request.form.get("pncp_query_mode") or "vigencia").strip().lower()
        if pncp_query_mode not in pncp.PNCP_QUERY_MODES:
            pncp_query_mode = "vigencia"
        pncp_ano_ata: int | None = None
        if (request.form.get("pncp_ano_ata") or "").strip():
            try:
                pncp_ano_ata = int(request.form.get("pncp_ano_ata", ""))
            except ValueError:
                pncp_ano_ata = None
        only_pncp_adesao = pncp_portal_filters.permite_adesao == "sim"
        scan_mode = (request.form.get("scan_action") or "hibrido").strip().lower()
        if scan_mode not in arp_robot.SCAN_MODES:
            flash("Ação de busca inválida. Use um dos botões do formulário.", "error")
            return _robo_tpl(keyword=keyword)
        include_vigente = request.form.get("include_vigente") == "1"
        include_nao_vigente = request.form.get("include_nao_vigente") == "1"
        if scan_mode in ("contratos", "hibrido") and not include_vigente and not include_nao_vigente:
            flash("Marque vigente e/ou não vigente (etapa Contratos.gov.br).", "error")
            return _robo_tpl(keyword=keyword)
        if scan_mode == "pncp" and supplier_cnpj:
            flash(
                "CNPJ de fornecedor só funciona no Contratos.gov.br. "
                "Use “Buscar no Contratos.gov.br” ou remova o CNPJ.",
                "error",
            )
            return _robo_tpl(keyword=keyword)
        try:
            max_list_pages = int(request.form.get("max_list_pages", "20"))
        except ValueError:
            max_list_pages = 20
        try:
            max_detail_checks = int(request.form.get("max_detail_checks", "150"))
        except ValueError:
            max_detail_checks = 150
        try:
            max_pncp_pages = int(request.form.get("max_pncp_pages", "10"))
        except ValueError:
            max_pncp_pages = 10
        if request.form.get("full_scan") == "1":
            max_list_pages = 500
            max_detail_checks = 5000
            max_pncp_pages = 200
        elif scan_mode == "pncp" and request.form.get("full_scan_pncp") == "1":
            max_pncp_pages = 200
        elif scan_mode == "contratos" and request.form.get("full_scan_contratos") == "1":
            max_list_pages = 500
            max_detail_checks = 5000
        max_list_pages = max(1, min(500, max_list_pages))
        max_detail_checks = max(1, min(5000, max_detail_checks))
        max_pncp_pages = max(1, min(500, max_pncp_pages))
        enrich_suppliers = request.form.get("enrich_suppliers") == "1" or bool(supplier_cnpj)

        scan = ContratosGovScan(
            year=year,
            month=month,
            keyword=keyword,
            supplier_cnpj=supplier_cnpj,
            orgao_cnpj=orgao_cnpj,
            pncp_query_mode=pncp_query_mode,
            pncp_ano_ata=pncp_ano_ata,
            only_pncp_adesao=only_pncp_adesao,
            pncp_filters_json=json.dumps(
                pncp_portal_filters.to_json_dict(), ensure_ascii=False
            ),
            scan_mode=scan_mode,
            include_vigente=include_vigente,
            include_nao_vigente=include_nao_vigente,
            max_list_pages=max_list_pages,
            max_detail_checks=max_detail_checks,
            max_pncp_pages=max_pncp_pages,
            enrich_suppliers=enrich_suppliers,
            status="running",
        )
        db.session.add(scan)
        db.session.commit()

        known_arp_ids = _known_contratos_arp_ids(exclude_scan_id=scan.id)
        known_pncp_ids = _known_pncp_control_ids(exclude_scan_id=scan.id)

        org_resolver = _pncp_org_resolver_for_robo()

        try:
            hits, stats = arp_robot.run_arp_robot(
                year,
                month=month,
                keyword=keyword,
                supplier_cnpj=supplier_cnpj,
                orgao_cnpj=orgao_cnpj,
                pncp_query_mode=pncp_query_mode,
                pncp_ano_ata=pncp_ano_ata,
                only_pncp_adesao=only_pncp_adesao,
                pncp_portal_filters=pncp_portal_filters,
                org_resolver=org_resolver,
                scan_mode=scan_mode,
                include_vigente=include_vigente,
                include_nao_vigente=include_nao_vigente,
                max_list_pages=max_list_pages,
                max_detail_checks=max_detail_checks,
                max_pncp_pages=max_pncp_pages,
                enrich_suppliers=enrich_suppliers,
            )
            scan.list_pages_read = stats.list_pages_read
            scan.list_pages_total = stats.list_pages_total
            scan.atas_listed = stats.atas_listed
            scan.atas_checked = stats.atas_checked
            scan.atas_with_adesao = stats.atas_with_adesao
            scan.duplicates_skipped = stats.duplicates_skipped
            scan.item_details_fetched = stats.item_details_fetched
            scan.list_scan_complete = stats.list_scan_complete
            scan.detail_limit_hit = stats.detail_limit_hit
            scan.pncp_pages_read = stats.pncp_pages_read
            scan.pncp_total_pages = stats.pncp_total_pages
            scan.pncp_rows_api = stats.pncp_rows_api
            scan.pncp_rows_matched = stats.pncp_rows_matched
            scan.status = "done"
            scan.finished_at = datetime.utcnow()
            if scan_mode == "pncp":
                scan.atas_with_adesao = len(hits)
            if stats.errors:
                scan.error_message = "\n".join(stats.errors[:20])[:4000]
            for hit in hits:
                items_adesao = hit.get("items_adesao") or []
                suppliers = _contratos_collect_suppliers(items_adesao)
                arp_id = hit.get("arp_id")
                arp_id = int(arp_id) if arp_id is not None else None
                pncp_id = (hit.get("pncp_control_id") or "")[:220] or None
                known = False
                if arp_id is not None and arp_id in known_arp_ids:
                    known = True
                if pncp_id and pncp_id in known_pncp_ids:
                    known = True
                db.session.add(
                    ContratosGovScanResult(
                        scan_id=scan.id,
                        arp_id=arp_id,
                        pncp_control_id=pncp_id,
                        numero_ata=(hit.get("numero") or "")[:40] or None,
                        unidade=(hit.get("unidade") or "")[:300] or None,
                        compra_ano=(hit.get("compra_ano") or "")[:40] or None,
                        status_ata=(hit.get("status") or "")[:40] or None,
                        valor_total=(hit.get("valor_total") or "")[:80] or None,
                        vigencia_inicial=(hit.get("vigencia_inicial") or "")[:20] or None,
                        vigencia_final=(hit.get("vigencia_final") or "")[:20] or None,
                        modalidade=(hit.get("modalidade") or "")[:120] or None,
                        objeto=(hit.get("objeto") or "")[:8000] or None,
                        verification_level=(hit.get("verification_level") or "item")[:20],
                        pncp_ata_url=(hit.get("pncp_ata_url") or "")[:300] or None,
                        pncp_compra_url=(hit.get("pncp_compra_url") or "")[:300] or None,
                        detail_url=hit["detail_url"],
                        items_json=json.dumps(items_adesao, ensure_ascii=False)
                        if items_adesao
                        else None,
                        suppliers_json=json.dumps(suppliers, ensure_ascii=False)
                        if suppliers
                        else None,
                        was_known_before=known,
                    )
                )
            db.session.commit()
            period = arp_robot.period_label(year, month)
            completeness = "completa" if stats.scan_fully_complete else "parcial"
            mode_labels = {
                "contratos": "Contratos.gov.br",
                "pncp": "PNCP API",
                "hibrido": "Híbrido",
            }
            msg = (
                f"Robô {mode_labels.get(scan_mode, scan_mode)} ({completeness}) — {period}"
            )
            if keyword:
                msg += f' · "{keyword}"'
            if supplier_cnpj:
                msg += f" · CNPJ {supplier_cnpj}"
            if supplier_cnpj and scan_mode in ("pncp", "hibrido"):
                msg += " (busca por CNPJ usa Contratos.gov.br)"
            if scan_mode == "pncp":
                msg += f": {stats.pncp_rows_matched} ata(s) PNCP com adesão."
            else:
                msg += (
                    f": {stats.atas_with_adesao} com adesão confirmada em item"
                    f" ({stats.atas_checked}/{stats.atas_listed} analisadas)."
                )
                if scan_mode == "hibrido" and stats.pncp_rows_matched:
                    msg += f" PNCP: {stats.pncp_rows_matched} candidata(s) no período."
            flash(msg, "ok")
            return redirect(url_for("admin_robo_contratos_scan", scan_id=scan.id))
        except Exception as exc:
            db.session.rollback()
            scan = db.session.get(ContratosGovScan, scan.id)
            if scan is not None:
                scan.status = "error"
                scan.error_message = str(exc)[:4000]
                scan.finished_at = datetime.utcnow()
                db.session.commit()
            flash(f"Erro no robô: {exc}", "error")
            if scan is not None:
                return redirect(url_for("admin_robo_contratos_scan", scan_id=scan.id))
            return redirect(url_for("admin_robo_contratos"))

    return _robo_tpl()


@app.route("/admin/robo-contratos/<int:scan_id>")
@admin_login_required
def admin_robo_contratos_scan(scan_id):
    ensure_contratos_gov_scan_tables()
    scan = db.session.get(ContratosGovScan, scan_id)
    if scan is None:
        flash("Execução não encontrada.", "error")
        return redirect(url_for("admin_robo_contratos"))
    results = (
        ContratosGovScanResult.query.filter_by(scan_id=scan.id)
        .order_by(ContratosGovScanResult.id.asc())
        .all()
    )
    ensure_arp_pipeline_tables()
    result_ids = [r.id for r in results]
    analyses_by_result: dict[int, ArpAnalysis] = {}
    if result_ids:
        for a in ArpAnalysis.query.filter(
            ArpAnalysis.scan_result_id.in_(result_ids)
        ).all():
            analyses_by_result[a.scan_result_id] = a
    return render_template(
        "admin/contratos_gov_robo_result.html",
        scan=scan,
        results=results,
        analyses_by_result=analyses_by_result or {},
    )


@app.route(
    "/admin/robo-contratos/<int:scan_id>/result/<int:result_id>/catalog",
    methods=["POST"],
)
@admin_login_required
def admin_robo_contratos_result_catalog(scan_id: int, result_id: int):
    ensure_contratos_gov_scan_tables()
    ensure_source_pncp_column()
    result = ContratosGovScanResult.query.filter_by(
        scan_id=scan_id, id=result_id
    ).first_or_404()
    item = create_catalog_from_contratos_result(result)
    db.session.commit()
    flash(f"Produto cadastrado no catálogo: {item.title}", "ok")
    return redirect(url_for("admin_robo_contratos_scan", scan_id=scan_id))


@app.route(
    "/admin/robo-contratos/<int:scan_id>/result/<int:result_id>/opportunity",
    methods=["POST"],
)
@admin_login_required
def admin_robo_contratos_result_opportunity(scan_id: int, result_id: int):
    ensure_contratos_gov_scan_tables()
    result = ContratosGovScanResult.query.filter_by(
        scan_id=scan_id, id=result_id
    ).first_or_404()
    opp = create_opportunity_from_contratos_result(result)
    db.session.commit()
    flash(f"Oportunidade criada no CRM: {opp.title}", "ok")
    return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))


@app.route("/admin/robo-contratos/<int:scan_id>/catalog-all", methods=["POST"])
@admin_login_required
def admin_robo_contratos_catalog_all(scan_id: int):
    ensure_contratos_gov_scan_tables()
    ensure_source_pncp_column()
    scan = db.session.get(ContratosGovScan, scan_id)
    if scan is None:
        flash("Execução não encontrada.", "error")
        return redirect(url_for("admin_robo_contratos"))
    results = ContratosGovScanResult.query.filter_by(scan_id=scan.id).all()
    created = 0
    linked = 0
    for result in results:
        before = result.catalog_item_id
        create_catalog_from_contratos_result(result)
        if result.catalog_item_id and not before:
            created += 1
        elif result.catalog_item_id:
            linked += 1
    db.session.commit()
    flash(
        f"Catálogo: {created} novo(s), {linked} já vinculado(s) ou existente(s).",
        "ok",
    )
    return redirect(url_for("admin_robo_contratos_scan", scan_id=scan_id))


@app.route("/admin/robo-contratos/<int:scan_id>/opportunities-all", methods=["POST"])
@admin_login_required
def admin_robo_contratos_opportunities_all(scan_id: int):
    ensure_contratos_gov_scan_tables()
    scan = db.session.get(ContratosGovScan, scan_id)
    if scan is None:
        flash("Execução não encontrada.", "error")
        return redirect(url_for("admin_robo_contratos"))
    results = ContratosGovScanResult.query.filter_by(scan_id=scan.id).all()
    created = 0
    for result in results:
        if result.opportunity_id:
            continue
        create_opportunity_from_contratos_result(result)
        created += 1
    db.session.commit()
    flash(f"CRM: {created} oportunidade(s) criada(s).", "ok")
    return redirect(url_for("admin_robo_contratos_scan", scan_id=scan_id))


@app.route(
    "/admin/robo-contratos/<int:scan_id>/result/<int:result_id>/analise",
    methods=["POST"],
)
@admin_login_required
def admin_robo_contratos_result_analysis(scan_id: int, result_id: int):
    import arp_pipeline as ap

    ensure_contratos_gov_scan_tables()
    ensure_arp_pipeline_tables()
    result = ContratosGovScanResult.query.filter_by(
        scan_id=scan_id, id=result_id
    ).first_or_404()
    row = ap.arp_analysis_from_scan_result(result)
    db.session.commit()
    flash(f"ARP registrada na análise prévia: {row.titulo[:80]}", "ok")
    return redirect(url_for("admin_arp_analysis_edit", analysis_id=row.id))


@app.route("/admin/analise-arp")
@admin_login_required
def admin_arp_analysis_list():
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()[:120]
    query = ArpAnalysis.query
    if status and status in ap.ARP_STATUS_LABELS:
        query = query.filter_by(status=status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                ArpAnalysis.titulo.ilike(like),
                ArpAnalysis.orgao.ilike(like),
                ArpAnalysis.numero_ata.ilike(like),
                ArpAnalysis.fornecedor_nome.ilike(like),
            )
        )
    rows = query.order_by(ArpAnalysis.updated_at.desc()).limit(400).all()
    return render_template(
        "admin/arp_analysis_list.html",
        rows=rows,
        statuses=ap.ARP_ANALYSIS_STATUSES,
        status_labels=ap.ARP_STATUS_LABELS,
        filter_status=status,
        qtext=q,
    )


@app.route("/admin/analise-arp/nova", methods=["GET", "POST"])
@admin_login_required
def admin_arp_analysis_new():
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    if request.method == "POST":
        try:
            row = ap.save_arp_analysis_from_form(request.form)
            db.session.commit()
            flash("ARP cadastrada na análise prévia.", "ok")
            return redirect(url_for("admin_arp_analysis_edit", analysis_id=row.id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "admin/arp_analysis_form.html",
        row=None,
        statuses=ap.ARP_ANALYSIS_STATUSES,
    )


@app.route("/admin/analise-arp/<int:analysis_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_arp_analysis_edit(analysis_id: int):
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    row = db.session.get(ArpAnalysis, analysis_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_arp_analysis_list"))
    if request.method == "POST":
        try:
            ap.save_arp_analysis_from_form(request.form, row)
            db.session.commit()
            flash("Análise salva.", "ok")
            return redirect(url_for("admin_arp_analysis_edit", analysis_id=row.id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "admin/arp_analysis_form.html",
        row=row,
        statuses=ap.ARP_ANALYSIS_STATUSES,
        status_labels=ap.ARP_STATUS_LABELS,
    )


@app.route("/admin/analise-arp/<int:analysis_id>/publicar", methods=["POST"])
@admin_login_required
def admin_arp_analysis_publish(analysis_id: int):
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    ensure_source_pncp_column()
    row = db.session.get(ArpAnalysis, analysis_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_arp_analysis_list"))
    item = ap.create_catalog_from_arp_analysis(
        row,
        slugify_fn=slugify,
        unique_slug_fn=unique_slug,
        parse_br_date_fn=_parse_contratos_br_date,
        sphere_from_unidade_fn=_sphere_from_contratos_unidade,
    )
    db.session.commit()
    flash(f"Publicado no site: {item.title}", "ok")
    return redirect(url_for("admin_catalog_edit", item_id=item.id))


@app.route("/admin/analise-arp/<int:analysis_id>/oportunidade", methods=["POST"])
@admin_login_required
def admin_arp_analysis_opportunity(analysis_id: int):
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    row = db.session.get(ArpAnalysis, analysis_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_arp_analysis_list"))
    opp = ap.create_opportunity_from_arp_analysis(row)
    db.session.commit()
    flash(f"Oportunidade criada: {opp.title}", "ok")
    return redirect(url_for("crm.crm_op_edit", opp_id=opp.id))


@app.route("/admin/analise-arp/<int:analysis_id>/excluir", methods=["POST"])
@admin_login_required
def admin_arp_analysis_delete(analysis_id: int):
    ensure_arp_pipeline_tables()
    row = db.session.get(ArpAnalysis, analysis_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_arp_analysis_list"))
    db.session.delete(row)
    db.session.commit()
    flash("Registro excluído.", "ok")
    return redirect(url_for("admin_arp_analysis_list"))


@app.route("/admin/licitacoes-andamento")
@admin_login_required
def admin_licitacao_watch_list():
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()[:120]
    query = LicitacaoWatch.query
    if status and status in ap.LICITACAO_STATUS_LABELS:
        query = query.filter_by(status=status)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                LicitacaoWatch.titulo.ilike(like),
                LicitacaoWatch.orgao.ilike(like),
                LicitacaoWatch.numero_edital.ilike(like),
            )
        )
    rows = query.order_by(LicitacaoWatch.updated_at.desc()).limit(400).all()
    return render_template(
        "admin/licitacao_watch_list.html",
        rows=rows,
        statuses=ap.LICITACAO_WATCH_STATUSES,
        status_labels=ap.LICITACAO_STATUS_LABELS,
        filter_status=status,
        qtext=q,
    )


@app.route("/admin/licitacoes-andamento/nova", methods=["GET", "POST"])
@admin_login_required
def admin_licitacao_watch_new():
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    if request.method == "POST":
        try:
            row = ap.save_licitacao_watch_from_form(request.form)
            db.session.commit()
            flash("Licitação cadastrada para acompanhamento.", "ok")
            return redirect(url_for("admin_licitacao_watch_edit", watch_id=row.id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "admin/licitacao_watch_form.html",
        row=None,
        statuses=ap.LICITACAO_WATCH_STATUSES,
    )


@app.route("/admin/licitacoes-andamento/<int:watch_id>/editar", methods=["GET", "POST"])
@admin_login_required
def admin_licitacao_watch_edit(watch_id: int):
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    row = db.session.get(LicitacaoWatch, watch_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_licitacao_watch_list"))
    if request.method == "POST":
        try:
            ap.save_licitacao_watch_from_form(request.form, row)
            db.session.commit()
            flash("Licitação salva.", "ok")
            return redirect(url_for("admin_licitacao_watch_edit", watch_id=row.id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "admin/licitacao_watch_form.html",
        row=row,
        statuses=ap.LICITACAO_WATCH_STATUSES,
        status_labels=ap.LICITACAO_STATUS_LABELS,
    )


@app.route("/admin/licitacoes-andamento/<int:watch_id>/gerar-arp", methods=["POST"])
@admin_login_required
def admin_licitacao_watch_to_arp(watch_id: int):
    import arp_pipeline as ap

    ensure_arp_pipeline_tables()
    row = db.session.get(LicitacaoWatch, watch_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_licitacao_watch_list"))
    analysis = ap.create_arp_analysis_from_licitacao(row)
    db.session.commit()
    flash("Entrada criada na análise prévia de ARP.", "ok")
    return redirect(url_for("admin_arp_analysis_edit", analysis_id=analysis.id))


@app.route("/admin/licitacoes-andamento/<int:watch_id>/excluir", methods=["POST"])
@admin_login_required
def admin_licitacao_watch_delete(watch_id: int):
    ensure_arp_pipeline_tables()
    row = db.session.get(LicitacaoWatch, watch_id)
    if row is None:
        flash("Registro não encontrado.", "error")
        return redirect(url_for("admin_licitacao_watch_list"))
    db.session.delete(row)
    db.session.commit()
    flash("Registro excluído.", "ok")
    return redirect(url_for("admin_licitacao_watch_list"))


@app.route("/admin/sincronizar-orgaos-pncp", methods=["GET", "POST"])
@admin_login_required
def admin_sync_pncp_orgaos():
    try:
        import pncp_client
    except ImportError:
        flash(
            "Instale as dependências do projeto e reinicie o servidor.",
            "error",
        )
        return render_template("admin/pncp_org_sync.html", total_orgaos=0)
    if request.method == "POST":
        try:
            d0 = datetime.strptime(
                request.form.get("data_inicial", "")[:10], "%Y-%m-%d"
            ).date()
            d1 = datetime.strptime(
                request.form.get("data_final", "")[:10], "%Y-%m-%d"
            ).date()
        except ValueError:
            flash("Datas inválidas. Use AAAA-MM-DD.", "error")
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        if d1 < d0:
            flash("A data final deve ser maior ou igual à inicial.", "error")
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        if (d1 - d0).days > 365:
            flash(
                "O PNCP limita a consulta a no máximo 365 dias por requisição.",
                "error",
            )
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        try:
            max_pages = int(request.form.get("max_paginas", "25"))
        except ValueError:
            max_pages = 25
        max_pages = max(1, min(500, max_pages))
        use_c = request.form.get("fonte_contratos") == "1"
        use_a = request.form.get("fonte_atas") == "1"
        if not use_c and not use_a:
            flash("Marque ao menos uma fonte: contratos e/ou atas.", "error")
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        combined: list[dict] = []
        try:
            if use_c:
                p1, _st = pncp_client.coletar_org_payloads_contratos(
                    d0, d1, max_pages=max_pages, pause_sec=0.2
                )
                combined.extend(p1)
            if use_a:
                p2, _st2 = pncp_client.coletar_org_payloads_atas(
                    d0, d1, max_pages=max_pages, pause_sec=0.2
                )
                combined.extend(p2)
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao consultar o PNCP: {exc}", "error")
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        deduped = _dedupe_org_payloads(combined)
        ins = upd = 0
        for p in deduped:
            r = _upsert_pncp_org_row(p)
            if r == "insert":
                ins += 1
            else:
                upd += 1
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro ao gravar no banco: {exc}", "error")
            return render_template(
                "admin/pncp_org_sync.html",
                total_orgaos=PncpOrgaoUnidade.query.count(),
            )
        flash(
            f"Lista de órgãos atualizada: {ins} novos, {upd} mesclados/atualizados "
            f"({len(deduped)} unidades únicas após deduplicação).",
            "ok",
        )
        return redirect(url_for("admin_sync_pncp_orgaos"))
    return render_template(
        "admin/pncp_org_sync.html",
        total_orgaos=PncpOrgaoUnidade.query.count(),
    )


@app.route("/admin/mercado-publico", methods=["GET", "POST"])
@admin_login_required
def admin_mercado_publico():
    """Atualiza snapshot PNCP usado nos gráficos da área do cliente."""
    default_df = date.today() - timedelta(days=1)
    default_di = default_df - timedelta(days=13)
    if request.method == "POST":
        di = parse_optional_date(request.form.get("data_inicio") or "")
        df = parse_optional_date(request.form.get("data_fim") or "")
        try:
            max_pc = int(request.form.get("max_pages_contratos") or 25)
        except ValueError:
            max_pc = 25
        try:
            max_pa = int(request.form.get("max_pages_atas") or 15)
        except ValueError:
            max_pa = 15
        max_pc = max(1, min(120, max_pc))
        max_pa = max(1, min(80, max_pa))
        if not di or not df:
            flash("Informe a data inicial e a data final.", "error")
        elif (df - di).days > 31:
            flash("Use no máximo 31 dias por atualização (limite prático da API).", "error")
        else:
            try:
                res = pncp_mercado_stats.coletar_resumo(
                    di,
                    df,
                    max_pages_contratos=max_pc,
                    max_pages_atas=max_pa,
                )
                snap = PncpMercadoSnapshot(
                    data_inicio=res["data_inicio"],
                    data_fim=res["data_fim"],
                    contratos_total_api=res["contratos_total_api"],
                    contratos_processados=res["contratos_processados"],
                    atas_total_api=res["atas_total_api"],
                    atas_processadas=res["atas_processadas"],
                    valor_contratos_despesa=res["valor_contratos_despesa"],
                    valor_contratos_receita=res["valor_contratos_receita"],
                    amostra_incompleta=res["amostra_incompleta"],
                    json_categorias=res["json_categorias"],
                    json_tipos_contrato=res["json_tipos_contrato"],
                    json_esfera=res["json_esfera"],
                    json_keywords_objeto=res["json_keywords_objeto"],
                    erro=res["erro"],
                )
                db.session.add(snap)
                db.session.commit()
                msg = "Painel do cliente atualizado com dados do PNCP."
                if res["amostra_incompleta"]:
                    msg += " Atenção: amostra parcial (ver aviso na página)."
                if res["erro"]:
                    flash(f"{msg} Observação: {res['erro'][:500]}", "ok")
                else:
                    flash(msg, "ok")
            except Exception as exc:
                db.session.rollback()
                flash(f"Erro ao coletar dados: {exc}", "error")
        return redirect(url_for("admin_mercado_publico"))
    latest = (
        PncpMercadoSnapshot.query.order_by(PncpMercadoSnapshot.created_at.desc())
        .first()
    )
    return render_template(
        "admin/mercado_publico.html",
        latest=latest,
        default_di=default_di,
        default_df=default_df,
        format_currency_brl=_format_currency_brl,
    )


@app.route("/admin/orgaos-publicos-br", methods=["GET", "POST"])
@admin_login_required
def admin_br_orgaos_import():
    """Alimenta o diretório: IBGE, estados, executivo federal, autarquias, jurídico, TRT/MPT, legislativo, segurança, educação (IF/UF/MEC), Sistema S, aprendizagem, DETRAN, PNCP."""
    total = BrOrgaoPublico.query.count()
    if request.method == "POST":
        acao = (request.form.get("acao") or "").strip()
        try:
            if acao == "municipios":
                ins, sk, err = br_org_import.import_ibge_municipios(
                    db, BrOrgaoPublico
                )
                if err:
                    flash(f"Erro ao consultar o IBGE: {err}", "error")
                else:
                    flash(
                        f"Municípios (prefeituras): {ins} novos, {sk} já cadastrados.",
                        "ok",
                    )
            elif acao == "estados":
                ins, sk = br_org_import.seed_orgaos_estaduais(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Órgãos estaduais: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "sistema_s":
                ins, sk = br_org_import.seed_sistema_s(db, BrOrgaoPublico, BR_UFS)
                flash(
                    f"Sistema S: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "pncp":
                ins, sk = br_org_import.copiar_pncp_para_br_orgaos(
                    db, BrOrgaoPublico, PncpOrgaoUnidade
                )
                flash(
                    f"Cópia PNCP para o diretório: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "federal_executivo":
                ins, sk = br_org_import.seed_federal_executivo(db, BrOrgaoPublico)
                flash(
                    f"Presidência e ministérios: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "autarquias_federais":
                ins, sk = br_org_import.seed_autarquias_federais_catalogo(
                    db, BrOrgaoPublico
                )
                flash(
                    f"Autarquias federais: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "orgaos_juridicos":
                ins, sk = br_org_import.seed_orgaos_juridicos_catalogo(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Órgãos jurídicos (tribunais, MP): {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "servico_aprendizagem":
                ins, sk = br_org_import.seed_servico_aprendizagem_complementar(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"SENAR, SEBRAE, SENAT, SESCOOP: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "detran":
                ins, sk = br_org_import.seed_detran_estaduais(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"DETRAN por UF: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "autarquias_estaduais_demais":
                ins, sk = br_org_import.seed_demais_autarquias_estaduais(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Demais autarquias estaduais (guia por UF): {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "justica_trabalho_mpt":
                ins, sk = br_org_import.seed_justica_trabalho_mpt_catalogo(
                    db, BrOrgaoPublico
                )
                flash(
                    f"TRT + MPT (PGT e PRTs): {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "legislativo":
                ins, sk = br_org_import.seed_orgaos_legislativos_catalogo(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Órgãos legislativos: {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "seguranca_publica":
                ins, sk = br_org_import.seed_seguranca_publica_catalogo(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Segurança pública (PM, PC, bombeiros e federais): {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "educacao":
                ins, sk = br_org_import.seed_educacao_instituicoes_catalogo(
                    db, BrOrgaoPublico, BR_UFS
                )
                flash(
                    f"Educação (órgãos MEC, UFs, IFs e guia estadual): {ins} novos, {sk} já cadastrados.",
                    "ok",
                )
            elif acao == "ibge_pop_orcamento":
                n_pop, n_orc, err = br_ibge_sync.sincronizar_populacao_e_potencial(
                    db, BrOrgaoPublico
                )
                if err:
                    flash(f"Sincronização IBGE: {err}", "error")
                else:
                    flash(
                        f"População IBGE atualizada em {n_pop} registro(s); "
                        f"potencial orçamentário recalculado em {n_orc} linha(s).",
                        "ok",
                    )
            else:
                flash("Ação inválida.", "error")
        except Exception as exc:
            db.session.rollback()
            flash(f"Erro: {exc}", "error")
        return redirect(url_for("admin_br_orgaos_import"))
    return render_template(
        "admin/br_orgaos_import.html",
        total_br_orgaos=total,
        pncp_count=PncpOrgaoUnidade.query.count(),
    )


@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Banco criado.")


@app.cli.command("seed")
def cli_seed():
    from seed import seed

    seed()


@app.cli.command("create-admin-rep")
@click.option(
    "--email",
    default="comercial@arpgov.com",
    show_default=True,
    help="E-mail de login (área comercial, CRM e painel se administrador).",
)
@click.option(
    "--name",
    default="Comercial ARPGOV",
    show_default=True,
    help="Nome exibido do representante.",
)
@click.option(
    "--password",
    default=None,
    help="Senha com no mínimo 8 caracteres. Se omitida, gera uma senha aleatória.",
)
@click.option(
    "--reset-password",
    is_flag=True,
    help="Redefine a senha quando o e-mail já estiver cadastrado.",
)
def cli_create_admin_rep(email: str, name: str, password: str | None, reset_password: bool):
    """Cria ou promove representante comercial administrador (Painel + CRM + Comercial)."""
    email_norm = _normalize_rep_email(email)
    if not email_norm or "@" not in email_norm:
        print("E-mail inválido.")
        raise SystemExit(1)
    display_name = (name or "").strip() or "Administrador ARPGOV"
    rep = SalesRepresentative.query.filter_by(email=email_norm).first()
    created = rep is None
    if created:
        rep = SalesRepresentative(
            name=display_name,
            email=email_norm,
            is_active=True,
            is_admin=True,
            access_comercial=True,
            access_crm=True,
            access_painel=True,
            password_hash="",
        )
        db.session.add(rep)
    else:
        rep.name = display_name
        rep.is_active = True
        rep.is_admin = True
        rep.access_comercial = True
        rep.access_crm = True
        rep.access_painel = True
    plain = (password or "").strip() or None
    if plain and len(plain) < 8:
        print("Senha deve ter no mínimo 8 caracteres.")
        raise SystemExit(1)
    if plain is None and (created or reset_password):
        alphabet = string.ascii_letters + string.digits
        plain = "".join(secrets.choice(alphabet) for _ in range(14))
    if plain:
        rep.password_hash = generate_password_hash(plain)
    db.session.commit()
    if created:
        print(f"Administrador criado: {email_norm}")
    else:
        print(f"Administrador atualizado: {email_norm}")
    if plain:
        print(f"Senha de acesso: {plain}")
    else:
        print("Senha anterior mantida (use --reset-password para gerar outra).")
    print("Acesso: /comercial/entrar, /crm/entrar ou /admin/entrar")


@app.errorhandler(500)
def _internal_server_error(_e):
    import traceback

    traceback.print_exc()
    app.logger.exception("Erro 500 em %s %s", request.method, request.path)
    return (
        "<h1>Erro interno</h1><p>O servidor encontrou um erro. No terminal onde o <code>app.py</code> "
        "está rodando deve aparecer o detalhe (traceback).</p>",
        500,
    )


@app.errorhandler(413)
def _request_entity_too_large(_e):
    flash(
        "O envio é grande demais. Use no máximo 8 arquivos de até 15 MB cada.",
        "error",
    )
    ref = request.referrer
    if ref:
        try:
            u = urlparse(ref)
            if u.scheme in ("http", "https") and u.netloc == request.host:
                return redirect(u.path + (f"?{u.query}" if u.query else ""))
        except Exception:
            pass
    return redirect(url_for("home"))


init_schema()


if __name__ == "__main__":
    # Porta padrão 5001 (evita conflito com outros Flask na 5000).
    # No Windows: host 127.0.0.1 evita "localhost" resolver para IPv6 (::1) sem listener;
    # reloader em debug costuma dar porta em uso / processo duplicado — desliga por padrão.
    portal_port = int(os.environ.get("PORTAL_PORT", "5001"))
    default_host = "127.0.0.1" if sys.platform == "win32" else "0.0.0.0"
    portal_host = os.environ.get("PORTAL_HOST", default_host)

    _rel = os.environ.get("FLASK_USE_RELOADER", "").strip().lower()
    if _rel in ("1", "true", "yes"):
        use_reloader = True
    elif _rel in ("0", "false", "no"):
        use_reloader = False
    else:
        use_reloader = sys.platform != "win32"

    print(f"\n  >>> ARPGOV: http://127.0.0.1:{portal_port}/\n")
    if portal_host != "127.0.0.1":
        print(f"      (escutando em {portal_host} — use o IP da máquina na rede, se for o caso)\n")

    if sys.platform == "win32" and os.environ.get("PORTAL_OPEN_BROWSER", "1") != "0":

        def _open_browser():
            import time

            time.sleep(1.2)
            webbrowser.open(f"http://127.0.0.1:{portal_port}/")

        threading.Thread(target=_open_browser, daemon=True).start()

    flask_debug = _env_bool("FLASK_DEBUG", False)
    app.run(
        debug=flask_debug,
        host=portal_host,
        port=portal_port,
        use_reloader=use_reloader and flask_debug,
        threaded=True,
    )
