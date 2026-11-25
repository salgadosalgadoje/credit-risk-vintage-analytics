"""Contrato de configuración del data warehouse: variables de entorno y URI SQLAlchemy."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Carga y valida la conexión PostgreSQL y la ruta del extracto crudo."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_user: str
    postgres_password: str
    postgres_db: str = "credit_risk_dw"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    raw_data_path: Path = Path("data/raw/accepted_2007_to_2018Q4.csv")
    kaggle_api_token: str | None = None

    def model_post_init(self, context: Any) -> None:
        """Expone el token a kagglehub, que solo lo lee desde el entorno del proceso."""
        if self.kaggle_api_token:
            os.environ["KAGGLE_API_TOKEN"] = self.kaggle_api_token

    @computed_field
    @property
    def sqlalchemy_database_uri(self) -> str:
        """URI `postgresql+psycopg` lista para `create_engine`."""
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración validada, cacheada por proceso."""
    return Settings()

settings = Settings()
