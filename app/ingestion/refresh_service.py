"""Rebuild workspace intelligence from committed imports, then project it to Neo4j."""
import logging
import uuid
from sqlalchemy.orm import Session
from app.analytics.ai_review import review_workspace
from app.core.read_cache import invalidate_read_caches
from app.graph.config import neo4j_settings
from app.graph.overview_service import reset_neo4j_circuit
from app.graph.sync_service import GraphSyncService

logger = logging.getLogger(__name__)


def refresh_imported_workspace(db: Session, user_id: uuid.UUID) -> dict:
    # Imports are already committed. A downstream failure must never undo them or
    # misreport the upload as failed; retrying this projection is idempotent.
    result = {"analysis_status": "failed", "graph_status": "not_started"}
    try:
        result.update(review_workspace(db, user_id))
    except Exception as exc:
        db.rollback()
        logger.exception("Post-import analysis failed for workspace %s", user_id)
        from app.models.workspace_intelligence import WorkspaceIntelligence
        from app.core.config import settings
        state = db.get(WorkspaceIntelligence, user_id)
        if not state:
            state = WorkspaceIntelligence(user_id=user_id, model=settings.GEMINI_MODEL, report={})
            db.add(state)
        state.status, state.error = 'failed', str(exc) if isinstance(exc, ValueError) else 'AI review failed. Retry analysis.'
        db.commit()
        result["message"] = state.error
        invalidate_read_caches()
        return result
    try:
        from app.core.auth import current_identity
        from app.models.workspace_intelligence import EmailPreference
        from app.analytics.email_alerts import enqueue_report
        identity = current_identity.get()
        if identity and identity.email:
            pref = db.get(EmailPreference, user_id)
            if not pref:
                db.add(EmailPreference(user_id=user_id, recipient=identity.email, reports_enabled=True, reminders_enabled=True))
            else:
                pref.recipient = identity.email
            db.commit()
            result['email_status'] = 'queued' if enqueue_report(db, user_id) else 'disabled_or_already_queued'
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Report email could not be queued for workspace %s", user_id)
        result["email_status"] = "failed"
    if not neo4j_settings.ENABLED:
        result.update(graph_status="disabled", message="Dashboard updated. Neo4j is disabled; relationships use database records.")
    else:
        try:
            sync = GraphSyncService(db).sync_user_graph(user_id)
            reset_neo4j_circuit()
            result.update(graph_status="completed", nodes_synced=sync.nodes_synced,
                          relationships_synced=sync.relationships_synced,
                          message="Dashboard and Neo4j relationships updated from your imported records.")
        except Exception:
            db.rollback()
            logger.exception("Post-import Neo4j sync failed for workspace %s", user_id)
            result.update(graph_status="failed", message="Dashboard updated. Neo4j sync needs a retry; your file is saved.")
    invalidate_read_caches()
    return result
