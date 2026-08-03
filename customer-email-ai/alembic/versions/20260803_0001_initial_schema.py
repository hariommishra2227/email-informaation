"""initial production schema

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260803_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from database.connection import Base
    from database import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    from database.connection import Base
    from database import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind)
