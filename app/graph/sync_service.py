"""Stream a workspace projection into indexed Neo4j nodes in bounded batches."""

from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from time import perf_counter
import uuid

from neo4j import Query, unit_of_work
from sqlalchemy import literal, select, union
from sqlalchemy.orm import Session

from app.core.read_cache import graph_cache
from app.graph.config import neo4j_settings as settings
from app.graph.driver import get_neo4j_driver
from app.graph.exceptions import (
    Neo4jConnectionError,
    Neo4jDisabledError,
    Neo4jSyncError,
)
from app.graph.schema import initialize_graph_schema
from app.graph.schemas import GraphSyncResponse
from app.models.account import Account
from app.models.cancellation_event import CancellationEvent
from app.models.commitment import Commitment
from app.models.commitment_insight import CommitmentInsight
from app.models.commitment_transaction import CommitmentTransaction
from app.models.demo_user import DemoUser
from app.models.descriptor_alias import DescriptorAlias
from app.models.insight_evidence import InsightEvidence
from app.models.merchant import Merchant
from app.models.transaction import Transaction

_schema_lock = Lock()
_schema_ready = False
_sync_locks = [Lock() for _ in range(32)]


def _value(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class GraphSyncService:
    def __init__(self, db: Session):
        self.db = db

    def sync_user_graph(self, user_id: uuid.UUID) -> GraphSyncResponse:
        if not settings.ENABLED:
            raise Neo4jDisabledError("Neo4j integration is disabled.")
        driver = get_neo4j_driver()
        if not driver:
            raise Neo4jConnectionError("Neo4j is unavailable.")
        if not self.db.scalar(select(DemoUser.id).where(DemoUser.id == user_id)):
            raise ValueError("Workspace was not found.")
        # Serialize overlapping snapshots of the same workspace. No read uses this lock.
        with _sync_locks[hash(user_id) % len(_sync_locks)]:
            return self._sync(driver, user_id)

    def _sync(self, driver, user_id):
        global _schema_ready
        started = perf_counter()
        now = datetime.now(timezone.utc)
        token = str(uuid.uuid4())
        workspace = str(user_id)
        used_merchants = union(
            select(Commitment.merchant_id).where(
                Commitment.user_id == user_id, Commitment.merchant_id.is_not(None)
            ),
            select(Transaction.merchant_id).where(
                Transaction.user_id == user_id, Transaction.merchant_id.is_not(None)
            ),
        )
        own = lambda model: model.user_id == user_id
        nodes = [
            (
                "User",
                select(
                    DemoUser.id, DemoUser.workspace_key, DemoUser.display_name
                ).where(DemoUser.id == user_id),
                True,
            ),
            (
                "Merchant",
                select(Merchant.id, Merchant.canonical_name, Merchant.category).where(
                    Merchant.id.in_(used_merchants)
                ),
                False,
            ),
            (
                "DescriptorAlias",
                select(
                    DescriptorAlias.id,
                    DescriptorAlias.alias_text.label("raw_pattern"),
                    DescriptorAlias.matcher_type.label("alias_type"),
                ).where(DescriptorAlias.merchant_id.in_(used_merchants)),
                False,
            ),
            (
                "Account",
                select(
                    Account.id,
                    Account.user_id,
                    Account.label,
                    Account.account_type,
                    Account.masked_reference,
                    Account.currency,
                ).where(own(Account)),
                True,
            ),
            (
                "Transaction",
                select(
                    Transaction.id,
                    Transaction.user_id,
                    Transaction.raw_descriptor,
                    Transaction.amount,
                    Transaction.currency,
                    Transaction.direction,
                    Transaction.transaction_status,
                    Transaction.transaction_date,
                    Transaction.payment_channel,
                ).where(own(Transaction)),
                True,
            ),
            (
                "Commitment",
                select(
                    Commitment.id,
                    Commitment.user_id,
                    Commitment.display_name,
                    Commitment.commitment_type,
                    Commitment.cadence,
                    Commitment.representative_amount,
                    Commitment.status,
                    Commitment.confidence_band,
                ).where(own(Commitment)),
                True,
            ),
            (
                "CommitmentInsight",
                select(
                    CommitmentInsight.id,
                    CommitmentInsight.user_id,
                    CommitmentInsight.insight_type,
                    CommitmentInsight.severity,
                    CommitmentInsight.status,
                    CommitmentInsight.title,
                    CommitmentInsight.summary,
                    CommitmentInsight.detected_at,
                ).where(own(CommitmentInsight)),
                True,
            ),
            (
                "CancellationEvent",
                select(
                    CancellationEvent.id,
                    CancellationEvent.user_id,
                    CancellationEvent.event_source,
                    CancellationEvent.confirmation_type,
                    CancellationEvent.event_timestamp,
                ).where(own(CancellationEvent)),
                True,
            ),
        ]
        pair = lambda source, target: (source.label("source"), target.label("target"))
        root = literal(workspace)
        relations = [
            (
                "OWNS_ACCOUNT",
                "User",
                "Account",
                select(*pair(root, Account.id)).where(own(Account)),
            ),
            (
                "HAS_MERCHANT",
                "User",
                "Merchant",
                select(root.label("source"), Merchant.id.label("target")).where(
                    Merchant.id.in_(used_merchants)
                ),
            ),
            (
                "ALIAS_OF",
                "DescriptorAlias",
                "Merchant",
                select(*pair(DescriptorAlias.id, DescriptorAlias.merchant_id)).where(
                    DescriptorAlias.merchant_id.in_(used_merchants)
                ),
            ),
            (
                "HAS_TRANSACTION",
                "Account",
                "Transaction",
                select(*pair(Transaction.account_id, Transaction.id)).where(
                    own(Transaction), Transaction.account_id.is_not(None)
                ),
            ),
            (
                "PAID_TO",
                "Transaction",
                "Merchant",
                select(*pair(Transaction.id, Transaction.merchant_id)).where(
                    own(Transaction), Transaction.merchant_id.is_not(None)
                ),
            ),
            (
                "HAS_DESCRIPTOR",
                "Transaction",
                "DescriptorAlias",
                select(
                    *pair(Transaction.id, Transaction.matched_descriptor_alias_id)
                ).where(
                    own(Transaction),
                    Transaction.matched_descriptor_alias_id.is_not(None),
                ),
            ),
            (
                "HAS_COMMITMENT",
                "User",
                "Commitment",
                select(*pair(root, Commitment.id)).where(own(Commitment)),
            ),
            (
                "ASSOCIATED_WITH",
                "Commitment",
                "Merchant",
                select(*pair(Commitment.id, Commitment.merchant_id)).where(
                    own(Commitment), Commitment.merchant_id.is_not(None)
                ),
            ),
            (
                "SUPPORTED_BY",
                "Commitment",
                "Transaction",
                select(
                    *pair(
                        CommitmentTransaction.commitment_id,
                        CommitmentTransaction.transaction_id,
                    )
                )
                .join(Commitment, Commitment.id == CommitmentTransaction.commitment_id)
                .join(
                    Transaction, Transaction.id == CommitmentTransaction.transaction_id
                )
                .where(own(Commitment), own(Transaction)),
            ),
            (
                "HAS_INSIGHT",
                "Commitment",
                "CommitmentInsight",
                select(
                    *pair(CommitmentInsight.commitment_id, CommitmentInsight.id)
                ).where(own(CommitmentInsight)),
            ),
            (
                "EVIDENCED_BY",
                "CommitmentInsight",
                "Transaction",
                select(
                    *pair(InsightEvidence.insight_id, InsightEvidence.transaction_id)
                )
                .join(
                    CommitmentInsight,
                    CommitmentInsight.id == InsightEvidence.insight_id,
                )
                .join(Transaction, Transaction.id == InsightEvidence.transaction_id)
                .where(own(CommitmentInsight), own(Transaction)),
            ),
            (
                "HAS_CANCELLATION_EVENT",
                "Commitment",
                "CancellationEvent",
                select(
                    *pair(CancellationEvent.commitment_id, CancellationEvent.id)
                ).where(own(CancellationEvent)),
            ),
        ]
        node_count = edge_count = 0
        try:
            with _schema_lock:
                if not _schema_ready:
                    initialize_graph_schema(driver, database=settings.DATABASE)
                    _schema_ready = True
            with driver.session(database=settings.DATABASE) as session:
                for label, stmt, owned in nodes:
                    query = f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row"
                    if owned:
                        query += " SET n.user_id=$user_id, n.sync_token=$token"
                    node_count += self._write_batches(
                        session, stmt, query, workspace, token
                    )
                for rel, source, target, stmt in relations:
                    query = f"""UNWIND $rows AS row
                    MATCH (s:{source} {{id: row.source}}), (t:{target} {{id: row.target}})
                    MERGE (s)-[r:{rel}]->(t) SET r.user_id=$user_id, r.sync_token=$token"""
                    edge_count += self._write_batches(
                        session, stmt, query, workspace, token
                    )
                # Remove only stale derived data owned by this workspace, after a full successful pass.
                session.run(
                    Query(
                        "MATCH ()-[r {user_id:$user_id}]->() WHERE type(r) <> 'ALIAS_OF' AND coalesce(r.sync_token, '') <> $token DELETE r",
                        timeout=settings.SYNC_QUERY_TIMEOUT,
                    ),
                    user_id=workspace,
                    token=token,
                ).consume()
                for label in (
                    "Account",
                    "Transaction",
                    "Commitment",
                    "CommitmentInsight",
                    "CancellationEvent",
                ):
                    session.run(
                        Query(
                            f"MATCH (n:{label} {{user_id:$user_id}}) WHERE coalesce(n.sync_token, '') <> $token DETACH DELETE n",
                            timeout=settings.SYNC_QUERY_TIMEOUT,
                        ),
                        user_id=workspace,
                        token=token,
                    ).consume()
                session.run(
                    "MATCH (u:User {id:$id}) SET u.last_sync_at=$now",
                    id=workspace,
                    now=now.isoformat(),
                ).consume()
            graph_cache.clear()
            from app.graph.overview_service import reset_neo4j_circuit

            reset_neo4j_circuit()
        except Exception as exc:
            raise Neo4jSyncError(
                "Neo4j projection could not be synchronized. Existing source records are unchanged."
            ) from exc
        return GraphSyncResponse(
            status="success",
            nodes_synced=node_count,
            relationships_synced=edge_count,
            execution_time_ms=round((perf_counter() - started) * 1000, 2),
            last_sync_at=now,
        )

    def _write_batches(self, session, statement, query, workspace, token):
        count = 0
        batch_size = max(50, min(settings.SYNC_BATCH_SIZE, 2000))
        result = self.db.execute(
            statement.execution_options(yield_per=batch_size)
        ).mappings()
        for partition in result.partitions(batch_size):
            rows = [
                {key: _value(value) for key, value in row.items()} for row in partition
            ]

            @unit_of_work(timeout=settings.SYNC_QUERY_TIMEOUT)
            def write(tx):
                tx.run(
                    query,
                    rows=rows,
                    user_id=workspace,
                    token=token,
                ).consume()

            session.execute_write(write)
            count += len(rows)
        return count
