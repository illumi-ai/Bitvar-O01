"""BitVar IA — módulo de análise de tênis por vídeo (M/F · clipe/partida).

Implementa o blueprint ``docs/bitvar-ia-tenis-blueprint.html``:

    upload de vídeo
      → roteamento (gênero × modo detectado por duração)
      → Gemini 3.5 Flash (vídeo → JSON estruturado)      [chamada 1]
      → Gemini 3.5 Flash (JSON → narrativa PT-BR)         [chamada 2]
      → Gemini 3.1 Flash TTS (narrativa → áudio)          [chamada 3]
      → três saídas: métricas, texto, áudio

Sem banco obrigatório nesta fase (estado efêmero por sessão); a persistência
em Postgres é opcional (``app/tennis/store.py``).
"""

from .config import tennis_settings

__all__ = ["tennis_settings"]
