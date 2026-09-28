"""A credential can no longer take its post history with it

post_platforms.platform_id was ON DELETE CASCADE to platform_credentials.id, and every
connect path "replaced" a connection by deleting the old credential row and inserting a
new one. With foreign_keys=ON that deleted every post_platforms row for the platform:
remote ids, permalinks and pending retries, on every reconnect — and Instagram's 60-day
token makes reconnecting routine. Disconnect did the same.

The code now updates the credential in place and disconnect keeps the row. This makes
the database hold the line as well: RESTRICT refuses a credential delete that would
orphan history, so a future regression fails loudly instead of erasing records.

SQLite can't ALTER a foreign key, so batch mode rebuilds the table. The constraint was
created unnamed in 0006; the naming convention lets batch mode find and drop it.

Revision ID: 0033_keep_history_on_reconnect
Revises: 0032_reel_engagement
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0033_keep_history_on_reconnect"
down_revision: Union[str, None] = "0032_reel_engagement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_FK = "fk_post_platforms_platform_id_platform_credentials"


def _swap(ondelete: str) -> None:
    with op.batch_alter_table(
        "post_platforms", naming_convention=_NAMING, recreate="always"
    ) as b:
        b.drop_constraint(_FK, type_="foreignkey")
        b.create_foreign_key(
            _FK, "platform_credentials", ["platform_id"], ["id"], ondelete=ondelete
        )


def upgrade() -> None:
    _swap("RESTRICT")


def downgrade() -> None:
    _swap("CASCADE")
