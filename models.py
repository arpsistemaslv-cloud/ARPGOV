import json
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, event, text

db = SQLAlchemy()


class CatalogCategory(db.Model):
    """Catálogo (nível raiz) ou subcatálogo (parent_id preenchido)."""

    __tablename__ = "catalog_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("catalog_categories.id"), nullable=True, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    parent = db.relationship(
        "CatalogCategory",
        remote_side=[id],
        foreign_keys=[parent_id],
        back_populates="children",
    )
    children = db.relationship(
        "CatalogCategory",
        back_populates="parent",
        foreign_keys=[parent_id],
        order_by="CatalogCategory.sort_order, CatalogCategory.id",
    )
    items = db.relationship("CatalogItem", back_populates="category", lazy="dynamic")


class CatalogItem(db.Model):
    __tablename__ = "catalog_items"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("catalog_categories.id"), nullable=True, index=True)
    title = db.Column(db.String(300), nullable=False)
    section = db.Column(db.String(80), nullable=False, index=True)
    sphere = db.Column(db.String(80), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    # Estoque opcional (número inteiro; pode ficar 0 em representação sem estoque físico).
    stock_on_hand = db.Column(db.Integer, nullable=False, default=0)
    unit_price = db.Column(db.Numeric(14, 2), nullable=False)
    valid_until = db.Column(db.Date, nullable=True)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    highlight = db.Column(db.Boolean, default=False)
    source_pncp_id = db.Column(db.String(220), nullable=True, index=True)
    images_json = db.Column(db.Text, nullable=True)
    # Empresa dona da ata — uso interno no painel; não exibir no site público.
    ata_owner_company = db.Column(db.String(200), nullable=True, index=True)
    # Fabricante / marca do produto — exibido no site e filtrável na loja.
    manufacturer = db.Column(db.String(200), nullable=True, index=True)
    # Página do produto no site do fabricante (referência interna).
    source_product_url = db.Column(db.String(500), nullable=True)
    # Link da ata/compra no Portal Nacional de Contratações Públicas (PNCP).
    pncp_url = db.Column(db.String(700), nullable=True)
    # Link da página de contratação (origem da ARP) — facilita identificar a cópia da ata.
    contract_page_url = db.Column(db.String(700), nullable=True)
    # Garantia contratada na licitação / ata (ex.: 36 meses on-site).
    warranty = db.Column(db.String(300), nullable=True)
    # Especificações técnicas exibidas no site (texto livre).
    technical_description = db.Column(db.Text, nullable=True)
    # PDF/DOC anexos do item (catálogo do produto) — só painel admin / download interno.
    catalog_attachments_json = db.Column(db.Text, nullable=True)
    # Documentos da empresa dona da ata — apenas administradores do painel (não CRM, não site).
    ata_company_docs_json = db.Column(db.Text, nullable=True)

    @staticmethod
    def _paths_from_json(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x) for x in data if x and isinstance(x, str)]
        except (json.JSONDecodeError, TypeError):
            pass
        return []

    @property
    def image_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.images_json)

    @property
    def catalog_attachment_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.catalog_attachments_json)

    @property
    def ata_company_doc_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.ata_company_docs_json)

    category = db.relationship("CatalogCategory", back_populates="items")


class SiteSettings(db.Model):
    """Uma única linha (id=1) com textos exibidos no site público."""

    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)
    hero_headline = db.Column(db.String(220), nullable=True)
    hero_text = db.Column(db.Text, nullable=True)
    contact_email = db.Column(db.String(120), nullable=True)
    contact_phone = db.Column(db.String(40), nullable=True)
    footer_note = db.Column(db.String(500), nullable=True)
    site_brand_primary = db.Column(db.String(120), nullable=True)
    site_brand_accent = db.Column(db.String(120), nullable=True)
    contact_intro = db.Column(db.Text, nullable=True)
    contact_address = db.Column(db.String(500), nullable=True)
    social_whatsapp = db.Column(db.String(120), nullable=True)
    social_instagram = db.Column(db.String(200), nullable=True)
    social_tiktok = db.Column(db.String(200), nullable=True)
    custom_css = db.Column(db.Text, nullable=True)
    meta_description = db.Column(db.String(320), nullable=True)


class ContratosGovScan(db.Model):
    """Execução do robô de busca de atas no Contratos.gov.br (transparência)."""

    __tablename__ = "contratos_gov_scans"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=True)
    keyword = db.Column(db.String(120), nullable=True)
    supplier_cnpj = db.Column(db.String(22), nullable=True)
    orgao_cnpj = db.Column(db.String(22), nullable=True)
    pncp_query_mode = db.Column(db.String(20), default="vigencia", nullable=False)
    pncp_ano_ata = db.Column(db.Integer, nullable=True)
    only_pncp_adesao = db.Column(db.Boolean, default=True, nullable=False)
    pncp_filters_json = db.Column(db.Text, nullable=True)
    scan_mode = db.Column(db.String(20), default="contratos", nullable=False)
    include_vigente = db.Column(db.Boolean, default=True, nullable=False)
    include_nao_vigente = db.Column(db.Boolean, default=True, nullable=False)
    max_list_pages = db.Column(db.Integer, default=20, nullable=False)
    max_detail_checks = db.Column(db.Integer, default=150, nullable=False)
    max_pncp_pages = db.Column(db.Integer, default=30, nullable=False)
    status = db.Column(db.String(20), default="done", nullable=False)
    list_pages_read = db.Column(db.Integer, default=0, nullable=False)
    list_pages_total = db.Column(db.Integer, nullable=True)
    atas_listed = db.Column(db.Integer, default=0, nullable=False)
    atas_checked = db.Column(db.Integer, default=0, nullable=False)
    atas_with_adesao = db.Column(db.Integer, default=0, nullable=False)
    duplicates_skipped = db.Column(db.Integer, default=0, nullable=False)
    item_details_fetched = db.Column(db.Integer, default=0, nullable=False)
    list_scan_complete = db.Column(db.Boolean, default=False, nullable=False)
    detail_limit_hit = db.Column(db.Boolean, default=False, nullable=False)
    enrich_suppliers = db.Column(db.Boolean, default=True, nullable=False)
    pncp_pages_read = db.Column(db.Integer, default=0, nullable=False)
    pncp_total_pages = db.Column(db.Integer, default=0, nullable=False)
    pncp_rows_api = db.Column(db.Integer, default=0, nullable=False)
    pncp_rows_matched = db.Column(db.Integer, default=0, nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)

    @property
    def detail_scan_complete(self) -> bool:
        return not self.detail_limit_hit and self.atas_checked >= self.atas_listed

    @property
    def scan_fully_complete(self) -> bool:
        if self.scan_mode == "pncp":
            return bool(
                self.pncp_total_pages > 0
                and self.pncp_pages_read >= self.pncp_total_pages
            )
        return bool(self.list_scan_complete and self.detail_scan_complete)

    @property
    def period_label(self) -> str:
        if self.month and 1 <= self.month <= 12:
            names = (
                "",
                "Jan",
                "Fev",
                "Mar",
                "Abr",
                "Mai",
                "Jun",
                "Jul",
                "Ago",
                "Set",
                "Out",
                "Nov",
                "Dez",
            )
            return f"{names[self.month]}/{self.year}"
        return str(self.year)

    results = db.relationship(
        "ContratosGovScanResult",
        back_populates="scan",
        cascade="all, delete-orphan",
        order_by="ContratosGovScanResult.id.asc()",
    )


class ContratosGovScanResult(db.Model):
    """Ata encontrada com pelo menos um item que aceita adesão."""

    __tablename__ = "contratos_gov_scan_results"

    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(
        db.Integer, db.ForeignKey("contratos_gov_scans.id"), nullable=False, index=True
    )
    arp_id = db.Column(db.Integer, nullable=True, index=True)
    pncp_control_id = db.Column(db.String(220), nullable=True, index=True)
    numero_ata = db.Column(db.String(40), nullable=True)
    unidade = db.Column(db.String(300), nullable=True)
    compra_ano = db.Column(db.String(40), nullable=True)
    status_ata = db.Column(db.String(40), nullable=True)
    valor_total = db.Column(db.String(80), nullable=True)
    vigencia_inicial = db.Column(db.String(20), nullable=True)
    vigencia_final = db.Column(db.String(20), nullable=True)
    detail_url = db.Column(db.String(300), nullable=False)
    modalidade = db.Column(db.String(120), nullable=True)
    objeto = db.Column(db.Text, nullable=True)
    verification_level = db.Column(db.String(20), default="item", nullable=False)
    pncp_ata_url = db.Column(db.String(300), nullable=True)
    pncp_compra_url = db.Column(db.String(300), nullable=True)
    items_json = db.Column(db.Text, nullable=True)
    suppliers_json = db.Column(db.Text, nullable=True)
    was_known_before = db.Column(db.Boolean, default=False, nullable=False)
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    opportunity_id = db.Column(
        db.Integer, db.ForeignKey("opportunities.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    scan = db.relationship("ContratosGovScan", back_populates="results")
    catalog_item = db.relationship("CatalogItem", foreign_keys=[catalog_item_id])
    opportunity = db.relationship("Opportunity", foreign_keys=[opportunity_id])

    @property
    def items_adesao(self) -> list[dict]:
        if not self.items_json:
            return []
        try:
            data = json.loads(self.items_json)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def suppliers(self) -> list[dict]:
        if not self.suppliers_json:
            return []
        try:
            data = json.loads(self.suppliers_json)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def primary_supplier(self) -> dict | None:
        for it in self.items_adesao:
            forn = it.get("fornecedores") or []
            if forn:
                return forn[0]
        for s in self.suppliers:
            if s.get("fornecedor"):
                return s
        return None


class ArpAnalysis(db.Model):
    """Análise prévia de ata de registro de preço — pipeline até publicação no site."""

    __tablename__ = "arp_analyses"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(400), nullable=False)
    orgao = db.Column(db.String(400), nullable=True)
    arp_url = db.Column(db.String(500), nullable=False)
    pncp_url = db.Column(db.String(500), nullable=True)
    contratos_url = db.Column(db.String(500), nullable=True)
    numero_ata = db.Column(db.String(40), nullable=True, index=True)
    objeto = db.Column(db.Text, nullable=True)
    vigencia_inicial = db.Column(db.String(20), nullable=True)
    vigencia_final = db.Column(db.String(20), nullable=True)
    fornecedor_nome = db.Column(db.String(300), nullable=True)
    fornecedor_cnpj = db.Column(db.String(22), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="preliminar", index=True)
    partner_contact = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    scan_result_id = db.Column(
        db.Integer, db.ForeignKey("contratos_gov_scan_results.id"), nullable=True, index=True
    )
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    opportunity_id = db.Column(
        db.Integer, db.ForeignKey("opportunities.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    scan_result = db.relationship("ContratosGovScanResult", foreign_keys=[scan_result_id])
    catalog_item = db.relationship("CatalogItem", foreign_keys=[catalog_item_id])
    opportunity = db.relationship("Opportunity", foreign_keys=[opportunity_id])
    licitacoes = db.relationship(
        "LicitacaoWatch",
        back_populates="arp_analysis",
        foreign_keys="LicitacaoWatch.arp_analysis_id",
    )


class LicitacaoWatch(db.Model):
    """Licitação em andamento que pode virar ARP com adesão."""

    __tablename__ = "licitacao_watches"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(400), nullable=False)
    orgao = db.Column(db.String(400), nullable=True)
    link = db.Column(db.String(500), nullable=False)
    numero_edital = db.Column(db.String(160), nullable=True, index=True)
    modalidade = db.Column(db.String(120), nullable=True)
    permite_adesao = db.Column(db.Boolean, default=True, nullable=False)
    data_abertura = db.Column(db.Date, nullable=True)
    data_resultado = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="acompanhando", index=True)
    notes = db.Column(db.Text, nullable=True)
    arp_analysis_id = db.Column(
        db.Integer, db.ForeignKey("arp_analyses.id"), nullable=True, index=True
    )
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    arp_analysis = db.relationship(
        "ArpAnalysis",
        back_populates="licitacoes",
        foreign_keys=[arp_analysis_id],
    )
    catalog_item = db.relationship("CatalogItem", foreign_keys=[catalog_item_id])


opportunity_catalog_items = db.Table(
    "opportunity_catalog_items",
    db.Column("opportunity_id", db.Integer, db.ForeignKey("opportunities.id"), primary_key=True),
    db.Column("catalog_item_id", db.Integer, db.ForeignKey("catalog_items.id"), primary_key=True),
)


class OpportunityCatalogLine(db.Model):
    """Produto vinculado a um lead com quantidade de adesão."""

    __tablename__ = "opportunity_catalog_lines"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "catalog_item_id", name="uq_opp_catalog_line"),
    )

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(
        db.Integer, db.ForeignKey("opportunities.id"), nullable=False, index=True
    )
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=False, index=True
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    opportunity = db.relationship("Opportunity", back_populates="catalog_lines")
    catalog_item = db.relationship("CatalogItem", lazy="joined")


class SitePage(db.Model):
    """Páginas extras com HTML livre, opcionalmente no menu."""

    __tablename__ = "site_pages"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    nav_label = db.Column(db.String(100), nullable=True)
    body_html = db.Column(db.Text, nullable=False, default="")
    show_in_nav = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class SalesRepresentative(db.Model):
    """Vendedor / representante comercial — login próprio e leads atribuídos."""

    __tablename__ = "sales_reps"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    opportunities = db.relationship("Opportunity", back_populates="sales_rep")
    financial_entries = db.relationship(
        "RepFinancialEntry",
        back_populates="sales_rep",
        order_by="RepFinancialEntry.created_at.desc()",
    )


class PortalClient(db.Model):
    """Cliente cadastrado no site — acessa seus leads (oportunidades vinculadas)."""

    __tablename__ = "portal_clients"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    organization = db.Column(db.String(200), nullable=True)
    razao_social = db.Column(db.String(300), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    cnpj = db.Column(db.String(22), nullable=True)
    cpf = db.Column(db.String(14), nullable=True)
    job_title = db.Column(db.String(120), nullable=True)
    sector = db.Column(db.String(120), nullable=True)
    sphere = db.Column(db.String(80), nullable=True)
    address_street = db.Column(db.String(200), nullable=True)
    address_number = db.Column(db.String(20), nullable=True)
    address_complement = db.Column(db.String(120), nullable=True)
    address_neighborhood = db.Column(db.String(120), nullable=True)
    address_city = db.Column(db.String(120), nullable=True)
    address_state = db.Column(db.String(2), nullable=True)
    address_zip = db.Column(db.String(10), nullable=True)
    photo_path = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    opportunities = db.relationship("Opportunity", back_populates="portal_client")

    @property
    def address_line(self) -> str | None:
        parts: list[str] = []
        street = (self.address_street or "").strip()
        number = (self.address_number or "").strip()
        if street:
            parts.append(f"{street}{', ' + number if number else ''}")
        comp = (self.address_complement or "").strip()
        if comp:
            parts.append(comp)
        hood = (self.address_neighborhood or "").strip()
        if hood:
            parts.append(hood)
        city = (self.address_city or "").strip()
        uf = (self.address_state or "").strip().upper()
        if city and uf:
            parts.append(f"{city} — {uf}")
        elif city:
            parts.append(city)
        zipc = (self.address_zip or "").strip()
        if zipc:
            parts.append(f"CEP {zipc}")
        return " · ".join(parts) if parts else None


class LeadMessage(db.Model):
    """Mensagem no fio do lead — cliente (portal) ou equipe (CRM)."""

    __tablename__ = "lead_messages"

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(db.Integer, db.ForeignKey("opportunities.id"), nullable=False, index=True)
    sender = db.Column(db.String(20), nullable=False)  # "client" | "staff"
    body = db.Column(db.Text, nullable=False, default="")
    attachments_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    opportunity = db.relationship("Opportunity", back_populates="lead_messages")

    @property
    def attachment_list(self) -> list[dict]:
        if not self.attachments_json:
            return []
        try:
            data = json.loads(self.attachments_json)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict) and x.get("relpath")]
        except (json.JSONDecodeError, TypeError):
            pass
        return []


class Opportunity(db.Model):
    __tablename__ = "opportunities"

    id = db.Column(db.Integer, primary_key=True)
    sales_rep_id = db.Column(db.Integer, db.ForeignKey("sales_reps.id"), nullable=True, index=True)
    portal_client_id = db.Column(db.Integer, db.ForeignKey("portal_clients.id"), nullable=True, index=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(120), nullable=True)
    organization = db.Column(db.String(200), nullable=True)
    cnpj = db.Column(db.String(22), nullable=True, index=True)
    email = db.Column(db.String(120), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    sphere = db.Column(db.String(80), nullable=True)
    stage = db.Column(db.String(40), nullable=False, default="novo", index=True)
    value_brl = db.Column(db.Numeric(14, 2), nullable=True)
    rep_commission_brl = db.Column(db.Numeric(14, 2), nullable=True)
    rep_commission_note = db.Column(db.Text, nullable=True)
    commission_project_id = db.Column(
        db.Integer, db.ForeignKey("commission_projects.id"), nullable=True, index=True
    )
    commission_tier_id = db.Column(
        db.Integer, db.ForeignKey("commission_project_tiers.id"), nullable=True, index=True
    )
    process_ref = db.Column(db.String(120), nullable=True, index=True)
    pipeline_data_json = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    catalog_lines = db.relationship(
        "OpportunityCatalogLine",
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityCatalogLine.id",
        lazy="selectin",
    )
    sales_rep = db.relationship("SalesRepresentative", back_populates="opportunities")
    portal_client = db.relationship("PortalClient", back_populates="opportunities")
    partner = db.relationship("Partner", foreign_keys=[partner_id])
    lead_messages = db.relationship(
        "LeadMessage",
        back_populates="opportunity",
        order_by=LeadMessage.created_at,
        cascade="all, delete-orphan",
    )
    rep_financial_entries = db.relationship(
        "RepFinancialEntry",
        back_populates="opportunity",
        order_by="RepFinancialEntry.created_at.desc()",
    )
    commission_project = db.relationship("CommissionProject", foreign_keys=[commission_project_id])
    commission_tier = db.relationship("CommissionProjectTier", foreign_keys=[commission_tier_id])
    commission_splits = db.relationship(
        "OpportunityCommissionSplit",
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityCommissionSplit.id",
    )

    @property
    def pipeline_data(self) -> dict:
        if not self.pipeline_data_json:
            return {}
        try:
            data = json.loads(self.pipeline_data_json)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def pipeline_stage_data(self, stage_key: str) -> dict:
        block = self.pipeline_data.get(stage_key)
        return block if isinstance(block, dict) else {}

    def pipeline_attachments(self, stage_key: str) -> list[dict]:
        raw = self.pipeline_stage_data(stage_key).get("attachments")
        if not isinstance(raw, list):
            return []
        return [x for x in raw if isinstance(x, dict) and x.get("relpath")]


def _finance_attachment_list(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict) and x.get("relpath")]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


class CompanyExpense(db.Model):
    """Despesa / custo interno da empresa (CRM) com anexos (ex.: notas fiscais)."""

    __tablename__ = "company_expenses"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    amount_brl = db.Column(db.Numeric(14, 2), nullable=True)
    expense_date = db.Column(db.Date, nullable=True)
    category = db.Column(db.String(80), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    attachments_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def attachment_list(self) -> list[dict]:
        return _finance_attachment_list(self.attachments_json)


class RepFinancialEntry(db.Model):
    """Documento de comissão / negociação enviado pelo representante (NF, comprovantes)."""

    __tablename__ = "rep_financial_entries"

    id = db.Column(db.Integer, primary_key=True)
    sales_rep_id = db.Column(db.Integer, db.ForeignKey("sales_reps.id"), nullable=False, index=True)
    opportunity_id = db.Column(db.Integer, db.ForeignKey("opportunities.id"), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    amount_brl = db.Column(db.Numeric(14, 2), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="enviado")
    attachments_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    sales_rep = db.relationship("SalesRepresentative", back_populates="financial_entries")
    opportunity = db.relationship("Opportunity", back_populates="rep_financial_entries")

    @property
    def attachment_list(self) -> list[dict]:
        return _finance_attachment_list(self.attachments_json)


class CompanyStakeholder(db.Model):
    """Sócio ou reserva (fluxo de caixa) — participação no rateio de comissões."""

    __tablename__ = "company_stakeholders"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    share_percent = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    role_key = db.Column(db.String(40), nullable=False, default="socio")
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CompanyFinanceGoal(db.Model):
    """Metas financeiras da empresa (singleton id=1)."""

    __tablename__ = "company_finance_goals"

    id = db.Column(db.Integer, primary_key=True)
    company_label = db.Column(db.String(200), nullable=True)
    goal_year = db.Column(db.Integer, nullable=False, default=2026)
    goal_annual_brl = db.Column(db.Numeric(14, 2), nullable=True)
    goal_monthly_brl = db.Column(db.Numeric(14, 2), nullable=True)
    commission_tier_id = db.Column(
        db.Integer, db.ForeignKey("commission_project_tiers.id"), nullable=True, index=True
    )
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    commission_tier = db.relationship("CommissionProjectTier")


class FinanceSimulationLine(db.Model):
    """Operação simulada para projeção de comissões e retorno da empresa."""

    __tablename__ = "finance_simulation_lines"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    value_brl = db.Column(db.Numeric(14, 2), nullable=False)
    commission_tier_id = db.Column(
        db.Integer, db.ForeignKey("commission_project_tiers.id"), nullable=False, index=True
    )
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    commission_tier = db.relationship(
        "CommissionProjectTier",
        backref=db.backref("finance_simulations", lazy="dynamic"),
    )


class CommissionProject(db.Model):
    """Projeto de comissionamento global (com ou sem vendedor), aplicável a qualquer lead."""

    __tablename__ = "commission_projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    catalog_item_id = db.Column(db.Integer, db.ForeignKey("catalog_items.id"), nullable=True, index=True)
    with_seller = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    rateio_mode = db.Column(db.String(20), nullable=False, default="no_seller")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    catalog_item = db.relationship("CatalogItem")
    tiers = db.relationship(
        "CommissionProjectTier",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="CommissionProjectTier.sort_order",
    )
    rateio_lines = db.relationship(
        "CommissionProjectRateioLine",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="CommissionProjectRateioLine.sort_order",
    )


@event.listens_for(CommissionProject, "before_insert")
def _commission_project_default_catalog_item(_mapper, connection, target):
    """Banco legado exige catalog_item_id; preenche com o primeiro produto do catálogo."""
    if target.catalog_item_id is not None:
        return
    cat_id = connection.execute(
        text("SELECT id FROM catalog_items ORDER BY id ASC LIMIT 1")
    ).scalar()
    if cat_id is not None:
        target.catalog_item_id = int(cat_id)


class CommissionProjectRateioLine(db.Model):
    """Participante do modelo de rateio personalizado (% do total da comissão)."""

    __tablename__ = "commission_project_rateio_lines"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("commission_projects.id"), nullable=False, index=True
    )
    recipient_kind = db.Column(db.String(20), nullable=False)
    label = db.Column(db.String(160), nullable=False)
    stakeholder_id = db.Column(
        db.Integer, db.ForeignKey("company_stakeholders.id"), nullable=True, index=True
    )
    pool_share_percent = db.Column(db.Numeric(8, 4), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    project = db.relationship("CommissionProject", back_populates="rateio_lines")
    stakeholder = db.relationship("CompanyStakeholder")


class CommissionProjectTier(db.Model):
    """Faixa de comissão (% total) com ou sem vendedor."""

    __tablename__ = "commission_project_tiers"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "percent_total", "with_seller", name="uq_commission_tier_project_pct"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("commission_projects.id"), nullable=False, index=True)
    percent_total = db.Column(db.Numeric(8, 4), nullable=False)
    with_seller = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    project = db.relationship("CommissionProject", back_populates="tiers")
    splits = db.relationship(
        "CommissionTierSplit",
        back_populates="tier",
        cascade="all, delete-orphan",
        order_by="CommissionTierSplit.id",
    )


class CommissionTierSplit(db.Model):
    """Rateio pré-calculado de uma faixa (% sobre a operação)."""

    __tablename__ = "commission_tier_splits"

    id = db.Column(db.Integer, primary_key=True)
    tier_id = db.Column(
        db.Integer, db.ForeignKey("commission_project_tiers.id"), nullable=False, index=True
    )
    recipient_kind = db.Column(db.String(20), nullable=False)
    stakeholder_id = db.Column(db.Integer, db.ForeignKey("company_stakeholders.id"), nullable=True, index=True)
    share_percent = db.Column(db.Numeric(8, 4), nullable=False)
    label = db.Column(db.String(160), nullable=False)

    tier = db.relationship("CommissionProjectTier", back_populates="splits")
    stakeholder = db.relationship("CompanyStakeholder")


class OpportunityCommissionSplit(db.Model):
    """Rateio efetivo de um lead (valores em R$ quando há valor estimado)."""

    __tablename__ = "opportunity_commission_splits"

    id = db.Column(db.Integer, primary_key=True)
    opportunity_id = db.Column(db.Integer, db.ForeignKey("opportunities.id"), nullable=False, index=True)
    tier_id = db.Column(db.Integer, db.ForeignKey("commission_project_tiers.id"), nullable=True, index=True)
    recipient_kind = db.Column(db.String(20), nullable=False)
    stakeholder_id = db.Column(db.Integer, db.ForeignKey("company_stakeholders.id"), nullable=True, index=True)
    sales_rep_id = db.Column(db.Integer, db.ForeignKey("sales_reps.id"), nullable=True, index=True)
    recipient_name = db.Column(db.String(160), nullable=False)
    share_percent = db.Column(db.Numeric(8, 4), nullable=False)
    amount_brl = db.Column(db.Numeric(14, 2), nullable=True)
    payout_status = db.Column(db.String(20), nullable=False, default="pendente")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    opportunity = db.relationship("Opportunity", back_populates="commission_splits")
    tier = db.relationship("CommissionProjectTier")
    stakeholder = db.relationship("CompanyStakeholder")
    sales_rep = db.relationship("SalesRepresentative")


class CommissionSale(db.Model):
    """Venda/adesão avulsa para comissionamento manual (relatório e acompanhamento)."""

    __tablename__ = "commission_sales"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    organization = db.Column(db.String(200), nullable=True)
    value_brl = db.Column(db.Numeric(14, 2), nullable=True)
    sale_date = db.Column(db.Date, nullable=True)
    process_ref = db.Column(db.String(120), nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    splits = db.relationship(
        "CommissionSaleSplit",
        back_populates="sale",
        cascade="all, delete-orphan",
        order_by="CommissionSaleSplit.sort_order",
    )


class CommissionSaleSplit(db.Model):
    """Participante do rateio de uma venda avulsa."""

    __tablename__ = "commission_sale_splits"

    id = db.Column(db.Integer, primary_key=True)
    commission_sale_id = db.Column(
        db.Integer, db.ForeignKey("commission_sales.id"), nullable=False, index=True
    )
    recipient_name = db.Column(db.String(160), nullable=False)
    organization = db.Column(db.String(200), nullable=True)
    share_percent = db.Column(db.Numeric(8, 4), nullable=True)
    amount_brl = db.Column(db.Numeric(14, 2), nullable=True)
    payout_status = db.Column(db.String(20), nullable=False, default="pendente")
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    sale = db.relationship("CommissionSale", back_populates="splits")


class PncpOrgaoUnidade(db.Model):
    """Órgão / unidade cadastrada no PNCP, agregada das APIs de contratos e atas.

    A API pública de consulta não expõe e-mail ou telefone; o painel pode complementar.
    """

    __tablename__ = "pncp_orgao_unidades"
    __table_args__ = (
        UniqueConstraint("cnpj", "codigo_unidade", name="uq_pncp_org_unidade"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    codigo_unidade = db.Column(db.String(24), nullable=False, default="0000")
    razao_social = db.Column(db.String(320), nullable=False)
    nome_unidade = db.Column(db.String(420), nullable=True)
    uf_sigla = db.Column(db.String(2), nullable=True, index=True)
    municipio_nome = db.Column(db.String(220), nullable=True)
    codigo_municipio_ibge = db.Column(db.String(12), nullable=True)
    esfera_id = db.Column(db.String(2), nullable=True)
    poder_id = db.Column(db.String(2), nullable=True)
    fontes = db.Column(db.String(80), nullable=True)
    email_licitacoes = db.Column(db.String(200), nullable=True)
    telefone_licitacoes = db.Column(db.String(80), nullable=True)
    contato_licitacoes_obs = db.Column(db.Text, nullable=True)
    synced_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class PncpMercadoSnapshot(db.Model):
    """Amostra agregada da API pública de consulta do PNCP (contratos + atas) para o painel do cliente."""

    __tablename__ = "pncp_mercado_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim = db.Column(db.Date, nullable=False)
    contratos_total_api = db.Column(db.Integer, nullable=True)
    contratos_processados = db.Column(db.Integer, nullable=False, default=0)
    atas_total_api = db.Column(db.Integer, nullable=True)
    atas_processadas = db.Column(db.Integer, nullable=False, default=0)
    valor_contratos_despesa = db.Column(db.Numeric(20, 2), nullable=True)
    valor_contratos_receita = db.Column(db.Numeric(20, 2), nullable=True)
    amostra_incompleta = db.Column(db.Boolean, default=False, nullable=False)
    json_categorias = db.Column(db.Text, nullable=True)
    json_tipos_contrato = db.Column(db.Text, nullable=True)
    json_esfera = db.Column(db.Text, nullable=True)
    json_keywords_objeto = db.Column(db.Text, nullable=True)
    erro = db.Column(db.Text, nullable=True)


class BrOrgaoPublico(db.Model):
    """Diretório nacional para prospecção comercial: municípios, estados, Sistema S, cópias PNCP, etc."""

    __tablename__ = "br_orgaos_publicos"

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(40), nullable=False, index=True)
    nome = db.Column(db.String(400), nullable=False)
    nome_unidade = db.Column(db.String(200), nullable=True)
    uf = db.Column(db.String(2), nullable=True, index=True)
    regiao = db.Column(db.String(24), nullable=True, index=True)
    municipio_nome = db.Column(db.String(220), nullable=True)
    ibge_municipio_id = db.Column(db.String(7), nullable=True, unique=True)
    chave_externa = db.Column(db.String(100), nullable=True, unique=True, index=True)
    cnpj = db.Column(db.String(14), nullable=True, index=True)
    populacao_local = db.Column(db.Integer, nullable=True)
    populacao_ibge = db.Column(db.Integer, nullable=True)
    ano_referencia_pop_ibge = db.Column(db.Integer, nullable=True)
    potencial_orcamento_anual_brl = db.Column(db.Numeric(18, 2), nullable=True)
    orcamento_metodo = db.Column(db.String(60), nullable=True)
    email_contato = db.Column(db.String(200), nullable=True)
    telefone_contato = db.Column(db.String(80), nullable=True)
    nome_contato = db.Column(db.String(120), nullable=True)
    contato_obs = db.Column(db.Text, nullable=True)
    fonte = db.Column(db.String(40), nullable=True)
    sales_rep_updated_id = db.Column(db.Integer, db.ForeignKey("sales_reps.id"), nullable=True)
    contact_updated_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    sales_rep_updated = db.relationship(
        "SalesRepresentative",
        foreign_keys=[sales_rep_updated_id],
    )


class Partner(db.Model):
    """Parceiro (fornecedor / fabricante) — login próprio para cadastrar produtos e comissões por ARP."""

    __tablename__ = "partners"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    company_name = db.Column(db.String(200), nullable=True)
    razao_social = db.Column(db.String(300), nullable=True)
    phone = db.Column(db.String(40), nullable=True)
    cnpj = db.Column(db.String(22), nullable=True, index=True)
    cpf = db.Column(db.String(14), nullable=True)
    job_title = db.Column(db.String(120), nullable=True)
    sector = db.Column(db.String(120), nullable=True)
    website = db.Column(db.String(300), nullable=True)
    address_street = db.Column(db.String(200), nullable=True)
    address_number = db.Column(db.String(20), nullable=True)
    address_complement = db.Column(db.String(120), nullable=True)
    address_neighborhood = db.Column(db.String(120), nullable=True)
    address_city = db.Column(db.String(120), nullable=True)
    address_state = db.Column(db.String(2), nullable=True)
    address_zip = db.Column(db.String(10), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    products = db.relationship(
        "PartnerProduct",
        back_populates="partner",
        cascade="all, delete-orphan",
        order_by="PartnerProduct.updated_at.desc()",
    )

    @property
    def address_line(self) -> str | None:
        parts: list[str] = []
        street = (self.address_street or "").strip()
        number = (self.address_number or "").strip()
        if street:
            parts.append(f"{street}{', ' + number if number else ''}")
        comp = (self.address_complement or "").strip()
        if comp:
            parts.append(comp)
        hood = (self.address_neighborhood or "").strip()
        if hood:
            parts.append(hood)
        city = (self.address_city or "").strip()
        uf = (self.address_state or "").strip().upper()
        if city and uf:
            parts.append(f"{city} — {uf}")
        elif city:
            parts.append(city)
        zipc = (self.address_zip or "").strip()
        if zipc:
            parts.append(f"CEP {zipc}")
        return " · ".join(parts) if parts else None


class PartnerProduct(db.Model):
    """Produto do parceiro: proposta de item de catálogo (aprovação no painel) ou perfil legado só com comissões por ARP."""

    __tablename__ = "partner_products"

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    allowed_ufs_json = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Fluxo de aprovação: legacy = cadastro antigo (só comissões por ARP); pending → approved / rejected
    approval_status = db.Column(db.String(24), nullable=True, index=True)
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    rejection_note = db.Column(db.Text, nullable=True)
    # Parceiro pediu remoção (admin confirma no painel)
    deletion_requested_at = db.Column(db.DateTime, nullable=True, index=True)
    deletion_request_note = db.Column(db.Text, nullable=True)

    # Rascunho espelhando o formulário de produto do painel (até aprovação)
    draft_category_id = db.Column(
        db.Integer, db.ForeignKey("catalog_categories.id"), nullable=True, index=True
    )
    draft_section = db.Column(db.String(80), nullable=True)
    draft_sphere = db.Column(db.String(80), nullable=True)
    draft_quantity = db.Column(db.Integer, nullable=True)
    draft_stock_on_hand = db.Column(db.Integer, nullable=True)
    draft_unit_price = db.Column(db.Numeric(14, 2), nullable=True)
    draft_valid_until = db.Column(db.Date, nullable=True)
    draft_slug = db.Column(db.String(200), nullable=True)
    draft_highlight = db.Column(db.Boolean, default=False, nullable=False)
    draft_images_json = db.Column(db.Text, nullable=True)
    draft_catalog_attachments_json = db.Column(db.Text, nullable=True)
    draft_ata_company_docs_json = db.Column(db.Text, nullable=True)
    draft_ata_owner_company = db.Column(db.String(200), nullable=True, index=True)
    draft_manufacturer = db.Column(db.String(200), nullable=True, index=True)
    draft_source_product_url = db.Column(db.String(500), nullable=True)
    draft_pncp_url = db.Column(db.String(700), nullable=True)
    draft_contract_page_url = db.Column(db.String(700), nullable=True)
    draft_warranty = db.Column(db.String(300), nullable=True)
    draft_technical_description = db.Column(db.Text, nullable=True)

    partner = db.relationship("Partner", back_populates="products")
    catalog_item = db.relationship("CatalogItem", foreign_keys=[catalog_item_id])
    draft_category = db.relationship("CatalogCategory", foreign_keys=[draft_category_id])
    commissions = db.relationship(
        "PartnerProductArpCommission",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="PartnerProductArpCommission.id",
    )

    @property
    def is_legacy_commission_profile(self) -> bool:
        return (self.approval_status or "") == "legacy"

    @property
    def is_pending_catalog_proposal(self) -> bool:
        return self.approval_status in ("pending", "rejected")

    @property
    def is_approved_in_catalog(self) -> bool:
        return self.approval_status == "approved" and self.catalog_item_id is not None

    @property
    def has_pending_deletion_request(self) -> bool:
        return self.deletion_requested_at is not None

    def draft_image_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.draft_images_json)

    def draft_catalog_attachment_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.draft_catalog_attachments_json)

    def draft_ata_company_doc_paths(self) -> list[str]:
        return CatalogItem._paths_from_json(self.draft_ata_company_docs_json)

    def allowed_ufs_codes(self) -> list[str]:
        if not self.allowed_ufs_json:
            return []
        try:
            data = json.loads(self.allowed_ufs_json)
            if isinstance(data, list):
                return [str(x).upper().strip() for x in data if x]
        except (json.JSONDecodeError, TypeError):
            pass
        return []


class PartnerProductArpCommission(db.Model):
    """Comissão que o parceiro declara para uma ARP específica (produto do catálogo do site)."""

    __tablename__ = "partner_product_arp_commissions"

    id = db.Column(db.Integer, primary_key=True)
    partner_product_id = db.Column(
        db.Integer, db.ForeignKey("partner_products.id"), nullable=False, index=True
    )
    catalog_item_id = db.Column(
        db.Integer, db.ForeignKey("catalog_items.id"), nullable=False, index=True
    )
    commission_brl = db.Column(db.Numeric(14, 2), nullable=True)
    commission_percent = db.Column(db.Numeric(6, 2), nullable=True)
    note = db.Column(db.Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "partner_product_id",
            "catalog_item_id",
            name="uq_partner_product_catalog_arp",
        ),
    )

    product = db.relationship("PartnerProduct", back_populates="commissions")
    catalog_item = db.relationship("CatalogItem")


class CompanyEmployee(db.Model):
    """Colaborador da empresa — acesso à intranet (setores, chat, Kanban)."""

    __tablename__ = "company_employees"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    department_slug = db.Column(db.String(48), nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    chat_messages = db.relationship(
        "IntranetChatMessage",
        back_populates="author",
    )
    kanban_cards = db.relationship(
        "KanbanCard",
        back_populates="created_by",
    )


class IntranetChatMessage(db.Model):
    """Mensagem no chat interno (canal geral ou por setor)."""

    __tablename__ = "intranet_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(48), nullable=False, index=True)
    employee_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=False, index=True
    )
    body = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    author = db.relationship(
        "CompanyEmployee",
        back_populates="chat_messages",
        foreign_keys=[employee_id],
    )


class KanbanBoard(db.Model):
    __tablename__ = "kanban_boards"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    columns = db.relationship(
        "KanbanColumn",
        back_populates="board",
        order_by="KanbanColumn.sort_order",
        cascade="all, delete-orphan",
    )


class KanbanColumn(db.Model):
    __tablename__ = "kanban_columns"

    id = db.Column(db.Integer, primary_key=True)
    board_id = db.Column(db.Integer, db.ForeignKey("kanban_boards.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    board = db.relationship("KanbanBoard", back_populates="columns")
    cards = db.relationship(
        "KanbanCard",
        back_populates="column",
        order_by="KanbanCard.sort_order",
        cascade="all, delete-orphan",
    )


class KanbanCard(db.Model):
    """Cartão visível a todos os setores; pode ser etiquetado por setor."""

    __tablename__ = "kanban_cards"

    id = db.Column(db.Integer, primary_key=True)
    column_id = db.Column(db.Integer, db.ForeignKey("kanban_columns.id"), nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, nullable=True)
    department_slug = db.Column(db.String(48), nullable=True, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    column = db.relationship("KanbanColumn", back_populates="cards")
    created_by = db.relationship(
        "CompanyEmployee",
        back_populates="kanban_cards",
        foreign_keys=[created_by_id],
    )


class EmpresaProduto(db.Model):
    """Catálogo interno do setor de Compras: part numbers, descrição e acessórios."""

    __tablename__ = "empresa_produtos"
    __table_args__ = (UniqueConstraint("part_number", name="uq_empresa_produto_part_number"),)

    id = db.Column(db.Integer, primary_key=True)
    part_number = db.Column(db.String(120), nullable=False, index=True)
    nome = db.Column(db.String(400), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    # Lista JSON: [{"nome": "...", "part_number": "..."}, ...] ou strings
    acessorios_json = db.Column(db.Text, nullable=True)
    unidade = db.Column(db.String(20), nullable=False, default="UN")
    # NCM (8 dígitos) — usado no fechamento de preço / estimativa de impostos
    ncm = db.Column(db.String(10), nullable=True, index=True)
    beneficio_fiscal = db.Column(db.Text, nullable=True)
    observacoes_fiscais = db.Column(db.Text, nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
    empenho_itens = db.relationship(
        "EmpresaEmpenhoItem",
        back_populates="produto",
        cascade="all, delete-orphan",
    )
    fechamento_itens = db.relationship(
        "EmpresaFechamentoPrecoItem",
        back_populates="produto",
    )
    impostos_uf = db.relationship(
        "EmpresaProdutoImpostoUF",
        back_populates="produto",
        cascade="all, delete-orphan",
    )

    @property
    def acessorios_list(self) -> list:
        if not self.acessorios_json:
            return []
        try:
            data = json.loads(self.acessorios_json)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return []


class EmpresaEmpenho(db.Model):
    """Empenho processado (vinculável a itens do catálogo de produtos)."""

    __tablename__ = "empresa_empenhos"

    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(160), nullable=False, index=True)
    orgao_nome = db.Column(db.String(400), nullable=False)
    cnpj_orgao = db.Column(db.String(14), nullable=True, index=True)
    valor_total = db.Column(db.Numeric(18, 2), nullable=True)
    data_emissao = db.Column(db.Date, nullable=True)
    data_processamento = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="processado")
    observacoes = db.Column(db.Text, nullable=True)
    contrato_id = db.Column(
        db.Integer, db.ForeignKey("empresa_contratos_orgao.id"), nullable=True, index=True
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
    contrato = db.relationship("EmpresaContratoOrgao", back_populates="empenhos")
    itens = db.relationship(
        "EmpresaEmpenhoItem",
        back_populates="empenho",
        cascade="all, delete-orphan",
        order_by="EmpresaEmpenhoItem.sort_order",
    )


class EmpresaEmpenhoItem(db.Model):
    """Liga produto cadastrado a um empenho (quantidade / valor unitário)."""

    __tablename__ = "empresa_empenho_itens"

    id = db.Column(db.Integer, primary_key=True)
    empenho_id = db.Column(db.Integer, db.ForeignKey("empresa_empenhos.id"), nullable=False, index=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("empresa_produtos.id"), nullable=False, index=True)
    quantidade = db.Column(db.Numeric(18, 4), nullable=False, default=1)
    valor_unitario = db.Column(db.Numeric(18, 4), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    observacao = db.Column(db.String(500), nullable=True)

    empenho = db.relationship("EmpresaEmpenho", back_populates="itens")
    produto = db.relationship("EmpresaProduto", back_populates="empenho_itens")


class EmpresaContratoOrgao(db.Model):
    """Contrato com órgão público — saldo e vigência; empenhos podem referenciar."""

    __tablename__ = "empresa_contratos_orgao"
    __table_args__ = (UniqueConstraint("numero_contrato", name="uq_empresa_contrato_numero"),)

    id = db.Column(db.Integer, primary_key=True)
    numero_contrato = db.Column(db.String(160), nullable=False, index=True)
    orgao_nome = db.Column(db.String(400), nullable=False)
    cnpj_orgao = db.Column(db.String(14), nullable=True, index=True)
    email_contato = db.Column(db.String(120), nullable=True)
    telefone = db.Column(db.String(40), nullable=True)
    cliente_razao_social = db.Column(db.String(400), nullable=True)
    cliente_cnpj = db.Column(db.String(14), nullable=True, index=True)
    vigencia_inicio = db.Column(db.Date, nullable=True)
    vigencia_fim = db.Column(db.Date, nullable=True)
    valor_total = db.Column(db.Numeric(18, 2), nullable=True)
    saldo_referencia = db.Column(db.Numeric(18, 2), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="vigente")
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
    empenhos = db.relationship("EmpresaEmpenho", back_populates="contrato")


class EmpresaRemessa(db.Model):
    """Entrega / remessa — rastreio e vínculo opcional a empenho."""

    __tablename__ = "empresa_remessas"

    id = db.Column(db.Integer, primary_key=True)
    codigo_rastreio = db.Column(db.String(120), nullable=True, index=True)
    nf_referencia = db.Column(db.String(80), nullable=True)
    destino_orgao = db.Column(db.String(400), nullable=False)
    endereco_resumo = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="em_transito")
    data_prevista = db.Column(db.Date, nullable=True)
    data_entrega = db.Column(db.Date, nullable=True)
    empenho_id = db.Column(db.Integer, db.ForeignKey("empresa_empenhos.id"), nullable=True, index=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    empenho = db.relationship("EmpresaEmpenho", backref=db.backref("remessas", lazy="dynamic"))
    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaChamadoTecnico(db.Model):
    """Chamado de suporte / implantação."""

    __tablename__ = "empresa_chamados_tecnicos"

    id = db.Column(db.Integer, primary_key=True)
    numero_interno = db.Column(db.String(64), nullable=False, index=True)
    titulo = db.Column(db.String(400), nullable=False)
    orgao_cliente = db.Column(db.String(400), nullable=True)
    prioridade = db.Column(db.String(24), nullable=False, default="media")
    status = db.Column(db.String(40), nullable=False, default="aberto")
    descricao = db.Column(db.Text, nullable=True)
    solucao_resumo = db.Column(db.Text, nullable=True)
    aberto_em = db.Column(db.Date, nullable=True)
    fechado_em = db.Column(db.Date, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaProcessoJuridico(db.Model):
    """Processo ou demanda jurídica com prazo."""

    __tablename__ = "empresa_processos_juridicos"

    id = db.Column(db.Integer, primary_key=True)
    numero_processo = db.Column(db.String(120), nullable=False, index=True)
    tipo = db.Column(db.String(80), nullable=True)
    tribunal = db.Column(db.String(300), nullable=True)
    polo = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(60), nullable=False, default="ativo")
    proximo_prazo = db.Column(db.Date, nullable=True, index=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaProtocoloGarantia(db.Model):
    """Protocolo de troca / garantia."""

    __tablename__ = "empresa_protocolos_garantia"
    __table_args__ = (UniqueConstraint("numero_protocolo", name="uq_empresa_prot_garantia_num"),)

    id = db.Column(db.Integer, primary_key=True)
    numero_protocolo = db.Column(db.String(80), nullable=False, index=True)
    orgao_solicitante = db.Column(db.String(400), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey("empresa_produtos.id"), nullable=True, index=True)
    descricao_produto = db.Column(db.String(500), nullable=True)
    defeito_relato = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="aberto")
    data_abertura = db.Column(db.Date, nullable=True)
    data_conclusao = db.Column(db.Date, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    produto = db.relationship("EmpresaProduto")
    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaNotaFiscalEmitida(db.Model):
    """NF-e ou documento fiscal emitido (controle interno)."""

    __tablename__ = "empresa_notas_fiscais_emitidas"

    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(32), nullable=False)
    serie = db.Column(db.String(8), nullable=True)
    data_emissao = db.Column(db.Date, nullable=True, index=True)
    valor_total = db.Column(db.Numeric(18, 2), nullable=True)
    orgao_cliente = db.Column(db.String(400), nullable=True)
    empenho_numero_ref = db.Column(db.String(160), nullable=True, index=True)
    status = db.Column(db.String(40), nullable=False, default="autorizada")
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaPendencia(db.Model):
    """Pendência multi-setor."""

    __tablename__ = "empresa_pendencias"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(300), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    setor_origem_slug = db.Column(db.String(48), nullable=True, index=True)
    setor_responsavel_slug = db.Column(db.String(48), nullable=True, index=True)
    prioridade = db.Column(db.String(24), nullable=False, default="media")
    status = db.Column(db.String(40), nullable=False, default="aberta")
    data_alvo = db.Column(db.Date, nullable=True, index=True)
    resolvida_em = db.Column(db.Date, nullable=True)
    observacoes_fechamento = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaProjeto(db.Model):
    """Projeto com cliente público."""

    __tablename__ = "empresa_projetos"
    __table_args__ = (UniqueConstraint("codigo", name="uq_empresa_projeto_codigo"),)

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(64), nullable=False, index=True)
    nome = db.Column(db.String(400), nullable=False)
    orgao_cliente = db.Column(db.String(400), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="planejamento")
    data_inicio = db.Column(db.Date, nullable=True)
    data_fim_prevista = db.Column(db.Date, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
    marcos = db.relationship(
        "EmpresaProjetoMarco",
        back_populates="projeto",
        cascade="all, delete-orphan",
        order_by="EmpresaProjetoMarco.sort_order",
    )


class EmpresaProjetoMarco(db.Model):
    """Marco de projeto."""

    __tablename__ = "empresa_projeto_marcos"

    id = db.Column(db.Integer, primary_key=True)
    projeto_id = db.Column(db.Integer, db.ForeignKey("empresa_projetos.id"), nullable=False, index=True)
    nome = db.Column(db.String(300), nullable=False)
    data_prevista = db.Column(db.Date, nullable=True)
    data_realizada = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="pendente")
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    projeto = db.relationship("EmpresaProjeto", back_populates="marcos")


class EmpresaLicitacao(db.Model):
    """Oportunidade de licitação (pipeline)."""

    __tablename__ = "empresa_licitacoes"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(400), nullable=False)
    orgao = db.Column(db.String(400), nullable=True)
    modalidade = db.Column(db.String(120), nullable=True)
    numero_edital = db.Column(db.String(160), nullable=True, index=True)
    data_abertura = db.Column(db.Date, nullable=True)
    data_envio_proposta = db.Column(db.Date, nullable=True)
    data_limite_impugnacao = db.Column(db.Date, nullable=True)
    data_limite_esclarecimento = db.Column(db.Date, nullable=True)
    prazo_entrega_objeto = db.Column(db.Date, nullable=True)
    local_entrega_edital = db.Column(db.Text, nullable=True)
    multas_edital = db.Column(db.Text, nullable=True)
    documentacao_solicitada = db.Column(db.Text, nullable=True)
    esclarecimentos = db.Column(db.Text, nullable=True)
    questionamento_impugnacao = db.Column(db.Text, nullable=True)
    checklist_documentos_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(40), nullable=False, default="estudo")
    valor_proposta = db.Column(db.Numeric(18, 2), nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    fechamentos = db.relationship(
        "EmpresaFechamentoPreco",
        back_populates="licitacao",
        lazy="dynamic",
    )

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaNcmPerfil(db.Model):
    """Alíquotas de referência por NCM (8 dígitos) para estimativa no fechamento de preço."""

    __tablename__ = "empresa_ncm_perfis"
    __table_args__ = (UniqueConstraint("ncm", name="uq_empresa_ncm_perfil_ncm"),)

    id = db.Column(db.Integer, primary_key=True)
    ncm = db.Column(db.String(8), nullable=False, index=True)
    descricao = db.Column(db.String(300), nullable=True)
    # Percentuais para estimativa (ajuste conforme regime da empresa / UF)
    aliquota_icms = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_ipi = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_pis = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_cofins = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    observacao = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class EmpresaFechamentoPreco(db.Model):
    """Planilha de fechamento de preço (vinculável a licitação; fluxo com Compras)."""

    __tablename__ = "empresa_fechamento_precos"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(400), nullable=False)
    local_entrega = db.Column(db.Text, nullable=True)
    licitacao_id = db.Column(db.Integer, db.ForeignKey("empresa_licitacoes.id"), nullable=True, index=True)
    beneficio_fiscal = db.Column(db.Text, nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    percentual_frete = db.Column(db.Numeric(8, 4), nullable=True)
    custo_financeiro_percent = db.Column(db.Numeric(8, 4), nullable=True)
    markup_final_percent = db.Column(db.Numeric(8, 4), nullable=True)
    aprovacao_prejuizo = db.Column(db.Boolean, default=False, nullable=False)
    compras_aprovado = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(40), nullable=False, default="rascunho")
    uf_entrega = db.Column(db.String(2), nullable=True, index=True)
    planilha_fechada = db.Column(db.Boolean, default=False, nullable=False)
    fechada_em = db.Column(db.DateTime, nullable=True)
    snapshot_json = db.Column(db.Text, nullable=True)
    account_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    competencia_faturamento = db.Column(db.Date, nullable=True, index=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    licitacao = db.relationship("EmpresaLicitacao", back_populates="fechamentos")
    account = db.relationship("CompanyEmployee", foreign_keys=[account_id])
    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
    itens = db.relationship(
        "EmpresaFechamentoPrecoItem",
        back_populates="fechamento",
        cascade="all, delete-orphan",
        order_by="EmpresaFechamentoPrecoItem.sort_order",
    )
    comissoes = db.relationship(
        "EmpresaFechamentoComissao",
        back_populates="fechamento",
        cascade="all, delete-orphan",
        order_by="EmpresaFechamentoComissao.id",
    )


class EmpresaFechamentoPrecoItem(db.Model):
    """Linha da planilha de fechamento."""

    __tablename__ = "empresa_fechamento_preco_itens"

    id = db.Column(db.Integer, primary_key=True)
    fechamento_id = db.Column(
        db.Integer, db.ForeignKey("empresa_fechamento_precos.id"), nullable=False, index=True
    )
    produto_id = db.Column(db.Integer, db.ForeignKey("empresa_produtos.id"), nullable=False, index=True)
    quantidade = db.Column(db.Numeric(18, 4), nullable=False, default=1)
    custo_unitario = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    observacao = db.Column(db.String(500), nullable=True)

    fechamento = db.relationship("EmpresaFechamentoPreco", back_populates="itens")
    produto = db.relationship("EmpresaProduto", back_populates="fechamento_itens")


class EmpresaFechamentoComissao(db.Model):
    """Participação na comissão da equipe por planilha de fechamento (% sobre o total sugerido / faturamento previsto)."""

    __tablename__ = "empresa_fechamento_comissoes"
    __table_args__ = (
        UniqueConstraint("fechamento_id", "employee_id", name="uq_empresa_fech_comissao_fech_emp"),
    )

    id = db.Column(db.Integer, primary_key=True)
    fechamento_id = db.Column(
        db.Integer, db.ForeignKey("empresa_fechamento_precos.id"), nullable=False, index=True
    )
    employee_id = db.Column(db.Integer, db.ForeignKey("company_employees.id"), nullable=False, index=True)
    percentual_comissao = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    papel = db.Column(db.String(120), nullable=True)

    fechamento = db.relationship("EmpresaFechamentoPreco", back_populates="comissoes")
    employee = db.relationship("CompanyEmployee", foreign_keys=[employee_id])


class EmpresaProdutoImpostoUF(db.Model):
    """Alíquotas e benefício por produto e UF — usado no fechamento de preço (prioridade sobre NCM)."""

    __tablename__ = "empresa_produto_imposto_uf"
    __table_args__ = (
        UniqueConstraint("produto_id", "uf", name="uq_empresa_prod_imposto_uf"),
    )

    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("empresa_produtos.id"), nullable=False, index=True)
    uf = db.Column(db.String(2), nullable=False, index=True)
    beneficio_fiscal = db.Column(db.Text, nullable=True)
    aliquota_icms = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_ipi = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_pis = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    aliquota_cofins = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    observacao = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    produto = db.relationship("EmpresaProduto", back_populates="impostos_uf")


class EmpresaDocumentoRepositorio(db.Model):
    """Repositório central de documentos corporativos (certidões, modelos, atas, etc.)."""

    __tablename__ = "empresa_documentos_repositorio"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(400), nullable=False)
    categoria = db.Column(db.String(120), nullable=True, index=True)
    descricao = db.Column(db.Text, nullable=True)
    nome_original = db.Column(db.String(260), nullable=False)
    caminho_relativo = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(120), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])


class EmpresaAnexo(db.Model):
    """Anexo genérico vinculado a um registro de módulo (contexto + ref_id)."""

    __tablename__ = "empresa_anexos"

    id = db.Column(db.Integer, primary_key=True)
    contexto = db.Column(db.String(32), nullable=False, index=True)
    ref_id = db.Column(db.Integer, nullable=False, index=True)
    nome_original = db.Column(db.String(260), nullable=False)
    caminho_relativo = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(120), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("company_employees.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    created_by = db.relationship("CompanyEmployee", foreign_keys=[created_by_id])
