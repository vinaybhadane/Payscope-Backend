"""Regression tests for recurring-payment requirement gaps."""
import uuid
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import pytest
from app.analytics.cadence_classifier import classify_cadence
from app.analytics.amount_analyzer import analyze_amounts
from app.analytics.interval_analyzer import analyze_intervals
from app.analytics.insights.usage_analyzer import analyze_usage, analyze_commitment_usage
from app.analytics.insights.post_cancellation_detector import detect_post_cancellation_charge
from app.analytics.insights.price_change_detector import detect_price_increase
from app.analytics.insights.trial_conversion_detector import detect_trial_conversion
from app.analytics.financial_impact.normalization import calculate_normalized_costs
from app.analytics.notification_discovery import discover_notification_candidates, notification_cadence
from app.analytics.cancellations.mechanism_classifier import classify_payment_mechanism
from app.analytics.cancellations.playbook_resolver import resolve_playbook
from app.analytics.cancellations.schemas import PaymentMechanism as PM
from app.analytics.cancellations.config import ALL_PLAYBOOKS
from app.models.commitment import Commitment
from app.models.payment_notification import PaymentNotification
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)

def commitment(**kw):
    return Commitment(**(dict(id=uuid.uuid4(), user_id=uuid.uuid4(), display_name="Example Service",
        commitment_type="subscription", cadence="monthly", currency="INR", status="active",
        representative_amount=Decimal("100"), detection_reason_codes=[]) | kw))

def tx(amount="100", when=NOW, **kw):
    return NS(**(dict(id=uuid.uuid4(), amount=Decimal(amount), transaction_date=when,
        direction="debit", transaction_status="successful", payment_channel="CARD",
        raw_descriptor="Example Service", normalised_descriptor="Example Service", currency="INR", merchant_id=None) | kw))

def cov(c, **kw):
    return NS(**(dict(commitment_id=c.id, user_id=c.user_id, coverage_start=date(2026,5,1),
        coverage_end=date(2026,10,31), coverage_complete=True, source_label="synthetic activity") | kw))

def note(c, **kw):
    return PaymentNotification(**(dict(id=uuid.uuid4(), user_id=c.user_id, merchant_hint=c.display_name,
        event_type="mandate_created", event_timestamp=NOW-timedelta(days=30), message_text="Synthetic monthly recurring mandate",
        payment_mechanism="upi_autopay", currency="INR") | kw))

def test_usage_evidence_is_scoped_to_service():
    c = commitment(); other = commitment(user_id=c.user_id)
    assert analyze_commitment_usage(c, [cov(other)], []).status == "USAGE_UNAVAILABLE"
    ev = NS(commitment_id=other.id, user_id=c.user_id, event_at=NOW-timedelta(days=3))
    assert analyze_commitment_usage(c, [cov(c)], [ev]).status == "POTENTIALLY_UNUSED"

def test_usage_requires_charge_and_latest_observation():
    c = commitment()
    assert analyze_usage(c, [], [cov(c)], []).status == "INSUFFICIENT_COVERAGE"
    assert analyze_usage(c, [tx()], [cov(c)], []).status == "POTENTIALLY_UNUSED"
    recent = cov(c, coverage_start=date(2026,10,1), coverage_end=date(2026,11,1))
    assert analyze_commitment_usage(c, [cov(c), recent], []).status == "INSUFFICIENT_COVERAGE"

def test_other_services_cancellation_never_flags_charge():
    c = commitment()
    cancel = NS(commitment_id=uuid.uuid4(), user_id=c.user_id, event_timestamp=NOW-timedelta(days=3), confirmation_type="user_confirmed")
    assert detect_post_cancellation_charge(c, [tx()], [cancel], []) is None

def test_irregular_estimate_is_nonzero_and_explained():
    monthly, annual, method = calculate_normalized_costs(Decimal("100"), "irregular", median_interval_days=50)
    assert (monthly, annual) == (Decimal("60.88"), Decimal("730.50"))
    assert "Estimate" in method and "50.0 days" in method
    assert calculate_normalized_costs(Decimal("100"), "unknown")[1] == 0

def test_widely_irregular_repeats_are_reviewable_candidates():
    dates = [date(2026,1,1)+timedelta(days=d) for d in [0,42,110,153,230]]
    result = classify_cadence(analyze_intervals(dates), analyze_amounts([Decimal("100")]*5))
    assert result.cadence == "irregular" and result.is_candidate_only

def test_shifted_calendar_month_billing_is_detected():
    dates = [date(2026,1,31),date(2026,2,25),date(2026,3,22),date(2026,4,19)]
    result = classify_cadence(analyze_intervals(dates), analyze_amounts([Decimal("100")]*4))
    assert result.cadence == "monthly"

def test_small_sustained_increase_and_flat_prices():
    c = commitment()
    transactions = [tx(a, NOW-timedelta(days=30*(3-i))) for i,a in enumerate(["100","100","101","101"])]
    result = detect_price_increase(c, transactions, [])
    assert result and result.percentage_change == Decimal("1.00")
    assert detect_price_increase(c, [tx() for _ in range(4)], []) is None

@pytest.mark.parametrize("channel,desc", [("UPI","UPI/shop@upi"),("CARD","POS/VISA/SIMPLE STORE")])
def test_ordinary_purchase_is_not_auto_debit(channel, desc):
    assert classify_payment_mechanism(commitment(), [tx(payment_channel=channel, raw_descriptor=desc)])[0] == PM.UNKNOWN

def test_mixed_evidence_requires_confirmation():
    assert classify_payment_mechanism(commitment(), [tx(raw_descriptor="UPI/AUTOPAY"),tx(raw_descriptor="NACH/UMRN")])[0] == PM.UNKNOWN

def test_notification_only_cancellation_mechanism():
    c = commitment()
    assert classify_payment_mechanism(c, [], [note(c)])[0] == PM.UPI_AUTOPAY

def test_merchant_playbook_cannot_override_mechanism():
    book = resolve_playbook(PM.UPI_AUTOPAY, commitment=commitment(display_name="Netflix"), playbook_key="merchant_netflix")
    assert book.payment_mechanism == PM.UPI_AUTOPAY and book.playbook_key == "generic_upi_autopay"

def test_every_playbook_has_escalation_and_monitoring():
    for book in ALL_PLAYBOOKS:
        assert {s.id.rsplit("_",1)[-1] for s in book.steps} >= {"nonresponse","escalation","monitor"}
        assert len({s.id for s in book.steps}) == len(book.steps)

def test_notification_candidate_idempotency_and_mandate_limit():
    c = commitment(); n = note(c, amount=Decimal("100000")); rows = []
    db = MagicMock()
    db.scalars.side_effect = lambda query: NS(all=lambda: [n] if "payment_notifications" in str(query) else rows)
    db.add.side_effect = lambda obj: rows.append(obj) if isinstance(obj,Commitment) else None
    with patch("app.analytics.notification_discovery.MerchantResolver.from_db") as factory:
        factory.return_value.resolve.return_value = NS(status="UNRESOLVED",merchant_id=None)
        first = discover_notification_candidates(db,c.user_id,[])
        second = discover_notification_candidates(db,c.user_id,[])
    assert len(rows) == 1 and first[0].id == second[0].id
    assert rows[0].representative_amount is None and rows[0].status == "candidate"
    assert rows[0].detection_reason_codes == [f"notification:{n.id}"]

def test_trial_plus_first_charge_can_generate_insight():
    c = commitment(status="candidate")
    assert detect_trial_conversion(c,[tx()],[note(c,event_type="trial_started")]) is not None

def test_notification_frequency_is_not_assumed():
    c = commitment()
    assert notification_cadence(note(c,message_text="Mandate limit is 10000")) == "unknown"
    assert notification_cadence(note(c,message_text="Billed every 3 months")) == "quarterly"

def test_notification_discovery_persists_and_links_first_charge_in_isolated_database():
    """Exercise ORM persistence and reruns, without a live PostgreSQL connection."""
    from sqlalchemy import create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.models.demo_user import DemoUser
    from app.models.transaction import Transaction
    from app.analytics.commitment_detector import CommitmentDetector

    @compiles(JSONB, "sqlite")
    def compile_jsonb(type_, compiler, **kw):
        return "JSON"

    engine = create_engine("sqlite://")
    # Savings has a pre-existing duplicate index definition unrelated to this flow.
    tables = [t for t in Base.metadata.sorted_tables if t.name != "savings_scenarios"]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as db:
        user = DemoUser(id=uuid.uuid4(), workspace_key="synthetic_gap_test", display_name="Synthetic Test")
        db.add(user)
        c = commitment(user_id=user.id)
        n = note(c, event_type="trial_started", message_text="Synthetic monthly trial", external_synthetic_id="SYN-GAP-001", is_synthetic=True)
        db.add(n)
        db.commit()
        result = CommitmentDetector(db).discover_user_commitments(user.id)
        assert result.total_discovered == 1 and result.candidate_count == 1
        candidate = result.commitments[0]
        assert candidate.representative_amount is None
        debit = Transaction(id=uuid.uuid4(), user_id=user.id, transaction_date=NOW,
            amount=Decimal("100"), currency="INR", direction="debit", transaction_status="successful",
            raw_descriptor="Example Service", normalised_descriptor="Example Service", is_synthetic=True)
        db.add(debit)
        db.commit()
        for _ in range(2):
            result = CommitmentDetector(db).discover_user_commitments(user.id)
            db.refresh(candidate)
            assert len(candidate.commitment_transactions) == 1
        assert db.query(Commitment).count() == 1
        # SQLite strips timezone metadata; normalize only these test values for the pure detector.
        n.event_timestamp = n.event_timestamp.replace(tzinfo=timezone.utc)
        debit.transaction_date = debit.transaction_date.replace(tzinfo=timezone.utc)
        assert detect_trial_conversion(candidate, [debit], [n]) is not None
    engine.dispose()


def test_small_price_increase_effective_date_is_first_higher_charge():
    c = commitment()
    transactions = [tx(a, NOW-timedelta(days=30*(5-i)))
                    for i,a in enumerate(["100","100","100","101","101","101"])]
    result = detect_price_increase(c, transactions, [])
    assert result.effective_date == transactions[3].transaction_date
    assert len(result.old_transactions) == 3


def test_first_paid_charge_is_linked_once_even_without_expiring_session():
    c = commitment(status="candidate")
    n = note(c, event_type="trial_started")
    debit = tx()
    db = MagicMock()
    db.scalars.side_effect = lambda query: NS(all=lambda: [n] if "payment_notifications" in str(query) else [c])
    with patch("app.analytics.notification_discovery.MerchantResolver.from_db") as factory:
        factory.return_value.resolve.return_value = NS(status="UNRESOLVED",merchant_id=None)
        for _ in range(2):
            discover_notification_candidates(db,c.user_id,[debit])
    assert [link.transaction_id for link in c.commitment_transactions] == [debit.id]


def test_financial_summary_includes_estimates_and_reports_unpriced_items():
    from app.analytics.financial_impact.impact_summary import get_user_financial_summary
    from app.analytics.financial_impact import impact_summary as module
    items = [NS(is_confirmed=True,is_candidate=False,normalized_monthly_cost=Decimal("100"),annualized_cost=Decimal("1200")),
             NS(is_confirmed=False,is_candidate=True,normalized_monthly_cost=Decimal("60"),annualized_cost=Decimal("720")),
             NS(is_confirmed=False,is_candidate=True,normalized_monthly_cost=Decimal("0"),annualized_cost=Decimal("0"))]
    with patch.object(module,"get_user_commitment_costs",return_value=items), \
         patch.object(module,"get_user_historical_spend",return_value=NS(periods=[])), \
         patch.object(module,"get_user_upcoming_expenditure",return_value=NS(total_upcoming_30d=Decimal("0"))), \
         patch.object(module,"get_user_savings_summary",return_value=NS(total_potential_annual_savings=Decimal("0"))):
        result = get_user_financial_summary(MagicMock(),uuid.uuid4())
    assert result.annualized_recurring_spend == Decimal("1200")
    assert result.estimated_annual_total == Decimal("1920")
    assert result.unpriced_commitments_count == 1
