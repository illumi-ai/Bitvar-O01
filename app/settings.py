"""Configuração lida do ambiente."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variáveis de ambiente da API. ``DATABASE_URL`` é injetada pelo compose."""

    database_url: str  # postgresql://user:pass@db:5432/bitvar
    db_pool_min: int = 1
    db_pool_max: int = 5
    db_connect_timeout: int = 5

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)


settings = Settings()  # lê DATABASE_URL do ambiente
