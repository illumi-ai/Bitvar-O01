"""Formatação acessível das análises.

Gera descrições em texto simples, pensadas para leitores de tela e
futura conversão em áudio (TTS). Evita símbolos, abreviações e
formatação que dependa de visão.
"""


def descrever_partida(partida: dict, estatisticas: dict) -> str:
    """Gera uma descrição em linguagem natural simples de uma partida."""
    time_casa = partida.get("time_casa", "time da casa")
    time_fora = partida.get("time_fora", "time visitante")
    gols_casa = partida.get("gols_casa", 0)
    gols_fora = partida.get("gols_fora", 0)

    placar = (
        f"{time_casa} marcou {_gols(gols_casa)} e "
        f"{time_fora} marcou {_gols(gols_fora)}."
    )

    if estatisticas.get("empate"):
        resultado = "A partida terminou empatada."
    else:
        resultado = f"{estatisticas['vencedor']} venceu a partida."

    total = estatisticas.get("total_gols", 0)
    resumo = f"No total, foram marcados {_gols(total)}."

    return " ".join([placar, resultado, resumo])


def _gols(quantidade: int) -> str:
    if quantidade == 1:
        return "1 gol"
    return f"{quantidade} gols"
