"""Observed spending remains useful even when no recurrence is confirmed."""
import uuid
from decimal import Decimal
from sqlalchemy import Date, case, cast, func, literal, select
from app.models.transaction import Transaction
from app.models.commitment import Commitment
from app.models.commitment_transaction import CommitmentTransaction


def statement_spending(db, user_id, report):
    categories, merchants = {}, {}
    for label in report.get('transaction_classifications', []):
        for value in label.get('transaction_ids', []):
            try:
                key = uuid.UUID(value)
            except (ValueError, TypeError):
                continue
            categories[key] = label['category']
            merchants[key] = label['merchant_name']
    links = db.execute(select(CommitmentTransaction.transaction_id, Commitment.display_name, Commitment.category)
        .join(Commitment, Commitment.id == CommitmentTransaction.commitment_id)
        .where(Commitment.user_id == user_id)).all()
    for key, name, group in links:
        categories.setdefault(key, group or 'Uncategorized')
        merchants.setdefault(key, name)
    category = case(categories, value=Transaction.id, else_='Uncategorized') if categories else literal('Uncategorized')
    merchant = case(merchants, value=Transaction.id, else_=Transaction.raw_descriptor) if merchants else Transaction.raw_descriptor
    scope = (Transaction.user_id == user_id, Transaction.currency == 'INR',
             Transaction.direction.ilike('debit'), Transaction.transaction_status.ilike('successful'))
    day = cast(Transaction.transaction_date, Date)
    daily = db.execute(select(day, func.sum(Transaction.amount)).where(*scope).group_by(day).order_by(day)).all()
    monthly = {}
    for when, amount in daily:
        month = when.replace(day=1)
        monthly[month] = monthly.get(month, Decimal('0')) + amount
    grouped_categories = db.execute(select(category, func.sum(Transaction.amount)).where(*scope).group_by(category)).all()
    grouped_merchants = db.execute(select(merchant, func.sum(Transaction.amount)).where(*scope)
        .group_by(merchant).order_by(func.sum(Transaction.amount).desc()).limit(10)).all()
    count = len(monthly)
    total = sum(monthly.values(), Decimal('0'))
    return dict(currency='INR', total_debits=total, monthly_average=total/count if count else 0,
        period_start=daily[0][0] if daily else None, period_end=daily[-1][0] if daily else None,
        historical_months=[m.strftime('%b %Y') for m in monthly], historical_amounts=list(monthly.values()),
        daily_spend={d.isoformat(): a for d, a in daily}, category_distribution=dict(grouped_categories),
        top_merchants=[dict(name=name, total_spend=amount, monthly_average=amount/count if count else 0)
                       for name, amount in grouped_merchants],
        overview=report.get('overview', ''), spending_analysis=report.get('spending_analysis', ''),
        savings_analysis=report.get('savings_analysis', ''), limitations=report.get('limitations', []))
