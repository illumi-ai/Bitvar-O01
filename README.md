# Bitvar O01

Módulo de IA de análise esportiva acessível.

## Objetivo

Fornecer análises esportivas (estatísticas, tendências e resumos de partidas) com foco em **acessibilidade**: saídas em linguagem natural simples, compatíveis com leitores de tela, e descrições textuais de gráficos e dados visuais.

## Estrutura

```
Bitvar-O01/
├── bitvar/
│   ├── __init__.py
│   ├── analise.py        # Núcleo de análise esportiva (estatísticas e tendências)
│   └── acessibilidade.py # Formatação acessível das análises (texto simples, leitores de tela)
├── tests/
│   └── test_analise.py
├── requirements.txt
└── README.md
```

## Instalação

```bash
pip install -r requirements.txt
```

## Uso rápido

```python
from bitvar import AnalisadorEsportivo

analisador = AnalisadorEsportivo()
resultado = analisador.analisar_partida({
    "time_casa": "Time A",
    "time_fora": "Time B",
    "gols_casa": 2,
    "gols_fora": 1,
})
print(resultado.descricao_acessivel)
```

## Acessibilidade

- Todas as análises geram uma `descricao_acessivel` em texto simples.
- Sem dependência de elementos visuais para entender os resultados.
- Estruturado para integração futura com TTS (texto-para-voz).

## Roadmap

- [ ] Integração com APIs de dados esportivos em tempo real
- [ ] Modelo de IA para previsão de tendências
- [ ] Saída em áudio (TTS)
- [ ] Suporte a múltiplos esportes
