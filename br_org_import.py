"""
Importações do diretório de órgãos públicos (IBGE, seeds, cópia PNCP).
"""

from __future__ import annotations

import time
from typing import Any, Type

import requests

from brasil_geo import POPULACAO_UF, regiao_de_uf
from br_educacao_catalogo import (
    INSTITUTOS_FEDERAIS,
    ORGANISMOS_EDUCACAO_FEDERAL,
    UNIVERSIDADES_FEDERAIS,
)
from br_orgaos_catalogo import (
    AUTARQUIAS_FEDERAIS_UNICAS,
    FEDERAL_EXECUTIVO_ITENS,
    SEGURANCA_FEDERAL_EXTRAS,
    TRF_SEDES,
    TRT_REGIOES,
)

_UF_DF = "DF"

IBGE_MUNICIPIOS_URL = (
    "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
)
REQUEST_TIMEOUT = 300


def import_ibge_municipios(db: Any, BrOrgaoPublico: Type[Any]) -> tuple[int, int, str | None]:
    """
    Baixa todos os municípios brasileiros. Retorna (inseridos, já_existentes, erro).
    """
    try:
        r = requests.get(IBGE_MUNICIPIOS_URL, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return 0, 0, str(exc)

    ins = skip = 0
    batch = 0
    for m in data:
        mid = str(m.get("municipio-id") or "").strip()
        nome_mun = (m.get("municipio-nome") or "").strip()
        uf_sigla = (m.get("UF-sigla") or "").strip().upper()
        if not mid or not nome_mun or len(uf_sigla) != 2:
            continue
        chave = f"ibge:{mid}"
        if BrOrgaoPublico.query.filter_by(chave_externa=chave).first():
            skip += 1
            continue
        regiao = (m.get("regiao-nome") or "").strip() or regiao_de_uf(uf_sigla)
        db.session.add(
            BrOrgaoPublico(
                tipo="prefeitura",
                nome=f"Prefeitura Municipal de {nome_mun}",
                nome_unidade=f"Município de {nome_mun}",
                uf=uf_sigla,
                regiao=regiao,
                municipio_nome=nome_mun,
                ibge_municipio_id=mid,
                chave_externa=chave,
                fonte="ibge_municipio",
            )
        )
        ins += 1
        batch += 1
        if batch >= 400:
            db.session.commit()
            batch = 0
            time.sleep(0.05)
    if batch:
        db.session.commit()
    return ins, skip, None


def seed_orgaos_estaduais(db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple) -> tuple[int, int]:
    """Uma linha por UF — administração estadual + população da UF."""
    ins = skip = 0
    for sigla, nome_estado in br_ufs:
        sigla = sigla.upper()
        key = f"estado:{sigla}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        pop = POPULACAO_UF.get(sigla)
        db.session.add(
            BrOrgaoPublico(
                tipo="orgao_estadual",
                nome=f"Administração pública estadual — {nome_estado}",
                nome_unidade=f"Poder executivo estadual ({sigla})",
                uf=sigla,
                regiao=regiao_de_uf(sigla),
                populacao_local=pop,
                chave_externa=key,
                fonte="estado_seed",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip


def seed_sistema_s(db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple) -> tuple[int, int]:
    marcas = (
        ("SESI", "Serviço Social da Indústria"),
        ("SENAI", "Serviço Nacional de Aprendizagem Industrial"),
        ("SESC", "Serviço Social do Comércio"),
        ("SENAC", "Serviço Nacional de Aprendizagem Comercial"),
    )
    ins = skip = 0
    for sigla_uf, nome_estado in br_ufs:
        u = sigla_uf.upper()
        for sig_serv, desc in marcas:
            key = f"sistema_s:{sig_serv}:{u}"
            if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
                skip += 1
                continue
            db.session.add(
                BrOrgaoPublico(
                    tipo="sistema_s",
                    nome=f"{sig_serv} — {nome_estado} ({u})",
                    nome_unidade=desc,
                    uf=u,
                    regiao=regiao_de_uf(u),
                    chave_externa=key,
                    fonte="sistema_s_seed",
                )
            )
            ins += 1
    db.session.commit()
    return ins, skip


def copiar_pncp_para_br_orgaos(
    db: Any, BrOrgaoPublico: Type[Any], PncpOrgaoUnidade: Type[Any]
) -> tuple[int, int]:
    ins = skip = 0
    for row in PncpOrgaoUnidade.query.all():
        key = f"pncp:{row.cnpj}:{row.codigo_unidade}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        ibge = None
        if row.codigo_municipio_ibge:
            s = str(row.codigo_municipio_ibge).strip()
            if len(s) == 7 and s.isdigit():
                if not BrOrgaoPublico.query.filter_by(ibge_municipio_id=s).first():
                    ibge = s
        reg = regiao_de_uf(row.uf_sigla) if row.uf_sigla else None
        db.session.add(
            BrOrgaoPublico(
                tipo="pncp",
                nome=row.razao_social or "Órgão PNCP",
                nome_unidade=row.nome_unidade,
                uf=row.uf_sigla,
                regiao=reg,
                municipio_nome=row.municipio_nome,
                ibge_municipio_id=ibge,
                chave_externa=key,
                cnpj=row.cnpj,
                email_contato=row.email_licitacoes,
                telefone_contato=row.telefone_licitacoes,
                contato_obs=row.contato_licitacoes_obs,
                fonte="pncp_copia",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip


def seed_servico_aprendizagem_complementar(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """SENAR, SEBRAE, SENAT, SESCOOP — uma entrada por UF (como o Sistema S)."""
    marcas = (
        ("SENAR", "Serviço Nacional de Aprendizagem Rural"),
        ("SEBRAE", "Serviço Brasileiro de Apoio às Micro e Pequenas Empresas"),
        ("SENAT", "Serviço Nacional de Aprendizagem do Transporte"),
        ("SESCOOP", "Serviço Nacional de Aprendizagem do Cooperativismo"),
    )
    ins = skip = 0
    for sigla_uf, nome_estado in br_ufs:
        u = sigla_uf.upper()
        for sig_serv, desc in marcas:
            key = f"svc_apr:{sig_serv}:{u}"
            if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
                skip += 1
                continue
            db.session.add(
                BrOrgaoPublico(
                    tipo="servico_aprendizagem",
                    nome=f"{sig_serv} — {nome_estado} ({u})",
                    nome_unidade=desc,
                    uf=u,
                    regiao=regiao_de_uf(u),
                    chave_externa=key,
                    fonte="servico_aprendizagem_seed",
                )
            )
            ins += 1
    db.session.commit()
    return ins, skip


def seed_federal_executivo(db: Any, BrOrgaoPublico: Type[Any]) -> tuple[int, int]:
    """Presidência, ministérios e órgãos centrais da União (sede em Brasília/DF)."""
    ins = skip = 0
    reg = regiao_de_uf(_UF_DF)
    for slug, nome, un in FEDERAL_EXECUTIVO_ITENS:
        key = f"federal_exec:{slug}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="federal_executivo",
                nome=nome,
                nome_unidade=un,
                uf=_UF_DF,
                regiao=reg,
                municipio_nome="Brasília",
                chave_externa=key,
                fonte="catalogo_federal_executivo",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip


def seed_autarquias_federais_catalogo(db: Any, BrOrgaoPublico: Type[Any]) -> tuple[int, int]:
    """Autarquias, agências e institutos federais (sede federal em DF)."""
    ins = skip = 0
    reg = regiao_de_uf(_UF_DF)
    for slug, nome, un in AUTARQUIAS_FEDERAIS_UNICAS:
        key = f"aut_federal:{slug}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="autarquia_federal",
                nome=nome,
                nome_unidade=un,
                uf=_UF_DF,
                regiao=reg,
                municipio_nome="Brasília",
                chave_externa=key,
                fonte="catalogo_autarquia_federal",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip


def seed_orgaos_juridicos_catalogo(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """STF, STJ, TST, TSE, STM, CNJ, CNMP, TCU, DPU, MPF, TRFs, TJs e MPE por UF."""
    ins = skip = 0

    def try_add(
        nome: str,
        nome_unidade: str | None,
        uf: str | None,
        chave_externa: str,
        fonte: str,
    ) -> None:
        nonlocal ins, skip
        if BrOrgaoPublico.query.filter_by(chave_externa=chave_externa).first():
            skip += 1
            return
        db.session.add(
            BrOrgaoPublico(
                tipo="orgao_juridico",
                nome=nome,
                nome_unidade=nome_unidade,
                uf=uf,
                regiao=regiao_de_uf(uf) if uf else None,
                chave_externa=chave_externa,
                fonte=fonte,
            )
        )
        ins += 1

    superiores = (
        ("jud:STF", "Supremo Tribunal Federal", "STF"),
        ("jud:STJ", "Superior Tribunal de Justiça", "STJ"),
        ("jud:TST", "Tribunal Superior do Trabalho", "TST"),
        ("jud:TSE", "Tribunal Superior Eleitoral", "TSE"),
        ("jud:STM", "Superior Tribunal Militar", "STM"),
        ("jud:CNJ", "Conselho Nacional de Justiça", "CNJ"),
        ("jud:CNMP", "Conselho Nacional do Ministério Público", "CNMP"),
        ("jud:TCU", "Tribunal de Contas da União", "TCU"),
        ("jud:DPU", "Defensoria Pública da União", "DPU"),
        (
            "jud:MPF",
            "Ministério Público Federal (Procuradoria-Geral da República)",
            "MPF / PGR",
        ),
    )
    for key, nome, un in superiores:
        try_add(nome, un, _UF_DF, key, "catalogo_judiciario")

    for _sig, nome_trf, uf_trf in TRF_SEDES:
        try_add(
            nome_trf,
            f"Sede principal ({uf_trf})",
            uf_trf,
            f"jud:{_sig}",
            "catalogo_trf",
        )

    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        if u == "DF":
            nome_tj = "Tribunal de Justiça do Distrito Federal e dos Territórios"
            key_tj = "jud:TJDFT"
        else:
            nome_tj = f"Tribunal de Justiça do Estado de {nome_est}"
            key_tj = f"jud:TJ:{u}"
        try_add(
            nome_tj,
            "Poder Judiciário estadual",
            u,
            key_tj,
            "catalogo_tj",
        )
        try_add(
            f"Ministério Público do Estado de {nome_est}",
            "Ministério Público estadual",
            u,
            f"jud:MPE:{u}",
            "catalogo_mpe",
        )

    db.session.commit()
    return ins, skip


def seed_justica_trabalho_mpt_catalogo(
    db: Any, BrOrgaoPublico: Type[Any]
) -> tuple[int, int]:
    """TST já está no catálogo jurídico; aqui: TRT (24 regiões), PGT e PRT (MPT regional)."""
    ins = skip = 0
    key_pgt = "mpt:PGT"
    if not BrOrgaoPublico.query.filter_by(chave_externa=key_pgt).first():
        db.session.add(
            BrOrgaoPublico(
                tipo="justica_trabalho",
                nome="Procuradoria-Geral do Trabalho (Ministério Público do Trabalho)",
                nome_unidade="PGT — sede nacional",
                uf=_UF_DF,
                regiao=regiao_de_uf(_UF_DF),
                municipio_nome="Brasília",
                chave_externa=key_pgt,
                fonte="catalogo_mpt_pgt",
            )
        )
        ins += 1
    else:
        skip += 1

    for n, uf, cidade in TRT_REGIOES:
        ord_ = f"{n}ª"
        nome_trt = f"Tribunal Regional do Trabalho da {ord_} Região"
        key_trt = f"trt:R{n:02d}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key_trt).first():
            skip += 1
        else:
            db.session.add(
                BrOrgaoPublico(
                    tipo="justica_trabalho",
                    nome=nome_trt,
                    nome_unidade=f"Sede — {cidade}/{uf}",
                    uf=uf,
                    regiao=regiao_de_uf(uf),
                    municipio_nome=cidade,
                    chave_externa=key_trt,
                    fonte="catalogo_trt",
                )
            )
            ins += 1

        nome_prt = f"Procuradoria Regional do Trabalho da {ord_} Região"
        key_prt = f"mpt:PRT:R{n:02d}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key_prt).first():
            skip += 1
        else:
            db.session.add(
                BrOrgaoPublico(
                    tipo="justica_trabalho",
                    nome=nome_prt,
                    nome_unidade=f"MPT — {cidade}/{uf}",
                    uf=uf,
                    regiao=regiao_de_uf(uf),
                    municipio_nome=cidade,
                    chave_externa=key_prt,
                    fonte="catalogo_mpt_prt",
                )
            )
            ins += 1

    db.session.commit()
    return ins, skip


def seed_orgaos_legislativos_catalogo(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """Congresso Nacional (Câmara e Senado) e assembleias estaduais / CLDF."""
    ins = skip = 0

    federais = (
        ("leg:cdep", "Câmara dos Deputados", "Poder Legislativo federal — câmara baixa"),
        ("leg:senado", "Senado Federal", "Poder Legislativo federal — câmara alta"),
    )
    reg_df = regiao_de_uf(_UF_DF)
    for key, nome, un in federais:
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="orgao_legislativo",
                nome=nome,
                nome_unidade=un,
                uf=_UF_DF,
                regiao=reg_df,
                municipio_nome="Brasília",
                chave_externa=key,
                fonte="catalogo_legislativo_federal",
            )
        )
        ins += 1

    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        if u == "DF":
            nome = "Câmara Legislativa do Distrito Federal"
            key = "leg:cldf"
            un = "Poder Legislativo distrital"
        else:
            nome = f"Assembleia Legislativa do Estado de {nome_est}"
            key = f"leg:ale:{u}"
            un = "Poder Legislativo estadual"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="orgao_legislativo",
                nome=nome,
                nome_unidade=un,
                uf=u,
                regiao=regiao_de_uf(u),
                chave_externa=key,
                fonte="catalogo_legislativo_estadual",
            )
        )
        ins += 1

    db.session.commit()
    return ins, skip


def seed_educacao_instituicoes_catalogo(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """
    Órgãos do MEC (INEP, FNDE, CONIF, etc.), universidades federais, institutos
    federais e linha-guia por UF para ensino superior estadual e faculdades públicas.
    """
    ins = skip = 0
    reg_df = regiao_de_uf(_UF_DF)

    for slug, nome, un_short in ORGANISMOS_EDUCACAO_FEDERAL:
        key = f"edu:org:{slug}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="educacao_instituicoes",
                nome=nome,
                nome_unidade=un_short,
                uf=_UF_DF,
                regiao=reg_df,
                municipio_nome="Brasília",
                chave_externa=key,
                fonte="catalogo_educacao_org_mec",
            )
        )
        ins += 1

    for sigla, nome, uf in UNIVERSIDADES_FEDERAIS:
        key = f"edu:uf:{sigla}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="educacao_instituicoes",
                nome=nome,
                nome_unidade=f"Universidade federal — {sigla}",
                uf=uf,
                regiao=regiao_de_uf(uf),
                chave_externa=key,
                fonte="catalogo_universidade_federal",
            )
        )
        ins += 1

    for slug, nome, uf in INSTITUTOS_FEDERAIS:
        key = f"edu:if:{slug}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="educacao_instituicoes",
                nome=nome,
                nome_unidade="Instituto Federal — educação profissional e tecnológica",
                uf=uf,
                regiao=regiao_de_uf(uf),
                chave_externa=key,
                fonte="catalogo_instituto_federal",
            )
        )
        ins += 1

    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        key = f"edu:estadual:{u}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="educacao_instituicoes",
                nome=(
                    "Universidades estaduais, faculdades e centros universitários "
                    f"públicos — {nome_est}"
                ),
                nome_unidade="Prospectar rede estadual e instituições mantidas pelo estado",
                uf=u,
                regiao=regiao_de_uf(u),
                chave_externa=key,
                fonte="catalogo_educacao_estadual_guia",
            )
        )
        ins += 1

    db.session.commit()
    return ins, skip


def seed_seguranca_publica_catalogo(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """
    Polícia Militar, Polícia Civil e Corpo de Bombeiros Militar por UF;
    órgãos federais complementares (PPEN, Força Nacional, SENASP, DEPEN).
    Polícia Federal e PRF permanecem no seed de autarquias federais.
    """
    ins = skip = 0
    reg_df = regiao_de_uf(_UF_DF)

    for slug, nome, un in SEGURANCA_FEDERAL_EXTRAS:
        key = f"seg:fed:{slug}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="seguranca_publica",
                nome=nome,
                nome_unidade=un,
                uf=_UF_DF,
                regiao=reg_df,
                municipio_nome="Brasília",
                chave_externa=key,
                fonte="catalogo_seguranca_federal",
            )
        )
        ins += 1

    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        if u == "DF":
            pm_nome = "Polícia Militar do Distrito Federal"
            pc_nome = "Polícia Civil do Distrito Federal"
            cbm_nome = "Corpo de Bombeiros Militar do Distrito Federal"
            pm_u = "Polícia ostensiva e preservação da ordem (PMDF)"
            pc_u = "Polícia judiciária e investigação (PCDF)"
            cbm_u = "Defesa civil, salvamento e combate a incêndios (CBMDF)"
        else:
            pm_nome = f"Polícia Militar do Estado de {nome_est}"
            pc_nome = f"Polícia Civil do Estado de {nome_est}"
            cbm_nome = f"Corpo de Bombeiros Militar do Estado de {nome_est}"
            pm_u = "Polícia ostensiva e preservação da ordem pública"
            pc_u = "Polícia judiciária e investigação criminal"
            cbm_u = "Defesa civil, salvamento e combate a incêndios"

        trio = (
            (f"seg:PM:{u}", pm_nome, pm_u),
            (f"seg:PC:{u}", pc_nome, pc_u),
            (f"seg:CBM:{u}", cbm_nome, cbm_u),
        )
        for key, nome_org, nome_un in trio:
            if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
                skip += 1
                continue
            db.session.add(
                BrOrgaoPublico(
                    tipo="seguranca_publica",
                    nome=nome_org,
                    nome_unidade=nome_un,
                    uf=u,
                    regiao=regiao_de_uf(u),
                    chave_externa=key,
                    fonte="catalogo_pm_pc_cbm",
                )
            )
            ins += 1

    db.session.commit()
    return ins, skip


def seed_detran_estaduais(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """DETRAN em cada UF."""
    ins = skip = 0
    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        key = f"detran:{u}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="autarquia_estadual",
                nome=f"DETRAN {u} — Departamento Estadual de Trânsito",
                nome_unidade=f"Órgão estadual de trânsito — {nome_est}",
                uf=u,
                regiao=regiao_de_uf(u),
                chave_externa=key,
                fonte="catalogo_detran",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip


def seed_demais_autarquias_estaduais(
    db: Any, BrOrgaoPublico: Type[Any], br_ufs: tuple
) -> tuple[int, int]:
    """Linha-guia por UF para demais autarquias, fundações e agências estaduais."""
    ins = skip = 0
    for sigla, nome_est in br_ufs:
        u = sigla.upper()
        key = f"aut_est_outros:{u}"
        if BrOrgaoPublico.query.filter_by(chave_externa=key).first():
            skip += 1
            continue
        db.session.add(
            BrOrgaoPublico(
                tipo="autarquia_estadual",
                nome=(
                    f"Demais autarquias, fundações e agências estaduais — {nome_est}"
                ),
                nome_unidade="Prospectar órgãos além do DETRAN e secretarias",
                uf=u,
                regiao=regiao_de_uf(u),
                chave_externa=key,
                fonte="catalogo_aut_estadual_geral",
            )
        )
        ins += 1
    db.session.commit()
    return ins, skip
