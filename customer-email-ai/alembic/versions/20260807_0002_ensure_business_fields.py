"""Safely add business fields missing from databases created by older deployments."""

from alembic import op
import sqlalchemy as sa


revision = "20260807_0002"
down_revision = "20260803_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("extracted_contacts")}
    for name, column_type in (
        ("location", sa.String(255)),
        ("subject", sa.Text()),
        ("email_date", sa.String(64)),
    ):
        if name not in existing:
            op.add_column(
                "extracted_contacts",
                sa.Column(name, column_type, nullable=False, server_default=""),
            )


def downgrade() -> None:
    # These may predate this revision; never remove production business data.
    pass
