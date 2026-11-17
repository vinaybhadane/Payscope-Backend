"""Import-to-dashboard integration and recoverable Neo4j projection failures."""
from types import SimpleNamespace
from unittest.mock import patch
from app.core.read_cache import invalidate_read_caches
from app.matching.seed_merchants import seed_canonical_merchants
from app.models.account import Account
from app.models.commitment import Commitment
from app.models.commitment_transaction import CommitmentTransaction
from app.models.transaction import Transaction


def upload(client):
    rows = "Date,Description,Amount,Currency,Direction,Channel,External_ID\n"
    for month in range(5, 11):
        rows += f"2026-{month:02}-03,STREAMPULSE SUB,499,INR,debit,card,refresh-{month}\n"
    return client.post('/api/v1/imports', files={'file': ('refresh.csv', rows.encode(), 'text/csv')},
                       data={'synthetic_confirmed': 'true'})


def test_import_updates_dashboard_evidence_and_syncs_idempotently(client, db_session):
    seed_canonical_merchants(db_session)
    invalidate_read_caches()
    assert client.get('/api/v1/dashboard/summary').json()['kpis']['active_commitments_count'] == 0
    with patch('app.ingestion.refresh_service.neo4j_settings.ENABLED', True), patch(
        'app.ingestion.refresh_service.GraphSyncService.sync_user_graph',
        return_value=SimpleNamespace(nodes_synced=12, relationships_synced=20),
    ) as sync:
        first = upload(client)
        assert first.status_code == 201
        assert first.json()['workspace_refresh']['analysis_status'] == 'completed'
        assert first.json()['workspace_refresh']['graph_status'] == 'completed'
        summary = client.get('/api/v1/dashboard/summary').json()
        assert summary['kpis']['active_commitments_count'] == 1
        assert float(summary['kpis']['monthly_recurring_spend']) == 499
        assert len(summary['financial_overview']['historical_months']) == 6
        assert db_session.query(CommitmentTransaction).count() == 6
        assert all(t.account_id for t in db_session.query(Transaction).all())
        second = upload(client)
        assert second.json()['duplicate_count'] == 6
        assert db_session.query(Commitment).count() == 1
        assert db_session.query(Account).count() == 1
        assert db_session.query(Transaction).count() == 6
        assert sync.call_count == 2


def test_import_survives_graph_failure_and_can_retry(client, db_session):
    seed_canonical_merchants(db_session)
    with patch('app.ingestion.refresh_service.neo4j_settings.ENABLED', True), patch(
        'app.ingestion.refresh_service.GraphSyncService.sync_user_graph', side_effect=RuntimeError('offline')
    ):
        response = upload(client)
    result = response.json()
    assert response.status_code == 201
    assert result['workspace_refresh']['analysis_status'] == 'completed'
    assert result['workspace_refresh']['graph_status'] == 'failed'
    assert db_session.query(Transaction).count() == 6
    with patch('app.ingestion.refresh_service.neo4j_settings.ENABLED', False):
        retry = client.post(f"/api/v1/imports/{result['import_id']}/refresh")
    assert retry.status_code == 200
    assert retry.json()['analysis_status'] == 'completed'
    assert db_session.query(CommitmentTransaction).count() == 6
