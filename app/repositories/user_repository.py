"""Demo User repository operations."""
import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.demo_user import DemoUser
from app.core.auth import current_identity
from fastapi import HTTPException


class UserRepository:
    """Repository for DemoUser persistence operations."""

    @staticmethod
    def create(
        db: Session,
        workspace_key: str,
        display_name: Optional[str] = "Demo User",
        home_currency: str = "INR",
        is_demo: bool = True,
    ) -> DemoUser:
        """Create and persist a new demo user."""
        user = DemoUser(
            workspace_key=workspace_key,
            display_name=display_name,
            home_currency=home_currency,
            is_demo=is_demo,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def get_by_id(db: Session, user_id: uuid.UUID) -> Optional[DemoUser]:
        """Retrieve a demo user by UUID."""
        stmt = select(DemoUser).where(DemoUser.id == user_id)
        return db.scalar(stmt)

    @staticmethod
    def get_by_workspace_key(db: Session, workspace_key: str) -> Optional[DemoUser]:
        """Retrieve a demo user by unique workspace key."""
        stmt = select(DemoUser).where(DemoUser.workspace_key == workspace_key)
        return db.scalar(stmt)

    @classmethod
    def get_or_create_demo_user(
        cls,
        db: Session,
        workspace_key: str = "demo_workspace_primary",
        display_name: str = "Demo User",
    ) -> DemoUser:
        """Retrieve existing demo user or create one if not found."""
        identity = current_identity.get()
        if identity:
            workspace_key = identity.workspace_key
            display_name = identity.display_name
        user = cls.get_by_workspace_key(db, workspace_key)
        if not user:
            try:
                user = cls.create(db, workspace_key=workspace_key, display_name=display_name,
                                  is_demo=identity.is_demo if identity else True)
            except IntegrityError:
                # Concurrent first requests may race on the unique workspace key.
                db.rollback()
                user = cls.get_by_workspace_key(db, workspace_key)
                if user is None:
                    raise
        if identity:
            db.info["workspace_user_id"] = user.id
        return user


def resolve_workspace_user_id(db: Session, user_id=None):
    """Reject caller-selected workspaces unless they belong to the verified identity."""
    current = UserRepository.get_or_create_demo_user(db).id
    if user_id is not None and user_id != current:
        raise HTTPException(403, "You cannot access another workspace.")
    return current
