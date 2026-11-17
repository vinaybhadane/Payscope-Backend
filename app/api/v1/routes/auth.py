"""Public guest session creation; authenticated workspace identity."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.core.auth import create_guest_token, require_identity
from app.db.session import get_db
from app.repositories.user_repository import UserRepository, resolve_workspace_user_id

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/guest")
def guest_session(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return create_guest_token()


@router.get("/me", dependencies=[Depends(require_identity)])
def workspace_identity(db: Session = Depends(get_db)):
    user = UserRepository.get_or_create_demo_user(db)
    return {"id": str(user.id), "display_name": user.display_name, "is_demo": user.is_demo}
