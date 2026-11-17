"""Standalone Render entry point: python -m app.render_server."""
import os
from collections.abc import Mapping
from app.core.config import settings


def server_options(environment: Mapping[str, str] | None = None) -> dict:
    env = os.environ if environment is None else environment
    try:
        port = int(env.get("PORT", "8000"))
    except ValueError as exc:
        raise ValueError("PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535.")
    return dict(host="0.0.0.0", port=port, workers=1, proxy_headers=True,
                limit_concurrency=8, timeout_keep_alive=5, timeout_graceful_shutdown=25)


def validate_configuration(config=settings) -> None:
    missing = []
    for name in ("DATABASE_URL", "FIREBASE_PROJECT_ID", "GEMINI_API_KEY", "GEMINI_MODEL"):
        if not getattr(config, name, "").strip():
            missing.append(name)
    if config.ALLOW_GUEST_SESSIONS and len(config.API_SESSION_SECRET) < 32:
        missing.append("API_SESSION_SECRET (at least 32 characters)")
    if config.DATABASE_URL.strip():
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError
        try:
            database_url = make_url(config.DATABASE_URL)
            if not database_url.host or database_url.host in ("localhost", "127.0.0.1", "::1"):
                missing.append("DATABASE_URL (use the hosted Neon connection)")
        except (ArgumentError, ValueError):
            missing.append("DATABASE_URL (use a valid PostgreSQL connection string)")
    if missing:
        # Configuration names only; credentials must never appear in the error.
        raise ValueError("Set these Render environment variables: " + ", ".join(missing))


def main():
    validate_configuration()
    import uvicorn
    uvicorn.run("app.main:app", **server_options())


if __name__ == "__main__":
    main()
