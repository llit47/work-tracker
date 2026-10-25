from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .origins import origin_list, validate_browser_origins, validate_ingress_profile


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    webhook_token: str = Field(min_length=32)
    database_url: str = "sqlite:///./work_tracker.db"
    browser_allowed_origins: str = Field(min_length=1)
    cors_origins: str = ""
    session_cookie_secure: bool = True
    ingress_profile: Literal["lan", "split"] = "lan"
    browser_hsts_seconds: int = Field(default=0, ge=0, le=31536000)

    @model_validator(mode="after")
    def explicit_cors_origins(self):
        validate_browser_origins(self.browser_allowed_origins, self.cors_origins)
        validate_ingress_profile(self.ingress_profile, self.browser_allowed_origins, self.cors_origins,
                                 self.session_cookie_secure, self.browser_hsts_seconds)
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return origin_list(self.cors_origins)

    @property
    def browser_origin_list(self) -> list[str]:
        return origin_list(self.browser_allowed_origins, required=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
