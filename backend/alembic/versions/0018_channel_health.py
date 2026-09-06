"""channel auth health on platform_credentials

Revises: 0017_collab_tracking
Create Date: 2026-09-06 09:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_channel_health"
down_revision: Union[str, None] = "0017_collab_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("platform_credentials") as b:
        # "ok" | "reauth_required". Set when a failure is classified REAUTH — the grant
        # is gone or insufficient and no amount of retrying will bring it back. Cleared
        # by the next success or by reconnecting.
        b.add_column(sa.Column("auth_status", sa.String, nullable=False,
                               server_default="ok"))
        b.add_column(sa.Column("auth_error", sa.Text, nullable=True))
        b.add_column(sa.Column("auth_flagged_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("platform_credentials") as b:
        b.drop_column("auth_flagged_at")
        b.drop_column("auth_error")
        b.drop_column("auth_status")
