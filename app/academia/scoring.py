"""Nota de execução determinística + harmonização de consistência da análise.

v2 "academia_categorias_v2_bandas" (23jul2026) — substitui os clamps constantes
do v1 (risco ⇒ min(nota,39), inadequada ⇒ min(nota,49)), que colapsavam TODAS
as notas de produção em 49.0/39.0, por um modelo "Code of Points":

* **base** = média ponderada das 7 categorias do checklist (como no v1);
* **deduções subtrativas por erro** (leve/moderada), com fator de recorrência e
  desconto marginal decrescente para múltiplos erros da mesma gravidade;
* **gates de COMPRESSÃO de banda** (nunca clamp em constante): risco de lesão
  comprime a nota para a banda 0-25 (RF-003, o "hard-zero" do FMS); erro
  relevante impede nota >75; um único erro leve pontual pode tangenciar 76-82.
  Compressões são lineares e injetivas — preservam ordenação e variância;
* o **veredito de 4 níveis** (muito_inadequada · pouco_inadequada ·
  pouco_adequada · muito_adequada) é DERIVADO da banda da nota
  (≤25 · ≤50 · ≤75 · >75) — nunca decidido pelo VLM.

A divisão de trabalho segue a do módulo original e do ``app/tennis/weights.py``:
**o VLM só dá notas 0..10, status por categoria e gravidade/recorrência dos
erros; toda a aritmética é Python**.

Três funções públicas, chamadas pelo service logo após a chamada 1, NESTA ordem:

* :func:`harmonize_analysis` — consistência checklist↔erros e flag de risco em
  CÓDIGO (não toca mais no veredito);
* :func:`compute_nota_execucao` — a nota 0..100 com breakdown e deduções;
* :func:`finalize_veredito` — sobrescreve ``analysis.veredito`` com o nível
  derivado da banda da nota (ou do perfil de erros, quando não há nota válida).

Calibrado contra os 11 vídeos de ``docs/videos-calibragem-academia/``
(ordenação 11/11 idêntica ao ground truth na simulação; MAE 1.3). Os pesos
continuam um indicador observacional de POC — não medem risco de lesão, carga,
esforço nem ativação muscular.
"""

from __future__ import annotations

from typing import get_args

from .models import (
    AcademiaAnalysis,
    CategoriaChecklist,
    ComponenteNota,
    CriterioChecklist,
    DeducaoNota,
    ErroTecnico,
    NotaExecucao,
    Veredito,
)

# Ordem canônica das 7 categorias (a mesma da varredura do prompt).
CATEGORIAS: tuple[str, ...] = get_args(CategoriaChecklist)

# Modelo de pesos (soma 1.00) — INALTERADOS do v1: a ordenação/espalhamento do
# dataset foi atingida sem mexer neles. Joelhos e amplitude pesam mais porque
# são as categorias dos erros mais graves do dataset de calibragem (637 =
# joelhos com risco de lesão; 563/619/633 = amplitude). Recalibrar = editar
# este dict (e dar bump no WEIGHT_MODEL_NAME).
WEIGHT_MODEL_NAME = "academia_categorias_v2_bandas"
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

# Teto da nota 0..10 da categoria com erro (aplicado no harmonize) — a ÚNICA
# punição localizada na base; mantém a rubrica coerente (0-3 grave · 4-6
# moderado · 7-8 leve/refinamento · 9-10 limpo).
_TETO_NOTA_POR_GRAVIDADE = {"leve": 8.0, "moderada": 6.0, "risco_lesao": 3.0}

# Gate de validade da nota (inalterado do v1): nota frágil não é publicada.
MIN_CRITERIOS_AVALIADOS = 3

# --- deduções subtrativas por erro (Code of Points) ------------------------- #
# risco_lesao = 0.0 de propósito: risco pune por 3 vias — categoria capada ≤3
# na base, compressão da nota para a banda 0-25 e adicional por risco extra.
DEDUCAO_BASE = {"leve": 6.0, "moderada": 10.0, "risco_lesao": 0.0}
FATOR_RECORRENTE = 1.3            # erro presente em 2+ reps (ErroTecnico.recorrente)
FATOR_POSICAO = (1.0, 0.75, 0.50, 0.25)  # 2º/3º/4º+ erro da MESMA gravidade custa menos
TETO_DEDUCAO_LEVES = 13.0         # saturação da SOMA de leves (= 1 moderada recorrente)
DEDUCAO_RISCO_ADICIONAL = 3.0     # por risco além do 1º, dentro da banda 0-25

# --- bandas e gates de compressão ------------------------------------------- #
# Bandas (limite superior inclusivo) — o veredito DERIVA daqui (nota → banda).
BANDAS: tuple[tuple[float, float, str], ...] = (
    (0.0,  25.0,  "muito_inadequada"),
    (25.0, 50.0,  "pouco_inadequada"),
    (50.0, 75.0,  "pouco_adequada"),
    (75.0, 100.0, "muito_adequada"),
)
# Gate de coerência: moderada OU ≥2 erros OU leve recorrente, com nota_pre >
# limiar ⇒ compressão (75,100] → (68.75,75]. Garante por construção que
# "muito_adequada" exige erros=[] ou exatamente 1 leve pontual.
GATE_COERENCIA_LIMIAR = 75.0
# Tangência: exatamente 1 erro leve não-recorrente com nota_pre > 75 ⇒
# compressão (75,100] → (76,82] — o único caminho para "muito_adequada" com erro.
TANGENCIA_MIN = 76.0
TANGENCIA_MAX = 82.0

# --- fallback de veredito quando nota=None (pontos de severidade) ------------ #
PONTOS_GRAVIDADE = {"leve": 1, "moderada": 3}   # risco_lesao não pontua: é gate
LIMIAR_MUITO_INADEQUADA = 12                    # P ≥ 12 (4+ moderadas equiv.)
LIMIAR_POUCO_INADEQUADA = 4                     # 2 moderadas, 1 moderada + 1 leve, 4+ leves

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


def _tem_risco(analysis: AcademiaAnalysis) -> bool:
    """Risco = erro com gravidade risco_lesao OU flag risco_lesao do modelo.

    A flag sem erro correspondente TAMBÉM dispara (conservador): o histórico do
    módulo é de SUBnotificação de risco, e o custo de um falso negativo (lesão)
    supera o de um falso positivo (nota baixa com warnings visíveis).
    """
    return analysis.risco_lesao or any(e.gravidade == "risco_lesao" for e in analysis.erros)


# --------------------------------------------------------------------------- #
# harmonização determinística (regras de calibragem viram código)              #
# --------------------------------------------------------------------------- #
def harmonize_analysis(analysis: AcademiaAnalysis) -> tuple[AcademiaAnalysis, list[str]]:
    """Reconcilia a análise do VLM com as regras de calibragem, em código.

    Nunca inventa conteúdo — só corrige inconsistências internas do que o modelo
    devolveu, e registra cada ajuste num aviso PT-BR (vira ``warnings`` da
    resposta, visível na UI). Retorna uma cópia ajustada + a lista de avisos.

    v2: NÃO toca mais no ``veredito`` — ele é 100% derivado depois, em
    :func:`finalize_veredito` (RF-003 vive no gate de risco da nota).
    """
    fixed = analysis.model_copy(deep=True)
    avisos: list[str] = []

    # Consistência da flag de risco (RF-003): erro risco_lesao ⇒ flag true.
    tem_erro_risco = any(e.gravidade == "risco_lesao" for e in fixed.erros)
    if tem_erro_risco and not fixed.risco_lesao:
        fixed.risco_lesao = True
        avisos.append("consistência: risco_lesao ajustado para true — há erro com essa gravidade (RF-003).")
    if fixed.risco_lesao and not tem_erro_risco:
        # Direção inversa: flag de risco SEM erro dessa gravidade. Conservador —
        # não apagamos um sinal de segurança do modelo (o histórico do módulo é
        # de SUBnotificação de risco); a flag mantida dispara o gate de risco da
        # nota, e o aviso expõe a lacuna.
        avisos.append(
            "consistência: risco_lesao=true sem erro de gravidade 'risco_lesao' na lista de erros."
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
def _normalizado(item: CriterioChecklist) -> float | None:
    """Valor 0..1 da categoria para o cálculo, ou None se não observável.

    A nota 0..10 já chega capada pela gravidade do erro (harmonize) — não há
    segunda punição aqui (o v1 aplicava um teto normalizado extra; removido).
    """
    if item.status == "nao_observavel":
        return None
    if item.nota_0a10 is not None:
        return max(0.0, min(1.0, item.nota_0a10 / 10.0))
    return _FALLBACK_STATUS[item.status]


def _calcular_deducoes(erros: list[ErroTecnico]) -> tuple[list[DeducaoNota], float]:
    """Deduções subtrativas por erro (leve/moderada), auditáveis linha a linha.

    d = DEDUCAO_BASE[gravidade] × (FATOR_RECORRENTE se recorrente) ×
    FATOR_POSICAO[posição entre erros da MESMA gravidade, por valor decrescente].
    A soma das leves satura em TETO_DEDUCAO_LEVES (único min() do sistema — sobre
    uma soma de penalidades, nunca sobre a nota). Erros risco_lesao não deduzem.
    """
    por_gravidade: dict[str, list[tuple[int, ErroTecnico, float]]] = {"leve": [], "moderada": []}
    for idx, erro in enumerate(erros):
        if erro.gravidade not in por_gravidade:
            continue
        bruto = DEDUCAO_BASE[erro.gravidade] * (FATOR_RECORRENTE if erro.recorrente else 1.0)
        por_gravidade[erro.gravidade].append((idx, erro, bruto))

    linhas: list[DeducaoNota] = []
    total = 0.0
    for gravidade, lista in por_gravidade.items():
        lista.sort(key=lambda t: t[2], reverse=True)
        pontos_grav: list[float] = []
        for pos, (idx, erro, bruto) in enumerate(lista):
            fator = FATOR_POSICAO[min(pos, len(FATOR_POSICAO) - 1)]
            pontos_grav.append(bruto * fator)
        soma = sum(pontos_grav)
        if gravidade == "leve" and soma > TETO_DEDUCAO_LEVES:
            escala = TETO_DEDUCAO_LEVES / soma
            pontos_grav = [p * escala for p in pontos_grav]
            soma = TETO_DEDUCAO_LEVES
        for pos, ((idx, erro, _), pontos) in enumerate(zip(lista, pontos_grav)):
            linhas.append(DeducaoNota(
                indice_erro=idx, categoria=erro.categoria, gravidade=gravidade,
                recorrente=erro.recorrente,
                fator_posicao=FATOR_POSICAO[min(pos, len(FATOR_POSICAO) - 1)],
                pontos=round(pontos, 2),
            ))
        total += soma

    linhas.sort(key=lambda d: d.indice_erro)
    return linhas, total


def compute_nota_execucao(analysis: AcademiaAnalysis) -> NotaExecucao:
    """Nota 0..100 com breakdown e deduções (chamar APÓS ``harmonize_analysis``).

    Fórmula (toda em Python — o VLM só forneceu notas 0..10 e gravidades):

    1. gates de validade: qualidade de vídeo "ruim" ou menos de
       ``MIN_CRITERIOS_AVALIADOS`` categorias observáveis ⇒ ``nota=None,
       valida=False`` (nada de nota frágil);
    2. BASE = Σ peso renormalizado × (nota_0a10/10) × 100 sobre as categorias
       observáveis (``nao_observavel`` sai do cálculo — nunca vira zero);
    3. nota_pre = max(0, BASE − Σ deduções por erro) — ver
       :func:`_calcular_deducoes`;
    4. gates de COMPRESSÃO de banda (nunca clamp em constante):
       risco de lesão ⇒ nota = 25×nota_pre/100 − adicional por risco extra
       (banda 0-25, RF-003); erro moderado / 2+ erros / leve recorrente com
       nota_pre>75 ⇒ compressão para (68.75,75]; exatamente 1 leve pontual com
       nota_pre>75 ⇒ tangência (76,82].

    O veredito NÃO é decidido aqui — ver :func:`finalize_veredito` (banda da nota).
    """
    itens = {i.categoria: i for i in analysis.checklist}

    valores: dict[str, float] = {}
    for cat in CATEGORIAS:
        item = itens.get(cat)
        if item is None:
            continue
        valor = _normalizado(item)
        if valor is not None:
            valores[cat] = valor

    presentes = len(valores)
    peso_presente = sum(PESOS[c][1] for c in valores)
    notas_calc: list[str] = []

    # gates de validade
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
    base = 0.0
    for cat in CATEGORIAS:
        label, peso = PESOS[cat]
        valor = valores.get(cat)
        presente = valor is not None
        efetivo = peso / peso_presente if presente and peso_presente > 0 else 0.0
        contribuicao = efetivo * valor * 100 if presente else 0.0
        base += contribuicao
        componentes.append(ComponenteNota(
            categoria=cat, label=label, peso=round(peso, 4),
            peso_efetivo=round(efetivo, 4),
            normalizado=round(valor, 4) if presente else None,
            contribuicao_pontos=round(contribuicao, 2), presente=presente,
        ))

    deducoes: list[DeducaoNota] = []
    nota: float | None = None
    nota_pre_arred: float | None = None
    gate: str | None = None
    teto: float | None = None

    if valida:
        deducoes, total_deducoes = _calcular_deducoes(analysis.erros)
        nota_pre = max(0.0, base - total_deducoes)
        nota_pre_arred = round(nota_pre, 1)
        if total_deducoes:
            notas_calc.append(
                f"deduções por erro: −{total_deducoes:.1f} pontos sobre a base {base:.1f}"
            )

        if _tem_risco(analysis):
            # gate de risco (RF-003, hard-zero do FMS): compressão linear
            # [0,100]→[0,25] — injetiva, preserva ordenação dentro da banda.
            n_riscos = sum(1 for e in analysis.erros if e.gravidade == "risco_lesao")
            nota_final = max(
                0.0, 25.0 * nota_pre / 100.0 - DEDUCAO_RISCO_ADICIONAL * max(0, n_riscos - 1)
            )
            gate, teto = "risco", 25.0
            notas_calc.append("nota comprimida para a banda 0-25 por risco de lesão (RF-003)")
        else:
            erros = analysis.erros
            impede_muito_adequada = (
                any(e.gravidade == "moderada" for e in erros)
                or len(erros) >= 2
                or any(e.gravidade == "leve" and e.recorrente for e in erros)
            )
            if impede_muito_adequada and nota_pre > GATE_COERENCIA_LIMIAR:
                nota_final = 50.0 + 25.0 * nota_pre / 100.0
                gate, teto = "coerencia", 75.0
                notas_calc.append(
                    "nota comprimida para ≤75 por coerência: há erro registrado que impede 'muito_adequada'"
                )
            elif (
                len(erros) == 1 and erros[0].gravidade == "leve"
                and not erros[0].recorrente and nota_pre > GATE_COERENCIA_LIMIAR
            ):
                nota_final = TANGENCIA_MIN + (TANGENCIA_MAX - TANGENCIA_MIN) * (nota_pre - 75.0) / 25.0
                gate, teto = "tangencia", TANGENCIA_MAX
                notas_calc.append(
                    "um único erro leve pontual: nota comprimida para a faixa de tangência 76-82"
                )
            else:
                nota_final = nota_pre

        nota = round(nota_final, 1)
        if presentes < len(CATEGORIAS):
            notas_calc.append(
                f"nota parcial, renormalizada sobre {presentes}/{len(CATEGORIAS)} categorias observáveis"
            )

    notas_calc.append(_AVISO_POC)
    return NotaExecucao(
        nota=nota, valida=valida, modelo_pesos=WEIGHT_MODEL_NAME,
        criterios_presentes=presentes, criterios_totais=len(CATEGORIAS),
        cobertura=round(presentes / len(CATEGORIAS), 4),
        componentes=componentes, deducoes=deducoes,
        nota_pre_gates=nota_pre_arred, gate=gate, teto_aplicado=teto,
        observacao="; ".join(notas_calc) + ".",
    )


# --------------------------------------------------------------------------- #
# veredito de 4 níveis — derivado, nunca decidido pelo VLM                     #
# --------------------------------------------------------------------------- #
def _banda(nota: float) -> Veredito:
    """Lookup do veredito pela banda da nota (limite superior inclusivo)."""
    for _, teto, veredito in BANDAS:
        if nota <= teto:
            return veredito  # type: ignore[return-value]
    return "muito_adequada"


def derive_veredito(analysis: AcademiaAnalysis, nota: NotaExecucao | None) -> tuple[Veredito, str]:
    """(veredito derivado, base da decisão) — determinístico, VLM-independente.

    Com nota válida: a banda da nota decide (os gates de compressão já puseram a
    nota na banda certa). Sem nota válida (gate de vídeo/cobertura): fallback por
    pontos de severidade derivado SÓ dos erros harmonizados — risco ⇒
    muito_inadequada; senão P = 3×moderadas + 1×leves (≥12 muito_inadequada,
    ≥4 pouco_inadequada, senão pouco_adequada). Sem nota válida NUNCA se declara
    "muito_adequada" — não há evidência para isso.
    """
    if nota is not None and nota.valida and nota.nota is not None:
        return _banda(nota.nota), f"banda da nota {nota.nota:.1f}"
    if _tem_risco(analysis):
        return "muito_inadequada", "perfil de erros: risco de lesão (sem nota válida)"
    pontos = sum(PONTOS_GRAVIDADE.get(e.gravidade, 0) for e in analysis.erros)
    if pontos >= LIMIAR_MUITO_INADEQUADA:
        veredito: Veredito = "muito_inadequada"
    elif pontos >= LIMIAR_POUCO_INADEQUADA:
        veredito = "pouco_inadequada"
    else:
        veredito = "pouco_adequada"
    return veredito, f"perfil de erros: severidade {pontos} (sem nota válida)"


def finalize_veredito(analysis: AcademiaAnalysis, nota: NotaExecucao | None) -> list[str]:
    """Sobrescreve ``analysis.veredito`` com o nível derivado; retorna avisos.

    O veredito do VLM é só sinal de triagem — o derivado vence SEMPRE; quando
    diverge, o aviso cita os dois (transparência da substituição).
    """
    avisos: list[str] = []
    derivado, base_decisao = derive_veredito(analysis, nota)
    if analysis.veredito != derivado:
        avisos.append(
            f"consistência: veredito '{analysis.veredito}' (modelo) substituído por "
            f"'{derivado}' ({base_decisao})."
        )
    analysis.veredito = derivado
    sem_nota = nota is None or not nota.valida or nota.nota is None
    if sem_nota and not analysis.erros and derivado == "pouco_adequada":
        avisos.append(
            "veredito conservador: sem nota válida não há evidência para 'muito_adequada'."
        )
    return avisos


__all__ = [
    "CATEGORIAS", "PESOS", "WEIGHT_MODEL_NAME", "MIN_CRITERIOS_AVALIADOS",
    "BANDAS", "DEDUCAO_BASE", "FATOR_RECORRENTE", "FATOR_POSICAO",
    "TETO_DEDUCAO_LEVES", "DEDUCAO_RISCO_ADICIONAL",
    "harmonize_analysis", "compute_nota_execucao",
    "derive_veredito", "finalize_veredito",
]
