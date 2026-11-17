"""Evidence-based recurring payment mechanism classification."""
import re
from typing import List, Optional, Tuple
from app.analytics.cancellations.schemas import PaymentMechanism
from app.models.commitment import Commitment
from app.models.transaction import Transaction


def classify_payment_mechanism(
    commitment: Commitment, transactions: Optional[List[Transaction]] = None, notifications=None,
) -> Tuple[PaymentMechanism, float]:
    from app.analytics.notification_discovery import name_matches
    explicit = {"upi_autopay": PaymentMechanism.UPI_AUTOPAY, "card_si": PaymentMechanism.CARD_SI,
                "e_nach": PaymentMechanism.E_NACH, "enach": PaymentMechanism.E_NACH,
                "nach": PaymentMechanism.E_NACH}
    matched = [n for n in (notifications or []) if n.user_id == commitment.user_id
               and name_matches(n.merchant_hint, commitment.display_name)
               and n.event_type in ("mandate_created", "mandate_registered", "upcoming_debit", "pre_debit")]
    if matched:
        latest = max(matched, key=lambda n: n.event_timestamp)
        mechanism = explicit.get((latest.payment_mechanism or "").lower())
        if mechanism:
            return mechanism, 0.95
    transactions = transactions if transactions is not None else list(commitment.transactions or [])
    if not transactions:
        return PaymentMechanism.UNKNOWN, 0.0
    votes = {m: 0 for m in (PaymentMechanism.UPI_AUTOPAY, PaymentMechanism.E_NACH, PaymentMechanism.CARD_SI)}
    for tx in transactions:
        channel = (tx.payment_channel or "").upper()
        text = f"{channel} {tx.raw_descriptor or ''} {tx.normalised_descriptor or ''}".upper()
        # A normal UPI/card purchase is not evidence of an automatic debit.
        if re.search(r"\b(NACH|ENACH|E[-_ ]NACH|ECS|UMRN)\b", text):
            votes[PaymentMechanism.E_NACH] += 1
        elif re.search(r"UPI.*AUTOPAY|UPI.*AUTO[- ]?DEBIT|\bUMN\b", text):
            votes[PaymentMechanism.UPI_AUTOPAY] += 1
        elif re.search(r"\bSI\b|STANDING[-_ ]INSTRUCTION|CARD[-_ ]RECURRING|RECURRING.*CARD", text):
            votes[PaymentMechanism.CARD_SI] += 1
    best = max(votes.values())
    winners = [m for m, n in votes.items() if n == best]
    if best == 0 or len(winners) != 1:
        return PaymentMechanism.UNKNOWN, 0.0
    return winners[0], round(best / len(transactions), 2)
