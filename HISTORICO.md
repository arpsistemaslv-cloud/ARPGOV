# Histórico do PortalGovCRM

Última atualização deste arquivo: **29/03/2026**.

Documento de continuidade: o que já foi implementado nas sessões de desenvolvimento e **onde paramos** para o próximo passo.

---

## Acesso local

- URL típica: **http://127.0.0.1:5001/** (preferir `127.0.0.1` em vez de `localhost` no Windows, por IPv4/IPv6).
- Subir: `run_portal.bat` ou `.venv\Scripts\python.exe app.py`.
- Ajustes antigos no Windows: host padrão `127.0.0.1`, reloader opcional, `load_dotenv` com caminho fixo do `.env` e `override=True`.

---

## Autenticação (resumo)

- **Painel** (`/admin/login`): senha em `PAINEL_ADMIN_PASSWORD` ou `PORTAL_ADMIN_PASSWORD` (`.env`).
- **CRM** (`/crm/login`): `CRM_ADMIN_PASSWORD`; há fallback para a senha do painel quando o CRM não está preenchido (comportamento documentado nas sessões).
- **Senha master opcional** (`PORTAL_MASTER_PASSWORD`): aceita nos fluxos de painel, CRM, cliente, comercial, parceiro e intranet empresa; nas áreas com e-mail, costuma assumir o **primeiro cadastro ativo** da respectiva tabela (ver código e comentários no `.env.example`).
- Textos das telas de login foram **desobstruídos** (remoção de parágrafos que citavam nomes de variáveis ou políticas internas na interface).

*Nunca commitar o `.env` com senhas reais; usar `.env.example` como referência.*

---

## Site público e painel

- Painel protegido por senha: catálogo, aparência/textos, páginas HTML, PNCP, categorias, etc.
- Páginas dinâmicas em `/p/<slug>`.
- Correções de rotas/redirect: endpoint `admin_home`, `next` seguro no login, URLs injetadas no contexto para evitar `url_for` quebrado.

---

## Catálogo e loja

- Produtos com múltiplas imagens (`images_json`, upload em `static/uploads/catalog/`), galeria na página do produto.
- Exclusão em massa de produtos (confirmação com texto `EXCLUIR TODOS`), com limpeza de vínculos no CRM e arquivos.
- **Categorias hierárquicas** (catálogo / subcatálogo), vínculo nos produtos, filtros na **loja** (categoria e esfera).
- Campo **só no painel**: `ata_owner_company` (empresa dona da ata); não aparece no site; filtro na listagem admin; mesmo dado exibido no CRM ao vincular produto à oportunidade.

---

## CRM

- Oportunidades com **CNPJ** e **vários produtos** do catálogo (associação N:N).
- Formulário de **contato** no site cria lead (`Opportunity`) com observações, órgão, contato, CNPJ, esfera, etc., com **checkboxes** para vincular produtos (mesma lógica do CRM).
- Botões “Quero aderir” passam `?produto=slug` para pré-marcar produto no contato.

---

## Visual (ARPGOV)

- Marca **ARPGOV** (`ARP` + destaque `GOV` em azul gov.br).
- Paleta alinhada ao [Contratos.gov.br](https://contratos.sistema.gov.br/transparencia): azul `#1351B4`, escuro `#071D41`, amarelo `#FFCD07`.
- Layout **full-width** no site público; conteúdo em `.page-shell` (até 1560px).
- Logo: `static/images/arpgov-mark.svg`, `arpgov-logo-horizontal.svg`.
- Tipografia: **Source Sans 3**.
- **Brand Kit** no CRM: `/crm/brand-kit` (cores, logos, assinatura de e-mail).

---

## Intranet empresa e outras áreas

- Módulos empresa (licitações, compras, projetos, etc.), área cliente, comercial, parceiro — evoluíram em paralelo ao núcleo portal; detalhes finos estão no código e templates sob `templates/empresa/`, `templates/cliente/`, etc.

---

## Onde paramos (próximos passos sugeridos)

Última frente fechada nas conversas: **base de produção** no código.

**Já feito:**

- `FLASK_DEBUG` controlado por ambiente (padrão desligado; ligar só em desenvolvimento).
- Cookies de sessão: `SESSION_COOKIE_SECURE`, `HTTPONLY`, `SESSION_COOKIE_SAMESITE`.
- `TRUST_PROXY` + `ProxyFix` para proxy reverso (HTTPS).
- `gunicorn` listado em `requirements.txt` para deploy em Linux.

**Ainda não implementado (explicitamente deixado para depois):**

- URI do banco via **`DATABASE_URL`** (PostgreSQL em produção em vez de SQLite).
- **CSRF** nos POSTs críticos.
- Demais itens da conversa de “nível empresa”: MFA/SSO, rate limit de login, auditoria, etc.

---

## Referência de conversas

[Chat portal e CRM](787dee69-2604-400a-8550-c875ef42b13a) — histórico principal das solicitações até o hardening parcial de produção.

---

*Atualize este arquivo ao concluir blocos grandes de trabalho, para facilitar retomada em outra máquina ou após push no GitHub.*
