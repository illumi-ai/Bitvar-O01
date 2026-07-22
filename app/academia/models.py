"""Schemas Pydantic da academia — fonte única de verdade.

São usados em dois papéis, mesmo padrão do :mod:`app.tennis.models`:

1. como ``response_schema`` da chamada 1 ao Gemini (vídeo → JSON estruturado);
2. para validar/normalizar o JSON que volta antes de alimentar as duas saídas
   seguintes (narrativa PT-BR e áudio TTS).

Contrato de schema fixo (nomes de campo travados, não renomear):

* :class:`ErroTecnico` — um erro técnico pontual detectado no exercício.
* :class:`AcademiaAnalysis` — saída bruta da chamada 1 (vídeo → JSON).
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
    narrative: str = Field(
        description="Narrativa PT-BR de treinador (chamada 2): erros primeiro se houver (RN-01), "
        "depois acertos, veredito, foco prático, limitações e encerramento motivador sóbrio (RF-007). "
        "Inclui disclaimer de que é relatório educacional, não substitui avaliação presencial (RN-05)."
    )
    audio_base64: str | None = Field(default=None, description="WAV base64 da narrativa (chamada 3), se solicitado e disponível.")
    warnings: list[str] = Field(default_factory=list, description="Falhas não-fatais (narrativa, áudio ou persistência).")
    persisted_id: str | None = Field(default=None, description="Id do registro persistido, se a persistência estiver habilitada.")
