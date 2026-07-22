"""Nota de execução determinística + harmonização de consistência da análise.

Reintroduz o ``scoring.py`` do módulo original (snapshot a368d14) adaptado ao
núcleo calibrado: em vez dos critérios por-exercício dos perfis, o modelo de
pesos cobre as 7 categorias fixas de RF-002 do checklist. A divisão de trabalho
é a mesma de lá (e do ``app/tennis/weights.py``): **o VLM só dá notas 0..10 e
status por categoria; toda a aritmética é Python** — normalização, pesos
renormalizados sobre o observável, gates e tetos.

Duas funções públicas, chamadas pelo service logo após a chamada 1:

* :func:`harmonize_analysis` — impõe em CÓDIGO as regras de calibragem que até
  aqui só existiam no prompt (RF-003: erro com risco de lesão força veredito
  "inadequada" + ``risco_lesao=True``) e a consistência checklist↔erros
  (categoria com erro → ``a_corrigir``; as 7 categorias sempre presentes).
* :func:`compute_nota_execucao` — a nota 0..100 com breakdown por categoria.

A nota é um indicador observacional de POC — os pesos NÃO foram calibrados
contra ground truth (mesma ressalva do modelo original e dos pesos do tênis).
"""

from __future__ import annotations

from typing import get_args

from .models import (
    AcademiaAnalysis,
    CategoriaChecklist,
    ComponenteNota,
    CriterioChecklist,
    NotaExecucao,
)

# Ordem canônica das 7 categorias (a mesma da varredura do prompt).
CATEGORIAS: tuple[str, ...] = get_args(CategoriaChecklist)

# Modelo de pesos POC (soma 1.00). Joelhos e amplitude pesam mais porque são as
# categorias dos erros mais graves do dataset de calibragem (637 = joelhos com
# risco de lesão; 563/619/633 = amplitude). Recalibrar = editar este dict.
WEIGHT_MODEL_NAME = "academia_categorias_poc_v1"
PESOS: dict[str, tuple[str, float]] = {
    "amplitude":       ("Amplitude do movimento", 0.20),
    "escapula_ombros": ("Escápula e ombros",      0.15),
    "tronco":          ("Tronco e base",          0.15),
    "cervical":        ("Cervical",               0.075),
    "cotovelos":       ("Cotovelos",              0.075),
    "joelhos":         ("Joelhos e pés",          0.20),
    "ritmo":           ("Ritmo (excêntrica)",     0.15),
}

# Nota 0..10 ausente → fallback pelo status (mesmos valores do módulo original).
_FALLBACK_STATUS = {"adequado": 0.85, "ajuste_leve": 0.65, "a_corrigir": 0.40}

# Teto da nota 0..10 exibida no checklist quando a categoria tem erro — mantém a
# rubrica coerente (0-3 grave · 4-6 moderado · 7-8 leve/refinamento · 9-10 limpo).
_TETO_NOTA_POR_GRAVIDADE = {"leve": 8.0, "moderada": 6.0, "risco_lesao": 3.0}

# Teto do valor normalizado 0..1 no CÁLCULO da nota agregada, por gravidade do
# erro na categoria. risco_lesao zera: um padrão perigoso não soma pontos.
_TETO_NORMALIZADO_POR_GRAVIDADE = {"leve": 0.8, "moderada": 0.6, "risco_lesao": 0.0}

# Gates e tetos de coerência da nota 0..100.
MIN_CRITERIOS_AVALIADOS = 3
_TETO_RISCO_LESAO = 39.0
_TETO_VEREDITO = {"inadequada": 49.0, "parcialmente_adequada": 79.0}

_AVISO_POC = (
    "indicador observacional de POC (pesos não calibrados) — não mede risco de "
    "lesão, carga, esforço nem ativação muscular"
)

_ORDEM_GRAVIDADE = {"leve": 0, "moderada": 1, "risco_lesao": 2}


def _pior_gravidade_por_categoria(analysis: AcademiaAnalysis) -> dict[str, str]:
    """Mapa categoria → gravidade mais severa entre os erros daquela categoria."""
    pior: dict[str, str] = {}
    for erro in analysis.erros:
        atual = pior.get(erro.categoria)
        if atual is None or _ORDEM_GRAVIDADE[erro.gravidade] > _ORDEM_GRAVIDADE[atual]:
            pior[erro.categoria] = erro.gravidade
    return pior


# --------------------------------------------------------------------------- #
# harmonização determinística (regras de calibragem viram código)              #
# --------------------------------------------------------------------------- #
def harmonize_analysis(analysis: AcademiaAnalysis) -> tuple[AcademiaAnalysis, list[str]]:
    """Reconcilia a análise do VLM com as regras de calibragem, em código.

    Nunca inventa conteúdo — só corrige inconsistências internas do que o modelo
    devolveu, e registra cada ajuste num aviso PT-BR (vira ``warnings`` da
    resposta, visível na UI). Retorna uma cópia ajustada + a lista de avisos.
    """
    fixed = analysis.model_copy(deep=True)
    avisos: list[str] = []

    # RF-003 em código: erro com risco de lesão força inadequada + risco_lesao.
    tem_risco = any(e.gravidade == "risco_lesao" for e in fixed.erros)
    if tem_risco and fixed.veredito != "inadequada":
        fixed.veredito = "inadequada"
        avisos.append(
            "consistência: veredito ajustado para 'inadequada' — há erro com risco de lesão (RF-003)."
        )
    if tem_risco and not fixed.risco_lesao:
        fixed.risco_lesao = True
        avisos.append("consistência: risco_lesao ajustado para true — há erro com essa gravidade (RF-003).")
    if fixed.risco_lesao and not tem_risco:
        # Direção inversa: flag de risco SEM erro dessa gravidade. Conservador —
        # não apagamos um sinal de segurança do modelo (o histórico do módulo é de
        # SUBnotificação de risco); o veredito acompanha a flag e o aviso expõe a
        # lacuna, mantendo veredito × flag × teto da nota (39 ≤ 49) coerentes.
        if fixed.veredito != "inadequada":
            fixed.veredito = "inadequada"
            avisos.append(
                "consistência: veredito ajustado para 'inadequada' — o modelo sinalizou risco de lesão (RF-003)."
            )
        avisos.append(
            "consistência: risco_lesao=true sem erro de gravidade 'risco_lesao' na lista de erros."
        )

    # 2+ erros moderados nunca são "adequada" (regra dura do prompt, agora em código).
    moderados = sum(1 for e in fixed.erros if e.gravidade == "moderada")
    if moderados >= 2 and fixed.veredito == "adequada":
        fixed.veredito = "parcialmente_adequada"
        avisos.append(
            "consistência: veredito ajustado para 'parcialmente_adequada' — múltiplos erros moderados."
        )

    # Checklist: exatamente uma entrada por categoria, todas as 7, na ordem canônica.
    por_categoria: dict[str, CriterioChecklist] = {}
    for item in fixed.checklist:
        if item.categoria not in por_categoria:  # duplicata: a primeira vence
            por_categoria[item.categoria] = item
    faltantes = [c for c in CATEGORIAS if c not in por_categoria]
    for cat in faltantes:
        por_categoria[cat] = CriterioChecklist(
            categoria=cat, status="nao_observavel", nota_0a10=None,
            observacao="categoria não avaliada pelo modelo nesta análise",
        )
    if faltantes and analysis.checklist:
        # checklist parcial é inconsistência do modelo; ausência total é o caminho
        # legado (análises antigas) e não merece aviso.
        avisos.append(
            "consistência: checklist completado com 'nao_observavel' para: " + ", ".join(faltantes) + "."
        )

    # Consistência checklist ↔ erros: categoria com erro → a_corrigir + teto de nota.
    pior = _pior_gravidade_por_categoria(fixed)
    for cat, gravidade in pior.items():
        item = por_categoria.get(cat)
        if item is None:  # "outro" não tem linha de checklist
            continue
        if item.status != "a_corrigir":
            item.status = "a_corrigir"
            avisos.append(
                f"consistência: checklist de '{cat}' ajustado para 'a_corrigir' — há erro registrado nessa categoria."
            )
        teto = _TETO_NOTA_POR_GRAVIDADE[gravidade]
        if item.nota_0a10 is not None and item.nota_0a10 > teto:
            item.nota_0a10 = teto
            avisos.append(
                f"consistência: nota de '{cat}' limitada a {teto:.0f}/10 pela gravidade do erro ({gravidade})."
            )

    # Direção inversa: 'a_corrigir' SEM erro correspondente em `erros`. Harmonizar
    # sem inventar conteúdo (não fabricamos um ErroTecnico): rebaixa para
    # 'ajuste_leve' (RF-004: erro não registrado não é erro) e limita a nota à
    # banda leve/refinamento, restaurando a invariante a_corrigir ⟺ erro na categoria.
    for cat, item in por_categoria.items():
        if item.status == "a_corrigir" and cat not in pior:
            item.status = "ajuste_leve"
            if item.nota_0a10 is not None and item.nota_0a10 > 8.0:
                item.nota_0a10 = 8.0
            avisos.append(
                f"consistência: checklist de '{cat}' rebaixado para 'ajuste_leve' — "
                "'a_corrigir' sem erro correspondente em 'erros' (RF-004)."
            )

    fixed.checklist = [por_categoria[c] for c in CATEGORIAS]
    return fixed, avisos


# --------------------------------------------------------------------------- #
# nota 0..100 determinística                                                   #
# --------------------------------------------------------------------------- #
def _normalizado(item: CriterioChecklist, gravidade_erro: str | None) -> float | None:
    """Valor 0..1 da categoria para o cálculo, ou None se não observável."""
    if item.status == "nao_observavel":
        return None
    if item.nota_0a10 is not None:
        valor = max(0.0, min(1.0, item.nota_0a10 / 10.0))
    else:
        valor = _FALLBACK_STATUS[item.status]
    if gravidade_erro is not None:
        valor = min(valor, _TETO_NORMALIZADO_POR_GRAVIDADE[gravidade_erro])
    return valor


def compute_nota_execucao(analysis: AcademiaAnalysis) -> NotaExecucao:
    """Nota 0..100 com breakdown por categoria (chamar APÓS ``harmonize_analysis``).

    Regras (mesmo desenho do módulo original + tetos de coerência novos):

    * ``nao_observavel`` nunca vira zero — sai do cálculo e os pesos restantes
      são renormalizados (as contribuições sempre somam a nota);
    * gates: qualidade de vídeo "ruim" ou menos de ``MIN_CRITERIOS_AVALIADOS``
      categorias observáveis ⇒ ``nota=None, valida=False`` (nada de nota frágil);
    * erro na categoria limita o valor normalizado (risco_lesao zera);
    * tetos de coerência: risco de lesão ⇒ ≤39; veredito "inadequada" ⇒ ≤49,
      "parcialmente_adequada" ⇒ ≤79 — a nota nunca contradiz o veredito. Quando
      um teto corta a nota, ``teto_aplicado`` é preenchido e as contribuições do
      breakdown somam o valor PRÉ-teto (o corte é global, não por categoria).
    """
    pior = _pior_gravidade_por_categoria(analysis)
    itens = {i.categoria: i for i in analysis.checklist}

    valores: dict[str, float] = {}
    for cat in CATEGORIAS:
        item = itens.get(cat)
        if item is None:
            continue
        valor = _normalizado(item, pior.get(cat))
        if valor is not None:
            valores[cat] = valor

    presentes = len(valores)
    peso_presente = sum(PESOS[c][1] for c in valores)
    notas_calc: list[str] = []

    # gates
    valida = True
    if analysis.qualidade_video == "ruim":
        valida = False
        notas_calc.append("nota bloqueada: qualidade de vídeo insuficiente para pontuar")
    if presentes < MIN_CRITERIOS_AVALIADOS:
        valida = False
        notas_calc.append(
            f"nota bloqueada: apenas {presentes}/{len(CATEGORIAS)} categorias observáveis "
            f"(mínimo {MIN_CRITERIOS_AVALIADOS})"
        )

    componentes: list[ComponenteNota] = []
    bruta = 0.0
    for cat in CATEGORIAS:
        label, peso = PESOS[cat]
        valor = valores.get(cat)
        presente = valor is not None
        efetivo = peso / peso_presente if presente and peso_presente > 0 else 0.0
        contribuicao = efetivo * valor * 100 if presente else 0.0
        bruta += contribuicao
        componentes.append(ComponenteNota(
            categoria=cat, label=label, peso=round(peso, 4),
            peso_efetivo=round(efetivo, 4),
            normalizado=round(valor, 4) if presente else None,
            contribuicao_pontos=round(contribuicao, 2), presente=presente,
        ))

    # tetos de coerência (só fazem sentido quando a nota vai ser publicada)
    teto: float | None = None
    if valida:
        candidatos = [t for t in (
            _TETO_RISCO_LESAO if analysis.risco_lesao else None,
            _TETO_VEREDITO.get(analysis.veredito),
        ) if t is not None]
        if candidatos:
            teto_min = min(candidatos)
            if bruta > teto_min:
                teto = teto_min
                motivo = "risco de lesão" if teto_min == _TETO_RISCO_LESAO and analysis.risco_lesao \
                    else f"veredito '{analysis.veredito}'"
                notas_calc.append(f"nota limitada a {teto_min:.0f} por coerência com {motivo}")
        if presentes < len(CATEGORIAS):
            notas_calc.append(
                f"nota parcial, renormalizada sobre {presentes}/{len(CATEGORIAS)} categorias observáveis"
            )

    nota = None
    if valida:
        nota = round(min(bruta, teto) if teto is not None else bruta, 1)

    notas_calc.append(_AVISO_POC)
    return NotaExecucao(
        nota=nota, valida=valida, modelo_pesos=WEIGHT_MODEL_NAME,
        criterios_presentes=presentes, criterios_totais=len(CATEGORIAS),
        cobertura=round(presentes / len(CATEGORIAS), 4),
        componentes=componentes, teto_aplicado=teto,
        observacao="; ".join(notas_calc) + ".",
    )


__all__ = [
    "CATEGORIAS", "PESOS", "WEIGHT_MODEL_NAME", "MIN_CRITERIOS_AVALIADOS",
    "harmonize_analysis", "compute_nota_execucao",
]
