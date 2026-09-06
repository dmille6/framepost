"""Default the camera-info line on

New posts pick the flag up from app_config.default_include_exif (see
import_pipeline). Existing posts that haven't published yet get it too, so the
setting applies to the queue the user already has rather than only to future
imports. Posts already out on the platforms are left alone: their flag records
what actually published, and the editor should keep telling the truth about that.

Revision ID: 0021_default_include_exif
Revises: 0020_include_exif
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_default_include_exif"
down_revision: Union[str, None] = "0020_include_exif"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES ('default_include_exif', 'true') "
            "ON CONFLICT(key) DO NOTHING"
        )
    )
    conn.execute(sa.text("UPDATE posts SET include_exif = 1 WHERE posted_at IS NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE posts SET include_exif = 0 WHERE posted_at IS NULL"))
    conn.execute(sa.text("DELETE FROM app_config WHERE key = 'default_include_exif'"))
