"""Carregador dos casos-ouro de regressao (gabarito do Juca).

Cada lance e um arquivo ``regression_<case_id>.json`` neste diretorio. Para
adicionar um lance novo no loop de calibragem (Mateus -> Juca -> 98%), basta
copiar o shape de ``regression_00000201.json`` e soltar o arquivo aqui — nenhum
codigo precisa mudar. ``golden_cases()`` devolve a lista carregada.
"""

from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def golden_cases() -> list[dict]:
    """Todos os casos-ouro (regression_*.json), ordenados por case_id."""
    cases = []
    for path in sorted(_DIR.glob("regression_*.json")):
        with path.open(encoding="utf-8") as fh:
            cases.append(json.load(fh))
    return cases


def golden_case(case_id: str) -> dict:
    """Um caso-ouro por id (ex.: '00000201'). KeyError se nao existir."""
    for case in golden_cases():
        if case.get("case_id") == case_id:
            return case
    raise KeyError(f"golden case ausente: {case_id!r}")
