# BitVar IA · Academia — musculação com identificação automática

Este documento descreve a vertical `/academia` implementada a partir do mesmo
padrão operacional de `/tennis`, mas com modelos, prompts, parâmetros,
persistência e eventos próprios. O produto recebe o vídeo de uma série de
musculação e identifica automaticamente exercício, variação e
equipamento/máquina; o usuário não seleciona o exercício.

A identificação cobre musculação em geral e separa dois escopos. Agachamento
livre usa a metodologia específica **`squat_poc_v1`**, com seu checklist e
indicador numérico provisório. Outras 14 famílias canônicas reconhecidas usam
**`general_execution_observational_v1`**, uma análise observacional geral da
execução, sem emprestar critérios ou score do agachamento. Ela descreve somente
o que o vídeo sustenta sobre ritmo, amplitude, trajetória, estabilidade,
alinhamento visível, consistência e interação com o equipamento, apresentando
classificação e confiabilidade em vez de nota numérica.

Quando a família é `other`, não existe rota segura e a resposta usa
`unsupported_exercise`. Conteúdo inconclusivo, misto ou com baixa confiança usa
`exercise_unknown`. A vertical é uma prova de conceito de feedback visual e não
um dispositivo médico, laudo clínico, prescrição de treino, medição de ativação
muscular ou garantia de prevenção de lesão, eficácia ou performance futura.

As fontes de produto são
[`PROBLEMA-pivo-academia.md`](PROBLEMA-pivo-academia.md) e
[`Requisitos-pivo-academia.md`](Requisitos-pivo-academia.md). A metodologia
`squat_poc_v1`, seus pesos/faixas e a classificação/confiabilidade de
`general_execution_observational_v1` são deliberadamente provisórios: ainda
precisam ser revisados por profissional habilitado e calibrados contra vídeos
rotulados. **Não existe, nesta fase, evidência que sustente alegar 95% de
acerto.**

## Escopo da POC

- página web isolada para enviar uma série de musculação de até três minutos,
  sem caixa de seleção de exercício;
- passe genérico para identificar exercício, variação, equipamento/máquina,
  intervalo ativo e rastreabilidade da pessoa-alvo;
- descrição opcional da pessoa-alvo por texto ou microfone, com transcrição
  temporária via Gemini e revisão do texto antes da análise;
- roteamento local da família identificada para uma metodologia técnica
  registrada, sem confiar no rótulo livre do modelo;
- para agachamento suportado, análise temporal estruturada de repetições e fases
  do movimento, checklist, score, relatório construtivo em PT-BR e áudio WAV
  opcional;
- para 14 famílias reconhecidas sem perfil biomecânico próprio, análise
  observacional geral no intervalo ativo a `8 fps`, com oito critérios,
  classificação da execução e confiabilidade/cobertura, sem nota numérica;
- para família `other`, resposta `unsupported_exercise`, sem checklist ou
  correção emprestada de outro exercício;
- para identificação inconclusiva, resposta `exercise_unknown`, também sem
  avaliação técnica;
- exportação local e persistência administrativa opcional, sem armazenar o vídeo bruto;
- recusa explícita de uma captura que não permita avaliação responsável.

Ficam fora do MVP: metodologias biomecânicas específicas e validadas para
exercícios além do agachamento, aferição de carga ou esforço, prescrição,
diagnóstico, previsão de adaptação/resultado, autenticação/ownership de usuário
final, cobrança e comparação longitudinal publicada ao usuário. Vídeos com
várias pessoas são aceitos somente quando a pessoa-alvo permanece inequívoca e
rastreável, usando as descrições opcionais do formulário; isso não equivale a
autenticação ou reconhecimento facial. O Bearer do histórico é uma proteção
administrativa da POC, não um sistema de contas.

## Pipeline e três saídas

Antes do upload do vídeo, a UI pode executar este fluxo auxiliar para preencher
as características e a posição da pessoa-alvo:

```text
MediaRecorder no navegador (até 30 s / 8 MB)
  → POST /academia/transcribe-target com consent=true
  → validação de MIME/tamanho e leitura antecipada de duração com ffprobe
  → FFmpeg: primeira faixa → WAV PCM 16-bit, mono, 16 kHz
  → duração real conferida novamente pelos frames do WAV normalizado
  → Gemini (gemini-3.5-flash por padrão), áudio inline → JSON estrito
  → higienização/limite local de 500 caracteres
  → texto inserido no formulário para revisão
  → remoção dos arquivos temporários original e normalizado
```

Esse fluxo **não usa a Files API** e não cria registro no Postgres. O áudio
normalizado é enviado diretamente na chamada `generate_content`; apenas o texto
que permanecer no campo `practitioner_notes` é enviado depois com o vídeo. O
usuário pode editar ou apagar a transcrição e também pode ignorar o microfone e
digitar normalmente.

O pipeline principal continua independente:

```text
upload multipart, salvo em chunks em arquivo temporário
  → validação de tamanho, assinatura do contêiner e duração
  → chamada 1: vídeo a 2 fps/LOW → exercício, variação, equipamento,
      intervalo ativo e rastreabilidade da pessoa-alvo em schema estrito
  → taxonomia local resolve a família, sem usar rótulo livre para escolher perfil
       ├─ inconclusiva/mista: exercise_unknown, sem checklist ou score
       ├─ família other: unsupported_exercise, sem avaliação técnica
       ├─ agachamento: perfil squat_poc_v1 + análise específica
       └─ 14 famílias conhecidas: general_execution_observational_v1
  → intervalo ativo identificado → análise técnica a 8 fps/HIGH
  → chamada 2: recorte lógico ativo → gate, movimento e segmentos em schema estrito
  → guarda preliminar em Python valida captura e âncoras temporais
       ├─ captura insuficiente: pula o checklist e cria 8 itens nao_avaliavel
       └─ captura suficiente:
            → chamada 3: mesmo intervalo ativo → checklist em um segundo schema estrito
  → materialização local do contrato público + gate/status final
       ├─ squat_poc_v1: score POC determinístico
       └─ geral: classificação + confiabilidade/cobertura, sem score numérico
  → reconstrução local da prosa estruturada publicável
       ├─ analysis_status=recapture_required: relatório local de recaptura
       └─ captura suficiente:
            → chamada 4: JSON → relatório acessível em PT-BR
            → chamada 5 opcional: relatório → áudio WAV
  → persistência best-effort somente com opt-in duplo, nunca do vídeo bruto
  → remoção do arquivo temporário local e tentativa auditada de remoção remota
```

Toda requisição bem-sucedida expõe primeiro a identificação estruturada. Quando
uma rota específica ou geral foi selecionada com segurança, a resposta pode
conter estas três saídas:

1. **métricas estruturadas** — captura, movimento, repetições/fases, critérios,
   prioridade de melhoria e, conforme o escopo, score específico ou
   classificação/confiabilidade geral;
2. **relatório PT-BR** — texto construtivo e compreensível por praticante ou
   personal;
3. **áudio opcional** — WAV da mesma narrativa, usando a voz configurada.

O transporte da Files API, a espera pelo estado `ACTIVE`, a conversão PCM para
WAV e os retries de TTS reutilizam componentes já testados de tênis. O mesmo
arquivo remoto é reutilizado nos passes autorizados e removido no bloco de
limpeza final; o arquivo local também é temporário e removido ao fim da
requisição. Os prompts de academia são independentes: nenhum benchmark, peso ou
regra de tênis entra na identificação, na avaliação específica de agachamento
ou na análise observacional geral.

| Componente | Responsabilidade |
|---|---|
| `app/academia/config.py` | env, modelos, limites e persistência |
| `models.py` | schemas estruturados e envelope HTTP |
| `profiles.py` | taxonomia local, perfis específico/gerais, critérios, pesos e guia de captura |
| `prompts.py` | identificação genérica, análise técnica, narrativa e TTS |
| `routing.py` | normalização de ângulo e metadados da rota técnica |
| `media.py` | assinatura do vídeo e duração |
| `audio.py` | validação, duração e normalização segura da gravação curta |
| `scoring.py` | gate/status e score determinístico |
| `gemini.py` | transcrição inline, passe de identificação, passes específicos/gerais no intervalo ativo, materialização, narrativa/TTS e transporte compartilhado |
| `service.py` | orquestração, limites, limpeza e degradação graciosa |
| `store.py` | Postgres opcional, sem vídeo bruto |
| `router.py` | UI, transcrição, análise, histórico, áudio e exportação |
| `app/static/academia/index.html` | POC web sem build |

## Endpoints

### `GET /academia/`

Entrega a página da POC. A própria página apresenta as orientações de captura,
envia o formulário e renderiza as três saídas sem outra requisição HTTP de
análise pelo navegador. Não há caixa de seleção de exercício.

### `GET /academia/health`

Informa se a chave do Gemini está configurada, os modelos, limite de três
minutos, `identification_mode="automatic"`, capacidades de identificação e as
amostragens de identificação/análise técnica. A capacidade
`general_execution_analysis` indica a disponibilidade da rota observacional
geral. A lista `profiles` enumera os perfis específicos registrados, suas
versões/status, orientações de captura e número recomendado de repetições
derivado do perfil principal. O objeto
`voice_transcription` informa disponibilidade, modelo e limites públicos da
gravação curta. A chave nunca é retornada.

### `POST /academia/transcribe-target`

Recebe `multipart/form-data` para preencher a descrição opcional da
pessoa-alvo:

- `audio` — gravação curta obrigatória, produzida pelo `MediaRecorder`;
- `consent` — deve ser `true`, confirmando o processamento temporário da voz.

A UI exige o mesmo checkbox de autorização usado no envio do vídeo antes de
abrir o microfone. A gravação para automaticamente no limite configurado
(`30 s` por padrão), pode ser encerrada antes pelo usuário e é limitada a
`8 MB`. WebM/Opus, Ogg/Opus e MP4/AAC são os formatos usuais do navegador; o
backend também aceita os MIME de áudio listados em `app/academia/audio.py`, mas
sempre exige uma faixa decodificável e não confia só no nome do arquivo.

O servidor afere a duração, remove vídeo/metadados e normaliza somente a
primeira faixa de áudio para WAV PCM `s16le`, mono, `16 kHz`. Esse WAV é enviado
inline ao modelo `ACADEMIA_TRANSCRIPTION_MODEL`, sem upload ou polling na Files
API. O prompt pede transcrição literal em PT-BR, trata a fala como dado não
confiável e proíbe executar comandos, responder perguntas, resumir ou inferir
características.

Resposta de sucesso:

```json
{
  "ok": true,
  "transcript": "camiseta azul, shorts preto, pessoa à esquerda",
  "duration_seconds": 4.238,
  "truncated": false
}
```

O texto é higienizado, limitado a `500` caracteres e acrescentado ao campo
editável `practitioner_notes`. A análise do vídeo não começa automaticamente:
o usuário revisa, corrige ou apaga a transcrição antes de continuar. A resposta
usa `Cache-Control: private, no-store` e `Pragma: no-cache`.

Erros esperados:

- `400`: gravação vazia;
- `413`: corpo/arquivo acima do limite ou áudio mais longo que o permitido;
- `415`: MIME não aceito, contêiner inválido ou faixa de áudio não decodificável;
- `422`: campo obrigatório/consentimento ausente ou nenhuma fala inteligível
  detectada;
- `429`: slots locais de transcrição ocupados, com `Retry-After: 5`;
- `502`: falha temporária do Gemini;
- `503`: chave ausente ou FFmpeg/ffprobe indisponíveis.

Falhas não bloqueiam o formulário: a pessoa pode gravar novamente ou digitar a
descrição. O microfone depende de contexto seguro e das APIs
`getUserMedia`/`MediaRecorder`; navegadores sem esse suporte preservam somente o
campo de texto.

Cancelar a operação, sair da página ou abortar o `fetch` interrompe a espera e
descarta o resultado na interface, mas não constitui um protocolo de
cancelamento do trabalho no backend. Se a requisição já tiver sido recebida, o
servidor pode concluir a aferição, a normalização e a chamada de transcrição
antes de executar a limpeza. Mesmo nesse caso, o áudio original e o WAV
normalizado continuam sendo arquivos temporários: são removidos ao término da
rotina e nunca são gravados no Postgres.

A rota tem proteções próprias antes do parser multipart: teto ASGI/proxy de
`9 MiB` para comportar `8 MiB` de áudio mais o envelope, até quatro requisições
em voo e rate limit no proxy de seis requisições por minuto por IP, com burst
três. O serviço ainda reconta os bytes do arquivo e limita transcrições
simultâneas por processo.

### `POST /academia/analyze`

Recebe `multipart/form-data` com os seguintes campos:

- `file` — vídeo obrigatório;
- `capture_angle` — `unknown`, `frontal`, `lateral`, `posterior` ou `diagonal`;
- `duration_seconds` — metadado opcional de UX do navegador; nunca autoriza o
  upload nem substitui a duração aferida no servidor;
- `practitioner_name` e `practitioner_id` — rótulos opcionais, com até 120
  caracteres;
- `practitioner_outfit` e `practitioner_notes` — dicas visuais opcionais para
  manter a mesma pessoa-alvo; são dados não confiáveis e nunca instruções ao
  modelo, com até 240 e 500 caracteres, respectivamente;
- `with_audio` — gera a terceira saída quando verdadeiro e uma rota de análise
  específica ou geral foi selecionada;
- `persist` — opt-in explícito desta análise. Só grava com `persist=true` **e**
  `ACADEMIA_PERSIST=true` **e** `ACADEMIA_HISTORY_TOKEN` configurado; o cliente
  não consegue habilitar persistência desligada no servidor. Quando a
  capacidade está ativa, o próprio `POST` também exige
  `Authorization: Bearer <ACADEMIA_HISTORY_TOKEN>`;
- `consent` — deve ser `true`; o backend recusa o processamento corporal sem
  esta confirmação.

Não existe campo `exercise`: exercício, variação e equipamento/máquina são
identificados automaticamente. `practitioner_outfit` e `practitioner_notes`
ajudam a distinguir a pessoa-alvo quando outras pessoas aparecem, mas não são
comandos para o modelo, não acionam reconhecimento facial e não substituem a
exigência de continuidade visual.

A resposta sempre inclui `identification`. Quando existe rota de análise,
também reúne perfil/escopo, métricas estruturadas, narrativa, áudio em base64
quando solicitado, avisos não fatais e o identificador persistido somente
quando banco, configuração e Bearer administrativo estiverem disponíveis.

```json
{
  "ok": true,
  "analysis_status": "complete | limited | recapture_required | unsupported_exercise | exercise_unknown",
  "identification": {
    "status": "identified | unknown | mixed | no_exercise",
    "exercise_family": "squat | leg_press | ... | unknown",
    "exercise_label": "Agachamento",
    "variation": "Agachamento livre",
    "confidence": "baixa | media | alta",
    "equipment": {
      "category": "barbell",
      "name": "barra"
    },
    "target": {
      "status": "tracked | ambiguous | not_found",
      "multiple_people_visible": true
    },
    "multiple_exercises_visible": false,
    "active_interval": {"start_s": 2.1, "end_s": 28.4},
    "profile_slug": "squat",
    "methodology_available": true,
    "methodology_scope": "exercise_specific",
    "reason": "supported"
  },
  "route": {
    "exercise": "squat",
    "methodology_version": "squat_poc_v1",
    "methodology_status": "poc_unvalidated",
    "methodology_scope": "exercise_specific",
    "capture_angle": "frontal | lateral | posterior | diagonal | unknown",
    "fps": 8,
    "media_resolution": "MEDIA_RESOLUTION_HIGH"
  },
  "practitioner": null,
  "metrics": {
    "capture_quality": {},
    "movement": {},
    "repetitions": [],
    "checklist": [
      {
        "id": "knee_tracking",
        "muscle_context": "Contexto educacional versionado da metodologia, ou null."
      }
    ],
    "positive_points": [],
    "priority_improvement": null,
    "secondary_improvements": [],
    "limitations": [],
    "muscle_activation_notice": "O vídeo não mede ativação muscular diretamente.",
    "literature_references": [
      {
        "citation": "Bryanton et al. (2012), Effect of squat depth and barbell load on relative muscular effort in squatting.",
        "url": "https://doi.org/10.1519/JSC.0b013e31826791a7"
      }
    ],
    "weighted_execution_score": {}
  },
  "narrative": null,
  "audio_base64": null,
  "audio_mime": "audio/wav",
  "warnings": [],
  "persisted_id": null
}
```

O exemplo mostra a forma de uma análise suportada, não valores completos.
Para uma das 14 famílias gerais, `identification.reason` é
`general_supported`, `methodology_scope` é `general_execution` e
`metrics.analysis_mode` também é `general_execution`. Nesse contrato:

- `movement` resume ritmo, consistência de amplitude/trajetória e duração
  observável das repetições;
- `checklist` materializa oito critérios com `adequado`, `a_corrigir`,
  `nao_avaliavel` ou `nao_aplicavel`;
- `execution_summary.classification` usa
  `adequada_ao_padrao_observado`, `parcialmente_adequada`,
  `necessita_ajustes` ou `nao_avaliavel`;
- `execution_summary.reliability` explicita nível, cobertura, critérios
  avaliados/aplicáveis e repetições completas;
- `training_relevance` descreve somente ênfases visíveis e lista o que não pode
  ser determinado pelo vídeo;
- `weighted_execution_score` é sempre `null`.

Para `unsupported_exercise` e `exercise_unknown`, `route`, `metrics` e
`audio_base64` são nulos; `narrative` contém apenas uma explicação local do
estado e orientação de nova captura quando cabível. A API não fabrica uma
avaliação usando critérios de outro exercício. O contrato autoritativo é
`AcademiaAnalysisResponse`/`ExerciseIdentification`/`SquatAnalysis`/
`GeneralExecutionAnalysis` em `app/academia/models.py`.

Status HTTP esperados:

- `400`: arquivo enviado sem nome/conteúdo; ausência do campo obrigatório é
  validada pelo FastAPI como `422`;
- `413`: arquivo ou duração acima dos limites;
- `415`: assinatura de contêiner não reconhecida como vídeo suportado;
- `422`: parâmetro de formulário inválido ou duração impossível de aferir com
  segurança no servidor;
- `429`: os slots locais de análise estão ocupados; a resposta inclui
  `Retry-After`;
- `502`: falha da dependência Gemini;
- `503`: `GEMINI_API_KEY` ausente ou histórico temporariamente indisponível.

Uma captura biomecanicamente insuficiente não é erro de transporte. Ela retorna
HTTP `200` com `analysis_status="recapture_required"`, razões e instruções de
recaptura, sem apresentar correções corporais como se fossem confiáveis.

A família `other`, ainda sem rota, retorna HTTP `200` com
`analysis_status="unsupported_exercise"` e a identificação estruturada.
Conteúdo inconclusivo, misto ou com baixa confiança retorna HTTP `200` com
`analysis_status="exercise_unknown"`. Esses estados não disparam os passes
técnicos, a narrativa generativa, TTS ou persistência; recebem somente uma
explicação local segura. As 14 famílias gerais, por outro lado, executam os
passes observacionais e podem terminar como `complete`, `limited` ou
`recapture_required`. Nenhum desses estados transforma a leitura visual em
análise clínica ou previsão de resultado.

### Histórico administrativo protegido e exportação

Todos os endpoints abaixo exigem `Authorization: Bearer <ACADEMIA_HISTORY_TOKEN>`.
Sem token configurado, o histórico não é publicado (`404`); credencial ausente
ou incorreta recebe `401`. As respostas usam `Cache-Control: private, no-store`.

- `GET /academia/analyses?limit=20&offset=0` — resumos paginados;
- `GET /academia/analyses/{analysis_id}` — resultado completo persistido;
- `GET /academia/analyses/{analysis_id}/audio` — WAV, ou `404` se ausente;
- `GET /academia/analyses/{analysis_id}/export?format=txt|json` — relatório
  compartilhável ou representação estruturada.
- `DELETE /academia/analyses/{analysis_id}` — remove resultado e WAV persistidos.

Exemplo administrativo:

```bash
curl -H "Authorization: Bearer $ACADEMIA_HISTORY_TOKEN" \
  https://seu-dominio/academia/analyses
```

Sem banco ou com persistência desativada, a análise corrente continua
funcionando. Sem pool, a listagem protegida degrada para `items=[]`,
`available=false` e aviso explícito. Detalhe, áudio, exportação ou exclusão
respondem `404` quando não há registro; uma falha ativa ao consultar o banco
pode responder `503`. TXT, JSON e áudio da resposta corrente podem ser baixados
localmente pela UI sem persistir nada.

## Upload e orientação de captura

O backend grava o corpo em chunks, revalida o tamanho real e reconhece o
contêiner pela assinatura do arquivo, sem confiar apenas no nome ou no MIME
enviado pelo navegador. São aceitos contêineres de vídeo usuais como MP4/MOV,
WebM, AVI e MPEG. A imagem Docker instala `ffprobe`; o parser do box `mvhd` é
fallback para MP4/MOV. Se nenhum deles aferir a duração, o vídeo é recusado em
vez de confiar no campo do cliente. Um guard ASGI compartilhado com o Tênis,
executado antes do parser multipart, limita o corpo total a 640 MiB e os uploads
em voo; o backend mantém o teto real do arquivo em 600 MiB. O proxy limita
globalmente a dois corpos de vídeo em voo e só então aplica um buffer com teto
de 640 MiB, evitando buffers ilimitados e recusando tamanhos declarados antes de
chegarem à aplicação. O guard ASGI repete as proteções para requisições chunked.

Defaults da POC:

- até `600 MB` por upload;
- até `180 s` (`3 min`) de vídeo;
- recomendação de `3` a `6` repetições no agachamento e de `3` a `8` na rota
  observacional geral;
- `2 fps` e `MEDIA_RESOLUTION_LOW` no passe genérico de identificação;
- `8 fps` e `MEDIA_RESOLUTION_HIGH` nos passes de análise específica ou geral,
  limitados ao intervalo ativo identificado quando ele é válido;
- no máximo `2` análises simultâneas por processo.

Para reduzir ambiguidades, a gravação deve:

- mostrar a pessoa-alvo, as regiões corporais relevantes, os apoios e o caminho
  do implemento ou da parte móvel da máquina;
- quando houver outras pessoas, descrever roupa/aparência e contexto visual da
  pessoa-alvo nos campos opcionais, mantendo-a distinguível e sem oclusões;
- manter a câmera estável, sem zoom ou cortes durante a série;
- usar boa iluminação e roupa que permita visualizar as articulações
  relevantes para a tarefa;
- evitar objetos que ocultem articulações;
- registrar poucas repetições completas, mostrando a posição inicial, a mudança
  principal de direção e o retorno; no agachamento, manter também corpo inteiro
  e pés visíveis;
- informar o ângulo de captura solicitado pelo formulário.

Outras pessoas no quadro não causam recusa automática. A condição crítica é
`target_person_trackable`: a pessoa descrita deve permanecer inequívoca durante
a execução. Se houver ambiguidade, desaparecimento ou troca de pessoa-alvo, o
sistema encerra antes da metodologia técnica.

Os ângulos frontal e lateral tornam observáveis aspectos diferentes. Um único
vídeo não autoriza inferir tudo: critérios não visíveis devem ser marcados como
inconclusivos. A padronização definitiva de ângulo, distância, roupa e
iluminação permanece sujeita à validação de Q-03.

## Análise observacional geral `general_execution_observational_v1`

As famílias abaixo usam a rota geral quando são identificadas com confiança e a
pessoa-alvo permanece rastreável:

- agachamento em máquina (`machine_squat`);
- leg press (`leg_press`);
- dobradiça de quadril (`hinge`);
- afundo ou passada (`lunge`);
- empurradas horizontal e vertical (`horizontal_press`, `vertical_press`);
- puxadas horizontal e vertical (`horizontal_pull`, `vertical_pull`);
- extensão e flexão de joelhos (`knee_extension`, `knee_flexion`);
- flexão e extensão de cotovelos (`elbow_flexion`, `elbow_extension`);
- elevação de panturrilhas (`calf_raise`);
- exercícios de core (`core`).

Depois que a identificação encontra `active_interval`, os dois passes gerais
usam metadados de início/fim para analisar esse trecho lógico a `8 fps`; o
arquivo remoto não precisa ser recortado nem duplicado. Os tempos das
repetições são recolocados na linha do tempo original do vídeo.

O primeiro passe avalia se a tarefa, as regiões corporais relevantes, a
trajetória/equipamento e a pessoa-alvo permanecem observáveis, além de segmentar
ciclos genéricos em início, transição e fim. Quando o gate permite, o segundo
passe classifica oito aspectos:

| ID canônico | Aspecto observado |
|---|---|
| `range_pattern` | amplitude visível e sua consistência |
| `tempo_pattern` | ritmo lento, moderado ou rápido e presença de controle |
| `trajectory_pattern` | repetibilidade da trajetória corporal/equipamento |
| `stability_pattern` | estabilidade e manutenção dos apoios relevantes |
| `alignment_pattern` | alinhamento qualitativo no plano visível |
| `equipment_pattern` | contato, ajuste e fim de curso do equipamento, quando aplicável |
| `repetition_consistency_pattern` | padronização ao longo da série |
| `transition_pattern` | controle na mudança de direção, sem impulso, rebote ou impacto abrupto |

Movimento lento ou rápido **não é erro por si só**. Um ritmo controlado pode ser
classificado como adequado; a correção é reservada a perda de controle,
irregularidade, impulso/rebote, impacto ou outra alteração visível e repetida.
Da mesma forma, a rota não exige amplitude máxima universal nem inventa ângulos
articulares. Critérios ocultos ficam `nao_avaliavel`; interação com equipamento
pode ficar `nao_aplicavel`, por exemplo em exercício com peso corporal.

A saída não contém nota de `0..100`. O backend calcula de forma determinística:

- uma classificação consolidada da execução observada;
- a cobertura dos critérios aplicáveis;
- confiabilidade `baixa`, `media` ou `alta`, baseada em qualidade de captura,
  repetições completas e cobertura — não uma probabilidade de acerto;
- pontos positivos e correções priorizadas a partir dos padrões canônicos;
- relevância condicional do ritmo/controle para o treino e uma lista do que o
  vídeo não permite determinar.

`general_execution_observational_v1` é uma metodologia geral de POC, não uma
substituição para avaliação específica do exercício. Ela não determina carga,
proximidade da falha, esforço, volume semanal, descanso, objetivo, histórico de
treino, dor ou condição clínica. Portanto, não conclui que a série “foi
eficaz”, que produzirá hipertrofia/força, que se transferirá para uma performance
esportiva ou que determinado músculo foi mais ou menos ativado.

## Metodologia provisória `squat_poc_v1`

`squat_poc_v1` é um perfil versionado de demonstração, não uma metodologia
clínica validada. O primeiro passe estruturado observa captura, movimento e
segmentos temporais. Somente após a guarda local, o passe de checklist produz um
item para cada critério, com `verdict` igual a `adequado`, `a_corrigir` ou
`nao_avaliavel`, nota observacional `0..10`, confiança, timestamps de evidência e
repetições afetadas. Labels, observações e correções publicáveis são inseridos
pela metodologia local, nunca copiados como prosa do modelo multimodal.

| ID canônico | Critério | Ângulos úteis | Peso POC |
|---|---|---|---:|
| `stance_and_foot_position` | Base e posição dos pés | frontal, posterior, diagonal | 0,10 |
| `foot_contact` | Contato dos pés com o solo | lateral, diagonal, frontal | 0,12 |
| `knee_tracking` | Trajetória dos joelhos em relação aos pés | frontal, posterior, diagonal | 0,18 |
| `squat_depth` | Profundidade observável e controlada | lateral, diagonal | 0,15 |
| `trunk_control` | Controle do tronco e da coluna | lateral, diagonal | 0,15 |
| `hip_knee_coordination` | Coordenação entre quadril e joelhos | lateral, diagonal, frontal | 0,12 |
| `tempo_control` | Controle do ritmo | frontal, lateral, posterior, diagonal | 0,08 |
| `bilateral_symmetry` | Simetria visual entre os lados | frontal, posterior | 0,10 |

Os pesos somam `1,00`, mas não significam relevância clínica. A rubrica que
orienta o passe condicional de checklist é: `0–3` desvio visual claro/repetido, `4–6` controle
parcial, `7–8` adequado e `9–10` consistente e claramente sustentado. A nota de
critério continua sendo uma observação do modelo; apenas a agregação aritmética
é determinística.

Esses itens são hipóteses de POC. A fonte especialista, critérios definitivos,
faixas adequadas e protocolo de rotulagem continuam pendentes em Q-02. O prompt
deve reconhecer acertos antes de sugerir um único ajuste prioritário, evitar
medidas angulares inventadas, não inferir dor ou risco clínico e pedir nova
captura quando faltar evidência visual.

Como defesa adicional, o serviço não publica uma prioridade ou recomendação
acionável vinda de campo livre: para cada `a_corrigir`, a orientação é substituída
pela `correction_guidance` do critério versionado, e prioridade/secundárias são
derivadas dessa ordem canônica. Veredito e nota incompatíveis são rebaixados para
`nao_avaliavel` e tornam a resposta `limited`. O ângulo efetivamente detectado
também rebaixa deterministicamente critérios que o perfil não declara como
observáveis naquele ângulo.

Nenhuma prosa livre dos passes multimodais segue diretamente para tela, banco ou
voz. O backend conserva apenas flags, vereditos, confiança e evidências
estruturadas; observações, limitações, instruções de recaptura e síntese de
movimento são reconstruídas de textos locais. Timestamps fora do vídeo,
repetições inexistentes, fases fora de ordem/duplicadas e fases
`observable=false` são removidos ou rebaixam o segmento. O passe de narrativa
ainda passa por um filtro conservador; linguagem clínica, prescrição de
carga/séries, medidas exatas ou promessas fazem o sistema usar o relatório local.

## Feedback muscular seguro

No agachamento específico, `metrics.checklist[].muscle_context` descreve apenas
o papel muscular **esperado** relacionado a cada critério. Na rota geral,
`metrics.expected_muscle_roles` lista papéis normalmente associados à família
identificada, sem atribuí-los à execução individual. Ambos os contratos incluem
`metrics.muscle_activation_notice` e referências de literatura registradas
localmente. Esses conteúdos vêm exclusivamente dos perfis versionados; o VLM
não escreve, escolhe ou completa afirmações sobre músculos e suas observações
visuais não alteram as referências publicadas.

O contexto pode explicar quais grupos normalmente participam do movimento e
como um critério se relaciona à tarefa, mas não é uma medição fisiológica.
Vídeo RGB não mede EMG, magnitude de ativação ou força muscular e não prova
fraqueza, inibição, compensação ou hiperatividade. Por isso o sistema não deve
afirmar que um músculo “não ativou”, diagnosticar causa muscular nem prescrever
tratamento.

As referências cadastradas para o contexto educacional de `squat_poc_v1` são
[Bryanton et al. (2012)](https://doi.org/10.1519/JSC.0b013e31826791a7),
[Lorenzetti et al. (2018)](https://doi.org/10.1186/s13018-018-0763-8),
[Lewis et al. (2023)](https://doi.org/10.1016/j.ptsp.2023.03.005) e
[Caterisano et al. (2002)](https://pubmed.ncbi.nlm.nih.gov/12173958/), além de
[Padua et al. (2012)](https://doi.org/10.4085/1062-6050-47.5.10). Elas fundamentam
contexto geral da metodologia; não validam clinicamente o checklist, o score ou
conclusões individuais produzidas pela POC. As referências gerais de
`general_execution_observational_v1` contextualizam prescrição de treino de
força, duração de repetição e amplitude; não provam eficácia individual nem
validam a classificação visual. Famílias gerais não recebem `muscle_context`
emprestado do agachamento.

## Segmentação temporal

A saída estruturada representa o movimento observado e suas repetições
completas. No perfil de agachamento, cada repetição deve conter início/fim
temporal e, quando observáveis, as fases:

1. `inicio` — posição inicial e preparação;
2. `descida` — fase descendente;
3. `fundo` — transição/ponto mais baixo;
4. `subida` — fase ascendente;
5. `fim` — retorno à posição inicial.

Repetições parciais, cortadas ou ambíguas não devem ser promovidas a repetição
completa. Critérios podem referenciar repetição, fase e timestamp para que o
relatório seja rastreável ao vídeo. “Segmentação” nesta POC significa produzir
essa estrutura temporal; não implica salvar arquivos de vídeo recortados.

Na rota geral, a segmentação usa três âncoras compatíveis com diferentes
exercícios: `start_s`, `transition_s` e `end_s`. Elas representam o início do
ciclo, sua principal mudança de direção e o retorno, não as fases específicas
de um agachamento.

## Gate `recapture_required`

Antes do gate técnico, o passe de identificação exige uma pessoa-alvo
`tracked`. Se ela estiver `ambiguous` ou `not_found`, a resposta termina como
`recapture_required`, sem selecionar perfil, checklist ou score. A presença de
outras pessoas, isoladamente, não reprova o vídeo.

Depois que `squat_poc_v1` foi selecionado, o gate técnico impede que uma captura
inadequada gere um laudo aparentemente preciso. A decisão final é feita em
Python. `recapture_required` é acionado por qualquer uma destas condições:

- `capture_quality.status == "inadequate"`;
- `capture_quality.exercise_visible` e `movement.exercise_detected` não são
  explicitamente `true`;
- `whole_body_visible` ou `feet_visible` não são explicitamente `true`;
- `target_person_trackable` não é explicitamente `true` (com
  `single_person_visible` apenas como fallback de compatibilidade do contrato);
- nenhuma repetição completa pôde ser segmentada.

Valores ausentes nesses sinais críticos são tratados como falta de evidência e
também pedem recaptura; não existe presunção otimista a partir de `null`.

O estado é `limited`, e não recaptura obrigatória, quando não existe falha
crítica, mas a captura foi classificada como limitada, a confiança de captura ou
movimento é baixa, `stable_camera` ou `adequate_lighting` não são explicitamente
`true`, o checklist devolvido violou o contrato canônico, há menos de três
repetições completas ou menos de quatro dos oito critérios são avaliáveis.
`complete` é reservado ao restante dos casos sustentados pelo vídeo. O score
também só é válido a partir de quatro critérios observáveis; mesmo em um estado
`limited`, os critérios sustentados pelo vídeo podem compor um score parcial
explicitamente identificado pela cobertura.

Quando `analysis_status="recapture_required"`:

- o resultado conserva o diagnóstico de captura e instruções de nova gravação;
- `weighted_execution_score` continua auditável, mas vem inválido e com
  `score=null`;
- produz somente um relatório neutro de recaptura e, se solicitado, áudio dessas
  instruções — nunca uma correção biomecânica;
- não transforma critérios inconclusivos em erros do praticante;
- o backend substitui deterministicamente qualquer avaliação técnica residual
  do modelo por oito itens `nao_avaliavel`, sem evidências ou correções;
- pode ser persistido administrativamente como tentativa recusada, com seu
  status, sem vídeo bruto.

Esse comportamento implementa o cenário CA-03: pedir um novo recorte é mais
seguro do que fabricar uma análise.

Na rota geral, o mesmo princípio é aplicado às regiões e apoios relevantes à
tarefa identificada, sem exigir corpo inteiro e pés para todo exercício. A
análise pede recaptura quando a tarefa ou a pessoa-alvo não permanece visível e
rastreável, quando as regiões necessárias estão ocultas ou quando nenhuma
repetição completa pode ser usada. Captura limitada, baixa cobertura ou baixa
confiabilidade podem produzir `limited`; itens não sustentados continuam
`nao_avaliavel`, sem virar erro da execução.

## Indicador específico de agachamento calculado em Python

O indicador `weighted_execution_score` usa o modelo
`squat_observational_poc_v1`. Ele é calculado deterministicamente no backend a
partir das notas estruturadas; o modelo multimodal não faz a aritmética final.
Cada nota `0..10` é normalizada para `0..1`. Somente critérios observáveis
participam, e os pesos presentes são renormalizados. São necessários pelo menos
quatro dos oito critérios e uma captura sem falha crítica; caso contrário,
`valid=false` e `score=null`. Confiança representa observabilidade e não altera
a nota.

O objeto inclui `weighting_model`, `methodology_version`, `valid`,
`criteria_present`, `criteria_total`, `coverage`, `component_breakdown` e uma
nota explicativa. Quando válido, o score fica em `0..100` e cada componente
expõe peso original, peso efetivo, valor normalizado e contribuição em pontos.
Como compatibilidade defensiva, um veredito observável sem nota usa `0,85` para
`adequado` e `0,40` para `a_corrigir`; `nao_avaliavel` nunca vira zero nem
participa da soma. O prompt, contudo, exige nota explícita para todo critério
observável.

O score é apenas um indicador de POC. Pesos, normalizações e limiares ainda não
foram calibrados contra ground truth e não representam probabilidade, precisão,
risco de lesão, nota clínica ou comparação populacional. Não deve ser usado
para afirmar “95% de confiança” nem para comparar pessoas. Recalibrar exige uma
nova versão explícita dos pesos/metodologia e preservação da versão nos
resultados antigos.

Esse indicador pertence exclusivamente a `squat_poc_v1`. Na análise geral,
`weighted_execution_score=null`; classificação e confiabilidade não são
convertidas em uma nota comparável entre exercícios ou pessoas.

## Configuração

`app/academia/config.py` usa configuração independente para evitar que uma
calibração de academia altere tênis. A aplicação pode iniciar sem a chave;
nesse caso a análise responde `503`.

| Variável | Default | Finalidade |
|---|---:|---|
| `GEMINI_API_KEY` | — | credencial necessária para analisar |
| `ACADEMIA_ANALYSIS_MODEL` | `gemini-3.1-pro-preview` | análise estruturada e narrativa |
| `ACADEMIA_TRANSCRIPTION_MODEL` | `gemini-3.5-flash` | transcrição inline da descrição curta |
| `ACADEMIA_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | síntese de voz |
| `ACADEMIA_TTS_VOICE` | `Vindemiatrix` | voz da narrativa |
| `ACADEMIA_ANALYSIS_THINKING_LEVEL` | `high` | esforço dos passes estruturados |
| `ACADEMIA_NARRATIVE_THINKING_LEVEL` | `high` | esforço da narrativa |
| `ACADEMIA_IDENTIFICATION_FPS` | `2` | amostragem do passe genérico de identificação |
| `ACADEMIA_IDENTIFICATION_MEDIA_RESOLUTION` | `MEDIA_RESOLUTION_LOW` | resolução do passe genérico |
| `ACADEMIA_FPS` | `8` | amostragem dos passes específicos/gerais no intervalo ativo |
| `ACADEMIA_MEDIA_RESOLUTION` | `MEDIA_RESOLUTION_HIGH` | resolução dos passes técnicos |
| `ACADEMIA_VIDEO_MAX_SECONDS` | `180` | duração máxima de uma série, em segundos |
| `ACADEMIA_MAX_CONCURRENT_ANALYSES` | `2` | limite local de concorrência |
| `ACADEMIA_VOICE_MAX_SECONDS` | `30` | duração máxima da descrição por voz |
| `ACADEMIA_VOICE_MAX_UPLOAD_MB` | `8` | tamanho máximo do áudio de entrada |
| `ACADEMIA_MAX_CONCURRENT_TRANSCRIPTIONS` | `4` | limite local de transcrições simultâneas |
| `ACADEMIA_VOICE_TRANSCODE_TIMEOUT_SECONDS` | `20` | timeout da normalização com FFmpeg |
| `ACADEMIA_VOICE_REQUEST_OVERHEAD_MB` | `1` | folga máxima do multipart de voz |
| `ACADEMIA_MAX_UPLOAD_MB` | `600` | tamanho máximo |
| `UPLOAD_CHUNK_BYTES` | `1048576` | chunk de gravação |
| `FILES_ACTIVE_TIMEOUT_S` | `3600` | espera pelo Files API |
| `FILES_POLL_INTERVAL_S` | `2` | intervalo de polling |
| `ACADEMIA_PERSIST` | `false` | habilitação administrativa da persistência |
| `ACADEMIA_HISTORY_TOKEN` | — | Bearer obrigatório para persistir/consultar/excluir |
| `TTS_MAX_RETRIES` | `3` | tentativas de TTS |
| `TTS_RETRY_BACKOFF_S` | `2` | backoff básico de TTS |
| `TTS_CHUNK_CHARS` | `1800` | tamanho dos trechos de narrativa |
| `TTS_SAMPLE_RATE` | `24000` | sample rate WAV de fallback |
| `TTS_CHANNELS` | `1` | áudio mono |
| `TTS_SAMPLE_WIDTH` | `2` | largura de amostra PCM, em bytes |

Os overrides `ACADEMIA_ANALYSIS_MODEL`, `ACADEMIA_TRANSCRIPTION_MODEL`,
`ACADEMIA_TTS_MODEL`, `ACADEMIA_TTS_VOICE`, níveis de raciocínio e limite de
upload têm precedência sobre os nomes genéricos equivalentes
(`ANALYSIS_MODEL`, `TRANSCRIPTION_MODEL`, `TTS_MODEL` etc.), que são aceitos
apenas como fallback de compatibilidade. Assim, calibrar a vertical academia
não altera a configuração de `/tennis`. A transcrição só fica disponível
quando `GEMINI_API_KEY`, `ffmpeg` e `ffprobe` estão presentes; a imagem Docker
de produção inclui as duas ferramentas. Se `ACADEMIA_VOICE_MAX_UPLOAD_MB` for
elevado acima de `8`, ajuste também `voice-upload-body-limit` no Traefik; o
proxy da stack raiz mantém deliberadamente o teto padrão de `9 MiB` incluindo
o multipart.

## Persistência e degradação graciosa

A tabela `academia_analyses` guarda exercício, versão da metodologia,
identificador/nome opcionais do praticante, ângulo, status, JSON do resultado,
WAV opcional e data. O vídeo bruto **nunca** é persistido.

Persistência usa opt-in duplo e é best-effort:

- resultados somente de identificação (`unsupported_exercise`,
  `exercise_unknown` ou pessoa-alvo ambígua antes da rota técnica) não são
  persistidos nesta fase; análises gerais com rota materializada seguem o mesmo
  opt-in administrativo das análises específicas;
- `ACADEMIA_PERSIST=false` desativa a gravação e não pode ser sobrescrito pelo
  formulário;
- a capacidade só fica disponível com `ACADEMIA_HISTORY_TOKEN`; cada gravação
  ainda exige `persist=true` explícito e o mesmo Bearer administrativo;
- pool/banco ausente retorna resultado normalmente e sem `persisted_id`; uma
  exceção ativa de persistência acrescenta aviso não fatal;
- falha de narrativa, áudio ou persistência não invalida métricas já obtidas;
- falha da análise estruturada ou captura recusada não é mascarada como sucesso
  biomecânico.

O identificador opaco de praticante permite uma futura série longitudinal, mas
o endpoint de evolução não é publicado no MVP anônimo.

## Privacidade, consentimento e retenção

Vídeos corporais e identificação do praticante são dados pessoais. Antes do
envio, a UI informa a finalidade e exige que o operador confirme ter autorização
para enviar o vídeo e, quando usado, processar temporariamente o áudio da
descrição; ambos os endpoints exigem `consent=true`. A opção de salvar deve ser
marcada somente quando a autorização também abranger retenção do resultado.
Para menores ou terceiros, aplicam-se as autorizações cabíveis ao operador da
POC.

- o nome é opcional e não é necessário para a análise visual;
- roupa/aparência e notas são opcionais e servem apenas para manter a mesma
  pessoa-alvo rastreável; não são usadas para reconhecer identidade pelo rosto;
- a gravação do microfone serve apenas para transcrever essas características,
  não para biometria vocal, autenticação ou análise da voz;
- os arquivos de voz original e WAV normalizado são temporários e removidos
  após sucesso ou falha; não entram na tabela `academia_analyses`;
- abortar no navegador encerra apenas a espera da UI; um processamento já
  recebido pelo servidor pode terminar antes da remoção desses temporários, sem
  que isso os transforme em dados persistidos;
- a gravação é enviada ao Gemini inline, sem Files API; somente o texto
  revisado segue como `practitioner_notes` para a análise;
- a descrição revisada é enviada ao Gemini como dica de rastreamento, mas a
  persistência atual não grava `practitioner_outfit` nem `practitioner_notes`;
  ainda assim, dados desnecessários devem ser corrigidos ou apagados antes da
  análise;
- não solicitar altura, peso, diagnóstico, dor ou outros dados de saúde nesta
  fase;
- o vídeo local é temporário e removido ao fim da requisição;
- o arquivo da Files API é removido após a análise; falhas durante polling
  também disparam tentativa imediata de exclusão, e falha de delete gera evento
  próprio (a expiração do provedor continua sendo fallback);
- o banco recebe apenas resultado/relatório e WAV opcional;
- logs e eventos não devem conter vídeo, base64, relatório integral nem dados
  identificáveis;
- a POC deve publicar prazo de retenção dos registros e canal/processo de
  exclusão antes de uso com pessoas reais.

Enquanto prazo e processo de exclusão não forem formalizados, persistência deve
ser tratada como ambiente controlado de demonstração, não repositório
indefinido de dados de alunos.

O checkbox de persistência permanece desabilitado na UI pública; a página
oferece downloads locais. O consentimento da requisição não é um registro
jurídico. O histórico usa Bearer administrativo, respostas `no-store` e oferece
exclusão por ID, mas ainda não possui contas/ownership por praticante nem TTL
automático. Por isso `ACADEMIA_PERSIST` vem desativado e a capacidade só é
liberada com token. Mesmo assim, ela deve permanecer em ambiente controlado até
existirem política formal de retenção e autorização por registro.

## Eventos e observabilidade

Eventos de domínio são enumerados em `app/events/catalog.py`, sempre com
correlation ID da requisição e sem payload sensível:

- `academia.analyze.received`;
- `academia.transcription.received` / `academia.transcription.completed` /
  `academia.transcription.failed`;
- `academia.upload.saved` / `academia.upload.rejected`;
- `academia.exercise.identified` / `academia.exercise.unresolved`;
- `academia.profile.selected`;
- `academia.capture.accepted` / `academia.capture.rejected`;
- `academia.score.computed` (somente quando o perfil específico possui score);
- `academia.report.generated`;
- `academia.analyze.completed` / `academia.analyze.failed`;
- `academia.persisted`;
- `academia.analysis.retrieved` / `academia.analysis.exported` /
  `academia.analysis.deleted`;
- `academia.warning`.

A chamada inline emite `gemini.transcribe.started` /
`gemini.transcribe.completed`; falhas usam também `gemini.call.failed`. Os
eventos registram somente metadados operacionais, como MIME, bytes, duração,
quantidade de caracteres e motivo categorizado — nunca áudio ou transcrição
integral. As demais chamadas externas continuam emitindo os eventos genéricos
`gemini.*`. Como no restante do sistema, auditoria é best-effort e nunca deve
derrubar uma requisição.

## Como adicionar uma metodologia técnica

A taxonomia já direciona 14 famílias para uma leitura observacional geral, mas
isso não equivale a uma metodologia biomecânica específica. O próximo perfil
específico não deve ser inserido por condicionais espalhadas no serviço. Para
substituir a rota geral de uma família por metodologia própria:

1. registrar ou revisar sua família canônica na taxonomia de identificação;
2. definir identificador e versão imutáveis da metodologia;
3. criar o contrato público e os schemas remotos pequenos de captura e checklist;
4. registrar critérios, gate de captura e prompt de análise próprios;
5. implementar a guarda condicional e o materializador local desses schemas no
   transporte da vertical;
6. definir, se existir, um modelo de pesos Python versionado;
7. mapear explicitamente a família para o perfil na camada local, mantendo
   rótulos livres fora da decisão de roteamento;
8. adicionar narrativa/instruções de captura específicas;
9. criar fixtures rotuladas, testes de schemas, ordem/skip dos passes, gate, score
   e aceitação;
10. documentar fonte especialista, limitações e protocolo de calibração.

Bíceps/rosca é o candidato citado nos requisitos para ganhar um perfil próprio,
mas hoje já recebe a observação geral de flexão de cotovelos. A metodologia
específica só entra depois da validação necessária. Adicionar um perfil não
autoriza reaproveitar critérios biomecânicos de outro exercício.

## Testes

Os testes rodam sem rede e sem banco real, usando um Gemini falso:

```bash
DATABASE_URL="postgresql://x:x@localhost:5432/x" \
GEMINI_API_KEY="test-key" \
python3 -m pytest tests/test_academia.py -q
```

As regras de roteamento, gate, score e materialização dos modelos são
determinísticas e têm cobertura unitária; as observações visuais que os
alimentam vêm do VLM. Os totais exatos de testes variam com a evolução da
vertical e devem ser obtidos executando os comandos acima na revisão atual.

Cobertura atual:

- upload vazio, inválido, acima do limite, duração excessiva ou não aferível e
  limpeza local;
- assinatura real dos contêineres suportados e filename não confiável;
- passe genérico de identificação seguido, quando existe rota específica ou
  geral, pelos dois schemas técnicos estritos na ordem captura → checklist;
- identificação automática de exercício, variação, equipamento e intervalo
  ativo, incluindo 14 famílias gerais, `unsupported_exercise` e
  `exercise_unknown` sem checklist ou score de agachamento;
- análise geral a 8 fps no intervalo ativo, segmentação
  início/transição/fim, oito critérios, classificação, confiabilidade e
  `weighted_execution_score=null`;
- ritmos lento/moderado/rápido controlados sem erro automático, amplitude sem
  máximo universal e equipamento `nao_aplicavel` quando cabível;
- limites explícitos contra alegações de eficácia, adaptação, transferência de
  performance ou ativação muscular individual;
- materialização do contrato público, segmentação em repetições/fases,
  timestamps incoerentes rebaixados e critérios inconclusivos;
- captura aceita com outras pessoas quando a pessoa-alvo é rastreável e
  `recapture_required` por alvo ambíguo, corpo cortado, oclusão, ângulo ou
  ausência de repetição completa;
- score calculado apenas em Python, renormalização e `score=null`/inválido na
  recaptura;
- roteamento por família canônica local, normalização de ângulos e checklist
  canônico de oito critérios;
- segurança dos prompts e reconstrução canônica de toda prosa estruturada antes
  de tela/TTS, incluindo conteúdo não confiável e proibições clínicas;
- resposta de sucesso com métricas, narrativa e áudio;
- falha de qualquer passe estruturado com limpeza local/remota, falha explícita
  do passe técnico de checklist, remoção remota após falha de polling e resposta
  `502`;
- fallback textual quando a narrativa falha ou contém prescrição/promessa
  insegura, TTS não fatal e falha de persistência sem vazamento do erro do banco;
- consentimento obrigatório e status `400`, `413`, `415`, `422` e `503`;
- gravação via `MediaRecorder`, limites e estados acessíveis da UI, fallback de
  digitação e contrato da transcrição temporária via Gemini;
- validação de MIME/tamanho/duração decodificada do áudio, inclusive quando o
  WebM não declara duração, normalização WAV mono a 16 kHz, ausência de Files
  API/persistência, limpeza em sucesso/falha e status
  `400`, `413`, `415`, `422`, `429`, `502` e `503` da transcrição;
- persistência impossível de forçar pelo cliente, opt-in duplo, Bearer do
  histórico, `no-store`, exclusão administrativa, banco ausente e TXT;
- contrato OpenAPI tipado e segurança Bearer declarada;
- catálogo/emissão dos eventos `academia.*`;
- CA-01 (execução correta sem erro inventado), CA-02 (desvio conhecido), CA-03
  (recaptura), estado `limited`, normalização defensiva do checklist e CA-04
  no contrato de relatório;
- health, frontend e descoberta da rota na raiz.

Ainda devem permanecer em gates mais amplos: persistência bem-sucedida com
Postgres real, detalhe/áudio/JSON de um registro existente, saturação concorrente
`429`, teste visual em navegador e validação com vídeos rotulados.
A metodologia específica de CA-05 permanece pós-MVP; sua família já é atendida
pela análise geral.

Mocks verificam contrato e orquestração, não precisão biomecânica. A validação
real requer os vídeos de Q-01 rotulados por especialista e um protocolo que
meça falsos positivos, falsos negativos e casos inconclusivos.

## Rastreabilidade RF/RNF/RN/CA

| Requisito | Evidência prevista na vertical |
|---|---|
| RF-001 | página sem seleção manual e `POST /academia/analyze` |
| RF-002 | guia de captura e descrição opcional da pessoa-alvo por texto ou microfone antes do upload |
| RF-003 | recomendação do perfil técnico `squat_poc_v1` de 3–6 repetições |
| RF-004 | segmentos/repetições com tempos e fases |
| RF-005 | identificação a 2 fps/LOW e análise específica/geral do intervalo ativo a 8 fps/HIGH |
| RF-006 | perfil versionado e veredito por critério |
| RF-007 | desvios, evidência e correção acessível |
| RF-008 | qualidade/rastreabilidade da pessoa-alvo no gate de captura |
| RF-009 | pontos corretos e pontos a melhorar |
| RF-010 | narrativa PT-BR para leigo |
| RF-011 | exportação TXT/JSON local; exportação administrativa quando persistido |
| RF-012 | taxonomia automática, agachamento específico e fallback observacional geral explícito, sem empréstimo de metodologia |
| RF-013 | `practitioner_id` e consulta longitudinal ainda não publicada |
| RNF-001 | fluxo web único, orientado e sem instalação |
| RNF-002 | meta pendente; não há alegação de precisão nesta POC |
| RNF-003 | fluxo síncrono com feedback de processamento e limites explícitos |
| RNF-004 | pacote/configuração/tabelas/eventos isolados de tênis |
| RNF-005 | POC acessível diretamente em `/academia/` |
| RNF-006 | erros claros e degradação independente de relatório/áudio/banco |
| RN-01 | academia implementada sem recalibrar/degradar tênis |
| RN-02 | `squat_poc_v1` é específico; 14 famílias usam análise geral sem score, e `other` fica somente na identificação |
| RN-03 | relatório reconhece acertos antes dos ajustes |
| RN-04 | metodologia explícita e versionada, ainda provisória |
| RN-05 | página isolada para demonstração comercial |
| CA-01 | fixture correta + revisão especialista pendente |
| CA-02 | fixture com desvio conhecido + revisão especialista pendente |
| CA-03 | gate `recapture_required` sem laudo corretivo |
| CA-04 | relatório/áudio/exportação apresentáveis sem edição |
| CA-05 | flexão de cotovelo recebe análise geral; sua metodologia específica é pós-MVP |

## Decisões sobre Q-01 a Q-07

| Questão | Decisão da POC | Pendência |
|---|---|---|
| Q-01 · vídeos de referência | testes de contrato usam fakes; nenhum vídeo foi tratado como ground truth | obter vídeos consentidos em diferentes ângulos e execuções |
| Q-02 · metodologia | `squat_poc_v1` é específico; `general_execution_observational_v1` é geral, ambos provisórios e versionados | revisão por personal/fisio e protocolos de rotulagem por escopo/família |
| Q-03 · captura | UI pede pessoa-alvo rastreável, regiões/apoios relevantes, câmera estável e 3–8 repetições; squat preserva corpo/pés inteiros e 3–6 repetições | validar ângulos/distâncias/roupa com especialista |
| Q-04 · granularidade | resposta conserva repetições/fases e produz relatório consolidado | validar se o cliente quer também relatório textual por repetição |
| Q-05 · identificação | nome/ID são opcionais; roupa/notas digitadas ou transcritas ajudam a rastrear a pessoa-alvo sem reconhecimento facial ou biometria vocal | autenticação/ownership ficam fora do MVP |
| Q-06 · prazo | entrega técnica não transforma urgência comercial em atalho de validação | Caio define data de demonstração |
| Q-07 · próximos exercícios | 14 famílias recebem critérios observacionais gerais e nenhuma recebe critérios de agachamento | criar e validar perfil próprio antes de oferecer conclusões específicas |

## Limitações conhecidas

- checklist, pesos e score ainda não foram validados por especialista;
- a classificação/confiabilidade da análise geral também não foi validada
  contra vídeos rotulados;
- não há conjunto ground-truth nem medição de precisão;
- uma câmera 2D não torna todos os critérios observáveis;
- a identificação usa 2 fps/LOW e a análise específica/geral usa 8 fps/HIGH no
  intervalo ativo; isso não significa examinar literalmente todos os frames
  originais nem equivale a captura instrumental;
- o modelo pode errar segmentação, pessoa, fase e interpretação visual;
- a saída não diagnostica dor, lesão, mobilidade ou adequação individual e não
  mede carga, esforço, fadiga, eficácia, performance futura ou ativação muscular;
- análise síncrona pode manter a conexão aberta por minutos;
- o microfone requer HTTPS/contexto seguro e suporte do navegador a
  `getUserMedia`/`MediaRecorder`; a digitação continua sendo o fallback;
- o Bearer do histórico é administrativo, sem ownership por aluno ou TTL;
  persistência não substitui política corporativa de identidade, consentimento,
  retenção e exclusão;
- comparação longitudinal e metodologias específicas para as famílias atendidas
  pela rota geral permanecem pós-MVP; a classificação observacional não as
  substitui.

Antes de qualquer promessa comercial de precisão, a metodologia e os resultados
devem ser revisados por especialista e comparados a um conjunto representativo
de vídeos rotulados.
