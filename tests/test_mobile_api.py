"""Native dashboard and persistence contract, with only Gemini mocked."""
from unittest.mock import patch
from decimal import Decimal
from app.analytics.ai_review import Review


def import_reviewed(client):
    def provider(records):
        return Review(subscriptions=[dict(name="Orbit Storage", category="Cloud Storage",
            transaction_ids=[r["id"] for r in records], cadence="monthly", confidence="high",
            explanation="Two observed monthly payments.")], overview="Reviewed",
            spending_analysis="Observed", savings_analysis="No recommendation", limitations=[])
    csv = "Date,Description,Amount,Currency,Direction\n2026-09-03,ORBIT STORAGE,247,INR,debit\n2026-10-03,ORBIT STORAGE,247,INR,debit\n"
    with patch("app.analytics.ai_review.call_gemini", side_effect=provider):
        response = client.post("/api/v1/imports", files={"file": ("orbit.csv", csv, "text/csv")})
    assert response.status_code == 201, response.text
    assert response.json()["workspace_refresh"]["analysis_status"] == "completed"


def test_mobile_snapshot_uses_saved_evidence(client):
    import_reviewed(client)
    response = client.get("/api/v1/mobile/dashboard")
    assert response.status_code == 200, response.text
    data = response.json()
    assert Decimal(str(data["monthly_recurring_spend"])) == 247
    assert data["active_subscriptions_count"] == 1
    assert [Decimal(str(n)) for n in data["historical_amounts"]] == [247, 247]
    assert data["historical_months"] == ["Sep 2026", "Oct 2026"]
    assert data["commitments"][0]["category"] == "Cloud Storage"
    assert data["upcoming"] == []  # An old statement never invents future countdowns.
    assert data["price_hikes"] == []


def test_android_edits_and_savings_plan_persist(client):
    import_reviewed(client)
    cid = client.get("/api/v1/mobile/dashboard").json()["commitments"][0]["id"]
    response = client.patch(f"/api/v1/commitments/{cid}", json={"representative_amount": 300,
        "category": "Storage", "cadence": "annual", "display_name": "Orbit annual"})
    assert response.status_code == 200, response.text
    updated = client.get("/api/v1/mobile/dashboard").json()
    assert Decimal(str(updated["monthly_recurring_spend"])) == 25
    assert updated["commitments"][0]["category"] == "Storage"
    response = client.post("/api/v1/savings/scenarios", json={"name": "Review storage",
        "selected_commitment_ids": [cid], "annual_savings": 999999})
    assert response.status_code == 201, response.text
    assert Decimal(str(response.json()["annual_savings"])) == 300
    saved = client.get("/api/v1/savings/scenarios").json()
    assert saved[0]["selected_commitment_ids"] == [cid]
    assert client.delete(f'/api/v1/savings/scenarios/{saved[0]["id"]}').status_code == 204
    assert client.get("/api/v1/savings/scenarios").json() == []


def test_unknown_or_foreign_commitments_cannot_be_saved(client):
    response = client.post("/api/v1/savings/scenarios", json={"name": "Invalid",
        "selected_commitment_ids": ["00000000-0000-0000-0000-000000000001"]})
    assert response.status_code == 400


def test_statement_transactions_remain_visible_without_recurring_subscriptions(client):
    review = Review(subscriptions=[], overview="No recurring pattern established.",
        spending_analysis="Two one-off payments.", savings_analysis="No recurring savings.", limitations=[])
    csv = ("Date,Description,Amount,Currency,Direction\n"
           "2026-11-01,Grocery purchase,850,INR,debit\n"
           "2026-11-02,Refund,100,INR,credit\n"
           "2026-11-03,One-off book,12,USD,debit\n")
    with patch("app.analytics.ai_review.call_gemini", return_value=review):
        uploaded = client.post("/api/v1/imports", files={"file": ("one-off.csv", csv, "text/csv")})
    assert uploaded.status_code == 201, uploaded.text
    import_id = uploaded.json()["import_id"]
    persisted = client.get(f"/api/v1/imports/{import_id}/status").json()
    assert persisted["status"] == "completed" and persisted["accepted_count"] == 3
    snapshot = client.get("/api/v1/mobile/dashboard").json()
    assert snapshot["commitments"] == []
    assert snapshot["imported_transactions_count"] == 3
    assert snapshot["imported_statements_count"] == 1
    assert snapshot["analysis_status"] == "completed"
    assert len(snapshot["recent_transactions"]) == 3
    assert {t["description"] for t in snapshot["recent_transactions"]} == {"Grocery purchase", "Refund", "One-off book"}
    assert snapshot["statement_totals"] == [
        {"currency": "INR", "debits": 850, "credits": 100},
        {"currency": "USD", "debits": 12, "credits": 0},
    ]


def test_ai_failure_preserves_visible_statement_data(client):
    csv = "Date,Description,Amount,Currency,Direction\n2026-11-01,Grocery purchase,850,INR,debit\n"
    with patch("app.analytics.ai_review.call_gemini", side_effect=ValueError("Provider unavailable")):
        uploaded = client.post("/api/v1/imports", files={"file": ("saved.csv", csv, "text/csv")})
    assert uploaded.status_code == 201
    snapshot = client.get("/api/v1/mobile/dashboard").json()
    assert snapshot["analysis_status"] == "failed"
    assert snapshot["imported_transactions_count"] == 1
    assert snapshot["recent_transactions"][0]["description"] == "Grocery purchase"
    assert snapshot["statement_totals"][0]["debits"] == 850
