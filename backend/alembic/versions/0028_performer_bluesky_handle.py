"""A performer's Bluesky handle, which is not their Instagram handle

Mentions are written into post.description once, at authoring time, as
'@instagram_handle' -- and that same text is sent to every platform. On Bluesky
those render as plain grey text: the account doesn't exist there under that
name, nobody is notified, and 334 of 518 captions carry at least one.

Bluesky handles are domain-shaped (name.bsky.social, or a custom domain) and
have no relationship to an Instagram handle, so they can't be derived -- only
recorded. Null means "not on Bluesky, or unknown", which is the honest default
for most performers.

Revision ID: 0028_performer_bluesky_handle
Revises: 0027_groups_overridden
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_performer_bluesky_handle"
down_revision: Union[str, None] = "0027_groups_overridden"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("performers", sa.Column("bluesky_handle", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("performers", "bluesky_handle")
