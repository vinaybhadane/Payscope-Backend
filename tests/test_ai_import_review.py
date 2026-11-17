"""New uploads use the real AI review service with a mocked provider boundary."""
from unittest.mock import patch
from app.analytics.ai_review import Review
from app.models.commitment import Commitment
from app.models.workspace_intelligence import WorkspaceIntelligence


def upload(client):
    csv = ('Date,Description,Amount,Currency,Direction,External_ID\n'
           '2026-09-03,ORBIT STORAGE MONTHLY,247,INR,debit,orbit-1\n'
           '2026-10-03,ORBIT STORAGE MONTHLY,247,INR,debit,orbit-2\n')
    return client.post('/api/v1/imports', files={'file': ('ai.csv', csv, 'text/csv')},
                       data={'synthetic_confirmed': 'true'})


def test_failed_ai_review_keeps_records_without_publishing_subscriptions(client, db_session):
    with patch('app.analytics.ai_review.call_gemini', side_effect=ValueError('Provider unavailable')):
        response = upload(client)
    assert response.status_code == 201
    assert response.json()['accepted_count'] == 2
    assert response.json()['workspace_refresh']['analysis_status'] == 'failed'
    assert db_session.query(Commitment).count() == 0
    assert db_session.query(WorkspaceIntelligence).one().status == 'failed'


def test_reviewed_upload_publishes_observed_amounts_and_categories(client, db_session):
    def provider(records):
        return Review(subscriptions=[dict(name='Orbit Storage', category='Cloud Storage',
            transaction_ids=[r['id'] for r in records], cadence='monthly', confidence='high',
            explanation='Two observed monthly payments.')], overview='Reviewed records',
            spending_analysis='Observed cloud storage payments', savings_analysis='No recommendation', limitations=[])
    with patch('app.analytics.ai_review.call_gemini', side_effect=provider) as gemini:
        response = upload(client)
    assert response.status_code == 201
    assert response.json()['workspace_refresh']['analysis_status'] == 'completed'
    gemini.assert_called_once()
    commitment = db_session.query(Commitment).one()
    assert commitment.category == 'Cloud Storage'
    assert float(commitment.representative_amount) == 247
    assert commitment.detector_version == 'gemini-v1'
    summary = client.get('/api/v1/dashboard/summary').json()
    assert float(summary['kpis']['monthly_recurring_spend']) == 247


def test_ai_cannot_silently_omit_newest_matching_payment(client, db_session):
    def incomplete(records):
        return Review(subscriptions=[dict(name='Orbit Storage', category='Cloud Storage',
            transaction_ids=[r['id'] for r in records[:2]], cadence='monthly', confidence='high',
            explanation='Recurring monthly charges.')], overview='Reviewed',
            spending_analysis='Observed', savings_analysis='None', limitations=[])
    csv = ('Date,Description,Amount,Currency,Direction\n'
           '2026-09-03,ORBIT STORAGE,247,INR,debit\n'
           '2026-10-03,ORBIT STORAGE,247,INR,debit\n'
           '2026-11-03,ORBIT STORAGE,247,INR,debit\n')
    with patch('app.analytics.ai_review.call_gemini', side_effect=incomplete):
        response = client.post('/api/v1/imports', files={'file': ('three.csv', csv, 'text/csv')})
    assert response.status_code == 201
    assert response.json()['accepted_count'] == 3
    assert response.json()['workspace_refresh']['analysis_status'] == 'failed'
    assert db_session.query(Commitment).count() == 0
