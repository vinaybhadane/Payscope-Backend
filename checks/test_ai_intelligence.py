"""Isolated AI and email checks. Never connect to a database or send provider traffic."""
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, Mock
import requests
from app.analytics.ai_review import call_gemini, review_workspace, Review
from app.analytics.email_alerts import process_email_jobs
from app.core.config import settings
from app.models import Transaction, Commitment
from app.models.workspace_intelligence import EmailPreference, EmailDelivery


class EmailDB:
    def __init__(self, dates):
        self.uid = uuid.uuid4()
        self.pref = EmailPreference(user_id=self.uid, recipient='verified@example.com', reports_enabled=False, reminders_enabled=True)
        self.subs = [Commitment(id=uuid.uuid4(), user_id=self.uid, display_name=f'Service {n}', currency='INR', representative_amount=Decimal('123'), next_expected_date=d) for n,d in enumerate(dates)]
        self.jobs = {}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def scalar(self, statement): return True
    def scalars(self, statement):
        model = statement.column_descriptions[0]['entity']
        values = [self.pref] if model is EmailPreference else self.subs if model is Commitment else [j for j in self.jobs.values() if j.status in ('pending','failed')]
        return SimpleNamespace(all=lambda: values)
    def get(self, model, key):
        return self.pref if model is EmailPreference else self.jobs.get(key)
    def add(self, value): self.jobs[value.delivery_key] = value
    def flush(self): pass
    def commit(self): pass


class AIReviewChecks(unittest.TestCase):
    def test_missing_key_does_not_call_provider(self):
        with patch.object(settings, 'GEMINI_API_KEY', ''), patch('app.analytics.ai_review.requests.post') as post:
            with self.assertRaisesRegex(ValueError, 'not configured'): call_gemini([])
            post.assert_not_called()

    def test_provider_error_is_actionable_without_returning_provider_body(self):
        response = Mock(ok=False, status_code=429)
        response.json.return_value = {'error': {'message': 'private provider diagnostic', 'details': []}}
        with patch.object(settings, 'GEMINI_API_KEY', 'unit-test'), patch('app.analytics.ai_review.requests.post', return_value=response), patch('app.core.gemini.time.sleep'):
            with self.assertRaisesRegex(ValueError, 'HTTP 429') as error: call_gemini([])
            self.assertNotIn('private provider diagnostic', str(error.exception))

    def test_truncated_generation_is_rejected(self):
        response = Mock(ok=True)
        response.json.return_value = {'candidates':[{'finishReason':'MAX_TOKENS'}]}
        with patch.object(settings, 'GEMINI_API_KEY', 'unit-test'), patch('app.analytics.ai_review.requests.post', return_value=response):
            with self.assertRaisesRegex(ValueError, 'incomplete'): call_gemini([])

    def test_ai_cannot_publish_unowned_evidence(self):
        uid = uuid.uuid4()
        tx = Transaction(id=uuid.uuid4(), user_id=uid, transaction_date=datetime(2026,10,1,tzinfo=timezone.utc), raw_descriptor='Synthetic', amount=Decimal('99'), currency='INR')
        db = Mock()
        db.scalars.return_value.all.return_value = [tx]
        review = Review(subscriptions=[{'name':'Invented','category':'Test','transaction_ids':[str(tx.id),str(uuid.uuid4())],
            'cadence':'monthly','confidence':'high','explanation':'Unowned evidence'}], overview='', spending_analysis='', savings_analysis='', limitations=[])
        with patch('app.analytics.ai_review.call_gemini', return_value=review):
            with self.assertRaisesRegex(ValueError, 'invalid or overlapping'): review_workspace(db, uid)
        db.add.assert_not_called()
        db.commit.assert_not_called()


class EmailChecks(unittest.TestCase):
    def test_reminders_only_for_5_3_1_days_and_no_duplicate_sends(self):
        today = datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date()
        db = EmailDB([today+timedelta(days=n) for n in (0,1,2,3,4,5,6)])
        response = Mock(ok=True)
        response.json.return_value = {'messageId':'unit-test-provider-id'}
        with patch('app.analytics.email_alerts.SessionLocal', return_value=db), patch.object(settings,'BREVO_API_KEY','unit-test'), patch('app.analytics.email_alerts.requests.post',return_value=response) as post:
            process_email_jobs()
            self.assertEqual(post.call_count, 3)
            self.assertEqual(len(db.jobs), 3)
            self.assertTrue(all(job.status=='sent' for job in db.jobs.values()))
            self.assertTrue(all(call.kwargs['json']['to']==[{'email':'verified@example.com'}] for call in post.call_args_list))
            self.assertTrue(all('idempotencyKey' in call.kwargs['json']['headers'] for call in post.call_args_list))
            process_email_jobs()
            self.assertEqual(post.call_count, 3)

    def test_unknown_delivery_acceptance_is_not_blindly_retried(self):
        today = datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date()
        db = EmailDB([today+timedelta(days=1)])
        with patch('app.analytics.email_alerts.SessionLocal',return_value=db), patch.object(settings,'BREVO_API_KEY','unit-test'), patch('app.analytics.email_alerts.requests.post',side_effect=requests.Timeout()) as post:
            process_email_jobs()
            self.assertEqual(next(iter(db.jobs.values())).status, 'uncertain')
            process_email_jobs()
            self.assertEqual(post.call_count, 1)


class SpendingChecks(unittest.TestCase):
    def items(self):
        return [SimpleNamespace(commitment_id=uuid.uuid4(), merchant_id=None, merchant_name=name,
            category=category, commitment_type='subscription', is_confirmed=True,
            normalized_monthly_cost=Decimal('100'), annualized_cost=Decimal('1200'))
            for name,category in [('Orbit storage','Cloud Storage'), ('Nova tools','Creative Tools')]]

    def test_ai_products_without_catalog_ids_are_ranked_separately(self):
        from app.analytics.financial_impact.merchant_analysis import get_user_merchant_analysis
        db = Mock()
        db.scalar.return_value = Decimal('0')
        with patch('app.analytics.financial_impact.merchant_analysis.get_user_commitment_costs',return_value=self.items()):
            result = get_user_merchant_analysis(db, uuid.uuid4())
        self.assertEqual(len(result),2)
        self.assertEqual({r.merchant_name for r in result},{'Orbit storage','Nova tools'})
        self.assertTrue(all(r.monthly_cost==Decimal('100') for r in result))

    def test_categories_come_from_ai_review(self):
        from app.analytics.financial_impact.category_analysis import get_user_category_analysis
        db = Mock()
        db.scalar.return_value = Decimal('0')
        with patch('app.analytics.financial_impact.category_analysis.get_user_commitment_costs',return_value=self.items()):
            result = get_user_category_analysis(db, uuid.uuid4())
        self.assertEqual({r.category_label for r in result}, {'Cloud Storage','Creative Tools'})

if __name__ == '__main__': unittest.main()
