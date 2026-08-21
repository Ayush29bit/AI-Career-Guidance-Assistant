"""Application configuration.

One settings object, read once from the environment (and from backend/.env when
present), cached for the process. Secrets live in backend/.env, which is
gitignored; backend/.env.example documents the shape without carrying values.

DATABASE_URL is deliberately required rather than defaulted. A default would
have to embed a password to be useful, and a wrong default fails at query time
with a confusing error instead of failing here with an actionable one.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

#: backend/.env is the documented location; the repo root is accepted as a
#: fallback so a .env placed there still works instead of silently doing nothing.
ENV_FILES = (BACKEND_DIR / ".env", REPO_ROOT / ".env")


class ConfigurationError(Exception):
    """Raised when required configuration is missing or malformed."""


class Settings(BaseSettings):
    """Environment-backed settings.

    Field names map to upper-case environment variables: `database_url` reads
    DATABASE_URL, `db_echo` reads DB_ECHO.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The backend will grow other settings (LLM keys, API config). Ignoring
        # unknown keys keeps one shared .env usable across phases.
        extra="ignore",
    )

    database_url: str
    db_echo: bool = False

    #: Comma-separated browser origins allowed to call the API. The defaults are
    #: the Vite and Create-React-App dev servers. Stored as a string rather than
    #: a list because pydantic-settings would otherwise require JSON in the env
    #: var, which is awkward to write by hand in a .env file.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings, converting validation failures into a clear error."""
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as error:
        missing = [
            ".".join(str(part) for part in problem["loc"])
            for problem in error.errors()
            if problem["type"] == "missing"
        ]
        if missing:
            names = ", ".join(name.upper() for name in missing)
            raise ConfigurationError(
                f"missing required configuration: {names}\n"
                f"  set it in {BACKEND_DIR / '.env'} "
                f"(copy {BACKEND_DIR / '.env.example'} to start)"
            ) from error
        raise ConfigurationError(f"invalid configuration:\n{error}") from error
