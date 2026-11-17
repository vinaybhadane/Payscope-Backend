"""Durable Brevo outbox and 5/3/1-day reminder worker, scoped to verified login email."""
import asyncio
import base64
import uuid
import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import requests
from sqlalchemy import select, text
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Commitment
from app.models.workspace_intelligence import WorkspaceIntelligence, EmailPreference, EmailDelivery
from app.analytics.report_pdf import build_report_pdf

logger = logging.getLogger(__name__)


def enqueue_report(db, user_id):
    state = db.get(WorkspaceIntelligence, user_id)
    pref = db.get(EmailPreference, user_id)
    if not state or state.status != 'completed' or not pref or not pref.reports_enabled:
        return False
    key = f'report:{user_id}:{state.generated_at.isoformat()}'
    if db.get(EmailDelivery, key): return False
    db.add(EmailDelivery(delivery_key=key, user_id=user_id, status='pending', payload={
        'recipient': pref.recipient, 'subject': 'Your PayScope subscription report',
        'html': '<p>Your subscription review is ready. The attached PDF contains the subscriptions, payment insights, spending analysis and savings scenarios generated from your uploaded records.</p>',
        'attachment': base64.b64encode(build_report_pdf(state)).decode()}))
    return True


def process_email_jobs():
    if not settings.EMAIL_WORKER_ENABLED:
        return
    with SessionLocal() as db:
        # One worker claims the scan; delivery rows also remain locked through provider acceptance.
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended('payscope-email-worker', 0))")): return
        today = datetime.now(ZoneInfo('Asia/Kolkata')).date()
        prefs = list(db.scalars(select(EmailPreference)).all())
        for pref in prefs:
            if pref.reports_enabled: enqueue_report(db, pref.user_id)
            if not pref.reminders_enabled: continue
            subscriptions = db.scalars(select(Commitment).where(Commitment.user_id == pref.user_id,
                Commitment.status.in_(['active', 'user_confirmed']), Commitment.detector_version == 'gemini-v1',
                Commitment.next_expected_date.is_not(None))).all()
            for sub in subscriptions:
                days = (sub.next_expected_date - today).days
                if days not in (5,3,1): continue
                key = f'due:{sub.id}:{sub.next_expected_date}:{days}'
                if db.get(EmailDelivery, key): continue
                db.add(EmailDelivery(delivery_key=key, user_id=pref.user_id, status='pending', payload={
                    'recipient': pref.recipient, 'subject': f'{sub.display_name}: payment expected in {days} day(s)',
                    'html': f'<p>{escape(sub.display_name)} has an estimated payment of {escape(sub.currency)} {sub.representative_amount} on {sub.next_expected_date.isoformat()} ({days} day(s) remaining).</p><p>This date is inferred from your statement history. Verify it with the provider.</p>',
                    'due_on': today.isoformat()}))
        db.flush()
        jobs = list(db.scalars(select(EmailDelivery).where(EmailDelivery.status.in_(['pending','failed']))
            .order_by(EmailDelivery.created_at).limit(30).with_for_update(skip_locked=True)).all())
        for job in jobs:
            pref = db.get(EmailPreference, job.user_id)
            report = job.delivery_key.startswith('report:')
            if not pref or not (pref.reports_enabled if report else pref.reminders_enabled):
                job.status = 'cancelled'
                continue
            if job.payload.get('due_on') and job.payload['due_on'] != today.isoformat():
                job.status = 'expired'
                continue
            if not settings.BREVO_API_KEY or not settings.BREVO_SENDER_EMAIL:
                job.status, job.error = 'failed', 'Brevo sender is not configured.'
                continue
            payload = {'sender': {'email': settings.BREVO_SENDER_EMAIL, 'name': 'PayScope'},
                'to': [{'email': pref.recipient}], 'subject': job.payload['subject'], 'htmlContent': job.payload['html'],
                'headers': {'idempotencyKey': str(uuid.uuid5(uuid.NAMESPACE_URL, job.delivery_key))}}
            if job.payload.get('attachment'):
                payload['attachment'] = [{'content': job.payload['attachment'], 'name': 'payscope-subscription-report.pdf'}]
            try:
                response = requests.post('https://api.brevo.com/v3/smtp/email', headers={'api-key': settings.BREVO_API_KEY}, json=payload, timeout=(10,30))
                if response.ok:
                    job.status, job.error, job.provider_id = 'sent', None, response.json().get('messageId')
                else:
                    job.status, job.error = 'failed', f'Brevo HTTP {response.status_code}. Check sender verification and account quota.'
            except requests.RequestException:
                # Unknown provider acceptance; don't blindly resend a potentially delivered message.
                job.status, job.error = 'uncertain', 'Provider connection interrupted; delivery acceptance is unknown.'
        db.commit()


async def email_worker():
    while True:
        try:
            await asyncio.to_thread(process_email_jobs)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Email outbox processing failed; will retry next interval')
        await asyncio.sleep(max(60, settings.EMAIL_REMINDER_INTERVAL_SECONDS))
