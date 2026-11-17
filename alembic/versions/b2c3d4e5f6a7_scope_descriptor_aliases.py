"""Isolate user-created descriptor aliases by workspace."""
from alembic import op
import sqlalchemy as sa
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("descriptor_aliases", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_descriptor_aliases_user", "descriptor_aliases", "demo_users", ["user_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_descriptor_aliases_user_id", "descriptor_aliases", ["user_id"])
    op.drop_constraint("uq_descriptor_aliases_merchant_normalised_alias", "descriptor_aliases", type_="unique")
    op.create_unique_constraint("uq_descriptor_aliases_merchant_normalised_alias", "descriptor_aliases", ["merchant_id", "user_id", "normalised_alias"])

def downgrade():
    # Private aliases cannot be represented safely by the old shared schema.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM descriptor_aliases WHERE user_id IS NOT NULL")):
        raise RuntimeError("Export or remove private aliases before downgrading this schema.")
    op.drop_constraint("uq_descriptor_aliases_merchant_normalised_alias", "descriptor_aliases", type_="unique")
    op.create_unique_constraint("uq_descriptor_aliases_merchant_normalised_alias", "descriptor_aliases", ["merchant_id", "normalised_alias"])
    op.drop_index("ix_descriptor_aliases_user_id", table_name="descriptor_aliases")
    op.drop_constraint("fk_descriptor_aliases_user", "descriptor_aliases", type_="foreignkey")
    op.drop_column("descriptor_aliases", "user_id")
