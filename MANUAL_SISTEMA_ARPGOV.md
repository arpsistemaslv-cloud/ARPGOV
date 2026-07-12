# Manual Completo do Sistema ARPGOV

**Versão:** julho/2026  
**Produção:** https://arpgov.com  
**Objetivo:** documentar todas as áreas, perfis de acesso e fluxos operacionais para apresentação à diretoria e operação diária.

---

## Índice

1. [Visão geral do sistema](#1-visão-geral-do-sistema)
2. [Mapa de áreas e URLs](#2-mapa-de-áreas-e-urls)
3. [Site público](#3-site-público)
4. [Área do Cliente (órgão público)](#4-área-do-cliente-órgão-público)
5. [Área do Parceiro (fornecedor)](#5-área-do-parceiro-fornecedor)
6. [Área Comercial (representantes)](#6-área-comercial-representantes)
7. [Painel Admin (gestão do site)](#7-painel-admin-gestão-do-site)
8. [CRM (gestão comercial e financeira)](#8-crm-gestão-comercial-e-financeira)
9. [Funil de vendas (pipeline)](#9-funil-de-vendas-pipeline)
10. [Fluxos integrados entre áreas](#10-fluxos-integrados-entre-áreas)
11. [Perfis de acesso e login](#11-perfis-de-acesso-e-login)
12. [Roteiro para reunião de sócios](#12-roteiro-para-reunião-de-sócios)

---

## 1. Visão geral do sistema

O **ARPGOV** é uma plataforma web que conecta três públicos:

| Público | O que faz no sistema |
|---------|----------------------|
| **Órgãos públicos (clientes)** | Consultam atas, montam solicitações de adesão e acompanham o andamento |
| **Fornecedores (parceiros)** | Cadastram produtos/atas para publicação no catálogo e gerenciam comissões |
| **Equipe ARPGOV** | Opera o site, o CRM, o funil comercial, comissões e financeiro |

### Arquitetura resumida

```
┌─────────────────────────────────────────────────────────────────┐
│                        SITE PÚBLICO                              │
│  Início · Atas (/arps) · Produto · Contato · Como aderir        │
└────────────┬───────────────────────────────┬────────────────────┘
             │                               │
    ┌────────▼────────┐             ┌────────▼────────┐
    │  ÁREA CLIENTE   │             │  ÁREA PARCEIRO  │
    │  /cliente/*     │             │  /parceiro/*    │
    └────────┬────────┘             └────────┬────────┘
             │                               │
             └──────────────┬────────────────┘
                            ▼
              ┌─────────────────────────┐
              │   LEADS (Oportunidades)  │
              │   Banco de dados único   │
              └────────────┬────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
  │  COMERCIAL  │   │     CRM     │   │    ADMIN    │
  │ /comercial  │   │    /crm     │   │   /admin    │
  └─────────────┘   └─────────────┘   └─────────────┘
```

**Tecnologia:** Flask (Python) + SQLite + Gunicorn + Nginx na VPS Hostinger.  
**Dados:** banco `instance/portal.db` e arquivos em `static/uploads/` (não vão para o GitHub).

---

## 2. Mapa de áreas e URLs

### Produção (arpgov.com)

| Área | URL de entrada | Quem acessa |
|------|----------------|-------------|
| Site público | https://arpgov.com | Qualquer visitante |
| Catálogo de atas | https://arpgov.com/arps | Qualquer visitante |
| Área do cliente | https://arpgov.com/cliente/entrar | Órgãos públicos cadastrados |
| Área do parceiro | https://arpgov.com/parceiro/entrar | Fornecedores cadastrados |
| Comercial | https://arpgov.com/comercial/entrar | Representantes de vendas |
| Painel Admin | https://arpgov.com/admin/login | Gestores do site |
| CRM | https://arpgov.com/crm/login | Gestão comercial e financeira |

> A URL antiga `/loja` redireciona automaticamente para `/arps`.

### O que cada área controla

| Área | Controla | Não controla |
|------|----------|--------------|
| **Site público** | Vitrine, formulário de contato, carrinho | Aprovação de produtos, comissões |
| **Cliente** | Perfil, carrinho, acompanhamento de leads, chat | Edição de catálogo, pipeline interno |
| **Parceiro** | Solicitação de produtos, comissões por ARP | Publicação direta no site |
| **Comercial** | Leads próprios, prospecção, envio de NF de comissão | Configuração do site, tipos de comissão |
| **Admin** | Catálogo, parceiros, site, importações PNCP | Rateio entre sócios (fica no CRM) |
| **CRM** | Clientes, fornecedores, leads, metas, financeiro | Layout do site público |

---

## 3. Site público

**Acesso:** livre, sem login.

### 3.1 Páginas disponíveis

| Página | URL | Conteúdo |
|--------|-----|----------|
| Início | `/` | Hero, atas em destaque, estatísticas, CTAs |
| Catálogo de atas | `/arps` | Lista de produtos com filtros |
| Ficha do produto | `/produto/<slug>` | Imagens, especificações, preço, adesão |
| Como aderir | `/como-aderir` | Explicação do processo de carona em ata |
| Institucional | `/institucional` | Sobre a ARPGOV, missão, diferencial |
| Contato / Solicitação | `/contato` | Formulário de adesão |
| Carrinho | `/carrinho` | Lista de produtos para solicitação (**exige login**) |
| Páginas extras | `/p/<slug>` | Páginas CMS criadas no admin |

### 3.2 Catálogo de atas (`/arps`)

**Filtros disponíveis:**
- Categoria e subcategoria
- Esfera administrativa (federal, estadual, municipal)
- Fabricante

**Ações do visitante:**
- Ver card do produto (imagem, preço, validade, quantidade na ata)
- Abrir ficha completa (`/produto/<slug>`)
- **Adicionar à solicitação** (carrinho) — pede login se não estiver logado
- **Quero aderir** — abre contato com produto pré-selecionado

### 3.3 Formulário de contato (`/contato`)

O visitante pode solicitar adesão informando:
- Produto(s) do catálogo (busca integrada)
- Assunto e mensagem
- Órgão/empresa, esfera, CNPJ
- Nome, e-mail e telefone do contato

**Ao enviar:** o sistema cria automaticamente um **lead** no estágio **Novo**, com fonte "Site — formulário contato". Se o cliente estiver logado, o lead fica vinculado à conta dele.

### 3.4 Carrinho / Solicitação de adesão

| Ação | URL | Login |
|------|-----|-------|
| Ver e editar carrinho | `/carrinho` | Obrigatório |
| Adicionar produto | POST `/carrinho/adicionar/<slug>` | Opcional (redireciona para login) |
| Atualizar quantidades | POST `/carrinho/atualizar` | Obrigatório |
| Remover item | POST `/carrinho/remover/<id>` | Obrigatório |
| Esvaziar lista | POST `/carrinho/limpar` | Obrigatório |

**Fluxo típico:** montar lista → **Enviar solicitação de adesão** → formulário de contato pré-preenchido → lead criado → carrinho limpo.

---

## 4. Área do Cliente (órgão público)

**Entrada:** https://arpgov.com/cliente/entrar  
**Cadastro:** https://arpgov.com/cliente/cadastro

### 4.1 O que o cliente consegue fazer

| Função | Onde | Descrição |
|--------|------|-----------|
| Criar conta | `/cliente/cadastro` | Nome, e-mail, senha, dados do órgão (CNPJ, esfera, endereço) |
| Entrar / sair | `/cliente/entrar`, `/cliente/sair` | Autenticação por e-mail e senha |
| Página inicial | `/cliente/inicio` | Atalhos rápidos + feed de notícias (mercado público) |
| Meu perfil | `/cliente/perfil` | Editar foto, e-mail, senha, dados pessoais e do órgão |
| Meus leads | `/cliente/meus-leads` | Lista de todas as solicitações vinculadas à conta |
| Detalhe do lead | `/cliente/lead/<id>` | Ver produtos, valor, estágio e **chat com a equipe** |
| Mercado público | `/cliente/mercado-publico` | Gráficos e estatísticas PNCP (dados atualizados pelo admin) |
| Carrinho | `/carrinho` | Montar e enviar nova solicitação de adesão |

### 4.2 Chat com a equipe

No detalhe do lead (`/cliente/lead/<id>`), o cliente pode:
- Enviar mensagens de texto
- Anexar até **8 arquivos** por mensagem (máx. 15 MB cada)
- Ver histórico completo da conversa
- Acompanhar o **estágio atual** do funil (somente leitura)

A equipe responde pelo **Comercial** ou **CRM** na mesma conversa.

### 4.3 O que o cliente vê (e o que não vê)

| Vê | Não vê |
|----|--------|
| Seus próprios leads e estágios | Leads de outros órgãos |
| Produtos e valores da solicitação | Comissões internas e rateio entre sócios |
| Chat com a equipe ARPGOV | Painel admin, CRM ou área de parceiros |
| Dados agregados do mercado público | Configurações do catálogo |

### 4.4 Cadastro — dados coletados

- **Pessoais:** nome, e-mail, telefone, cargo
- **Órgão:** razão social, nome fantasia, CNPJ (com busca automática), esfera
- **Endereço:** CEP (com busca automática), logradouro, número, complemento, bairro, cidade, UF

Após o cadastro, leads antigos com o mesmo e-mail são vinculados automaticamente à conta.

---

## 5. Área do Parceiro (fornecedor)

**Entrada:** https://arpgov.com/parceiro/entrar  
**Cadastro:** https://arpgov.com/parceiro/cadastro

### 5.1 O que o parceiro consegue fazer

| Função | Onde | Descrição |
|--------|------|-----------|
| Auto-cadastro | `/parceiro/cadastro` | Dados da empresa, contato e senha |
| Dashboard | `/parceiro` | Lista de todos os seus produtos |
| Novo produto | `/parceiro/produto/novo` | Enviar ata para aprovação |
| Editar produto | `/parceiro/produto/<id>/editar` | Conforme status (ver tabela abaixo) |
| Excluir rascunho | POST `/parceiro/produto/<id>/excluir` | Só produtos não publicados |
| Solicitar exclusão | `/parceiro/produto/<id>/solicitar-exclusao` | Para produtos já no site |
| Cancelar pedido de exclusão | POST `/parceiro/produto/<id>/cancelar-exclusao` | Enquanto pendente no admin |

### 5.2 Status dos produtos no dashboard

| Status | Significado | Ações do parceiro |
|--------|-------------|-------------------|
| **Aguardando aprovação** | Enviado, aguardando análise do admin | Editar tudo, excluir rascunho |
| **Recusado** | Admin recusou com observação | Corrigir e reenviar |
| **Publicado** | Visível em `/arps` | Editar comissões, abrangência, ativo/inativo |
| **Legado** | Cadastro antigo importado | Editar nome, descrição, comissões, abrangência |
| **Inativo** | Oculto do catálogo público | Reativar pelo formulário de edição |

### 5.3 O que pode editar em cada status

| Status | Campos editáveis |
|--------|------------------|
| Nova solicitação / Pendente / Recusado | **Formulário completo:** título, seção, slug, esfera, categoria, fabricante, preço, quantidade, estoque, validade, imagens, anexos, documentos da empresa, URLs PNCP/contrato, descrição técnica, abrangência (UFs) |
| Publicado | Descrição complementar, abrangência (UFs), ativo/inativo, **comissões por ARP** |
| Legado | Nome, descrição, abrangência, ativo/inativo, comissões por ARP |

> Dados principais da ata (preço, validade, etc.) de produtos **publicados** só podem ser alterados pelo **Painel Admin**.

### 5.4 Comissões por ARP

Para cada linha de ARP vinculada ao catálogo, o parceiro define:
- Valor em **R$** ou **percentual**
- Observação opcional

Essas comissões alimentam o cálculo financeiro no CRM quando um lead é fechado.

### 5.5 O que o parceiro vê (e o que não vê)

| Vê | Não vê |
|----|--------|
| Seus produtos e status | Produtos de outros parceiros |
| Comissões configuradas por ARP | Leads, clientes ou pipeline comercial |
| Observação de recusa do admin | Área comercial ou CRM |
| Pedidos de exclusão pendentes | Configurações do site |

### 5.6 Fluxo de aprovação de produto

```
Parceiro cadastra produto
        │
        ▼
Status: Aguardando aprovação
        │
        ├── Admin APROVA → Publicado no site (/arps)
        │
        └── Admin RECUSA → Parceiro corrige e reenvia
```

---

## 6. Área Comercial (representantes)

**Entrada:** https://arpgov.com/comercial/entrar

### 6.1 O que o representante consegue fazer

| Módulo | URL | Funções |
|--------|-----|---------|
| Dashboard | `/comercial` | Ver leads; filtrar por estágio |
| Nova captação | `/comercial/oportunidade/nova` | Criar lead vinculado a produto(s) |
| Editar lead | `/comercial/oportunidade/<id>` | Dados completos, produtos, chat, pipeline |
| Excluir lead | `/comercial/oportunidade/<id>/excluir` | Com confirmação (digitar EXCLUIR) |
| Alterar estágio | POST `/comercial/oportunidade/<id>/estagio` | Mover no funil; notifica cliente por e-mail |
| Chat | POST `/comercial/oportunidade/<id>/mensagem` | Responder cliente com anexos |
| Órgãos públicos | `/comercial/orgaos-publicos` | Diretório nacional para prospecção |
| Contato do órgão | `/comercial/orgaos-publicos/<id>/contato` | Registrar e-mail, telefone, observações |
| Financeiro | `/comercial/financeiro` | Ver comissões e enviar documentos |
| Novo envio financeiro | `/comercial/financeiro/novo` | Enviar NF/documento de comissão |

### 6.2 Escopo de visibilidade

| Tipo de representante | O que vê |
|-----------------------|----------|
| **Representante comum** | Apenas leads onde ele é o responsável (`sales_rep_id`) |
| **Representante administrador** | Todos os leads + acesso ao Painel Admin e CRM |

### 6.3 Dashboard de leads

- Lista todos os leads do representante (ou todos, se admin)
- Filtro por estágio do pipeline (`?stage=`)
- Coluna do representante responsável (visível só para admin)
- Acesso rápido para editar cada oportunidade

### 6.4 Prospecção — Órgãos públicos

Ferramenta de prospecção com diretório nacional importado pelo admin:

**Filtros:** texto livre, UF, região, tipo de órgão  
**Paginação:** 40 registros por página  
**Dados:** população (IBGE), contatos registrados pelo representante

O representante pode atualizar contato de cada órgão para uso em campanhas de captação.

### 6.5 Financeiro do representante

**Painel (`/comercial/financeiro`):**
- Total de comissão acumulada
- Valores pagos e em análise
- Lista de leads com comissão calculada
- Histórico de documentos enviados
- Filtro `?so_comissao=1` para ver só leads com comissão

**Enviar documento (`/comercial/financeiro/novo`):**
- Título do envio
- Valor
- Vínculo com lead (opcional)
- Anexos obrigatórios (NF, comprovantes)

**Status do documento:** Enviado → Em análise → Aprovado → Pago / Recusado

O representante pode atualizar o status e gerenciar anexos dos seus próprios envios. A equipe administrativa confirma pagamentos pelo **CRM → Financeiro**.

### 6.6 O que o comercial vê (e o que não vê)

| Vê | Não vê |
|----|--------|
| Seus leads e pipeline | Configuração de tipos de comissão (sócios, faixas) |
| Chat com clientes | Aprovação de produtos de parceiros |
| Diretório de órgãos públicos | Edição do layout do site |
| Suas comissões e envios de NF | Rateio entre sócios (detalhe no CRM) |

---

## 7. Painel Admin (gestão do site)

**Entrada:** https://arpgov.com/admin/login

O Painel Admin é o centro de gestão do **site público**, do **catálogo** e das **integrações**. Para leads e financeiro, use o **CRM** (alternância no topo da tela).

### 7.1 Módulos — Catálogo e conteúdo

| Módulo | URL | O que faz |
|--------|-----|-----------|
| Categorias | `/admin/categorias` | Hierarquia de categorias e subcategorias da loja |
| Produtos do site | `/admin/catalogo` | CRUD completo: preços, imagens, anexos, esfera, destaque |
| Artes para redes sociais | `/admin/redes-sociais` | Gerar artes Instagram, Stories, Reels, TikTok |
| Produtos de parceiros | `/admin/parceiros/produtos-pendentes` | Fila de aprovação de atas enviadas por parceiros |
| Aparência e textos | `/admin/site` | Hero, contato, redes sociais, CSS customizado, SEO |
| Brand Kit | `/admin/brand-kit` | Cores, logotipos, tipografia |
| Páginas CMS | `/admin/paginas` | Criar páginas extras (`/p/<slug>`) e links no menu |

#### Produtos do catálogo — campos principais

- Título, slug, seção (categoria de exibição)
- Esfera, fabricante, categoria
- Preço unitário, quantidade na ata, estoque
- Validade da ata, garantia
- Galeria de imagens e anexos (PDFs, editais)
- URLs PNCP e página do contrato
- Descrição técnica
- Destaque na home (sim/não)
- Ativo/inativo

**Ferramentas extras:**
- Sugerir imagem via IA (OpenAI) ou Pexels
- Importar imagens por URL
- Excluir todos os produtos (ação destrutiva com confirmação)

### 7.2 Módulos — ARP e licitações

| Módulo | URL | O que faz |
|--------|-----|-----------|
| Análise prévia de ARPs | `/admin/analise-arp` | Cadastrar ARPs para análise antes de publicar |
| Licitações em andamento | `/admin/licitacoes-andamento` | Acompanhar processos que podem virar ARP |

**Ações na análise de ARP:**
- Cadastrar link e dados da ata
- Editar análise
- **Publicar no catálogo** do site
- **Criar lead** no CRM automaticamente
- Excluir registro

**Ações em licitações:**
- Cadastrar licitação em acompanhamento
- Converter em análise de ARP
- Excluir

### 7.3 Módulos — Equipe e parceiros

| Módulo | URL | O que faz |
|--------|-----|-----------|
| Representantes | `/admin/representantes` | Cadastrar vendedores, senha, flag administrador |
| Parceiros | `/admin/parceiros` | Cadastrar/editar fornecedores e credenciais |
| Exclusões solicitadas | `/admin/parceiros/exclusoes-solicitadas` | Analisar pedidos de remoção de produtos |

#### Fluxo de aprovação de produto de parceiro

1. Parceiro envia em `/parceiro/produto/novo`
2. Admin revisa em `/admin/parceiros/produtos-pendentes`
3. Abre detalhe em `/admin/parceiros/produtos/<id>`
4. **Aprovar** → produto publicado em `/arps`
5. **Recusar** → parceiro recebe observação e pode corrigir

#### Fluxo de exclusão solicitada

1. Parceiro solicita em `/parceiro/produto/<id>/solicitar-exclusao`
2. Admin analisa em `/admin/parceiros/exclusoes-solicitadas`
3. **Confirmar exclusão** ou **Recusar e manter**

### 7.4 Ferramentas experimentais (Em desenvolvimento)

| Ferramenta | URL | Função |
|------------|-----|--------|
| Robô Contratos.gov.br | `/admin/robo-contratos` | Busca automática de atas no PNCP/Contratos.gov |
| Mercado público (PNCP) | `/admin/mercado-publico` | Atualizar dados para gráficos da área do cliente |
| Importar PNCP | `/admin/importar-pncp` | Importação em lote de atas pela API nacional |
| Sincronizar órgãos PNCP | `/admin/sincronizar-orgaos-pncp` | Cache local de órgãos |
| Órgãos públicos BR | `/admin/orgaos-publicos-br` | Importar diretório nacional (IBGE, estados, federal, Sistema S, educação, segurança, etc.) |

#### Robô Contratos.gov.br — ações nos resultados

- Criar produto no catálogo
- Criar lead no CRM
- Registrar na análise de ARP
- Ações em lote (catalogar todos, criar oportunidades em lote)

---

## 8. CRM (gestão comercial e financeira)

**Entrada:** https://arpgov.com/crm/login

O CRM é o núcleo operacional da ARPGOV para gestão de relacionamento, vendas e financeiro.

### 8.1 Menu principal

| Módulo | URL | Função |
|--------|-----|--------|
| Painel | `/crm/` | KPIs e lista de leads |
| Clientes | `/crm/clientes` | Cadastro de órgãos com acesso à área cliente |
| Fornecedores | `/crm/fornecedores` | Cadastro de parceiros com acesso à área parceiro |
| Produtos | `/crm/produtos` | Catálogo (mesmo formulário do admin) |
| Leads | `/crm/leads/nova` | Oportunidades comerciais |
| Comissionamento | `/crm/comissionamento` | Tipos de comissão, faixas e sócios |
| Metas | `/crm/metas` | Metas anuais e simulações |
| Financeiro | `/crm/financeiro` | Rateios, pagamentos e documentos |

### 8.2 Painel (dashboard)

Exibe em um só lugar:
- Total de clientes cadastrados
- Total de produtos no catálogo
- Total de leads
- Comissões em aberto
- Pagamentos pendentes
- Lista de leads com filtro por estágio (até 200 registros)

### 8.3 Clientes

| Ação | URL | Descrição |
|------|-----|-----------|
| Listar | `/crm/clientes` | Busca por nome, e-mail, organização, CNPJ, setor |
| Criar | `/crm/clientes/novo` | Cadastro com senha de acesso à área `/cliente/*` |
| Editar | `/crm/clientes/<id>` | Perfil completo, alterar senha, ver leads vinculados |

### 8.4 Fornecedores

| Ação | URL | Descrição |
|------|-----|-----------|
| Listar | `/crm/fornecedores` | Busca por nome, CNPJ, segmento |
| Criar | `/crm/fornecedores/novo` | Cadastro com senha de acesso à área `/parceiro/*` |
| Editar | `/crm/fornecedores/<id>` | Perfil, ativar/desativar, ver leads vinculados |

### 8.5 Produtos

| Ação | URL | Descrição |
|------|-----|-----------|
| Listar | `/crm/produtos` | Busca por título/slug (até 500 itens) |
| Criar | `/crm/produtos/novo` | Mesmo formulário do admin |
| Editar | `/crm/produtos/<id>` | Edição completa do catálogo |

### 8.6 Leads (oportunidades)

| Ação | URL | Descrição |
|------|-----|-----------|
| Criar | `/crm/leads/nova` | Lead completo: cliente, fornecedor, produtos, comissão |
| Editar | `/crm/leads/<id>` | Todos os campos + pipeline por estágio |
| Alterar estágio | POST `/crm/leads/<id>/estagio` | Mover no funil |
| Chat | POST `/crm/leads/<id>/mensagem` | Conversa com cliente (anexos) |
| Excluir | `/crm/leads/<id>/excluir` | Confirmação digitando EXCLUIR |
| Anexos do pipeline | GET `/crm/leads/<id>/pipeline-anexo/<stage>/<idx>` | Download de documentos por estágio |

#### Campos do lead

- Título, descrição, fonte
- Cliente (órgão) e contato
- Fornecedor/parceiro vinculado
- Representante responsável
- Produtos do catálogo com quantidades e valores
- Tipo de comissão e valor calculado
- Estágio do pipeline
- Campos específicos por estágio (anexos, datas, observações)

### 8.7 Comissionamento

**Tipos de projeto de comissão:**

| Modo | Descrição |
|------|-----------|
| Sem vendedor | Rateio só entre sócios/participantes |
| Com vendedor | Inclui percentual do representante |
| Rateio personalizado | Participantes customizados somando 100% |

**Gestão de sócios:**
- Cadastrar sócios da empresa com percentual de participação
- Editar e remover sócios

**Por tipo de comissão:**
- Criar/editar/excluir tipos
- Adicionar faixas percentuais (tiers)
- Configurar rateio por faixa
- Adicionar participantes customizados

**Vendas avulsas:**
- Registrar vendas fora do funil de leads
- Rateio manual entre participantes

### 8.8 Metas

| Função | Descrição |
|--------|-----------|
| Meta anual | Definir valor alvo por ano |
| Tier de comissão | Vincular faixa de comissão à meta |
| Projeção | Ver projeção de comissão com base nos leads |
| Simulações | Adicionar cenários hipotéticos de faturamento |

### 8.9 Financeiro

**Visão consolidada (`/crm/financeiro`):**
- Leads com comissão calculada
- Rateios em aberto e quitados (por sócio/participante)
- Envios de documentos dos representantes
- Totais gerais

**Ações:**
- Atualizar status de rateio de lead (pendente → aprovado → pago)
- Atualizar status de rateio de venda avulsa
- Atualizar status de documento do representante (enviado → em análise → aprovado → pago / recusado)

---

## 9. Funil de vendas (pipeline)

Todos os leads seguem o mesmo funil, visível no Comercial, CRM e (somente leitura) na área do Cliente.

| # | Estágio | O que registrar nesta etapa |
|---|---------|------------------------------|
| 1 | **Novo** | Lead recém-criado (site, comercial ou CRM) |
| 2 | **Qualificado** | Lead validado pela equipe |
| 3 | **Em andamento** | Negociação ativa com o órgão |
| 4 | **Documentação enviada** | Anexar documentação enviada ao órgão |
| 5 | **Cobrar aceite órgão gerenciador e empresa** | Anexar ofícios de aceite |
| 6 | **Empenho recebido** | Anexar empenho + data do empenho |
| 7 | **Acompanhar faturamento** | Previsão de faturamento |
| 8 | **Acompanhar entrega** | Previsão de entrega |
| 9 | **Acompanhar recebimento** | Interações de cobrança junto ao órgão |
| 10 | **Cobrar comissionamento** | Previsão de recebimento da comissão |

**Notificações:** ao alterar estágio, o sistema envia e-mail ao cliente (se SMTP estiver configurado no servidor).

---

## 10. Fluxos integrados entre áreas

### Fluxo 1 — Visitante solicita adesão (sem conta)

```
Visitante → /arps → /produto → /contato → Lead criado (Novo)
                                              │
                                              ▼
                                    Equipe vê no Comercial/CRM
                                              │
                                              ▼
                                    Cliente não acompanha online
```

### Fluxo 2 — Cliente com conta e carrinho

```
Cliente logado → /arps → Adiciona ao carrinho → /carrinho
                                                    │
                                                    ▼
                                          /contato (pré-preenchido)
                                                    │
                                                    ▼
                                          Lead criado + carrinho limpo
                                                    │
                                                    ▼
                                    /cliente/meus-leads → chat com equipe
```

### Fluxo 3 — Parceiro publica produto

```
Parceiro → /parceiro/produto/novo → Aguardando aprovação
                                              │
                                              ▼
                          Admin → /admin/parceiros/produtos-pendentes
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                         APROVAR                          RECUSAR
                              │                               │
                              ▼                               ▼
                    Visível em /arps                  Parceiro corrige
```

### Fluxo 4 — Lead até comissão paga

```
Lead criado (site/comercial/CRM)
        │
        ▼
Pipeline avança (documentação → empenho → entrega)
        │
        ▼
Estágio: Cobrar comissionamento
        │
        ▼
CRM calcula rateio (sócios + representante)
        │
        ▼
Representante envia NF em /comercial/financeiro
        │
        ▼
CRM Financeiro confirma pagamento
```

### Fluxo 5 — Captação ativa pelo comercial

```
Representante → /comercial/orgaos-publicos (prospecção)
        │
        ▼
Registra contato do órgão
        │
        ▼
/comercial/oportunidade/nova (cria lead)
        │
        ▼
Acompanha pipeline + chat com cliente
```

---

## 11. Perfis de acesso e login

### Tabela de perfis

| Perfil | URL de login | Sessão | Acesso |
|--------|--------------|--------|--------|
| Cliente (órgão) | `/cliente/entrar` | `client_id` | Área cliente + carrinho |
| Parceiro (fornecedor) | `/parceiro/entrar` | `partner_id` | Área parceiro |
| Representante | `/comercial/entrar` | `rep_id` | Comercial (só seus leads) |
| Rep administrador | `/comercial/entrar` ou `/admin/login` | `rep_id` + `admin_ok` + `crm_ok` | Comercial + Admin + CRM |
| Operador painel | `/admin/login` | `admin_ok` + `crm_ok` | Admin + CRM |
| Operador CRM | `/crm/login` | `crm_ok` | CRM completo |

### Senhas (configuradas no `.env` do servidor)

| Variável | Uso |
|----------|-----|
| `PAINEL_ADMIN_PASSWORD` | Login do painel `/admin/login` |
| `CRM_ADMIN_PASSWORD` | Login do CRM `/crm/login` |
| `PORTAL_MASTER_PASSWORD` | Senha universal de emergência (todas as telas) |
| `FLASK_SECRET_KEY` | Segurança das sessões |

### Representante administrador

O flag **administrador** no cadastro do representante (`/admin/representantes`) concede acesso automático ao Painel Admin e ao CRM ao fazer login comercial — sem precisar de senhas separadas.

---

## 12. Roteiro para reunião de sócios

Sugestão de apresentação (~45–60 min):

### Bloco 1 — Visão geral (10 min)
- O que é o ARPGOV e quem usa
- Mapa das 6 áreas (site, cliente, parceiro, comercial, admin, CRM)
- URL de produção: **https://arpgov.com**

### Bloco 2 — Site público e cliente (10 min)
- Demonstrar `/arps` e ficha de produto
- Fluxo do carrinho e solicitação de adesão
- Área do cliente: leads, chat, mercado público

### Bloco 3 — Parceiros (10 min)
- Cadastro e envio de produto
- Fluxo de aprovação no admin
- Comissões por ARP

### Bloco 4 — Comercial (10 min)
- Dashboard de leads e pipeline
- Prospecção de órgãos públicos
- Envio de documentos de comissão

### Bloco 5 — Admin e CRM (15 min)
- Painel: catálogo, parceiros, site, robô PNCP
- CRM: leads, comissionamento, metas, financeiro
- Funil completo até pagamento de comissão

### Bloco 6 — Próximos passos (5 min)
- Ferramentas em desenvolvimento (robô, import PNCP)
- Intranet `/empresa` descontinuada (código legado no repositório)
- Backup e deploy: dados no servidor, código no GitHub

---

## Anexo A — Checklist operacional diário

### Equipe comercial
- [ ] Verificar novos leads no dashboard
- [ ] Responder mensagens de clientes no chat
- [ ] Avançar estágios do pipeline
- [ ] Prospectar órgãos em `/comercial/orgaos-publicos`

### Gestão (admin)
- [ ] Aprovar produtos pendentes de parceiros
- [ ] Revisar exclusões solicitadas
- [ ] Manter catálogo atualizado (preços, validades)
- [ ] Atualizar dados do mercado público (PNCP)

### Financeiro (CRM)
- [ ] Conferir rateios de comissão em aberto
- [ ] Aprovar/pagar documentos enviados pelos representantes
- [ ] Acompanhar metas vs. faturamento real

---

## Anexo B — Onde ficam os dados

| Dado | Local |
|------|-------|
| Banco de dados (leads, usuários, produtos) | `instance/portal.db` na VPS |
| Imagens e anexos | `static/uploads/` na VPS |
| Código-fonte | GitHub (`arpsistemaslv-cloud/ARPGOV`) |
| Senhas e chaves | Arquivo `.env` (somente no servidor) |

**Importante:** o GitHub contém apenas código. Banco e uploads devem ser copiados separadamente (script `scripts/upload-data-to-vps.ps1`).

---

## Anexo C — Área descontinuada

A intranet **`/empresa/*`** (compras, logística, jurídico, faturamento, etc.) foi **descontinuada**. As rotas redirecionam para a home com aviso. O código permanece no repositório como referência histórica, mas não está ativo em produção.

---

*Documento gerado para a reunião de sócios — ARPGOV, julho/2026.*
