"""A bounded, connected graph snapshot; Neo4j reads never block dashboard metrics."""

from datetime import datetime
from threading import Lock
from time import monotonic
import uuid
from neo4j import Query, READ_ACCESS
from sqlalchemy import select, func
from sqlalchemy.orm import joinedload, Session
from app.graph.config import neo4j_settings as settings
from app.graph.driver import get_neo4j_driver
from app.graph.schemas import (
    GraphElements,
    GraphNode,
    GraphNodeData,
    GraphEdge,
    GraphEdgeData,
)
from app.models.account import Account
from app.models.commitment import Commitment
from app.models.commitment_insight import CommitmentInsight
from app.models.commitment_transaction import CommitmentTransaction
from app.models.transaction import Transaction
from app.models.import_record import ImportRecord

_retry_after = 0.0
_circuit_lock = Lock()


def reset_neo4j_circuit():
    global _retry_after
    with _circuit_lock:
        _retry_after = 0


OVERVIEW_QUERY = """
MATCH (u:User {id:$user_id})
CALL (u) { MATCH (u)-[:HAS_COMMITMENT]->(c:Commitment)
       WITH c ORDER BY c.id LIMIT $limit RETURN collect(c) AS commitments }
CALL (u) { MATCH (u)-[ownership]->(a:Account) WHERE type(ownership)='OWNS_ACCOUNT'
       WITH a ORDER BY a.id LIMIT 20 RETURN collect(a) AS accounts }
CALL (commitments) { UNWIND commitments AS c MATCH (c)-[:ASSOCIATED_WITH]->(m:Merchant)
       RETURN collect(DISTINCT m) AS merchants }
CALL (commitments) { UNWIND commitments AS c MATCH (c)-[:HAS_INSIGHT]->(i:CommitmentInsight)
       WITH DISTINCT i ORDER BY i.detected_at DESC LIMIT 50 RETURN collect(i) AS insights }
CALL (commitments) { UNWIND commitments AS c MATCH (c)-[:SUPPORTED_BY]->(t:Transaction)
       WITH DISTINCT t ORDER BY t.transaction_date DESC LIMIT $limit RETURN collect(t) AS transactions }
WITH u, [u]+accounts+commitments+merchants+insights+transactions AS selected
UNWIND selected AS n
OPTIONAL MATCH (n)-[r]->(other) WHERE other IN selected
RETURN collect(DISTINCT n) AS nodes, collect(DISTINCT r) AS relationships, u.last_sync_at AS last_sync_at
"""


def _node(node_id, kind, name, **props):
    return GraphNode(
        data=GraphNodeData(
            id=str(node_id), label=name, name=name, node_type=kind, **props
        )
    )


def _edge(source, target, kind):
    return GraphEdge(
        data=GraphEdgeData(
            id=f"{source}:{kind}:{target}",
            source=str(source),
            target=str(target),
            relationship_type=kind,
            label=kind.replace("_", " ").lower(),
        )
    )


def serialize_neo4j(record, limit):
    nodes = []
    for n in record["nodes"]:
        kind = next(iter(n.labels))
        name = (
            n.get("display_name")
            or n.get("canonical_name")
            or n.get("label")
            or n.get("title")
            or n.get("raw_descriptor")
            or kind
        )
        nodes.append(
            _node(
                n["id"],
                kind,
                name,
                amount=n.get("representative_amount", n.get("amount")),
                status=n.get("status"),
                category=n.get("category", n.get("insight_type")),
                cadence=n.get("cadence"),
                confidence_band=n.get("confidence_band"),
                metadata={
                    k: n.get(k)
                    for k in (
                        "currency",
                        "payment_channel",
                        "transaction_date",
                        "severity",
                        "masked_reference",
                    )
                    if n.get(k) is not None
                },
            )
        )
    ids = {n.data.id for n in nodes}
    edges = [
        _edge(r.start_node["id"], r.end_node["id"], r.type)
        for r in record["relationships"]
        if r.start_node["id"] in ids and r.end_node["id"] in ids
    ]
    by_id = {n.data.id: n for n in nodes}
    for e in edges:
        if e.data.relationship_type == "ASSOCIATED_WITH":
            if not by_id[e.data.source].data.category:
                by_id[e.data.source].data.category = by_id[e.data.target].data.category
    return GraphElements(
        nodes=nodes,
        edges=edges,
        source="neo4j",
        last_sync_at=record.get("last_sync_at"),
        truncated=sum(n.data.node_type == "Commitment" for n in nodes) >= limit,
        message="Indexed Neo4j workspace projection. Evidence is limited to the most recent records.",
    )


def get_overview(db: Session, user_id: uuid.UUID, limit=50):
    global _retry_after
    limit = max(1, min(limit, 100))
    if settings.ENABLED and monotonic() >= _retry_after:
        try:
            driver = get_neo4j_driver()
            if driver:
                with driver.session(
                    database=settings.DATABASE,
                    default_access_mode=READ_ACCESS,
                    fetch_size=200,
                ) as session:
                    record = session.run(
                        Query(OVERVIEW_QUERY, timeout=settings.QUERY_TIMEOUT),
                        user_id=str(user_id),
                        limit=limit,
                    ).single()
                    if record:
                        graph = serialize_neo4j(record, limit)
                        latest_import = db.scalar(select(func.max(ImportRecord.finished_at)).where(
                            ImportRecord.user_id == user_id, ImportRecord.status.in_(["completed", "partially_completed"])))
                        # Never serve an old projection after a saved import whose sync failed.
                        if not latest_import or (graph.last_sync_at and graph.last_sync_at >= latest_import):
                            return graph
        except Exception:
            with _circuit_lock:
                _retry_after = monotonic() + 30
    return relational_overview(db, user_id, limit)


def relational_overview(db, user_id, limit=50):
    """Same evidence model from SQL, with fixed query count and no lazy collections."""
    nodes = {str(user_id): _node(user_id, "User", "Your workspace")}
    edges = {}

    def edge(source, target, kind):
        e = _edge(source, target, kind)
        edges[e.data.id] = e

    commitments = db.scalars(
        select(Commitment)
        .options(joinedload(Commitment.merchant))
        .where(Commitment.user_id == user_id)
        .order_by(Commitment.id)
        .limit(limit + 1)
    ).all()
    truncated = len(commitments) > limit
    commitments = commitments[:limit]
    ids = [c.id for c in commitments]
    accounts = db.scalars(
        select(Account).where(Account.user_id == user_id).order_by(Account.id).limit(20)
    ).all()
    for a in accounts:
        nodes[str(a.id)] = _node(
            a.id,
            "Account",
            a.label,
            metadata={"currency": a.currency, "masked_reference": a.masked_reference},
        )
        edge(user_id, a.id, "OWNS_ACCOUNT")
    for c in commitments:
        nodes[str(c.id)] = _node(
            c.id,
            "Commitment",
            c.display_name,
            amount=float(c.representative_amount or 0),
            status=c.status,
            cadence=c.cadence,
            confidence_band=c.confidence_band,
            category=c.category or (c.merchant.category if c.merchant else c.commitment_type),
        )
        edge(user_id, c.id, "HAS_COMMITMENT")
        if c.merchant:
            m = c.merchant
            nodes[str(m.id)] = _node(
                m.id, "Merchant", m.canonical_name, category=m.category
            )
            edge(c.id, m.id, "ASSOCIATED_WITH")
    if ids:
        insights = db.scalars(
            select(CommitmentInsight)
            .where(
                CommitmentInsight.user_id == user_id,
                CommitmentInsight.commitment_id.in_(ids),
            )
            .order_by(CommitmentInsight.detected_at.desc())
            .limit(50)
        ).all()
        for i in insights:
            nodes[str(i.id)] = _node(
                i.id,
                "CommitmentInsight",
                i.title,
                status=i.status,
                category=i.insight_type,
                metadata={"severity": i.severity},
            )
            edge(i.commitment_id, i.id, "HAS_INSIGHT")
        txs = db.execute(
            select(
                Transaction.id,
                Transaction.account_id,
                Transaction.raw_descriptor,
                Transaction.amount,
                Transaction.transaction_date,
                Transaction.payment_channel,
                Transaction.merchant_id,
                CommitmentTransaction.commitment_id,
            )
            .join(
                CommitmentTransaction,
                Transaction.id == CommitmentTransaction.transaction_id,
            )
            .where(
                Transaction.user_id == user_id,
                CommitmentTransaction.commitment_id.in_(ids),
            )
            .order_by(Transaction.transaction_date.desc(), Transaction.id)
            .limit(limit)
        ).all()
        for t in txs:
            nodes[str(t.id)] = _node(
                t.id,
                "Transaction",
                t.raw_descriptor,
                amount=float(t.amount),
                metadata={
                    "transaction_date": t.transaction_date.isoformat(),
                    "payment_channel": t.payment_channel,
                },
            )
            edge(t.commitment_id, t.id, "SUPPORTED_BY")
            if str(t.account_id) in nodes:
                edge(t.account_id, t.id, "HAS_TRANSACTION")
            if str(t.merchant_id) in nodes:
                edge(t.id, t.merchant_id, "PAID_TO")
    return GraphElements(
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        source="relational",
        truncated=truncated,
        message="Showing source records from PostgreSQL. Sync the projection to enable Neo4j reads.",
    )
