"""Import endpoints for statement and notification file ingestion."""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from app.core.auth import require_identity, Identity
from app.db.session import get_db
from app.ingestion.import_service import ImportService
from app.ingestion.file_validator import MAX_FILE_SIZE_BYTES
from app.ingestion.refresh_service import refresh_imported_workspace
from starlette.concurrency import run_in_threadpool
from app.repositories.import_repository import ImportRepository
from app.repositories.user_repository import UserRepository, resolve_workspace_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["Imports"])


@router.post("/preview", status_code=status.HTTP_200_OK)
async def preview_import(
    file: UploadFile = File(...),
    import_type: str = Form("statement"),
    synthetic_confirmed: bool = Form(True),
    column_overrides: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_identity),
) -> Dict[str, Any]:
    """Generate unpersisted import preview with validation report and duplicate check."""
    synthetic_confirmed = True

    content = await file.read(MAX_FILE_SIZE_BYTES + 1)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, "File exceeds the 10 MB upload limit.")
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes).",
        )

    # Parse column overrides if provided
    overrides_dict = None
    if column_overrides:
        try:
            overrides_dict = json.loads(column_overrides)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="column_overrides must be valid JSON object.",
            )

    try:
        user = UserRepository.get_or_create_demo_user(db)
        user_id = user.id
    except Exception as exc:
        logger.warning("Could not resolve demo user from DB for preview: %s", exc)
        user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    result = await run_in_threadpool(ImportService.preview,
        db=db,
        user_id=user_id,
        filename=file.filename or "uploaded_file",
        content=content,
        import_type=import_type,
        synthetic_confirmed=synthetic_confirmed,
        column_overrides=overrides_dict,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to generate preview."),
        )

    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    import_type: str = Form("statement"),
    synthetic_confirmed: bool = Form(True),
    column_overrides: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_identity),
) -> Dict[str, Any]:
    """Execute import and persist valid non-duplicate records to PostgreSQL."""
    synthetic_confirmed = True

    content = await file.read(MAX_FILE_SIZE_BYTES + 1)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, "File exceeds the 10 MB upload limit.")
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes).",
        )

    overrides_dict = None
    if column_overrides:
        try:
            overrides_dict = json.loads(column_overrides)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="column_overrides must be valid JSON object.",
            )

    user = UserRepository.get_or_create_demo_user(db)

    try:
        result = await run_in_threadpool(ImportService.execute_import,
            db=db,
            user_id=user.id,
            filename=file.filename or "uploaded_file",
            content=content,
            import_type=import_type,
            synthetic_confirmed=synthetic_confirmed,
            column_overrides=overrides_dict,
        )
        if result["status"] != "failed":
            result["workspace_refresh"] = await run_in_threadpool(refresh_imported_workspace, db, user.id)
            from app.analytics.email_alerts import process_email_jobs
            background_tasks.add_task(process_email_jobs)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The operation could not be completed. Please retry.")


@router.get("", status_code=status.HTTP_200_OK)
def list_imports(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_identity),
) -> Dict[str, Any]:
    """Retrieve paginated list of previous imports."""
    try:
        user = UserRepository.get_or_create_demo_user(db)
        imports = ImportRepository.list_by_user(db, user_id=user.id, limit=limit, offset=offset)
        total_count = ImportRepository.count_by_user(db, user_id=user.id)
    except Exception as exc:
        logger.warning("Failed to query imports from db: %s", exc)
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }

    items = []
    for imp in imports:
        items.append({
            "id": str(imp.id),
            "filename": imp.filename_display,
            "file_type": imp.file_type,
            "source_label": imp.source_label,
            "status": imp.status,
            "row_count": imp.row_count,
            "accepted_count": imp.accepted_count,
            "duplicate_count": imp.duplicate_count,
            "rejected_count": imp.rejected_count,
            "created_at": imp.created_at.isoformat() if imp.created_at else None,
            "finished_at": imp.finished_at.isoformat() if imp.finished_at else None,
            "error_summary": imp.error_summary,
        })

    return {
        "items": items,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{import_id}/refresh")
def retry_import_refresh(import_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)) -> Dict[str, Any]:
    user_id = resolve_workspace_user_id(db)
    record = ImportRepository.get_by_id(db, import_id)
    if not record or record.user_id != user_id:
        raise HTTPException(404, "Import not found.")
    result = refresh_imported_workspace(db, user_id)
    from app.analytics.email_alerts import process_email_jobs
    background_tasks.add_task(process_email_jobs)
    return result


@router.get("/{import_id}/status", status_code=status.HTTP_200_OK)
@router.get("/{import_id}", status_code=status.HTTP_200_OK)
def get_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve detailed metadata for a specific import record."""
    try:
        imp = ImportRepository.get_by_id(db, import_id)
    except Exception as exc:
        logger.warning("Failed to get import by id from db: %s", exc)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")

    if not imp or imp.user_id != resolve_workspace_user_id(db):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found.")

    return {
        "id": str(imp.id),
        "filename": imp.filename_display,
        "file_type": imp.file_type,
        "source_label": imp.source_label,
        "status": imp.status,
        "row_count": imp.row_count,
        "accepted_count": imp.accepted_count,
        "duplicate_count": imp.duplicate_count,
        "rejected_count": imp.rejected_count,
        "created_at": imp.created_at.isoformat() if imp.created_at else None,
        "finished_at": imp.finished_at.isoformat() if imp.finished_at else None,
        "error_summary": imp.error_summary,
    }
