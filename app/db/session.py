"""SQLAlchemy database engine and session management."""
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.core.read_cache import invalidate_read_caches

# Create engine with connection pooling, pre-ping, and connect_timeout
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_recycle=300,
    echo=False,
    connect_args={'connect_timeout': 5},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)

# Imports, discovery, reviews and cancellation changes invalidate read snapshots.
event.listen(SessionLocal, "after_commit", invalidate_read_caches)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session.
    
    Yields:
        Session: Active SQLAlchemy session, closed upon completion.
    """
    db = SessionLocal()
    try:
        from app.core.auth import current_identity
        if current_identity.get():
            from app.repositories.user_repository import UserRepository
            UserRepository.get_or_create_demo_user(db)
        yield db
    finally:
        db.close()


@event.listens_for(Session, "do_orm_execute")
def scope_workspace_queries(execute_state):
    workspace_id = execute_state.session.info.get("workspace_user_id")
    if workspace_id is None:
        return
    from app.db.base import Base
    from sqlalchemy.orm import with_loader_criteria
    if execute_state.is_select or execute_state.is_update or execute_state.is_delete:
        for mapper in Base.registry.mappers:
            model = mapper.class_
            if hasattr(model, "user_id"):
                criterion = model.user_id == workspace_id
                if model.__name__ == "DescriptorAlias":
                    from sqlalchemy import or_
                    criterion = or_(criterion, model.user_id.is_(None))
                execute_state.statement = execute_state.statement.options(
                    with_loader_criteria(model, criterion, include_aliases=True)
                )


@event.listens_for(Session, "before_flush")
def guard_workspace_writes(session, flush_context, instances):
    workspace_id = session.info.get("workspace_user_id")
    if workspace_id is None:
        return
    from app.models.descriptor_alias import DescriptorAlias
    for item in session.new | session.dirty | session.deleted:
        if isinstance(item, DescriptorAlias) and item.user_id is None:
            if item in session.new:
                item.user_id = workspace_id
            else:
                raise ValueError("Curated reference aliases cannot be modified by workspace users")
        if hasattr(item, "user_id") and item.user_id is not None and item.user_id != workspace_id:
            raise ValueError("Cross-workspace writes are forbidden")
