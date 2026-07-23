"""Schemas Pydantic da academia — fonte única de verdade.

São usados em dois papéis, mesmo padrão do :mod:`app.tennis.models`:

1. como ``response_schema`` da chamada 1 ao Gemini (vídeo → JSON estruturado);
2. para validar/normalizar o JSON que volta antes de alimentar as duas saídas
   seguintes (narrativa PT-BR e áudio TTS).

Contrato de schema fixo (nomes de campo travados, não renomear):

* :class:`ErroTecnico` — um erro técnico pontual detectado no exercício.
* :class:`CriterioChecklist` — avaliação explícita de UMA das 7 categorias (RF-002 visível).
* :class:`RepeticaoSegmentada` — uma repetição segmentada com marcos temporais aproximados.
* :class:`AcademiaAnalysis` — saída bruta da chamada 1 (vídeo → JSON).
* :class:`NotaExecucao` / :class:`ComponenteNota` — nota 0..100 determinística (Python,
  ``scoring.py``; nunca preenchida pelo VLM).
* :class:`AcademiaAnalysisResponse` — payload final devolvido pela API.

Regras de calibragem que o schema precisa refletir (ver ``prompts.py`` para o
texto do system prompt que instrui o modelo a respeitá-las):

* RN-01 — se há erro relevante (sobretudo ``risco_lesao``), a narrativa NUNCA
  abre com elogios; o erro dominante vem primeiro.
* RF-002 — o modelo verifica EXPLICITAMENTE as 7 categorias de erro cobertas
  por ``ErroTecnico.categoria``.
* RF-003 — valgo dinâmico severo / pés mal posicionados / erro com risco de
  lesão implicam ``veredito="inadequada"`` + ``risco_lesao=True``.
* RF-004 — execução correta não recebe erros inventados: ``erros`` pode (e
  deve) vir vazio quando não há nada de relevante a apontar.
* RN-02 — o veredito é restrito ao observável: ``qualidade_video`` e
  ``angulo_camera`` existem para que ``confiabilidade`` reflita as limitações
  reais do vídeo, não uma certeza que o modelo não tem.
* RN-03/RNF-003 — o schema não tem (e não deve ganhar) campos de
  hipertrofia/força/emagrecimento/carga/esforço/ativação muscular: o produto
  não mede isso.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# catálogos fechados                                                          #
# --------------------------------------------------------------------------- #

# As 7 categorias de erro técnico que o system prompt instrui verificar
# explicitamente (RF-002). "outro" é a válvula de escape honesta para um erro
# real que não se encaixa nas 6 categorias nomeadas — evita forçar rótulo errado.
CategoriaErro = Literal[
    "amplitude",           # amplitude parcial/encurtada do movimento
    "escapula_ombros",     # sem depressão escapular, ombros protraídos
    "tronco",               # balanço/embalo, peitoral afastado do encosto, base errada
    "cervical",             # hiperextensão ou movimento brusco de cervical
    "cotovelos",             # projeção de cotovelos à frente (ex.: em roscas)
    "joelhos",               # bloqueio articular, valgo dinâmico, pés mal posicionados
    "ritmo",                 # fase excêntrica acelerada (sem controle)
    "outro",
]

GravidadeErro = Literal["leve", "moderada", "risco_lesao"]

QualidadeVideo = Literal["boa", "media", "ruim"]

# RF-003: valgo dinâmico severo, pés mal posicionados em leg press ou qualquer
# erro com risco de lesão real levam obrigatoriamente a "inadequada".
# RF-004: execução correta não ganha erros inventados — "adequada" fica livre
# de nitpicking punitivo.
Veredito = Literal["adequada", "parcialmente_adequada", "inadequada"]

# RN-02: confiabilidade do veredito é limitada pelo que é observável no vídeo
# (ângulo de câmera, qualidade, partes ocultas) — não é uma nota de execução.
Confiabilidade = Literal["baixa", "media", "alta"]

# --------------------------------------------------------------------------- #
# parâmetros reintroduzidos do módulo original (snapshot a368d14)              #
# --------------------------------------------------------------------------- #
# O módulo original expunha checklist por critério, repetições segmentadas,
# leitura do movimento e qualidade de captura detalhada; a versão calibrada os
# descartou e a saída ficou "seca". Estes tipos os trazem de volta ADAPTADOS ao
# núcleo calibrado: o checklist cobre exatamente as 7 categorias de RF-002 (uma
# entrada por categoria, sempre as 7), em vez dos critérios por-exercício do
# perfil original.

# As 7 categorias nomeadas do checklist — "outro" fica de fora de propósito: o
# checklist é a varredura fixa de RF-002; um erro fora das 7 ainda pode ser
# reportado em ``erros`` com categoria "outro", mas não ganha linha de checklist.
CategoriaChecklist = Literal[
    "amplitude", "escapula_ombros", "tronco", "cervical", "cotovelos", "joelhos", "ritmo",
]

# Espelha o ``ApproximateCriterionState`` do módulo original. "ajuste_leve" é um
# refinamento opcional, NÃO um erro (RF-004: não fabrica erro em execução boa);
# "a_corrigir" implica um erro correspondente em ``erros`` (consistência
# garantida deterministicamente em ``scoring.harmonize_analysis``).
StatusCriterio = Literal["adequado", "ajuste_leve", "a_corrigir", "nao_observavel"]

# Espelha o ``MovementConsistency`` original (resumo do padrão cíclico).
ConsistenciaMovimento = Literal["consistente", "variavel", "inconclusivo"]


# --------------------------------------------------------------------------- #
# bloco de erro técnico                                                       #
# --------------------------------------------------------------------------- #
class ErroTecnico(BaseModel):
    """Um erro técnico pontual observado na execução do exercício.

    Par obrigatório **o-que-está-errado → o-que-consertar**: todo erro carrega
    ``descricao`` (o problema observado) E ``correcao`` (a instrução acionável
    para consertá-lo). Isso garante que a plataforma nunca aponte um erro sem
    dizer como corrigi-lo, nem dilua o erro numa "sugestão de melhoria" — a UI
    renderiza os dois lado a lado (❌ errado / ✅ corrigir).

    ``gravidade="risco_lesao"`` é o gatilho que, junto com padrões como valgo
    dinâmico severo ou pés mal posicionados (RF-003), força
    ``AcademiaAnalysis.veredito == "inadequada"`` e
    ``AcademiaAnalysis.risco_lesao = True`` — e faz a narrativa (RN-01) abrir
    com esse erro antes de qualquer elogio.
    """

    categoria: CategoriaErro = Field(
        description="Categoria fechada do erro (uma das 7 verificadas explicitamente, RF-002)."
    )
    descricao: str = Field(
        description="O QUE ESTÁ ERRADO: descrição do erro em linguagem de treinador PT-BR — "
        "concreta, nomeando a região do corpo e o que foi observado (sem jargão acadêmico). "
        "Aponta o problema, não a solução (a solução vai em 'correcao')."
    )
    correcao: str = Field(
        description="O QUE CONSERTAR: a instrução prática e acionável para corrigir ESTE erro "
        "específico na próxima execução, em linguagem de treinador PT-BR (ex.: 'plante o pé "
        "inteiro na plataforma e mantenha o joelho apontando para a ponta do pé'). Sempre "
        "pareada com 'descricao' — todo erro apontado precisa vir com o seu conserto."
    )
    timestamp_s: float | None = Field(
        default=None, ge=0,
        description="Instante aproximado (em segundos) em que o erro ocorre no vídeo.",
    )
    gravidade: GravidadeErro = Field(
        description="Severidade do erro: leve, moderada ou risco_lesao (aciona veredito inadequada)."
    )


# --------------------------------------------------------------------------- #
# checklist das 7 categorias + repetições segmentadas (chamada 1)              #
# --------------------------------------------------------------------------- #
class CriterioChecklist(BaseModel):
    """Avaliação explícita de UMA das 7 categorias de RF-002.

    O prompt já obriga a varredura sequencial das 7 categorias; este item torna
    o resultado da varredura um parâmetro visível (como o ``CriterionAssessment``
    do módulo original), em vez de só materializar as categorias com erro.
    A nota 0..10 segue a rubrica do prompt e alimenta a ``NotaExecucao``
    determinística — o VLM nunca faz a agregação (aritmética é Python).
    """

    categoria: CategoriaChecklist = Field(
        description="Qual das 7 categorias de RF-002 este item avalia (uma entrada por categoria)."
    )
    status: StatusCriterio = Field(
        description="adequado (limpo) · ajuste_leve (refinamento opcional, NÃO é erro) · "
        "a_corrigir (há erro registrado em 'erros' nesta categoria) · nao_observavel "
        "(ângulo/qualidade não permitem avaliar — nunca vira erro do aluno)."
    )
    nota_0a10: float | None = Field(
        default=None, ge=0, le=10,
        description="Nota observacional 0..10 da categoria (rubrica no prompt); null quando "
        "nao_observavel. A agregação em nota 0..100 é feita em Python, nunca pelo modelo.",
    )
    observacao: str = Field(
        description="Evidência concreta em 1 frase PT-BR do que sustenta o status (ou por que "
        "não foi observável)."
    )


class RepeticaoSegmentada(BaseModel):
    """Segmentação lógica de uma repetição (espelha o ``RepetitionSegment`` original).

    Marcos temporais aproximados — ``None`` quando o marco não é observável;
    o modelo é instruído a não inventar precisão que o vídeo não sustenta.
    """

    indice: int = Field(ge=1, description="Número da repetição na ordem do vídeo (1..n).")
    completa: bool = Field(description="True se a repetição fecha o ciclo completo (parcial = False).")
    inicio_s: float | None = Field(default=None, ge=0, description="Início aproximado (s), ou null.")
    transicao_s: float | None = Field(
        default=None, ge=0,
        description="Momento aproximado da mudança de direção (fundo/pico do movimento), ou null.",
    )
    fim_s: float | None = Field(default=None, ge=0, description="Fim aproximado (s), ou null.")
    observacao: str | None = Field(
        default=None, description="Observação específica desta repetição (ou null)."
    )


# --------------------------------------------------------------------------- #
# CHAMADA 1 — vídeo → JSON estruturado                                        #
# --------------------------------------------------------------------------- #
class AcademiaAnalysis(BaseModel):
    """Schema estrito (``response_schema``) da chamada 1 — análise técnica do exercício.

    ``veredito`` e ``risco_lesao`` são o núcleo da calibragem: um erro grave o
    suficiente (RF-003) força ``veredito="inadequada"`` e ``risco_lesao=True``,
    independente de quantos acertos existam. Execução correta (RF-004) deve
    resultar em ``erros=[]`` — nunca inventar erro para preencher a lista.
    """

    exercicio_identificado: str = Field(
        description="Nome do exercício identificado no vídeo (ex.: 'supino reto com barra')."
    )
    equipamento: str | None = Field(
        default=None, description="Equipamento/máquina usado, quando identificável (ex.: 'banco reto', 'leg press 45°')."
    )
    angulo_camera: str = Field(
        description="Ângulo/posição da câmera em relação ao exercício (ex.: 'lateral direita', 'frontal')."
    )
    qualidade_video: QualidadeVideo = Field(
        description="Qualidade geral do vídeo para fins de análise (boa/media/ruim) — limita a confiabilidade (RN-02)."
    )
    partes_ocultas: list[str] = Field(
        default_factory=list,
        description="Partes do corpo relevantes que ficaram fora de quadro ou ocultas durante o movimento.",
    )
    repeticoes_visiveis: int | None = Field(
        default=None, ge=0, description="Número de repetições completas visíveis no vídeo, se contável."
    )
    veredito: Veredito = Field(
        description="Veredito geral da execução, restrito ao observável (RN-02). "
        "'inadequada' é obrigatório quando há erro com risco de lesão (RF-003)."
    )
    confiabilidade: Confiabilidade = Field(
        description="Confiança no veredito, dada a qualidade do vídeo/ângulo/partes ocultas."
    )
    erros: list[ErroTecnico] = Field(
        default_factory=list,
        description="Erros técnicos identificados (pode ser vazio — RF-004 proíbe erro inventado).",
    )
    acertos: list[str] = Field(
        default_factory=list,
        description="Pontos tecnicamente corretos da execução, em linguagem de treinador PT-BR.",
    )
    foco_pratico: str = Field(
        description="A principal orientação prática e acionável para a próxima execução."
    )
    risco_lesao: bool = Field(
        description="True se algum erro observado representa risco de lesão (RF-003) — "
        "nesse caso a narrativa deve orientar interromper/corrigir antes de qualquer outro conteúdo."
    )
    musculos_esperados: list[str] = Field(
        default_factory=list,
        description="Grupos musculares esperados para este exercício (informativo — não é medição de ativação, RN-03).",
    )
    observacoes: str | None = Field(
        default=None, description="Observações adicionais relevantes não cobertas pelos demais campos."
    )

    # ----- parâmetros reintroduzidos do módulo original (a368d14) -----
    checklist: list[CriterioChecklist] = Field(
        default_factory=list,
        description="Resultado da varredura das 7 categorias de RF-002 — exatamente uma entrada "
        "por categoria, TODAS as 7 sempre presentes (nao_observavel quando não der para avaliar). "
        "Consistência com 'erros' é obrigatória: categoria com erro → status a_corrigir.",
    )
    repeticoes: list[RepeticaoSegmentada] = Field(
        default_factory=list,
        description="Repetições segmentadas na ordem do vídeo, com marcos temporais aproximados "
        "(lista vazia se a segmentação não for possível).",
    )
    consistencia_amplitude: ConsistenciaMovimento | None = Field(
        default=None,
        description="A amplitude se mantém entre as repetições? (consistente/variavel/inconclusivo)",
    )
    consistencia_ritmo: ConsistenciaMovimento | None = Field(
        default=None,
        description="O ritmo se mantém entre as repetições? (consistente/variavel/inconclusivo)",
    )
    observacao_movimento: str | None = Field(
        default=None,
        description="Leitura geral do padrão cíclico do movimento em 1-2 frases PT-BR (ou null).",
    )
    corpo_inteiro_visivel: bool | None = Field(
        default=None,
        description="As regiões do corpo relevantes ao exercício permanecem no quadro? (null se incerto)",
    )
    camera_estavel: bool | None = Field(
        default=None, description="A câmera permanece estável durante a execução? (null se incerto)"
    )
    iluminacao_adequada: bool | None = Field(
        default=None, description="A iluminação permite ver contornos e articulações? (null se incerto)"
    )
    recomendacoes_gravacao: list[str] = Field(
        default_factory=list,
        description="Até 6 instruções práticas de como filmar melhor da próxima vez (ângulo, "
        "distância, enquadramento) — SOMENTE quando a captura limitou a análise; vazia se a "
        "captura está boa.",
    )


# --------------------------------------------------------------------------- #
# nota de execução determinística (Python, nunca o VLM)                        #
# --------------------------------------------------------------------------- #
class ComponenteNota(BaseModel):
    """Contribuição de uma categoria do checklist para a nota 0..100.

    Espelha o ``ExecutionScoreComponent`` do módulo original: peso original,
    peso efetivo (renormalizado sobre as categorias observáveis) e contribuição
    em pontos — as contribuições somam a nota (ou o valor pré-teto, quando
    ``NotaExecucao.teto_aplicado`` cortou a nota globalmente).
    """

    categoria: str = Field(description="Categoria do checklist (uma das 7 de RF-002).")
    label: str = Field(description="Rótulo PT-BR da categoria para exibição.")
    peso: float = Field(ge=0, le=1, description="Peso original da categoria no modelo de pesos.")
    peso_efetivo: float = Field(
        ge=0, le=1, description="Peso renormalizado sobre as categorias observáveis (0 se ausente)."
    )
    normalizado: float | None = Field(
        default=None, ge=0, le=1,
        description="Valor 0..1 da categoria (nota/10, com teto por gravidade de erro); null se não observável.",
    )
    contribuicao_pontos: float = Field(
        ge=0, le=100, description="Pontos que esta categoria soma à nota (peso_efetivo × normalizado × 100)."
    )
    presente: bool = Field(description="True se a categoria foi observável e entrou no cálculo.")


class NotaExecucao(BaseModel):
    """Nota 0..100 da execução, calculada DETERMINISTICAMENTE em Python.

    Reintroduz o ``weighted_execution_score`` do módulo original, adaptado às 7
    categorias calibradas (``scoring.py``). O VLM só fornece as notas 0..10 por
    categoria; agregação, pesos, gates e tetos são todos código:

    * categorias ``nao_observavel`` saem do cálculo (peso renormalizado — nunca
      viram zero);
    * qualidade de vídeo "ruim" ou menos de 3 categorias observáveis bloqueiam a
      nota (``nota=None, valida=False``) em vez de publicar um número frágil;
    * teto de coerência: risco de lesão e veredito ruim limitam a nota máxima
      (impossível "inadequada com 85/100").

    É um indicador observacional de POC, não validado clinicamente — não mede
    risco de lesão, carga, esforço nem ativação muscular.
    """

    nota: float | None = Field(
        default=None, ge=0, le=100,
        description="Nota 0..100 (1 casa decimal), ou null quando bloqueada pelos gates.",
    )
    valida: bool = Field(description="False quando os gates bloquearam a publicação da nota.")
    modelo_pesos: str = Field(description="Identificador do modelo de pesos usado (rastreável).")
    criterios_presentes: int = Field(ge=0, description="Categorias observáveis que entraram no cálculo.")
    criterios_totais: int = Field(ge=1, description="Total de categorias do modelo (7).")
    cobertura: float = Field(ge=0, le=1, description="criterios_presentes / criterios_totais.")
    componentes: list[ComponenteNota] = Field(
        default_factory=list, description="Breakdown por categoria — as contribuições somam a nota."
    )
    teto_aplicado: float | None = Field(
        default=None,
        description="Teto de coerência aplicado (por risco de lesão/veredito), ou null se nenhum.",
    )
    observacao: str = Field(
        description="Explicação do cálculo em PT-BR: bloqueios, renormalização parcial, tetos e o "
        "aviso fixo de indicador POC não validado."
    )


# --------------------------------------------------------------------------- #
# frame do momento do erro (Python + ffmpeg, nunca o VLM)                      #
# --------------------------------------------------------------------------- #
class FrameErro(BaseModel):
    """Print (JPEG) do instante exato de UM erro técnico, extraído com ffmpeg.

    Gerado em ``frames.py`` a partir do ``timestamp_s`` do erro, enquanto o
    vídeo temporário ainda existe — só para erros COM timestamp (o objetivo é
    mostrar ao aluno o momento exato do problema; execução limpa não gera
    frame nenhum). Nunca faz parte do ``response_schema`` da chamada 1.
    """

    erro_index: int = Field(
        ge=0, description="Índice do erro correspondente em metrics.erros (posição original)."
    )
    categoria: str = Field(description="Categoria do erro (redundante, facilita a UI/consumidores).")
    timestamp_s: float = Field(ge=0, description="Instante do vídeo capturado no frame (s).")
    image_base64: str = Field(description="JPEG base64 do frame no momento do erro.")
    mime: str = Field(default="image/jpeg", description="MIME type da imagem.")


# --------------------------------------------------------------------------- #
# modelo de saída da API                                                      #
# --------------------------------------------------------------------------- #
class AcademiaAnalysisResponse(BaseModel):
    """Payload final devolvido pelo endpoint ``POST /academia/analyze``.

    Espelha o shape de :class:`app.tennis.models.TennisAnalysisResponse`:
    métricas estruturadas + narrativa PT-BR (RF-007, calibrada por RN-01) +
    áudio TTS opcional + warnings não-fatais (narrativa/áudio/persistência
    podem falhar sem derrubar a análise) + id de persistência opcional.
    """

    exercicio: str = Field(description="Nome do exercício identificado (espelha metrics.exercicio_identificado).")
    metrics: AcademiaAnalysis = Field(description="JSON estruturado da chamada 1.")
    nota_execucao: NotaExecucao | None = Field(
        default=None,
        description="Nota 0..100 determinística calculada em Python a partir do checklist "
        "(scoring.py) — nunca preenchida pelo VLM; null só em registros antigos sem checklist.",
    )
    frames_erros: list[FrameErro] = Field(
        default_factory=list,
        description="Prints (JPEG base64) do momento exato de cada erro com timestamp — "
        "extraídos com ffmpeg em Python (frames.py); vazia quando não há erro com timestamp "
        "ou quando a extração falha (falha vira warning, nunca erro).",
    )
    narrative: str = Field(
        description="Narrativa PT-BR de treinador (chamada 2): erros primeiro se houver (RN-01), "
        "depois acertos, veredito, foco prático, limitações e encerramento motivador sóbrio (RF-007). "
        "Inclui disclaimer de que é relatório educacional, não substitui avaliação presencial (RN-05)."
    )
    audio_base64: str | None = Field(default=None, description="WAV base64 da narrativa (chamada 3), se solicitado e disponível.")
    warnings: list[str] = Field(default_factory=list, description="Falhas não-fatais (narrativa, áudio ou persistência).")
    persisted_id: str | None = Field(default=None, description="Id do registro persistido, se a persistência estiver habilitada.")
