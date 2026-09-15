"""Make the common case the default: every platform, home city

The editor asked questions on every photo whose answer was the same every time.
`city` started empty because IPTC carries no city and nothing else filled it;
and a platform connected but not marked `default_target` silently dropped out of
the fan-out, so "post everywhere" meant remembering to tick it.

`default_city` only lands when the key is absent: an existing value wins.

Revision ID: 0030_opinionated_defaults
Revises: 0029_venue_bluesky_handle
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_opinionated_defaults"
down_revision: Union[str, None] = "0029_venue_bluesky_handle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_CITY = "New Orleans"


def upgrade() -> None:
    conn = op.get_bind()

    # --- home city ------------------------------------------------------------
    existing = conn.execute(
        sa.text("SELECT value FROM app_config WHERE key = 'default_city'")
    ).scalar_one_or_none()
    if existing is None:
        conn.execute(
            sa.text("INSERT INTO app_config (key, value) VALUES ('default_city', :v)"),
            {"v": DEFAULT_CITY},
        )

    # --- every connected platform is a default target -------------------------
    # Unconditional on purpose: the photographer asked for "post to all platforms",
    # and a cleared flag here is indistinguishable from one never set. Re-clear any
    # individual platform in Settings -> Platforms; that choice survives from now on
    # because no later migration repeats this.
    conn.execute(
        sa.text(
            "UPDATE platform_credentials SET default_target = 1 "
            "WHERE access_token IS NOT NULL AND access_token != ''"
        )
    )


def downgrade() -> None:
    # Only the config key is unambiguously ours to remove. The target flags are
    # indistinguishable from the photographer's own edits by now, so putting them
    # back would be guessing.
    op.get_bind().execute(
        sa.text("DELETE FROM app_config WHERE key = 'default_city'")
    )
