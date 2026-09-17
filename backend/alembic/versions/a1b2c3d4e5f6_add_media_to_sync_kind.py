"""add 'media' to the sync_kind enum

Alembic's autogenerate compares tables and columns but NOT the members of an existing
Postgres enum, so adding a value to a Python Enum produces no migration and the insert
fails at runtime with "invalid input value for enum". This has to be written by hand.

Revision ID: a1b2c3d4e5f6
Revises: 133386ddbf4e
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "133386ddbf4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS keeps the migration replayable against a database where an earlier
    # attempt already added the value.
    op.execute("ALTER TYPE sync_kind ADD VALUE IF NOT EXISTS 'media'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum; removing it would mean rebuilding the type
    # and rewriting every dependent column, which is not worth it for a downgrade path.
    pass
