"""Remember the Instagram container between create and publish

Carousel children were already durable (0024). The container that actually publishes —
a photo's, a reel's, a carousel's parent — was held only in memory, so a crash or a
timeout between /media and /media_publish left the retry with two bad options: create
a new container (orphaning the old one) or, when the publish had in fact gone through,
publish a second copy. The checkpoint lets the retry ask Meta what became of the
container first.

JSON rather than columns: id, created_at, the "publish sent, outcome unknown" mark and
the collaborators the container was created with travel together and are only ever read
together, by one function.

Revision ID: 0034_ig_container_checkpoint
Revises: 0033_keep_history_on_reconnect
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_ig_container_checkpoint"
down_revision: Union[str, None] = "0033_keep_history_on_reconnect"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("post_platforms") as b:
        b.add_column(sa.Column("ig_container", sa.Text(), nullable=True))
    with op.batch_alter_table("reels") as b:
        b.add_column(sa.Column("ig_container", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reels") as b:
        b.drop_column("ig_container")
    with op.batch_alter_table("post_platforms") as b:
        b.drop_column("ig_container")
