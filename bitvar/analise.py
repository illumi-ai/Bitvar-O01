"""Núcleo de análise esportiva do Bitvar O01."""

from dataclasses import dataclass, field

from .acessibilidade import descrever_partida


@dataclass
class ResultadoAnalise:
    """Resultado de uma análise, sempre com descrição acessível em texto simples."""

    dados: dict
    estatisticas: dict = field(default_factory=dict)
    descricao_acessivel: str = ""


class AnalisadorEsportivo:
    """Analisa dados de partidas e gera saídas acessíveis.

    Ponto de extensão para modelos de IA: sobrescreva ``_calcular_estatisticas``
    ou conecte um modelo preditivo em ``analisar_partida``.
    """

    def analisar_partida(self, partida: dict) -> ResultadoAnalise:
        """Analisa uma partida a partir de um dicionário de dados.

        Campos esperados: time_casa, time_fora, gols_casa, gols_fora.
        """
        estatisticas = self._calcular_estatisticas(partida)
        descricao = descrever_partida(partida, estatisticas)
        return ResultadoAnalise(
            dados=partida,
            estatisticas=estatisticas,
            descricao_acessivel=descricao,
        )

    def _calcular_estatisticas(self, partida: dict) -> dict:
        gols_casa = partida.get("gols_casa", 0)
        gols_fora = partida.get("gols_fora", 0)
        total = gols_casa + gols_fora

        if gols_casa > gols_fora:
            vencedor = partida.get("time_casa", "time da casa")
        elif gols_fora > gols_casa:
            vencedor = partida.get("time_fora", "time visitante")
        else:
            vencedor = None

        return {
            "total_gols": total,
            "diferenca_gols": abs(gols_casa - gols_fora),
            "vencedor": vencedor,
            "empate": vencedor is None,
        }
