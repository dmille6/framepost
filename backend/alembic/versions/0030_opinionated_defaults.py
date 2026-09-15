"""Make the common case the default: burlesque tags, every platform, home city

The editor asked three questions on every photo whose answer was the same every
time. Tags started empty even though a core set applies to the whole catalogue;
`city` started empty because IPTC carries no city and nothing else filled it;
and a platform connected but not marked `default_target` silently dropped out of
the fan-out, so "post everywhere" meant remembering to tick it.

Only the global default tag profile is touched, and only when it is still empty
-- a profile the photographer has already written is his, not ours. Same for
`default_city`: an existing value wins.

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

# Tags true of essentially every frame in this catalogue, so they cost nothing to
# apply globally and save a decision per photo. Subject-specific tags (a performer,
# a venue, "circus", "concert") stay on the post where they belong -- this list is
# deliberately the floor, not the whole tag set. Editable in Settings -> Tag Profiles.
DEFAULT_TAGS = (
    "burlesque, burlesqueperformer, stagephotography, liveperformance, "
    "performancephotography, stagelighting, neworleans, nola"
)

DEFAULT_CITY = "New Orleans"


def upgrade() -> None:
    conn = op.get_bind()

    # --- burlesque tags on the global default profile -------------------------
    # ensure_default_profile() creates this row empty on first run; fill it only if
    # nobody has since written tags into it.
    conn.execute(
        sa.text(
            "UPDATE tag_profiles SET tags = :tags "
            "WHERE is_default = 1 AND COALESCE(TRIM(tags), '') = ''"
        ),
        {"tags": DEFAULT_TAGS},
    )

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
    # Only the config key is unambiguously ours to remove. The tag profile and the
    # target flags are indistinguishable from the photographer's own edits by now,
    # so putting them back would be guessing.
    op.get_bind().execute(
        sa.text("DELETE FROM app_config WHERE key = 'default_city'")
    )
