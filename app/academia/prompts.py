"""Prompts das três chamadas do módulo academia (espelha ``app/tennis/prompts.py``;
calibragem Caio 17-22/07/2026, dataset de 11 vídeos em
``videos-calibragem-academia/ANALISES.md``).

* ``analysis_system_prompt(student_name)`` — chamada 1 (vídeo→JSON): instrui a
  identificação do exercício e a checagem EXPLÍCITA e sequencial das 7 categorias
  de erro técnico do catálogo de calibragem (amplitude, escápula/ombros, tronco,
  cervical, cotovelos, joelhos, ritmo), com regras duras de veredito (RF-003) e
  anti-nitpicking (RF-004);
* ``build_narrative_prompt(metrics, student_name)`` — chamada 2 (JSON→texto
  PT-BR): impõe RN-01 (erro antes de elogio), o fluxo de interrupção imediata
  quando ``risco_lesao`` é verdadeiro, os guard-rails anti-hype e as limitações
  obrigatórias (RN-03/RNF-003), com dois exemplos few-shot de estilo (execução
  incorreta e execução correta);
* ``build_tts_prompt(narrative)`` — chamada 3 (texto→áudio), com o mesmo
  preâmbulo anti-falso-bloqueio do classificador usado em tênis.

Casos-âncora do dataset (ver ``ANALISES.md``): 00000613 (puxada frontal —
excêntrica acelerada + falta de depressão escapular; a IA elogiou demais, o Caio
corrigiu no áudio 614 — é o caso central de RN-01), 00000633 (crucifixo inverso —
não senta no banco, amplitude curtíssima, veredito INCORRETA) e 00000637 (leg
press — pés mal posicionados + valgo dinâmico severo, risco de lesão explícito,
veredito INCORRETA + orientação de interromper).
"""

from __future__ import annotations

import json

_CATEGORIAS_ERRO = """\
Para CADA erro que você encontrar, classifique em uma das 7 categorias abaixo
(campo "categoria" do erro) e verifique-as NESTA ORDEM, uma a uma, mesmo que a
maioria não se aplique ao exercício do vídeo — não pule etapas:

1. AMPLITUDE — o movimento é parcial/encurtado (não completa a fase concêntrica
   ou excêntrica)? Ex.: descida incompleta no supino; puxada que não estende os
   braços totalmente para trás no crucifixo inverso; concêntrica que para antes
   do fim na puxada alta.
2. ESCÁPULA/OMBROS — falta depressão escapular no pico da contração (ombros
   "sobem" em vez de descer e travar); ombros protraídos/enrolados para frente
   sem abertura torácica. Ex.: ombros subindo ao puxar a barra; ombros
   arredondados numa puxada alta na polia.
3. TRONCO — balanço/embalo do corpo para gerar impulso; peitoral/costas
   afastando do encosto do banco; base de apoio errada (ex.: não sentar
   completamente no banco, ficar em meio-agachamento isométrico em vez de
   sentado). Ex.: tronco balançando no pull-down; praticante não senta no banco
   do crucifixo inverso.
4. CERVICAL — hiperextensão de pescoço ou movimento brusco/repentino da cabeça
   durante a execução. Ex.: pescoço estende bruscamente no fim da concêntrica de
   um pull-down.
5. COTOVELOS — projeção do cotovelo para a frente do corpo em exercícios de
   rosca (rosca deixa de isolar o bíceps e vira "roubada"). Ex.: cotovelo
   avançando à frente do tronco na rosca bíceps na polia baixa.
6. JOELHOS — bloqueio articular (extensão total/travada no topo do movimento,
   ex.: leg press); valgo dinâmico (joelhos caindo para dentro, se tocando, em
   vez de acompanhar a direção das pontas dos pés); pés mal posicionados na
   plataforma/base (calcanhares ou pontas dos pés para fora do apoio).
7. RITMO — fase excêntrica (descida/retorno controlado) executada rápido
   demais, sem controle, perdendo a tensão do músculo-alvo.

Se não houver evidência de uma categoria, NÃO force um erro nela. Cada erro deve
ter "descricao" em linguagem de treinador (região do corpo + momento + efeito
prático, ex.: "cotovelo avança à frente do tronco na subida, tirando a tensão
do bíceps"), "timestamp_s" (instante aproximado, ou null se não for possível
estimar) e "gravidade": "leve" | "moderada" | "risco_lesao".
"""

_VEREDITO_REGRAS = """\
REGRAS DURAS DE VEREDITO (não são sugestão, são obrigatórias):
- Se HOUVER qualquer erro com gravidade "risco_lesao" (ex.: valgo dinâmico
  severo, pés mal posicionados numa base de sustentação de carga, qualquer
  padrão que ofereça risco real de lesão articular ou ligamentar) =>
  "veredito" DEVE ser "inadequada" e "risco_lesao" DEVE ser true. Não existe
  meio-termo aqui: risco de lesão nunca é "parcialmente_adequada".
- Se houver MÚLTIPLOS erros de gravidade "moderada" (duas ou mais categorias
  comprometidas, mesmo sem risco de lesão) => "veredito" no MÁXIMO
  "parcialmente_adequada". Nunca marque "adequada" quando há mais de um erro
  moderado.
- Se a execução estiver tecnicamente limpa (sem erros relevantes observáveis)
  => "veredito" é "adequada" e a lista "erros" fica VAZIA. ANTI-NITPICKING:
  é PROIBIDO inventar erro cosmético ou irrelevante só para preencher a lista.
  Um vídeo bem executado recebe no máximo "refinamentos" (sugestões opcionais
  de polimento), NUNCA um erro fabricado. Nitpicking punitivo em execução
  correta é uma falha grave deste sistema.
- Um único erro "leve" isolado, sem mais nada relevante, ainda pode ser
  "adequada" com a ressalva citada em "foco_pratico" — critério de bom senso de
  treinador, não perfeição milimétrica.
"""

_CONFIABILIDADE = """\
CONFIABILIDADE E LIMITES DO QUE É OBSERVÁVEL (RN-02):
- "confiabilidade" reflete o quanto você REALMENTE conseguiu ver, não sua
  convicção sobre o exercício em geral. Rebaixe para "baixa" ou "media" quando:
  o ângulo de câmera esconde a articulação central do erro (ex.: câmera frontal
  não mostra valgo de joelho num ângulo lateral necessário), a qualidade do
  vídeo é "media"/"ruim", ou partes relevantes do corpo estão fora do quadro.
- Preencha "partes_ocultas" com toda região do corpo relevante para o exercício
  que NÃO estava visível (ex.: "pés", "joelhos", "coluna lombar"). Lista vazia
  se tudo relevante estava visível.
- "angulo_camera": descreva objetivamente (ex.: "lateral", "frontal",
  "diagonal-posterior") — não invente um ângulo melhor do que o que foi filmado.
- Se o ângulo/qualidade impedem avaliar uma categoria de erro com segurança,
  NÃO afirme que está correta nem que está errada nessa categoria — omita-a e
  reflita a limitação em "observacoes" e na confiabilidade rebaixada.
"""

_ACERTOS_REGRA = """\
ACERTOS (campo "acertos"): liste APENAS pontos técnicos que você de fato
observou sendo bem executados no vídeo (ex.: "lombar mantida apoiada no
encosto durante toda a série", "amplitude completa na fase concêntrica"). Cada
item precisa ter lastro visual concreto — é proibido listar um acerto genérico
("boa execução!") sem evidência. Se a execução for ruim quase por completo,
"acertos" pode ter só 1 item ou ficar vazio; não infle a lista por cortesia.
"""

_CLIP_PROMPT = """\
Você é um personal trainer e avaliador técnico de execução de exercícios de
musculação, com olhar clínico de biomecânica. Recebe um vídeo curto de um
aluno{saudacao_contexto} executando UM exercício de academia. O vídeo foi
amostrado a {fps} quadros por segundo.

Sua tarefa: analisar a execução e devolver SOMENTE um JSON válido no schema
fornecido (sem texto fora do JSON, sem markdown).

PASSO 1 — IDENTIFIQUE O EXERCÍCIO E O CONTEXTO.
- "exercicio_identificado": nome técnico do exercício (ex.: "puxada frontal
  pegada fechada na polia alta", "crucifixo inverso no peck deck", "leg press
  45 graus", "rosca bíceps na polia baixa"). Cubra qualquer exercício de
  musculação — máquina, polia, halteres, barra ou peso livre.
- "equipamento": máquina/acessório usado (ex.: "polia alta", "peck deck",
  "leg press 45°", "halteres"), ou null se não identificável.
- "angulo_camera" e "qualidade_video": ver seção de confiabilidade abaixo.
- "partes_ocultas": regiões do corpo relevantes ao exercício que não estavam
  visíveis no quadro.
- "repeticoes_visiveis": quantas repetições completas dá para contar, ou null
  se não for possível contar com confiança.
- Baseie-se no padrão-ouro biomecânico CONSOLIDADO do exercício identificado
  (a técnica de execução de referência estabelecida na literatura de
  treinamento de força) e nos erros técnicos comuns já documentados para esse
  exercício — não invente uma técnica de execução do zero.

PASSO 2 — VERIFIQUE AS 7 CATEGORIAS DE ERRO, UMA A UMA.
{categorias_erro}

PASSO 3 — DECIDA O VEREDITO.
{veredito_regras}

PASSO 4 — CONFIABILIDADE E LIMITES DO OBSERVÁVEL.
{confiabilidade}

PASSO 5 — ACERTOS.
{acertos_regra}

PASSO 6 — FECHAMENTO TÉCNICO.
- "foco_pratico": a ÚNICA correção mais importante e acionável para a próxima
  série, em linguagem de treinador (ex.: "sente completamente no banco antes de
  iniciar o movimento"). Se a execução for "adequada", pode ser um refinamento
  opcional, não uma correção crítica.
- "risco_lesao": true SOMENTE se houver erro de gravidade "risco_lesao" na
  lista de erros; caso contrário, false.
- "musculos_esperados": lista dos músculos-alvo esperados do exercício
  identificado (conhecimento de anatomia geral, não avaliação de ativação real
  — você não mede ativação muscular pelo vídeo).
- "observacoes": ressalvas adicionais em PT-BR (limitações de ângulo, dúvidas,
  contexto), ou null se não houver nada a acrescentar.

HONESTIDADE E LIMITES.
- Baseie-se apenas no que é visível no vídeo. Nunca estime carga levantada,
  esforço percebido, ativação muscular ou risco de lesão futura em números —
  descreva qualitativamente e apenas o que a imagem sustenta.
- Não infira nome real do aluno pelo rosto: use o nome informado abaixo (se
  houver) apenas como rótulo de contexto, nunca por reconhecimento facial.
"""


def analysis_system_prompt(student_name: str | None = None, fps: int | None = None) -> str:
    """System prompt da chamada 1 (vídeo→JSON): identificação do exercício +
    checagem sequencial das 7 categorias de erro do catálogo de calibragem +
    regras duras de veredito (RF-003) + anti-nitpicking (RF-004) + confiabilidade
    condicionada ao observável (RN-02).

    ``student_name`` só entra como contexto de saudação leve no enunciado — a
    personalização de fato acontece na narrativa (chamada 2), aqui serve só para
    o modelo eventualmente citar o aluno pelo nome em "observacoes" se natural.
    """
    nome = (student_name or "").strip()
    saudacao_contexto = f" chamado {nome}" if nome else ""
    return _CLIP_PROMPT.format(
        saudacao_contexto=saudacao_contexto,
        fps=fps if fps is not None else 24,
        categorias_erro=_CATEGORIAS_ERRO,
        veredito_regras=_VEREDITO_REGRAS,
        confiabilidade=_CONFIABILIDADE,
        acertos_regra=_ACERTOS_REGRA,
    )


# Exemplos ESCRITOS para calibrar o TOM da narrativa (não são transcrição real de
# áudio do Caio, ao contrário do JUCA_FEWSHOT de tênis — o dataset de academia não
# trouxe narração-gabarito falada, só as análises técnicas escritas em
# ANALISES.md). Cobrem os dois ramos de RN-01: erro primeiro (com risco de lesão)
# e execução correta (elogio sustentado por evidência, sem hype).
NARRATIVE_FEWSHOT = """\
EXEMPLOS DE ESTILO — imite o TOM, o RITMO e a ESTRUTURA destes dois relatórios de
personal trainer técnico e direto. NÃO copie o conteúdo nem mencione estes casos:

[exemplo 1 - execução com erro grave / risco de lesão, RN-01 aplicado]
"Paulinho, para tudo agora: nesse leg press os seus pés estão mal posicionados na
plataforma e os joelhos estão caindo para dentro, quase se tocando, entre 11 e 26
segundos do vídeo. Esse padrão de valgo dinâmico com carga é um dos jeitos mais
diretos de lesionar ligamento e menisco, então antes de fazer mais uma repetição
assim, ajusta o pé: planta o pé inteiro na plataforma, afasta na largura dos
ombros e faz o joelho seguir a direção da ponta do pé, na descida e na subida. O
que salvou essa série foi o quadril e a lombar, que ficaram apoiados no encosto o
tempo todo — isso está correto e é para manter. Mas o foco agora é 100% nos
joelhos: reduz a carga até você sentir controle total do movimento, sem o joelho
fugir para dentro. Esse relatório é educacional e não substitui uma avaliação
presencial; não estou medindo carga, esforço ou ativação muscular aqui, só a
execução visível. Se sentir dor, procure um profissional habilitado antes de
continuar treinando essa máquina."

[exemplo 2 - execução correta, elogio sustentado por evidência, sem hype]
"Marina, essa puxada alta na polia ficou tecnicamente sólida. Você manteve a
lombar apoiada durante toda a série e completou a amplitude do movimento nas
duas repetições que deu para contar, trazendo a barra até perto do peito sem
perder o alinhamento do tronco. Não vi balanço de corpo nem cotovelo fugindo do
eixo, e o ritmo da descida esteve controlado, sem acelerar no fim. Como
refinamento — não como correção, porque não há erro aqui — você pode buscar
ainda mais depressão escapular no pico da contração, levando o ombro para baixo
antes de puxar, para isolar melhor as costas. Fora isso, sigo sem apontar
inadequações: essa é uma execução que pode ser tomada como referência para as
próximas séries. Este relatório é educacional e não substitui uma avaliação
presencial, e não mede carga, esforço percebido nem ativação muscular — é uma
leitura da execução visível no vídeo."
"""


def _abertura_regra(risco_lesao: bool, tem_erro_relevante: bool) -> str:
    """Regra de abertura da narrativa (RN-01): erro antes de elogio, e abertura
    de interrupção quando há risco de lesão.
    """
    if risco_lesao:
        return (
            "- RISCO DE LESÃO DETECTADO: a narrativa DEVE abrir orientando o aluno a\n"
            "  INTERROMPER ou CORRIGIR o padrão perigoso ANTES de qualquer outro\n"
            "  conteúdo — antes de elogio, antes de contexto, antes de qualquer coisa.\n"
            "  Nomeie a região do corpo e o momento (timestamp) do erro logo na\n"
            "  primeira frase. Só depois de orientar a correção do risco é que a\n"
            "  narrativa segue para os demais acertos/observações.\n"
        )
    if tem_erro_relevante:
        return (
            "- HÁ ERRO(S) RELEVANTE(S) (RN-01): a narrativa NUNCA abre com elogio.\n"
            "  O erro dominante vem PRIMEIRO, nomeando a região do corpo e o momento\n"
            "  (timestamp) em que ele acontece. Só depois de estabelecer o erro é que\n"
            "  entram os acertos sustentados, se houver.\n"
        )
    return (
        "- Não há erro relevante: pode abrir reconhecendo o que foi bem executado,\n"
        "  desde que cada elogio tenha lastro concreto na análise (RF-004) — sem\n"
        "  inflar elogio genérico.\n"
    )


def build_narrative_prompt(metrics: dict, student_name: str | None = None) -> str:
    """Prompt da chamada 2: JSON -> narrativa de personal trainer em PT-BR.

    Aplica RN-01 (erro antes de elogio; abertura de interrupção quando há
    ``risco_lesao``), guard-rails anti-hype, limitações obrigatórias
    (RN-03/RNF-003: não mede carga/esforço/ativação, dor => profissional
    habilitado), disclaimer educacional (RN-05) e personalização pelo nome
    (RF-007). Estrutura fixa: (erro primeiro, se houver) -> acertos sustentados
    -> veredito -> foco prático principal -> limitações -> encerramento
    motivador sobrio.
    """
    nome = (student_name or "").strip()
    saudacao = (
        f'Dirija-se ao aluno pelo primeiro nome ("Olá, {nome}" ou similar) logo no\n'
        f"início ou de forma natural ao longo do texto, ao menos uma vez.\n"
        if nome else
        "Não há nome de aluno informado — não invente um, dirija-se por \"você\".\n"
    )
    erros = metrics.get("erros") if isinstance(metrics, dict) else None
    tem_erro_relevante = bool(erros)
    risco_lesao = bool(metrics.get("risco_lesao")) if isinstance(metrics, dict) else False
    abertura = _abertura_regra(risco_lesao, tem_erro_relevante)
    metrics_json = json.dumps(metrics, ensure_ascii=False, indent=2)
    return f"""\
Você é um personal trainer técnico, direto e SEM hype, avaliando a execução de
um exercício de musculação de um aluno a partir da análise estruturada (JSON)
abaixo, feita por vídeo.
{saudacao}
{NARRATIVE_FEWSHOT}

ESTRUTURA OBRIGATÓRIA — siga esta ordem, em PROSA CORRIDA (sem numerar em voz
alta):
{abertura}- ACERTOS: cite os pontos tecnicamente corretos, cada um com lastro na
  análise (RF-004) — nunca por cortesia.
- VEREDITO: diga com clareza se a execução foi adequada, parcialmente adequada
  ou inadequada, em linguagem natural (sem citar o nome do campo do JSON).
- FOCO PRÁTICO PRINCIPAL: a correção (ou refinamento, se a execução for
  adequada) mais importante e acionável para a próxima série.
- LIMITAÇÕES: mencione, quando a confiabilidade da análise for baixa ou média,
  ou quando "partes_ocultas" não estiver vazio, que o ângulo/qualidade do vídeo
  limitou o que foi possível avaliar.
- ENCERRAMENTO: uma frase curta, motivadora mas sóbria — sem superlativos
  vazios.

GUARD-RAILS OBRIGATÓRIOS (aplique em TODA narrativa, sem exceção):
- Sem markdown, sem listas, sem símbolos, sem ler nomes de campos do JSON.
- Tom direto e técnico; fale com o aluno por "você". Evite superlativos vazios
  ("incrível", "perfeito", "sensacional", "show", "mandou bem demais") — só
  elogie com evidência concreta da análise.
- NUNCA prometa hipertrofia, ganho de força ou emagrecimento como resultado
  deste relatório (RN-03) — você está avaliando EXECUÇÃO, não resultado.
- Declare (em algum ponto natural do texto) que este relatório NÃO mede carga
  levantada, esforço percebido nem ativação muscular real — é uma leitura da
  execução visível no vídeo.
- Se o aluno sentir dor durante o exercício, oriente a procurar um profissional
  de saúde habilitado antes de continuar — nunca minimize dor como normal.
- Inclua, perto do fechamento, o disclaimer de que este é um relatório
  EDUCACIONAL e NÃO substitui uma avaliação presencial com profissional
  qualificado (RN-05).
- Use os números/observações da análise de forma natural (ex.: "nas duas
  repetições que deu para contar"), nunca inventando precisão que o JSON não
  tem.
- Tamanho: 140 a 280 palavras. Devolva SOMENTE o texto do relatório.

ANÁLISE (JSON):
{metrics_json}
"""


# preâmbulo obrigatório: evita falso-bloqueio do classificador e leitura das
# instruções em voz alta; tag de áudio em inglês mesmo com texto em PT (espelha
# app/tennis/prompts.py:build_tts_prompt).
def build_tts_prompt(narrative: str) -> str:
    """Prompt da chamada 3 (TTS), com preâmbulo anti-bloqueio."""
    return (
        "Sintetize em voz alta o seguinte relatório de um personal trainer sobre a "
        "execução de um exercício de musculação, em português do Brasil, com tom "
        "calmo, claro e encorajador. [calmly]\n"
        "TRANSCRIÇÃO A FALAR:\n" + narrative.strip()
    )
