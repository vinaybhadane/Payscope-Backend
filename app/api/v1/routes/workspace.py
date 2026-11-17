"""AI report, email controls and complete workspace clearing."""
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from app.core.auth import current_identity
from app.core.config import settings
from app.db.session import get_db
from app.db.base import Base
from app.repositories.user_repository import UserRepository
from app.models.workspace_intelligence import WorkspaceIntelligence, EmailPreference, EmailDelivery
from app.analytics.report_pdf import build_report_pdf
from app.analytics.email_alerts import enqueue_report

router = APIRouter(prefix='/workspace', tags=['Workspace intelligence'])

def owner(db): return UserRepository.get_or_create_demo_user(db).id

@router.post('/review')
def review_records(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from app.ingestion.refresh_service import refresh_imported_workspace
    from app.analytics.email_alerts import process_email_jobs
    result = refresh_imported_workspace(db, owner(db))
    background_tasks.add_task(process_email_jobs)
    if result['analysis_status'] == 'failed': raise HTTPException(503, result.get('message', 'AI review failed.'))
    return result

@router.get('/intelligence')
def intelligence(db: Session = Depends(get_db)):
    state = db.get(WorkspaceIntelligence, owner(db))
    return {'status': state.status if state else 'empty', 'model': state.model if state else settings.GEMINI_MODEL,
            'generated_at': state.generated_at if state else None, 'report': state.report if state else None,
            'error': state.error if state else None}

@router.get('/report.pdf')
def report_pdf(db: Session = Depends(get_db)):
    state = db.get(WorkspaceIntelligence, owner(db))
    if not state or state.status != 'completed': raise HTTPException(409, 'Complete an AI review before generating the report.')
    return Response(build_report_pdf(state), media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="payscope-subscription-report.pdf"', 'Cache-Control': 'no-store'})

@router.get('/email-settings')
def email_settings(db: Session = Depends(get_db)):
    uid = owner(db)
    identity = current_identity.get()
    pref = db.get(EmailPreference, uid)
    jobs = list(db.scalars(select(EmailDelivery).where(EmailDelivery.user_id == uid).order_by(EmailDelivery.created_at.desc()).limit(10)).all())
    return {'recipient': identity.email, 'sender': settings.BREVO_SENDER_EMAIL,
        'reports_enabled': pref.reports_enabled if pref else bool(identity.email),
        'reminders_enabled': pref.reminders_enabled if pref else bool(identity.email),
        'can_email': bool(identity.email), 'reminder_days': [5,3,1],
        'deliveries': [{'kind': 'report' if j.delivery_key.startswith('report:') else 'reminder', 'status': j.status, 'error': j.error, 'created_at': j.created_at} for j in jobs]}

class EmailSettingsRequest(BaseModel):
    reports_enabled: bool
    reminders_enabled: bool

@router.put('/email-settings')
def update_email_settings(payload: EmailSettingsRequest, db: Session = Depends(get_db)):
    email = current_identity.get().email
    if not email: raise HTTPException(400, 'Sign in with a verified email address to receive reports and reminders.')
    uid = owner(db)
    pref = db.get(EmailPreference, uid)
    if not pref:
        pref = EmailPreference(user_id=uid, recipient=email)
        db.add(pref)
    pref.recipient, pref.reports_enabled, pref.reminders_enabled = email, payload.reports_enabled, payload.reminders_enabled
    db.commit()
    return email_settings(db)

@router.post('/email-report')
def email_report(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    uid = owner(db)
    if not current_identity.get().email: raise HTTPException(400, 'Sign in with a verified email to send the report.')
    if not enqueue_report(db, uid): raise HTTPException(409, 'No new reviewed report is available, or report emails are disabled.')
    db.commit()
    from app.analytics.email_alerts import process_email_jobs
    background_tasks.add_task(process_email_jobs)
    return {'status': 'queued'}

class ClearRequest(BaseModel):
    confirmation: str

@router.post('/clear')
def clear_workspace(payload: ClearRequest, db: Session = Depends(get_db)):
    if payload.confirmation != 'CLEAR': raise HTTPException(400, 'Type CLEAR to remove all workspace records.')
    uid = owner(db)
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'ai:'+str(uid)})
    # Shared merchant reference data and the login identity are retained. All owned
    # rows and their cascading evidence records are deleted in dependency order.
    for table in reversed(Base.metadata.sorted_tables):
        if 'user_id' in table.c: db.execute(delete(table).where(table.c.user_id == uid))
    db.commit()
    graph_status = 'cleared'
    try:
        from app.graph.driver import get_neo4j_driver
        from app.graph.config import neo4j_settings
        if neo4j_settings.ENABLED:
            driver = get_neo4j_driver()
            with driver.session(database=neo4j_settings.DATABASE) as session:
                session.run('MATCH (n {user_id:$user_id}) DETACH DELETE n', user_id=str(uid)).consume()
    except Exception:
        graph_status = 'cleanup_pending'
    return {'status': 'cleared', 'graph_status': graph_status}
