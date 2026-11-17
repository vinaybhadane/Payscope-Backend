"""Pytest configuration and test database fixtures."""
import os
import pytest
from typing import Generator
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.core.auth import require_identity

# Explicit safety check: test engine must point strictly to test database
assert "mandatelens_test" in settings.TEST_DATABASE_URL, (
    f"FATAL: Safety check failed! TEST_DATABASE_URL points to non-test database: {settings.TEST_DATABASE_URL}"
)

# Test database engine connected to mandatelens_test
test_engine = create_engine(
    settings.TEST_DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
)

assert "mandatelens_test" in str(test_engine.url), (
    f"FATAL: Safety check failed! test_engine URL is not mandatelens_test: {test_engine.url}"
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def engine():
    """Session-scoped database engine fixture."""
    yield test_engine


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Function-scoped clean database session fixture.
    
    Truncates test tables after each test to ensure test isolation.
    """
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        # Clean up data between tests
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE TABLE cancellation_cases, savings_scenarios, insight_evidence, commitment_insights, price_events, cancellation_events, usage_events, usage_coverage, commitment_transactions, commitments, manual_review_items, descriptor_aliases, payment_notifications, transactions, imports, accounts, audit_events, merchants, demo_users CASCADE;"
                )
            )
        session.close()


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient fixture overriding get_db with the test database session."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_test_runtime(monkeypatch):
    # TestClient must never migrate the application database on startup.
    from app.graph.config import neo4j_settings
    monkeypatch.setattr(neo4j_settings, "ENABLED", False)
    monkeypatch.setattr(settings, "RUN_MIGRATIONS", False)
    monkeypatch.setattr(settings, "EMAIL_WORKER_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "API_SESSION_SECRET", "test-only-session-signing-key-at-least-32-characters")
    app.dependency_overrides[require_identity] = lambda: None
    yield
    app.dependency_overrides.pop(require_identity, None)


@pytest.fixture(scope="session", autouse=True)
def migrate_test_database():
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    api_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.TEST_DATABASE_URL.replace("-pooler.", "."))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def legacy_detection_test_double(request, monkeypatch):
    """Keep pre-AI lifecycle fixtures offline; AI behavior has separate checks.

    These three suites assert the original synthetic detector's exact merchant
    names, candidates and anomaly types. Replace only their review boundary.
    """
    if request.module.__name__.split('.')[-1] not in {
        'test_commitments_api', 'test_insights_api', 'test_import_refresh'
    }:
        return
    def review(db, user_id):
        from app.analytics.commitment_detector import CommitmentDetector
        from app.analytics.insights.insight_orchestrator import run_insight_detection
        discovery = CommitmentDetector(db).discover_user_commitments(user_id)
        run_insight_detection(db, user_id)
        db.commit()
        return dict(analysis_status='completed', commitments_discovered=discovery.total_discovered,
                    active_count=discovery.active_count, candidate_count=discovery.candidate_count,
                    needs_review_count=discovery.needs_review_count)
    monkeypatch.setattr('app.analytics.ai_review.review_workspace', review)
    monkeypatch.setattr('app.ingestion.refresh_service.review_workspace', review)
