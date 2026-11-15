from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The backend directory (the one holding ``app/``). Relative paths in settings resolve
# against it, so the API and the CLI scripts use the same files whatever the working dir.
BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:dev@localhost:5432/foodcoach"
    entra_issuer: str = ""
    entra_audience: str = ""
    allowed_oid: str = ""
    # Empty = local dev: tid and scp are not checked (dev tokens carry neither).
    allowed_tid: str = ""
    required_scope: str = "Food.ReadWrite"
    jwks_url: str = ""
    cors_origins: list[str] = ["http://localhost:5173"]
    deletion_ledger_path: Path = BACKEND_DIR / "data" / "deletion_ledger.jsonl"
    # Deployment (spec §3.5): refuse to start unless the ledger directory carries the marker.
    require_ledger_volume: bool = False

    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_api_key: str = ""
    # Empty = guideline retrieval ranks by keywords only (no embedding call).
    azure_openai_embedding_deployment: str = ""
    # False = the coach gets no guideline passages (the retrieval-off arm of the eval).
    coach_knowledge_enabled: bool = True
    # False = cited passages are kept without the second model call that checks them.
    coach_judge_sources: bool = True
    coach_monthly_cap_eur: Decimal = Decimal(2)
    coach_price_in_per_1k: Decimal = Decimal(0)
    coach_price_out_per_1k: Decimal = Decimal(0)
    coach_log_text: bool = False
    # Region gate (controller ruling, user-approved): real user weeks go to the LLM only
    # from an EU Data Zone deployment, or when global processing is explicitly allowed.
    azure_openai_deployment_type: Literal["global", "datazone"] = "global"
    coach_allow_global: bool = False
    # Kill switch (spec §3.9): False serves the deterministic template, no LLM call.
    coach_llm_enabled: bool = True
    # Deployment (spec §3.1): API routes under this prefix; built web app served from here.
    api_prefix: str = ""
    static_dir: Path | None = None

    @field_validator("deletion_ledger_path", mode="after")
    @classmethod
    def _absolute_ledger_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else BACKEND_DIR / value


@lru_cache
def get_settings() -> Settings:
    return Settings()
