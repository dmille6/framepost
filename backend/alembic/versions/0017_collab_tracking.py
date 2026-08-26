"""collaborator ledger + performer handle health

Revises: 0016_platform_metrics
Create Date: 2026-08-25 19:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_collab_tracking"
down_revision: Union[str, None] = "0016_platform_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # What actually went out per post. The event log already records this, but as JSON
    # blobs that analytics can't join against — this table is the queryable form.
    #
    # There is deliberately no "accepted" column: Meta exposes collaborator state only
    # on the Facebook-Login API surface, and this install uses Instagram Login, where
    # /{media}/collaborators is a schema error. Acceptance is inferred downstream from
    # engagement lift instead of stored as fact.
    op.create_table(
        "post_collaborators",
        sa.Column("post_id", sa.String, sa.ForeignKey("posts.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("handle", sa.Text, primary_key=True),
        sa.Column("performer_id", sa.String,
                  sa.ForeignKey("performers.id", ondelete="SET NULL"), nullable=True),
        # "sent" — Meta accepted the handle onto the container (an invite exists).
        # "rejected" — Meta refused it; the post shipped without this credit.
        sa.Column("status", sa.String, nullable=False, server_default="sent"),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_post_collab_handle", "post_collaborators", ["handle"])

    with op.batch_alter_table("performers") as b:
        # "ok" | "needs_check" — set to needs_check when Meta refuses the handle, which
        # means it is private, renamed, or gone. Surfaced in Settings → Performers so a
        # dead handle gets fixed instead of silently costing every future collab.
        b.add_column(sa.Column("handle_status", sa.String, nullable=False,
                               server_default="ok"))
        b.add_column(sa.Column("handle_checked_at", sa.DateTime, nullable=True))
        b.add_column(sa.Column("handle_error", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("performers") as b:
        b.drop_column("handle_error")
        b.drop_column("handle_checked_at")
        b.drop_column("handle_status")
    op.drop_index("idx_post_collab_handle", table_name="post_collaborators")
    op.drop_table("post_collaborators")
