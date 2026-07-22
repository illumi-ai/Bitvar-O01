"""Base de regras por categoria (gênero × nível) — gate de OBSERVAÇÃO no prompt.

Motivação (gabarito áudio 00000213, calibragem Caio 24/06): regras táticas
dependem da CATEGORIA. Ex.: o 'parceiro do sacador sair do fundo' só se aplica
em duplas MASCULINAS PROFISSIONAIS. Em amador a IA deve apenas OBSERVAR o
posicionamento, nunca exigir a saída do fundo.

Cada regra carrega metadados de aplicabilidade ``aplica_se={gender:[...], level:[...]}``.
``applicable_rules(gender, level)`` filtra as compatíveis (interseção gênero E
nível) e ``build_rules_block(...)`` monta o bloco PT-BR para o system prompt
(mesmo padrão de :func:`app.tennis.prompts.build_camera_block`: retorna ``str`` ou
``None``). NENHUMA regra vira campo de response_schema — é só instrução textual,
então este eixo não arrisca derrubar a análise estrita (invariante 1). E o bloco
manda OBSERVAR/descrever, nunca descontar nota: a nota é calculada em Python
(invariante 2), o VLM não pontua regra.

A base atual cobre a única regra validada pelo treinador. A rubrica completa de
beach tennis (duplas, jogo de rede) precisa do treinador para ser preenchida — ver
open questions; o MECANISMO já está pronto para receber novas entradas: cada nova
regra é um dict aqui, na mesma filosofia de "recalibrar = editar a tabela" dos
pesos (:mod:`app.tennis.weights`).
"""

from __future__ import annotations

from typing import TypedDict


class Applicability(TypedDict):
    gender: list[str]   # gêneros em que a regra se aplica (subconjunto de Gender)
    level: list[str]    # níveis em que a regra se aplica (subconjunto de Level)


class Rule(TypedDict):
    id: str
    texto: str          # enunciado PT-BR observável, em 1 frase de treinador
    aplica_se: Applicability


# Fonte de verdade da base de regras. Acrescentar regras = acrescentar dicts aqui.
RULES: list[Rule] = [
    {
        "id": "server_partner_leaves_baseline",
        "texto": (
            "Em duplas, o PARCEIRO DO SACADOR deve sair do fundo e avançar para a rede "
            "logo após o saque, assumindo a posição ofensiva de dupla."
        ),
        "aplica_se": {"gender": ["male"], "level": ["profissional"]},
    },
]


def applicable_rules(gender: str | None, level: str | None) -> list[str]:
    """Enunciados PT-BR das regras aplicáveis a (gênero × nível). Vazio se nenhuma."""
    g = (gender or "").strip().lower()
    lv = (level or "").strip().lower()
    return [
        r["texto"]
        for r in RULES
        if g in r["aplica_se"]["gender"] and lv in r["aplica_se"]["level"]
    ]


def build_rules_block(gender: str | None, level: str | None) -> str | None:
    """Bloco de regras da categoria p/ o system prompt (espelha build_camera_block).

    Injeta SOMENTE as regras compatíveis com a categoria e instrui o modelo a
    OBSERVAR/descrever o cumprimento delas em texto — NUNCA a descontar nota (a nota
    é calculada fora do modelo; invariante 2). O que não estiver listado (ex.: saída
    do fundo em amador) não deve ser cobrado nem comentado como falta. Retorna
    ``None`` quando não há regra aplicável à categoria (ex.: amador), para não poluir
    o prompt nem sugerir cobrança onde não cabe.
    """
    rules = applicable_rules(gender, level)
    if not rules:
        return None
    linhas = "\n- ".join(rules)
    return (
        "REGRAS TÁTICAS DESTA CATEGORIA (gênero × nível) — OBSERVE na sua leitura do "
        "lance APENAS as regras listadas abaixo e descreva o cumprimento delas em texto "
        "(em 'visual_evidence'/'observation'). NÃO descontar nota por regra: a nota é "
        "calculada fora do modelo. Regra de outra categoria que NÃO esteja aqui não deve "
        "ser cobrada nem comentada como falta. Avalie estas regras:\n- "
        + linhas
    )
