"""
Textos orientativos por setor da intranet (missão, rotinas, documentos, integrações).
Editável — ajuste conforme a operação real da empresa.
"""

from __future__ import annotations

from typing import Any

# Estrutura por slug: chaves usadas no template
SetorConteudo = dict[str, Any]


CONTEUDO_POR_SETOR: dict[str, SetorConteudo] = {
    "logistica": {
        "tagline": "Fluxo físico e de informações: do pedido à entrega.",
        "missao": (
            "Garantir que produtos, documentação e atendimentos cheguem ao cliente público "
            "no prazo, com rastreabilidade e custo previsível. Coordenar transporte, "
            "estoque, agendamentos e devoluções em alinhamento com Compras e Faturamento."
        ),
        "responsabilidades": [
            "Planejar rotas, entregas e recebimentos (incluindo notas e canhoto).",
            "Manter endereços e janelas de atendimento dos órgãos atualizados.",
            "Registrar ocorrências, atrasos e divergências de volume ou qualidade.",
            "Consultar rastreamento em transportadoras integradas (ex.: Braspress em Logística → Rastreamento Braspress, com credenciais no .env).",
            "Registrar remessas na intranet (rastreio, NF, vínculo opcional a empenho) para rastreabilidade.",
            "Apoiar o setor de Garantia em coletas e trocas quando aplicável.",
        ],
        "rotinas": [
            "Conferência diária de pedidos em trânsito e status no ERP/planilha de controle.",
            "Reunião breve com Compras sobre pendências de fornecimento.",
            "Atualização semanal de SLA médio de entrega por região ou cliente.",
        ],
        "documentos": [
            "Notas fiscais e CT-e (cópias arquivadas por pedido/contrato).",
            "Comprovantes de entrega ou termos de recebimento.",
            "Planilha ou sistema de rastreio de remessas.",
        ],
        "indicadores": [
            "% entregas no prazo",
            "Tempo médio de ciclo (expedição → confirmação)",
            "Ocorrências por motivo (atraso, extravio, divergência)",
        ],
        "integra_com": ["Compras", "Faturamento", "Garantia", "Contratos e Empenhos"],
    },
    "tecnico": {
        "tagline": "Especificação, implantação e suporte técnico ao cliente.",
        "missao": (
            "Assegurar que soluções atendam requisitos técnicos e normativos, com "
            "documentação de implantação, treinamento e suporte pós-venda. É o elo entre "
            "produto, projeto e operação do órgão."
        ),
        "responsabilidades": [
            "Elaborar e revisar memoriais, propostas técnicas e respostas a itens de edital.",
            "Executar ou supervisionar instalação, configuração e testes de aceite.",
            "Registrar chamados, versões de software e base de conhecimento interna.",
            "Abrir e acompanhar chamados técnicos na intranet (prioridade, status, solução).",
            "Validar compatibilidade com infraestrutura do cliente (rede, SO, integrações).",
        ],
        "rotinas": [
            "Fila de chamados priorizada (SLA por criticidade).",
            "Revisão de documentação técnica após cada projeto concluído.",
            "Sincronização com Projetos e Licitações sobre pendências de esclarecimentos.",
        ],
        "documentos": [
            "Atas de teste / termo de aceite.",
            "Manuais e registros de treinamento.",
            "Registro de incidentes e soluções (para auditoria e melhoria).",
        ],
        "indicadores": [
            "Chamados resolvidos no prazo",
            "Retrabalho pós-implantação",
            "Satisfação técnica (quando aplicável)",
        ],
        "integra_com": ["Projetos", "Licitações", "Garantia", "Jurídico"],
    },
    "juridico": {
        "tagline": "Conformidade legal, riscos e defesa dos contratos da empresa.",
        "missao": (
            "Orientar a empresa em licitações, contratos administrativos, LGPD, "
            "trabalhista e societário. Reduzir risco de multas, litígios e descumprimento "
            "de normas do setor público."
        ),
        "responsabilidades": [
            "Revisar minutas, aditivos, notificações e correspondências oficiais.",
            "Acompanhar prazos processuais e exigências de órgãos fiscalizadores.",
            "Apoiar Licitações e Contratos em cláusulas, garantias e responsabilidades.",
            "Registrar processos e demandas na intranet (número, tribunal, próximo prazo).",
            "Manter cadastro de procurações e limites de assinatura atualizados.",
        ],
        "rotinas": [
            "Painel de processos e prazos críticos (semanal).",
            "Alinhamento com Contratos sobre alterações contratuais pendentes.",
            "Atualização de modelos de documentos conforme mudança legislativa.",
        ],
        "documentos": [
            "Contratos e aditivos assinados (versão final).",
            "Pareceres e checklists de conformidade.",
            "Procurações e atas societárias relevantes.",
        ],
        "indicadores": [
            "Processos com prazo controlado",
            "Contratos revisados antes da assinatura (100% alvo)",
            "Tempo médio de resposta a consultas internas",
        ],
        "integra_com": ["Contratos e Empenhos", "Licitações", "Faturamento", "Projetos"],
    },
    "garantia": {
        "tagline": "Trocas, reparos e satisfação pós-venda dentro da política da empresa.",
        "missao": (
            "Tratar solicitações de garantia com agilidade e registro completo, "
            "diferenciando defeito de fabricação, mau uso ou dano logístico. Preservar "
            "a relação com o órgão e a base de evidências para fornecedores."
        ),
        "responsabilidades": [
            "Abrir e numerar protocolos de garantia com prazos de resposta.",
            "Coletar fotos, laudos e NF de devolução quando necessário.",
            "Acionar fornecedores ou fabricantes conforme contrato de compra.",
            "Registrar protocolos na intranet (vínculo opcional ao catálogo de produtos de Compras).",
            "Informar Faturamento e Contratos sobre impactos em faturamento ou prazo.",
        ],
        "rotinas": [
            "Triagem diária de novos protocolos.",
            "Follow-up de casos próximos ao limite de SLA.",
            "Relatório mensal: volume, motivo e tempo médio de conclusão.",
        ],
        "documentos": [
            "Protocolo e histórico de interações.",
            "Termos de devolução ou substituição.",
            "Comprovantes de envio/recebimento (integração com Logística).",
        ],
        "indicadores": [
            "Protocolos dentro do SLA",
            "% resolvidos na primeira intervenção",
            "Custo médio por caso",
        ],
        "integra_com": ["Logística", "Compras", "Técnico", "Faturamento"],
    },
    "compras": {
        "tagline": "Aquisições com melhor custo-benefício, dentro da lei e do cronograma.",
        "missao": (
            "Garantir materiais e serviços para projetos e operação, negociando com "
            "fornecedores homologados, mantendo histórico de cotações e cumprimento de "
            "entregas. Apoiar Licitações com preços e prazos realistas."
        ),
        "responsabilidades": [
            "Emitir pedidos de compra alinhados a contratos e empenhos disponíveis.",
            "Negociar prazos, garantias e condições de pagamento.",
            "Cadastrar e avaliar fornecedores (qualidade, pontualidade, documentação).",
            "Manter o catálogo interno de produtos (part number, NCM de 8 dígitos, nome, acessórios) e vincular itens do catálogo aos empenhos processados.",
            "Resolver divergências de NF, quantidade ou especificação com o fornecedor.",
        ],
        "rotinas": [
            "Conciliação de pedidos abertos vs necessidade dos projetos.",
            "Monitoramento de entregas atrasadas e alternativas.",
            "Atualização de tabela de preços de referência (quando aplicável).",
            "Atualização do cadastro de produtos e conferência de itens lançados em empenhos.",
        ],
        "documentos": [
            "Pedidos de compra e confirmações.",
            "Cotações comparativas (rastreio de decisão).",
            "Certidões e documentos de fornecedores em dia.",
        ],
        "indicadores": [
            "% entregas no prazo pelo fornecedor",
            "Economia vs orçamento (quando houver baseline)",
            "Número de ocorrências por fornecedor",
        ],
        "integra_com": ["Contratos e Empenhos", "Logística", "Projetos", "Faturamento"],
    },
    "contratos_empenhos": {
        "tagline": "Gestão do contrato com o órgão: saldo, empenhos e obrigações.",
        "missao": (
            "Controlar vigência, valores, entregas e empenhos vinculados a cada contrato "
            "público. Centralizar o que foi comprometido, faturado e pendente para evitar "
            "descumprimento e glosas."
        ),
        "responsabilidades": [
            "Cadastrar contratos com órgãos na intranet (vigência, valores) e alinhar empenhos de Compras ao contrato quando aplicável.",
            "Registrar e acompanhar notas de empenho, liquidações e anulações.",
            "Cruzar entregas e medições com o que está previsto no contrato/ATA.",
            "Alertar sobre vencimentos, renovações e garantias contratuais.",
            "Alinhar com Jurídico em aditivos, multas e rescisões.",
        ],
        "rotinas": [
            "Conciliação semanal: saldo de empenho vs pedido de faturamento.",
            "Cronograma de entregas/marcos vs realizado.",
            "Checklist antes de cada fatura crítica.",
        ],
        "documentos": [
            "Contrato, ATA ou instrumento equivalente.",
            "Notas de empenho e alterações.",
            "Relatórios de execução ou medições assinadas.",
        ],
        "indicadores": [
            "% do contrato executado no tempo",
            "Saldo disponível vs pipeline de entregas",
            "Glosas e estornos (valor e motivo)",
        ],
        "integra_com": ["Faturamento", "Compras", "Jurídico", "Projetos", "Logística"],
    },
    "faturamento": {
        "tagline": "Emissão correta e no tempo de NF-e, serviços e faturamento vinculado ao empenho.",
        "missao": (
            "Transformar entregas e medições em documentos fiscais válidos, com "
            "alíquotas, CFOP e vínculo ao órgão correto. Reduzir retrabalho e glosas por "
            "erro de dados ou prazo."
        ),
        "responsabilidades": [
            "Emitir e transmitir notas conforme regras do cliente e do produto/serviço.",
            "Conferir dados de empenho, pedido e contrato antes da emissão.",
            "Manter registro interno de NF emitidas na intranet (referência de empenho em texto).",
            "Registrar retornos de NF (rejeições) e corrigir com agilidade.",
            "Apoiar financeiro com previsão de recebimento e comprovantes.",
        ],
        "rotinas": [
            "Fila diária de NF pendentes com prioridade por vencimento de empenho.",
            "Conciliação com Contratos sobre valores faturados no mês.",
            "Arquivo mensal de XML/PDF por cliente e contrato.",
        ],
        "documentos": [
            "XML e DANFE arquivados.",
            "Comprovantes de autorização de faturamento (quando exigido).",
            "Planilha de controle de faturamento vs meta contratual.",
        ],
        "indicadores": [
            "NF emitidas no prazo interno",
            "Taxa de rejeição/correção",
            "Valor faturado vs previsto",
        ],
        "integra_com": ["Contratos e Empenhos", "Compras", "Logística", "Pendências"],
    },
    "pendencias": {
        "tagline": "Central de tudo que trava faturamento, entrega ou conformidade.",
        "missao": (
            "Registrar, priorizar e escalar pendências que envolvem mais de um setor "
            "(documentação em falta, empenho atrasado, certidão vencida, divergência de "
            "pedido). Atuar como ‘mesa de controle’ até o item sair do gargalo."
        ),
        "responsabilidades": [
            "Manter lista única de pendências na intranet (setor origem, responsável, data alvo, status).",
            "Facilitar reuniões rápidas entre setores quando houver bloqueio cruzado.",
            "Escalar para gestão casos críticos de prazo ou valor.",
            "Registrar fechamento e motivo (para aprendizado e auditoria).",
        ],
        "rotinas": [
            "Triagem diária de novas pendências e reclassificação de prioridade.",
            "Relatório semanal: itens abertos > X dias.",
            "Integração com Kanban: itens críticos podem virar cartões de acompanhamento.",
        ],
        "documentos": [
            "Registro da pendência (origem, impacto, dono).",
            "Evidência de conclusão (print, NF, e-mail oficial).",
        ],
        "indicadores": [
            "Tempo médio de resolução",
            "Pendências reabertas",
            "% resolvidas sem escalação",
        ],
        "integra_com": ["Todos os setores", "Kanban", "Chat geral"],
    },
    "projetos": {
        "tagline": "Do kick-off ao encerramento: escopo, prazo, custo e satisfação do órgão.",
        "missao": (
            "Planejar e executar entregas por marco, com comunicação clara ao cliente e "
            "às áreas internas. Garantir rastreabilidade de mudanças de escopo e decisões."
        ),
        "responsabilidades": [
            "Manter cronograma, dependências e riscos atualizados.",
            "Coordenar Técnico, Compras e Logística para cada marco.",
            "Registrar projetos e marcos na intranet (código único, datas, status).",
            "Registrar atas de reunião e aprovações de alteração de escopo.",
            "Encerrar projeto com lições aprendidas e documentação entregue.",
        ],
        "rotinas": [
            "Status semanal por projeto (semáforo: verde/amarelo/vermelho).",
            "Revisão de escopo vs contrato antes de novas compras.",
            "Handover para operação/suporte ao final.",
        ],
        "documentos": [
            "Plano de projeto e cronograma.",
            "Atas e aprovações formais.",
            "Lista de entregáveis e aceites.",
        ],
        "indicadores": [
            "Projetos no prazo vs atrasados",
            "Desvio de escopo (número de mudanças formais)",
            "Satisfação do cliente (quando medida)",
        ],
        "integra_com": ["Contratos e Empenhos", "Técnico", "Compras", "Licitações"],
    },
    "licitacoes": {
        "tagline": "Oportunidades públicas: participação estratégica e documentação impecável.",
        "missao": (
            "Identificar editais alinhados à empresa, analisar prazos (proposta, esclarecimento, impugnação, entrega do objeto), "
            "multas, local de entrega e exigências documentais. Registrar esclarecimentos e impugnações por edital, "
            "manter checklist de habilitação e usar o repositório central para certidões e modelos atualizados."
        ),
        "responsabilidades": [
            "Pipeline de licitações (estudo, go/no-go, preparação, envio).",
            "Preencher análise do edital na intranet: documentação solicitada, checklist, prazos críticos.",
            "Registrar pedidos de esclarecimento, respostas do órgão e trâmite de impugnação/questionamento.",
            "Coordenar Técnico e Jurídico em itens críticos do edital.",
            "Registrar oportunidades na intranet (órgão, edital, datas, status, valor proposta).",
            "Manter arquivos corporativos no repositório de documentos; anexar ao edital o que for específico da licitação.",
        ],
        "rotinas": [
            "Varredura diária de portais (PNCP, compras estaduais/municipais, etc.).",
            "Reunião de alinhamento antes do deadline de cada proposta relevante.",
            "Pós-licitação: registro de ganhos/perdas e motivos.",
        ],
        "documentos": [
            "Edital e anexos arquivados por processo.",
            "Proposta enviada (cópia com carimbo de data/hora quando houver).",
            "Certidões e atestados válidos na data da proposta.",
        ],
        "indicadores": [
            "Taxa de sucesso (ganhos / participações)",
            "Prazo interno cumprido (100% alvo)",
            "Motivos de desclassificação (para evitar repetição)",
        ],
        "integra_com": ["Jurídico", "Técnico", "Compras", "Projetos", "Contratos e Empenhos", "Fechamento de preço"],
    },
    "fechamento_preco": {
        "tagline": "Formação de preço de venda com rastreio fiscal e alinhamento a licitação e custos.",
        "missao": (
            "Montar planilhas com UF de entrega, custos de produtos cadastrados, estimativa de impostos "
            "priorizando a matriz produto × UF (setor Impostos) e, quando necessário, o perfil por NCM. "
            "Incluir benefícios fiscais, frete, custo financeiro e mark-up. Fechar a planilha para registrar "
            "snapshot imutável. Articular com Licitações e Compras (aprovação em propostas sensíveis)."
        ),
        "responsabilidades": [
            "Garantir UF de entrega e, quando possível, cadastro na matriz Impostos para cada produto relevante.",
            "Manter NCM nos produtos e perfis na tabela NCM como fallback quando não houver matriz por UF.",
            "Registrar planilhas vinculadas à licitação e fechar com snapshot após validação interna.",
            "Sinalizar aprovação no prejuízo e obter ok de Compras quando a política exigir.",
        ],
        "rotinas": [
            "Conferir matriz Impostos e NCM antes de enviar proposta.",
            "Conferência de frete, financeiro e markup com a diretoria comercial.",
        ],
        "documentos": [
            "Planilha fechada com snapshot na intranet.",
            "Parecer fiscal sobre benefícios quando o edital exigir.",
        ],
        "indicadores": [
            "Planilhas com fonte de imposto definida (matriz UF ou NCM)",
            "Tempo médio de fechamento por edital",
        ],
        "integra_com": ["Setor de Impostos", "Licitações a participar", "Compras", "Faturamento", "Contratos e Empenhos"],
    },
    "imposto": {
        "tagline": "Cadastro mestre de alíquotas e benefícios por produto e por estado.",
        "missao": (
            "Manter a matriz produto × UF com ICMS, IPI, PIS e COFINS de referência e texto de benefício fiscal "
            "por UF, alinhada à operação e ao jurídico. O fechamento de preço consome esta base com prioridade "
            "sobre o perfil por NCM."
        ),
        "responsabilidades": [
            "Cadastrar e revisar combinações produto (catálogo Compras) e UF usadas nas entregas públicas.",
            "Documentar benefícios estaduais ou regimes especiais em cada registro ou em anexo.",
            "Alinhar com Compras quando novos itens entrarem no portfólio ou mudarem classificação fiscal.",
        ],
        "rotinas": [
            "Revisão após mudança legislativa ou acordo interestadual relevante.",
            "Checagem de consistência com a tabela NCM (fallback) para os mesmos produtos.",
        ],
        "documentos": [
            "Matriz exportada ou relatório interno por UF.",
            "Ofício, DECRETO ou parecer que fundamenta benefício (quando aplicável).",
        ],
        "indicadores": [
            "Cobertura da matriz para UFs com volume de venda",
            "Divergências corrigidas após auditoria interna",
        ],
        "integra_com": ["Fechamento de preço", "Compras", "Faturamento", "Jurídico"],
    },
}


def get_conteudo_setor(slug: str) -> SetorConteudo | None:
    return CONTEUDO_POR_SETOR.get(slug)
