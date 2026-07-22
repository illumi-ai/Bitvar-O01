"""BitVar IA — vertical de musculação com identificação automática por vídeo.

O primeiro passe identifica exercício, variação, equipamento/máquina e a
pessoa-alvo. Somente exercícios com perfil técnico local seguem para checklist,
score, relatório PT-BR e áudio opcional; nesta POC, o único perfil disponível é
``squat_poc_v1``. Os demais são reportados sem reutilizar critérios de
agachamento. Modelos, prompts, persistência e rotas permanecem isolados do
produto de tênis.
"""

from .config import academia_settings

__all__ = ["academia_settings"]
