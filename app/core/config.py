"""Application configuration settings."""
from typing import List, Union
import json
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core settings for PayScope API."""
    
    PROJECT_NAME: str = "PayScope API"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    FIREBASE_PROJECT_ID: str = ""
    API_SESSION_SECRET: str = ""
    ALLOW_GUEST_SESSIONS: bool = True
    RUN_MIGRATIONS: bool = True
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "patilsakshi18027@gmail.com"
    EMAIL_REMINDER_INTERVAL_SECONDS: int = 3600
    EMAIL_WORKER_ENABLED: bool = True

    # Server configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # CORS settings - development defaults and production origins
    BACKEND_CORS_ORIGINS: Union[List[str], str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "https://pay-scope-lake.vercel.app",
        "https://payscope.vercel.app",
    ]
    BACKEND_CORS_ORIGIN_REGEX: str = r"^https:\/\/(pay-scope-lake|payscope)(-[a-zA-Z0-9_-]+)?\.vercel\.app$"

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        base_origins = [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "https://pay-scope-lake.vercel.app",
            "https://payscope.vercel.app",
        ]
        
        parsed_origins: List[str] = []
        if isinstance(v, str):
            v_str = v.strip()
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    parsed = json.loads(v_str)
                    if isinstance(parsed, list):
                        parsed_origins = [str(i).strip().rstrip("/") for i in parsed if str(i).strip()]
                except Exception:
                    parsed_origins = [i.strip().rstrip("/") for i in v_str.strip("[]").split(",") if i.strip()]
            else:
                parsed_origins = [i.strip().rstrip("/") for i in v_str.split(",") if i.strip()]
        elif isinstance(v, list):
            parsed_origins = [str(i).strip().rstrip("/") for i in v if str(i).strip()]
        
        all_origins = base_origins + parsed_origins
        seen = set()
        deduped: List[str] = []
        for origin in all_origins:
            if origin and origin != "*" and origin not in seen:
                seen.add(origin)
                deduped.append(origin)
        
        return deduped
    
    # Database Configuration (PostgreSQL 16 / Neon)
    POSTGRES_DB: str = "mandatelens"
    POSTGRES_USER: str = "mandatelens_dev"
    POSTGRES_PASSWORD: str = "mandatelens_dev_password"
    DATABASE_URL: str = "postgresql+psycopg://mandatelens_dev:mandatelens_dev_password@127.0.0.1:5432/mandatelens"
    DATABASE_URL_UNPOOLED: str = ""
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    TEST_DATABASE_URL: str = "postgresql+psycopg://mandatelens_dev:mandatelens_dev_password@127.0.0.1:5432/mandatelens_test"

    @field_validator("DATABASE_URL", "DATABASE_URL_UNPOOLED", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: str) -> str:
        if not v:
            return v
        # Detect unconfigured placeholder URLs (such as example 'ep-xyz.neon.tech' or 'CHANGE_ME')
        if "ep-xyz.neon.tech" in v or "CHANGE_ME" in v:
            return "postgresql+psycopg://mandatelens_dev:mandatelens_dev_password@127.0.0.1:5432/mandatelens"
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg://", 1)
        elif v.startswith("postgresql://") and not v.startswith("postgresql+psycopg://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def migration_database_url(self) -> str:
        """Use Neon's direct endpoint for DDL while runtime queries stay pooled."""
        if self.DATABASE_URL_UNPOOLED:
            return self.DATABASE_URL_UNPOOLED
        from sqlalchemy.engine import make_url
        url = make_url(self.DATABASE_URL)
        if url.host and url.host.endswith(".neon.tech") and "-pooler." in url.host:
            return url.set(host=url.host.replace("-pooler.", ".", 1)).render_as_string(hide_password=False)
        return self.DATABASE_URL
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
