"""Discover cautious commitment candidates from synthetic notification evidence."""
import re
import uuid
from decimal import Decimal
from sqlalchemy import select
from app.models.commitment import Commitment
from app.models.commitment_transaction import CommitmentTransaction
from app.models.payment_notification import PaymentNotification
from app.matching.resolver import MerchantResolver


def name_matches(hint, name):
    """Accept complete token sequences, never arbitrary substrings like 'Pro'."""
    h = re.findall(r"[a-z0-9]+", (hint or "").lower())
    n = re.findall(r"[a-z0-9]+", (name or "").lower())
    return bool(h and n and (h == n or (len(''.join(h)) >= 6 and n[:len(h)] == h)))


def notification_cadence(notification):
    text = notification.message_text.lower()
    for cadence, pattern in [
        ("fortnightly", r"fortnight|every 2 weeks"),
        ("weekly", r"weekly|/week"), ("quarterly", r"quarter|every 3 months"),
        ("annual", r"annual|yearly|/year"), ("monthly", r"monthly|/month|every month"),
    ]:
        if re.search(pattern, text):
            return cadence
    return "unknown"


def discover_notification_candidates(db, user_id, transactions):
    """Idempotently retain mandate/trial evidence, including before the first charge.

    A notification amount can be a mandate ceiling, so no debit or confirmed cost
    is invented. A first real debit can be linked to enable trial conversion.
    """
    notifications = list(db.scalars(select(PaymentNotification).where(
        PaymentNotification.user_id == user_id,
        PaymentNotification.event_type.in_(["mandate_created", "mandate_registered", "trial_started", "upcoming_debit", "pre_debit"]),
    )).all())
    commitments = list(db.scalars(select(Commitment).where(Commitment.user_id == user_id)).all())
    resolver = MerchantResolver.from_db(db)
    touched = []
    for note in notifications:
        if not note.merchant_hint:
            continue
        resolution = resolver.resolve(note.merchant_hint)
        merchant_id = resolution.merchant_id if resolution.status == "MATCHED_HIGH" else None
        name = resolution.merchant_name if merchant_id else note.merchant_hint
        matches = [c for c in commitments if c.currency == (note.currency or "INR") and
                   ((merchant_id and c.merchant_id == merchant_id) or name_matches(note.merchant_hint, c.display_name))]
        if len(matches) > 1:
            continue  # Ambiguous merchant identity requires review, not an arbitrary link.
        if matches:
            commitment = matches[0]
        else:
            commitment = Commitment(
                id=uuid.uuid4(), user_id=user_id, merchant_id=merchant_id,
                display_name=name, commitment_type="subscription", cadence=notification_cadence(note),
                amount_pattern="insufficient_data", status="candidate", currency=note.currency or "INR",
                first_seen_at=note.event_timestamp, last_seen_at=note.event_timestamp,
                representative_amount=None, confidence_score=Decimal("35"), confidence_band="low",
                detection_reason_codes=[], detector_version="v1.1",
                detection_explanation="Notification indicates a possible recurring commitment. Awaiting statement evidence; mandate limits are not charges.",
            )
            db.add(commitment)
            commitments.append(commitment)
        code = f"notification:{note.id}"
        codes = list(commitment.detection_reason_codes or [])
        if code not in codes:
            commitment.detection_reason_codes = codes + [code]
        if commitment.status == "candidate" and not commitment.user_decision:
            if commitment.cadence == "unknown":
                commitment.cadence = notification_cadence(note)
            # Attach only unambiguous real transaction evidence, not synthetic debits.
            linked = {ct.transaction_id for ct in commitment.commitment_transactions}
            for tx in transactions:
                if tx.currency != commitment.currency:
                    continue
                same = (commitment.merchant_id and tx.merchant_id == commitment.merchant_id) or name_matches(note.merchant_hint, tx.normalised_descriptor or tx.raw_descriptor)
                if same and tx.id not in linked:
                    commitment.commitment_transactions.append(CommitmentTransaction(transaction_id=tx.id,
                        relationship_role="recurrence_evidence", reason="Linked by notification merchant evidence"))
                    linked.add(tx.id)
                    commitment.representative_amount = tx.amount
                    commitment.last_seen_at = tx.transaction_date
        if commitment not in touched:
            touched.append(commitment)
    db.flush()
    return touched
