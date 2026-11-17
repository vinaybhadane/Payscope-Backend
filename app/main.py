"""PayScope FastAPI Application Entry Point."""
from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.api.v1.router import api_v1_router

logger = logging.getLogger('app.main')


def run_migrations():
    import sys
    import traceback
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.migration_database_url.replace("%", "%%"))
    print("INFO: Starting database migrations to head...", file=sys.stderr, flush=True)
    try:
        command.upgrade(config, "head")
        print("INFO: Database migrations completed successfully.", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"CRITICAL: Database migration failed with error: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Do not accept traffic until migrations complete; failures stop startup."""
    from starlette.concurrency import run_in_threadpool
    import sys
    import traceback
    if settings.RUN_MIGRATIONS:
        try:
            await run_in_threadpool(run_migrations)
        except Exception as exc:
            print(f"CRITICAL: Application lifespan startup failed: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise
    from app.analytics.email_alerts import email_worker
    worker = asyncio.create_task(email_worker()) if settings.EMAIL_WORKER_ENABLED else None
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
    from app.graph.driver import close_neo4j_driver
    close_neo4j_driver()


def create_application() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url=f"{settings.API_V1_STR}/docs",
        redoc_url=f"{settings.API_V1_STR}/redoc",
        lifespan=lifespan,
    )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.exception("Unhandled request failure: %s", request.url.path, exc_info=exc)
        response = JSONResponse(status_code=500, content={"detail": "An unexpected error occurred. Please try again."})
        origin = request.headers.get("origin")
        policy = CORSMiddleware(application, allow_origins=settings.BACKEND_CORS_ORIGINS, allow_origin_regex=settings.BACKEND_CORS_ORIGIN_REGEX)
        if origin and policy.is_allowed_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    # Root status endpoint for cloud health & quick browser checks
    @application.get('/', tags=['Root'])
    def root():
        return {
            'name': settings.PROJECT_NAME,
            'status': 'healthy',
            'version': settings.VERSION,
            'docs': f'{settings.API_V1_STR}/docs',
            'health': f'{settings.API_V1_STR}/health',
        }

    # Include API routers
    application.include_router(api_v1_router, prefix=settings.API_V1_STR)

    @application.middleware("http")
    async def resolve_identity_middleware(request: Request, call_next):
        from starlette.concurrency import run_in_threadpool
        from app.core.auth import verify_token, current_identity
        auth_header = request.headers.get("Authorization")
        token_ctx = None
        if auth_header and auth_header.startswith("Bearer "):
            raw_token = auth_header[7:].strip()
            if raw_token:
                try:
                    identity = await run_in_threadpool(verify_token, raw_token)
                    token_ctx = current_identity.set(identity)
                except Exception:
                    pass
        try:
            return await call_next(request)
        finally:
            if token_ctx is not None:
                current_identity.reset(token_ctx)

    return application


app = create_application()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_origin_regex=settings.BACKEND_CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
