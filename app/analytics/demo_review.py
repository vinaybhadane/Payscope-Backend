"""Deterministic, Gemini-free analysis for isolated guest demo workspaces."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.commitment_detector import CommitmentDetector
from app.analytics.insights import run_insight_detection
from app.models.commitment import Commitment
from app.models.transaction import Transaction
from app.models.workspace_intelligence import WorkspaceIntelligence


def save_predefined_demo_report(db: Session, user_id: uuid.UUID) -> dict:
    transaction_count = db.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user_id,
            Transaction.direction.ilike("debit"),
            Transaction.transaction_status.ilike("successful"),
        )
    ) or 0
    commitments = list(
        db.scalars(
            select(Commitment)
            .where(Commitment.user_id == user_id)
            .order_by(Commitment.display_name)
        ).all()
    )
    currencies: dict[str, float] = {}
    for commitment in commitments:
        if commitment.status not in ("active", "user_confirmed", "candidate", "review_required"):
            continue
        annualised = float(commitment.annualised_amount or 0)
        if annualised:
            currencies[commitment.currency] = currencies.get(commitment.currency, 0) + annualised / 12

    monthly_totals = ", ".join(
        f"{currency} {amount:,.0f}/month" for currency, amount in sorted(currencies.items())
    ) or "No recurring total is available yet."
    report = {
        "overview": (
            f"This sample workspace uses built-in payment-pattern rules to review "
            f"{transaction_count} synthetic debit records and identify {len(commitments)} recurring payments. "
            "It is a predefined demo analysis and does not call Gemini."
        ),
        "spending_analysis": (
            f"The sample commitments currently add up to about {monthly_totals} based on their observed periods. "
            "Open Subscriptions to review the amount and period for each sample payment."
        ),
        "savings_analysis": (
            "Choose a sample subscription in the Savings Simulator to see an illustrative annual estimate. "
            "This is a what-if calculation; it does not cancel a service or change an account."
        ),
        "limitations": [
            "Sample subscription amounts are illustrative INR values, not current company plan prices.",
            "Demo analysis uses synthetic sample data and deterministic rules; no Gemini key or AI provider is used.",
            "The sample findings are illustrative and are not a review of real financial records.",
        ],
        "transaction_count": int(transaction_count),
        "subscriptions": [
            {
                "commitment_id": str(commitment.id),
                "name": commitment.display_name,
                "currency": commitment.currency,
                "amount": str(commitment.representative_amount or 0),
                "cadence": commitment.cadence,
                "next_expected_date": commitment.next_expected_date.isoformat()
                if commitment.next_expected_date
                else None,
                "explanation": commitment.detection_explanation
                or "Recurring pattern identified in synthetic sample payments.",
                "insights": [],
                "savings_recommendation": "Illustrative demo: select this sample plan to estimate the cost avoided if you cancelled it.",
            }
            for commitment in commitments
        ],
    }

    state = db.get(WorkspaceIntelligence, user_id)
    if state is None:
        state = WorkspaceIntelligence(user_id=user_id)
        db.add(state)
    state.status = "completed"
    state.model = "predefined-demo-v1"
    state.report = report
    state.error = None
    state.generated_at = datetime.now(timezone.utc)
    db.commit()
    return report


def review_demo_workspace(db: Session, user_id: uuid.UUID) -> dict:
    """Recompute a guest workspace with local rules and predefined report copy."""
    preset = db.scalar(select(func.count(Commitment.id)).where(
        Commitment.user_id == user_id, Commitment.detector_version == 'predefined-demo-v2'))
    if preset:
        save_predefined_demo_report(db, user_id)
        return {'analysis_status': 'completed', 'commitments_discovered': int(preset),
                'active_count': int(preset), 'model': 'predefined-demo-v1',
                'message': 'Predefined sample analysis refreshed. Sample amounts are illustrative.'}
    discovery = CommitmentDetector(db).discover_user_commitments(user_id)
    insight_summary = run_insight_detection(db, user_id)
    save_predefined_demo_report(db, user_id)
    return {
        "analysis_status": "completed",
        "commitments_discovered": discovery.total_discovered,
        "active_count": discovery.active_count,
        "candidate_count": discovery.candidate_count,
        "needs_review_count": discovery.needs_review_count,
        "insights_detected": insight_summary.total_detected,
        "model": "predefined-demo-v1",
        "message": "Sample analysis refreshed with built-in rules; Gemini was not used.",
    }
