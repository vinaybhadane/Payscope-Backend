"""A single, evidence-backed read model for the native Android dashboard."""
from datetime import date
from decimal import Decimal
from statistics import median
from app.analytics.financial_impact.normalization import calculate_normalized_costs
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case
from sqlalchemy.orm import Session, selectinload
from app.db.session import get_db
from app.models.commitment import Commitment
from app.models.transaction import Transaction
from app.models.import_record import ImportRecord
from app.models.workspace_intelligence import WorkspaceIntelligence
from app.repositories.user_repository import resolve_workspace_user_id
from app.api.v1.routes.dashboard import _build_dashboard_summary

router = APIRouter(prefix="/mobile", tags=["Android"])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    uid = resolve_workspace_user_id(db)
    # Statement activity exists independently of whether recurrence was proven.
    # Aggregate in SQL and bound the visible ledger instead of loading every row.
    transaction_count = db.scalar(select(func.count(Transaction.id)).where(Transaction.user_id == uid))
    import_count = db.scalar(select(func.count(ImportRecord.id)).where(ImportRecord.user_id == uid,
        ImportRecord.status.in_(["completed", "partially_completed"])))
    totals = db.execute(select(Transaction.currency,
        func.sum(case((Transaction.direction.ilike("debit"), Transaction.amount), else_=0)).label("debits"),
        func.sum(case((Transaction.direction.ilike("credit"), Transaction.amount), else_=0)).label("credits"))
        .where(Transaction.user_id == uid, Transaction.transaction_status.ilike("successful"))
        .group_by(Transaction.currency).order_by(Transaction.currency)).all()
    recent = db.scalars(select(Transaction).where(Transaction.user_id == uid)
        .order_by(Transaction.transaction_date.desc(), Transaction.id).limit(50)).all()
    ai_state = db.get(WorkspaceIntelligence, uid)
    summary = _build_dashboard_summary(db, uid)
    commitments = list(db.scalars(select(Commitment).where(Commitment.user_id == uid)
        .options(selectinload(Commitment.transactions), selectinload(Commitment.price_events),
                 selectinload(Commitment.merchant), selectinload(Commitment.insights))
        .order_by(Commitment.representative_amount.desc())).all())
    items, upcoming, hikes = [], [], []
    daily, seen_tx = {}, set()
    today = date.today()
    for c in commitments:
        txs = sorted(c.transactions, key=lambda t: t.transaction_date)
        for tx in txs:
            if tx.id not in seen_tx and tx.currency == "INR" and tx.direction.lower() == "debit" and tx.transaction_status.lower() == "successful":
                day = tx.transaction_date.date().isoformat()
                daily[day] = daily.get(day, Decimal("0")) + tx.amount
                seen_tx.add(tx.id)
        if c.status in ("inactive", "ended_user_recorded", "marked_not_recurring"):
            continue
        gaps = [(b.transaction_date.date()-a.transaction_date.date()).days for a,b in zip(txs,txs[1:])]
        monthly, annual, _ = calculate_normalized_costs(c.representative_amount, c.cadence,
            [t.amount for t in txs], median(gaps) if gaps else None)
        # A numeric price change must be grounded in chronological evidence.
        previous = None
        has_insight = any(i.insight_type == "price_increase" and i.status == "open" for i in c.insights)
        if has_insight and len(txs) >= 2 and txs[-1].amount > txs[-2].amount:
            previous = txs[-2].amount
        name = c.merchant.canonical_name if c.merchant else c.display_name
        pct = float((c.representative_amount - previous) / previous * 100) if previous else None
        item = dict(id=str(c.id), display_name=c.display_name, merchant_name=name,
            representative_amount=c.representative_amount, normalized_monthly_cost=monthly, annualised_amount=annual,
            currency=c.currency, cadence=c.cadence, category=c.category or c.commitment_type,
            status=c.status, confidence_score=c.confidence_score,
            next_expected_date=c.next_expected_date, payment_rail=txs[-1].payment_channel if txs else "Unknown",
            has_price_hike=previous is not None, previous_amount=previous,
            price_hike_percentage=pct, reason_explanation=c.detection_explanation)
        items.append(item)
        if c.currency == "INR" and c.status in ("active", "user_confirmed"):
            if c.next_expected_date and 0 <= (c.next_expected_date - today).days <= 30:
                upcoming.append(dict(id=str(c.id), merchant_name=name, amount=c.representative_amount,
                    due_date=c.next_expected_date, days_remaining=(c.next_expected_date-today).days,
                    cadence=c.cadence, logo_key=name.lower()))
            if previous:
                factor = annual / c.representative_amount if c.representative_amount else 0
                hikes.append(dict(id=str(c.id), merchant_name=name, old_amount=previous,
                    new_amount=c.representative_amount, percentage_change=pct,
                    effective_date=txs[-1].transaction_date.date(), logo_key=name.lower(),
                    annualized_impact=(c.representative_amount-previous)*factor))
    return dict(monthly_recurring_spend=summary.kpis.monthly_recurring_spend,
        annualized_spend=summary.kpis.annualized_recurring_spend,
        active_subscriptions_count=summary.kpis.active_commitments_count,
        potential_savings_annual=summary.kpis.potential_annual_savings,
        price_hikes_count=len(hikes), currency="INR", commitments=items,
        historical_months=summary.financial_overview.historical_months,
        historical_amounts=summary.financial_overview.historical_amounts,
        category_distribution=summary.category_distribution, daily_spend=daily,
        upcoming=sorted(upcoming, key=lambda p: p["due_date"]), price_hikes=hikes,
        imported_transactions_count=transaction_count, imported_statements_count=import_count,
        analysis_status=ai_state.status if ai_state else "empty",
        analysis_error=ai_state.error if ai_state else None,
        statement_totals=[dict(currency=t.currency, debits=t.debits, credits=t.credits) for t in totals],
        recent_transactions=[dict(id=str(t.id), date=t.transaction_date.date(), description=t.raw_descriptor,
            amount=t.amount, currency=t.currency, direction=t.direction.lower(), status=t.transaction_status)
            for t in recent])
