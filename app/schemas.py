"""Modelos Pydantic v2 de request/response da API."""

from datetime import datetime

from pydantic import BaseModel, Field


class PartidaIn(BaseModel):
    """Dados de entrada de uma partida a ser analisada."""

    time_casa: str = Field(default="time da casa")
    time_fora: str = Field(default="time visitante")
    gols_casa: int = Field(default=0, ge=0)
    gols_fora: int = Field(default=0, ge=0)


class AnaliseOut(BaseModel):
    """Análise persistida, com descrição acessível sempre presente."""

    id: int | None = None
    dados: dict
    estatisticas: dict
    descricao_acessivel: str
    criado_em: datetime | None = None
