from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    webhook_token: str = Field(min_length=32)
    database_url: str = "sqlite:///./work_tracker.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    session_cookie_secure: bool = True

    @model_validator(mode="after")
    def explicit_cors_origins(self):
        if any("*" in origin or origin == "null" for origin in self.cors_origin_list):
            raise ValueError("Credentialed CORS requires explicit origins")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
