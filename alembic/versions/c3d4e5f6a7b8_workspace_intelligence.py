"""AI reviews, email preferences, and email outbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('commitments', sa.Column('category', sa.String(100), nullable=True))
    fk = lambda: sa.Column("user_id", sa.UUID(), sa.ForeignKey("demo_users.id", ondelete="CASCADE"), nullable=False)
    op.create_table("workspace_intelligence", fk(), sa.Column("status", sa.String(20), nullable=False), sa.Column("model", sa.String(100), nullable=False), sa.Column("report", postgresql.JSONB(), nullable=False), sa.Column("error", sa.Text()), sa.Column("generated_at", sa.DateTime(timezone=True)), sa.PrimaryKeyConstraint("user_id"))
    op.create_table("email_preferences", fk(), sa.Column("recipient", sa.String(320), nullable=False), sa.Column("reports_enabled", sa.Boolean(), nullable=False), sa.Column("reminders_enabled", sa.Boolean(), nullable=False), sa.PrimaryKeyConstraint("user_id"))
    op.create_table("email_deliveries", sa.Column("delivery_key", sa.String(255), primary_key=True), fk(), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("provider_id", sa.String(255)), sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_email_deliveries_user_id", "email_deliveries", ["user_id"])

def downgrade():
    op.drop_column('commitments', 'category')
    op.drop_table("email_deliveries")
    op.drop_table("email_preferences")
    op.drop_table("workspace_intelligence")
