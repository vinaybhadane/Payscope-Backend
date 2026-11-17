"""Playbook resolver selecting verified merchant or generic mechanism instructions."""
from typing import Optional
from app.analytics.cancellations.config import (
    ALL_PLAYBOOKS,
    GENERIC_CARD_SI_PLAYBOOK,
    GENERIC_E_NACH_PLAYBOOK,
    GENERIC_OTHER_PLAYBOOK,
    GENERIC_UPI_AUTOPAY_PLAYBOOK,
    VERIFIED_MERCHANT_PLAYBOOKS,
)
from app.analytics.cancellations.schemas import PaymentMechanism, Playbook
from app.models.commitment import Commitment
from app.models.merchant import Merchant


def resolve_playbook(
    mechanism: PaymentMechanism,
    commitment: Optional[Commitment] = None,
    merchant: Optional[Merchant] = None,
    playbook_key: Optional[str] = None,
) -> Playbook:
    """Resolves the most accurate and source-verified playbook.

    Priority:
    1. Explicit playbook_key requested by user/system
    2. Verified merchant playbook if merchant is recognized and verified
    3. Generic payment mechanism playbook
    4. General fallback playbook
    """
    if playbook_key:
        for pb in ALL_PLAYBOOKS:
            if pb.playbook_key == playbook_key and pb.payment_mechanism == mechanism:
                return pb

    # Check merchant
    merchant_name = ""
    if merchant and merchant.canonical_name:
        merchant_name = merchant.canonical_name.lower()
    elif commitment and commitment.merchant and commitment.merchant.canonical_name:
        merchant_name = commitment.merchant.canonical_name.lower()
    elif commitment and commitment.display_name:
        merchant_name = commitment.display_name.lower()

    if "netflix" in merchant_name and "netflix" in VERIFIED_MERCHANT_PLAYBOOKS:
        candidate = VERIFIED_MERCHANT_PLAYBOOKS["netflix"]
        if candidate.payment_mechanism == mechanism:
            return candidate
    elif "spotify" in merchant_name and "spotify" in VERIFIED_MERCHANT_PLAYBOOKS:
        candidate = VERIFIED_MERCHANT_PLAYBOOKS["spotify"]
        if candidate.payment_mechanism == mechanism:
            return candidate
    elif ("prime" in merchant_name or "amazon" in merchant_name) and "amazon_prime" in VERIFIED_MERCHANT_PLAYBOOKS:
        candidate = VERIFIED_MERCHANT_PLAYBOOKS["amazon_prime"]
        if candidate.payment_mechanism == mechanism:
            return candidate
    elif ("hotstar" in merchant_name or "disney" in merchant_name) and "hotstar" in VERIFIED_MERCHANT_PLAYBOOKS:
        candidate = VERIFIED_MERCHANT_PLAYBOOKS["hotstar"]
        if candidate.payment_mechanism == mechanism:
            return candidate

    # Mechanism fallback
    if mechanism == PaymentMechanism.UPI_AUTOPAY:
        return GENERIC_UPI_AUTOPAY_PLAYBOOK
    elif mechanism == PaymentMechanism.CARD_SI:
        return GENERIC_CARD_SI_PLAYBOOK
    elif mechanism == PaymentMechanism.E_NACH:
        return GENERIC_E_NACH_PLAYBOOK

    return GENERIC_OTHER_PLAYBOOK
