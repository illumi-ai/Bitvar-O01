"""Schemas Pydantic da academia — fonte única de verdade.

São usados em dois papéis, mesmo padrão do :mod:`app.tennis.models`:

1. como ``response_schema`` da chamada 1 ao Gemini (vídeo → JSON estruturado);
2. para validar/normalizar o JSON que volta antes de alimentar as duas saídas
   seguintes (narrativa PT-BR e áudio TTS).

Este schema espelha a estrutura do GABARITO escrito pelo time (as análises em
``videos-calibragem-academia/analises/*.txt``), que sempre traz, para CADA
execução, os três blocos de retorno em equilíbrio: **o que está bom**, **o que
melhorar** e o **feedback ideal** que sintetiza os dois — mesmo quando o veredito
é "inadequada". A rodada anterior modelava só ``erros`` (gated por regra
anti-nitpicking), o que fazia o feedback construtivo sumir em execuções boas
(caso-âncora 613 — "a IA elogiou demais"). Aqui o retorno é sempre balanceado.

Contrato de schema fixo (nomes de campo travados, não renomear):

* :class:`PontoMelhoria` — um ponto a melhorar (o-que-não-está-ideal →
  como-ajustar), com prioridade graduada de ``refinamento`` a ``risco_lesao``.
* :class:`AcademiaAnalysis` — saída bruta da chamada 1 (vídeo → JSON).
* :class:`AcademiaAnalysisResponse` — payload final devolvido pela API.

Regras de calibragem que o schema precisa refletir (ver ``prompts.py`` para o
texto do system prompt que instrui o modelo a respeitá-las):

* RF-002 — o modelo verifica EXPLICITAMENTE as 7 categorias técnicas cobertas
  por ``PontoMelhoria.categoria``.
* RF-003 — valgo dinâmico severo / pés mal posicionados / qualquer ponto com
  ``prioridade="risco_lesao"`` implicam ``veredito="inadequada"`` +
  ``risco_lesao=True``.
* RF-004 (reformulada) — execução correta NUNCA recebe *erro grave* inventado,
  mas SEMPRE recebe pelo menos um ``ponto_a_melhorar`` de prioridade
  ``refinamento``/``leve``: o dataset mostra que sempre há algo a refinar (até o
  "vídeo do certo", 619, tinha "amplitude levemente encurtada"). O proibido é
  INFLAR a prioridade — chamar de erro grave o que é polimento.
* RF-008 (a correção central desta rodada) — toda análise devolve o par
  balanceado: ``pontos_fortes`` (o que está bom) E ``pontos_a_melhorar`` (o que
  não está / o que melhorar), sintetizados em ``feedback_ideal``. O feedback
  construtivo nunca some, nem mesmo numa execução boa.
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

# As 7 categorias técnicas que o system prompt instrui verificar explicitamente
# (RF-002), extraídas do catálogo de erros do dataset de calibragem. "outro" é a
# válvula de escape honesta para um ponto real que não se encaixa nas 6
# categorias nomeadas — evita forçar um rótulo errado.
CategoriaMelhoria = Literal[
    "amplitude",           # amplitude parcial/encurtada do movimento
    "escapula_ombros",     # sem depressão escapular, ombros protraídos
    "tronco",               # balanço/embalo, peitoral afastado do encosto, base errada
    "cervical",             # hiperextensão ou movimento brusco de cervical
    "cotovelos",             # projeção de cotovelos à frente (ex.: em roscas)
    "joelhos",               # bloqueio articular, valgo dinâmico, pés mal posicionados
    "ritmo",                 # fase excêntrica acelerada (sem controle)
    "outro",
]

# Prioridade GRADUADA do ponto a melhorar. O nível "refinamento" é a novidade
# desta rodada: é o polimento opcional de uma execução que já está boa — é o que
# garante que o feedback construtivo apareça SEM punir o veredito (um vídeo bem
# executado fica "adequada" e ainda assim carrega ≥1 "refinamento"). "risco_lesao"
# no outro extremo força o veredito "inadequada" (RF-003).
Prioridade = Literal["refinamento", "leve", "moderada", "risco_lesao"]

QualidadeVideo = Literal["boa", "media", "ruim"]

# RF-003: valgo dinâmico severo, pés mal posicionados em leg press ou qualquer
# ponto com prioridade "risco_lesao" levam obrigatoriamente a "inadequada".
# RF-004: execução correta não vira "inadequada" por um refinamento — só por erro
# moderado repetido ou por risco de lesão.
Veredito = Literal["adequada", "parcialmente_adequada", "inadequada"]

# RN-02: confiabilidade do veredito é limitada pelo que é observável no vídeo
# (ângulo de câmera, qualidade, partes ocultas) — não é uma nota de execução.
Confiabilidade = Literal["baixa", "media", "alta"]


# --------------------------------------------------------------------------- #
# bloco "o que melhorar"                                                      #
# --------------------------------------------------------------------------- #
class PontoMelhoria(BaseModel):
    """Um ponto a melhorar na execução — o lado "o que NÃO está" do retorno.

    Par obrigatório **o-que-não-está-ideal → como-ajustar**: todo ponto carrega
    ``observacao`` (o que dá para melhorar, observado no vídeo) E ``ajuste`` (a
    instrução acionável para corrigi-lo). Garante que a plataforma nunca aponte
    algo a melhorar sem dizer COMO — a UI renderiza os dois lado a lado
    (⚠ observação / ✅ ajuste).

    ``prioridade`` gradua a severidade num eixo contínuo:

    * ``refinamento`` — a execução já está boa; é um polimento opcional. NÃO
      rebaixa o veredito. É o que garante o feedback construtivo em execução boa.
    * ``leve`` — pequeno desvio, cabível numa execução ainda "adequada".
    * ``moderada`` — desvio técnico real; dois ou mais rebaixam para no máximo
      "parcialmente_adequada".
    * ``risco_lesao`` — padrão perigoso (valgo dinâmico severo, pés mal
      posicionados sob carga…); força ``veredito="inadequada"`` +
      ``risco_lesao=True`` e faz a narrativa (RN-01) abrir mandando interromper.
    """

    categoria: CategoriaMelhoria = Field(
        description="Categoria fechada do ponto (uma das 7 verificadas explicitamente, RF-002)."
    )
    observacao: str = Field(
        description="O QUE NÃO ESTÁ IDEAL: o que dá para melhorar, em linguagem de treinador "
        "PT-BR — concreta, nomeando a região do corpo e o que foi observado (sem jargão "
        "acadêmico). Aponta o ponto, não a solução (a solução vai em 'ajuste')."
    )
    ajuste: str = Field(
        description="COMO AJUSTAR: a instrução prática e acionável para corrigir ESTE ponto "
        "específico na próxima execução, em linguagem de treinador PT-BR (ex.: 'plante o pé "
        "inteiro na plataforma e mantenha o joelho apontando para a ponta do pé'). Sempre "
        "pareada com 'observacao' — todo ponto apontado precisa vir com o seu ajuste."
    )
    timestamp_s: float | None = Field(
        default=None, ge=0,
        description="Instante aproximado (em segundos) em que o ponto ocorre no vídeo.",
    )
    prioridade: Prioridade = Field(
        description="Severidade graduada: refinamento (polimento, não rebaixa veredito) | leve | "
        "moderada | risco_lesao (aciona veredito 'inadequada')."
    )


# --------------------------------------------------------------------------- #
# CHAMADA 1 — vídeo → JSON estruturado                                        #
# --------------------------------------------------------------------------- #
class AcademiaAnalysis(BaseModel):
    """Schema estrito (``response_schema``) da chamada 1 — análise técnica do exercício.

    Retorno SEMPRE balanceado (RF-008): ``pontos_fortes`` (o que está bom) +
    ``pontos_a_melhorar`` (o que melhorar) + ``feedback_ideal`` (a síntese
    construtiva). ``veredito`` e ``risco_lesao`` são o núcleo da calibragem: um
    ponto de ``prioridade="risco_lesao"`` (RF-003) força ``veredito="inadequada"``
    e ``risco_lesao=True``, independente de quantos acertos existam. Execução
    correta (RF-004) não ganha erro grave inventado — mas ``pontos_a_melhorar``
    ainda traz ao menos um ``refinamento``, então o lado "o que melhorar" nunca
    fica vazio numa execução real.
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
        "'inadequada' é obrigatório quando há ponto com prioridade 'risco_lesao' (RF-003)."
    )
    confiabilidade: Confiabilidade = Field(
        description="Confiança no veredito, dada a qualidade do vídeo/ângulo/partes ocultas."
    )
    pontos_fortes: list[str] = Field(
        default_factory=list,
        description="O QUE ESTÁ BOM: pontos tecnicamente corretos e de fato observados na "
        "execução, em linguagem de treinador PT-BR (cada um com lastro visual concreto, RF-004).",
    )
    pontos_a_melhorar: list[PontoMelhoria] = Field(
        default_factory=list,
        description="O QUE MELHORAR: pares observacao→ajuste com prioridade graduada. Numa "
        "execução real quase nunca fica vazio — no mínimo traz um 'refinamento' (RF-004/RF-008).",
    )
    feedback_ideal: str = Field(
        description="FEEDBACK IDEAL: a síntese construtiva e positiva-para-frente da execução — "
        "reconhece o que já está bom e aponta o ajuste mais importante para a próxima série, "
        "em uma ou duas frases de treinador (espelha a seção 7 do gabarito escrito)."
    )
    risco_lesao: bool = Field(
        description="True se algum ponto observado tem prioridade 'risco_lesao' (RF-003) — nesse "
        "caso a narrativa deve orientar interromper/corrigir antes de qualquer outro conteúdo."
    )
    musculos_esperados: list[str] = Field(
        default_factory=list,
        description="Grupos musculares esperados para este exercício (informativo — não é medição de ativação, RN-03).",
    )
    observacoes: str | None = Field(
        default=None, description="Observações adicionais relevantes não cobertas pelos demais campos."
    )


# --------------------------------------------------------------------------- #
# modelo de saída da API                                                      #
# --------------------------------------------------------------------------- #
class AcademiaAnalysisResponse(BaseModel):
    """Payload final devolvido pelo endpoint ``POST /academia/analyze``.

    Espelha o shape de :class:`app.tennis.models.TennisAnalysisResponse`:
    métricas estruturadas + narrativa PT-BR (RF-007, calibrada por RN-01/RF-008) +
    áudio TTS opcional + warnings não-fatais (narrativa/áudio/persistência podem
    falhar sem derrubar a análise) + id de persistência opcional.
    """

    exercicio: str = Field(description="Nome do exercício identificado (espelha metrics.exercicio_identificado).")
    metrics: AcademiaAnalysis = Field(description="JSON estruturado da chamada 1.")
    narrative: str = Field(
        description="Narrativa PT-BR de treinador (chamada 2): retorno positivo e balanceado — "
        "o que está bom E o que melhorar (RF-008), com o erro/risco primeiro quando houver (RN-01), "
        "veredito, feedback ideal, limitações e encerramento motivador sóbrio (RF-007). "
        "Inclui disclaimer de que é relatório educacional, não substitui avaliação presencial (RN-05)."
    )
    audio_base64: str | None = Field(default=None, description="WAV base64 da narrativa (chamada 3), se solicitado e disponível.")
    warnings: list[str] = Field(default_factory=list, description="Falhas não-fatais (narrativa, áudio ou persistência).")
    persisted_id: str | None = Field(default=None, description="Id do registro persistido, se a persistência estiver habilitada.")
