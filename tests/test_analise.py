from bitvar import AnalisadorEsportivo


def test_vitoria_time_casa():
    analisador = AnalisadorEsportivo()
    resultado = analisador.analisar_partida({
        "time_casa": "Time A",
        "time_fora": "Time B",
        "gols_casa": 2,
        "gols_fora": 1,
    })
    assert resultado.estatisticas["vencedor"] == "Time A"
    assert "Time A venceu a partida." in resultado.descricao_acessivel


def test_empate():
    analisador = AnalisadorEsportivo()
    resultado = analisador.analisar_partida({
        "time_casa": "Time A",
        "time_fora": "Time B",
        "gols_casa": 1,
        "gols_fora": 1,
    })
    assert resultado.estatisticas["empate"] is True
    assert "empatada" in resultado.descricao_acessivel


def test_descricao_sempre_presente():
    analisador = AnalisadorEsportivo()
    resultado = analisador.analisar_partida({})
    assert resultado.descricao_acessivel
