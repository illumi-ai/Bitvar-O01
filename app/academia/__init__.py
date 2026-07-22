"""BitVar IA — módulo de análise técnica de exercícios de academia por vídeo.

Espelha o pipeline do módulo de tênis (``app/tennis/``):

    upload de vídeo
      → Gemini (vídeo → JSON estruturado, padrão-ouro do exercício no prompt)         [chamada 1]
      → Gemini (JSON → narrativa PT-BR calibrada — erro primeiro quando houver)        [chamada 2]
      → Gemini TTS (narrativa → áudio)                                                 [chamada 3]
      → três saídas: métricas, texto, áudio

Relatório educacional (RN-05): não substitui avaliação presencial, não mede
carga/esforço/ativação muscular e não promete hipertrofia/força/emagrecimento
(RN-03). Persistência em Postgres é opcional e opt-in (``app/academia/store.py``).
"""

from .config import academia_settings

__all__ = ["academia_settings"]
