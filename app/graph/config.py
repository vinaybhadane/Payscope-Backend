"""Configuration settings for Neo4j Graph Intelligence."""

import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure .env is resolved regardless of working directory
for candidate in [
    Path.cwd() / ".env",
    Path(__file__).resolve().parents[2] / ".env",
    Path(__file__).resolve().parents[3] / ".env",
]:
    if candidate.exists():
        load_dotenv(candidate, override=False)


class Neo4jSettings(BaseSettings):
    ENABLED: bool = os.getenv("NEO4J_ENABLED", "true").lower() in ("true", "1", "yes")
    URI: str = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    USERNAME: str = os.getenv("NEO4J_USERNAME", "neo4j")
    PASSWORD: str = os.getenv("NEO4J_PASSWORD", "payscope_password")
    DATABASE: str = os.getenv("NEO4J_DATABASE", "neo4j")
    MAX_CONNECTION_LIFETIME: int = int(
        os.getenv("NEO4J_MAX_CONNECTION_LIFETIME", "3600")
    )
    MAX_CONNECTION_POOL_SIZE: int = int(
        os.getenv("NEO4J_MAX_CONNECTION_POOL_SIZE", "50")
    )
    CONNECTION_TIMEOUT: float = 3.0
    CONNECTION_ACQUISITION_TIMEOUT: float = 4.0
    QUERY_TIMEOUT: float = 4.0
    SYNC_QUERY_TIMEOUT: float = 30.0
    SYNC_BATCH_SIZE: int = 500

    model_config = SettingsConfigDict(
        env_prefix="NEO4J_",
        env_file=(".env", "../.env"),
        extra="ignore",
    )


neo4j_settings = Neo4jSettings()
