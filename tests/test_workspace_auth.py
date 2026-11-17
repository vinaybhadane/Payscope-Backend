"""Exercise real session verification and tenant boundaries against the test database."""
from datetime import datetime, timezone
import time
import uuid
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import require_identity, verify_token
from app.core.config import settings
from app.main import app, run_migrations
from app.models.commitment import Commitment


@pytest.fixture
def authenticated_client(client):
    app.dependency_overrides.pop(require_identity, None)
    return client


def guest(client):
    response = client.post("/api/v1/auth/guest")
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_api_requires_verified_session(authenticated_client):
    client = authenticated_client
    assert client.get("/api/v1/commitments").status_code == 401
    assert client.get("/api/v1/commitments", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_guest_workspaces_are_distinct_and_cannot_be_selected(authenticated_client):
    client = authenticated_client
    first, second = guest(client), guest(client)
    owner = client.get("/api/v1/auth/me", headers=first).json()["id"]
    other = client.get("/api/v1/auth/me", headers=second).json()["id"]
    assert owner != other
    for path in ["dashboard/summary", "graph/explorer", "insights", "financial-impact/summary", "savings/summary"]:
        assert client.get(f"/api/v1/{path}?user_id={owner}", headers=second).status_code == 403


def test_id_lookup_does_not_expose_other_workspace(authenticated_client, db_session):
    client = authenticated_client
    first, second = guest(client), guest(client)
    owner = uuid.UUID(client.get("/api/v1/auth/me", headers=first).json()["id"])
    commitment = Commitment(user_id=owner, display_name="Private subscription", commitment_type="subscription",
                            cadence="monthly", amount_pattern="fixed", status="active", currency="INR",
                            first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc))
    db_session.add(commitment)
    db_session.commit()
    commitment_id = commitment.id
    response = client.get(f"/api/v1/commitments/{commitment_id}", headers=second)
    assert response.status_code == 404


def test_expired_guest_token_rejected():
    token = jwt.encode({"sub": str(uuid.uuid4()), "iss": "payscope-guest", "aud": "payscope-api",
                        "iat": int(time.time()) - 100, "exp": int(time.time()) - 10},
                       settings.API_SESSION_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        verify_token(token)
    assert error.value.status_code == 401


def test_firebase_verified_subject_is_workspace_identity(monkeypatch):
    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", "test-project")
    claims = {"sub": "firebase-user", "iss": "https://securetoken.google.com/test-project"}
    token = jwt.encode(claims, "irrelevant-test-key-at-least-32-characters", algorithm="HS256")
    with patch("app.core.auth.verify_firebase_token", return_value=claims) as verify:
        identity = verify_token(token)
        verify.assert_called_once()
        assert identity.workspace_key.startswith("firebase:")
        assert identity.is_demo is False
    with patch("app.core.auth.verify_firebase_token", side_effect=ValueError("wrong signature")):
        with pytest.raises(HTTPException):
            verify_token(token)


def test_disallowed_origin_not_reflected(authenticated_client):
    response = authenticated_client.get("/api/v1/health", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in response.headers


def test_private_aliases_are_not_visible_to_other_guests(authenticated_client, db_session):
    from app.models.merchant import Merchant
    client = authenticated_client
    first, second = guest(client), guest(client)
    merchant = Merchant(canonical_name="Synthetic private alias merchant", category="Software")
    db_session.add(merchant)
    db_session.commit()
    payload = {"merchant_id": str(merchant.id), "alias_text": "PRIVATE TEST ALIAS",
               "matcher_type": "exact", "confidence_band": "high"}
    created = client.post("/api/v1/merchant-aliases", json=payload, headers=first)
    assert created.status_code == 201, created.text
    alias_id = created.json()["id"]
    visible = client.get(f"/api/v1/merchants/{merchant.id}/aliases", headers=second)
    assert visible.status_code == 200
    assert all(item["id"] != alias_id for item in visible.json())
    assert client.patch(f"/api/v1/merchant-aliases/{alias_id}", json={"is_active": False}, headers=second).status_code == 404


def test_startup_migration_failure_propagates():
    with patch("alembic.command.upgrade", side_effect=RuntimeError("migration failed")):
        with pytest.raises(RuntimeError, match="migration failed"):
            run_migrations()
