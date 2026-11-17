"""Real database tests for single-period statements, candidates, and planning."""
from unittest.mock import patch
from app.analytics.ai_review import Review
from app.models import Commitment


def classify(records):
    service = [r['id'] for r in records if 'ORBIT' in r['description']]
    groceries = [r['id'] for r in records if 'MARKET' in r['description']]
    return Review(subscriptions=[], subscription_candidates=[dict(
        name='Orbit Cloud', category='Cloud Storage', transaction_ids=service, cadence='monthly',
        confidence='low', explanation='One cloud-service charge; confirm its billing cycle.')],
        transaction_classifications=[dict(transaction_ids=service, merchant_name='Orbit Cloud', category='Cloud Storage'),
                                    dict(transaction_ids=groceries, merchant_name='Local Market', category='Groceries')],
        overview='Two observed payments.', spending_analysis='Cloud storage and groceries.',
        savings_analysis='Consider the cloud service as an optional planning scenario; cadence is unconfirmed.', limitations=[])


def upload(client):
    csv = ('Date,Description,Amount,Currency,Direction\n'
           '2026-10-03,ORBIT CLOUD SERVICE,247,INR,debit\n'
           '2026-10-09,LOCAL MARKET,1500,INR,debit\n'
           '2026-10-11,REFUND,100,INR,credit\n')
    with patch('app.analytics.ai_review.call_gemini', side_effect=classify):
        result = client.post('/api/v1/imports', files={'file':('single-month.csv',csv,'text/csv')})
    assert result.status_code == 201, result.text
    assert result.json()['workspace_refresh']['analysis_status'] == 'completed'


def test_single_month_populates_spending_and_potential_services_without_fake_recurrence(client, db_session):
    upload(client)
    data = client.get('/api/v1/mobile/dashboard').json()
    assert data['imported_transactions_count'] == 3
    assert data['active_subscriptions_count'] == 0
    assert float(data['monthly_recurring_spend']) == 0
    assert float(data['statement_analytics']['total_debits']) == 1747
    assert data['historical_months'] == ['Oct 2026']
    assert list(map(float, data['historical_amounts'])) == [1747]
    assert {k:float(v) for k,v in data['category_distribution'].items()} == {'Cloud Storage':247,'Groceries':1500}
    assert len(data['statement_analytics']['top_merchants']) == 2
    candidate = data['commitments'][0]
    assert candidate['status'] == 'candidate' and candidate['next_expected_date'] is None
    assert float(candidate['annualised_amount']) == 2964
    assert db_session.query(Commitment).count() == 1


def test_candidate_savings_require_opt_in_and_are_recalculated_on_server(client, db_session):
    upload(client)
    cid = str(db_session.query(Commitment).one().id)
    body = {'name':'Possible cloud cut','selected_commitment_ids':[cid], 'annual_savings':999999}
    assert client.post('/api/v1/savings/scenarios',json=body).status_code == 400
    result = client.post('/api/v1/savings/scenarios',json={**body,'include_candidates':True})
    assert result.status_code == 201, result.text
    saved = result.json()
    assert float(saved['annual_savings']) == 2964
    assert saved['parameters']['estimates_only'] is True
    assert float(saved['projected_annual_cost']) == 0


def test_confirming_a_candidate_updates_all_recurring_values(client, db_session):
    upload(client)
    cid = str(db_session.query(Commitment).one().id)
    result = client.patch('/api/v1/commitments/'+cid,json={'status':'user_confirmed'})
    assert result.status_code == 200, result.text
    data = client.get('/api/v1/mobile/dashboard').json()
    assert data['active_subscriptions_count'] == 1
    assert float(data['monthly_recurring_spend']) == 247
    assert data['commitments'][0]['next_expected_date'] is not None


def test_spending_survives_ai_unavailability(client):
    csv='Date,Description,Amount,Currency,Direction\n2026-10-04,UNKNOWN PAYMENT,215,INR,debit\n'
    with patch('app.analytics.ai_review.call_gemini',side_effect=ValueError('Provider unavailable')):
        result=client.post('/api/v1/imports', files={'file':('offline.csv',csv,'text/csv')})
    assert result.status_code == 201
    data=client.get('/api/v1/mobile/dashboard').json()
    assert data['analysis_status']=='failed'
    assert float(data['statement_analytics']['total_debits']) == 215
    assert float(data['category_distribution']['Uncategorized']) == 215


def test_later_import_preserves_user_corrections(client, db_session):
    upload(client)
    cid=str(db_session.query(Commitment).one().id)
    result=client.patch('/api/v1/commitments/'+cid,json={'display_name':'My edited cloud plan',
        'category':'My category', 'cadence':'annual', 'representative_amount':111, 'status':'user_confirmed'})
    assert result.status_code == 200
    with patch('app.analytics.ai_review.call_gemini',side_effect=classify):
        result=client.post('/api/v1/imports',files={'file':('next.csv',
            'Date,Description,Amount,Currency,Direction\n2026-11-02,BOOK SHOP,50,INR,debit\n','text/csv')})
    assert result.status_code == 201
    item=client.get('/api/v1/mobile/dashboard').json()['commitments'][0]
    assert item['display_name']=='My edited cloud plan' and item['cadence']=='annual'
    assert item['category']=='My category' and float(item['representative_amount'])==111
    assert item['status']=='user_confirmed'
