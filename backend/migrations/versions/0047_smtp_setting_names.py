"""Rename the SMTP keys saved from the setup screen to the SMTP_* names

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-25 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

# Values are encrypted with a fixed purpose, not the key, so a rename is enough.
RENAMES = {
    "zoho_smtp_host": "smtp_host",
    "zoho_smtp_port": "smtp_port",
    "zoho_email": "smtp_user",
    "zoho_password": "smtp_password",
}


def _rename(pairs: dict[str, str]) -> None:
    for old, new in pairs.items():
        # A row already under the new name wins; the old one is dropped.
        op.execute(
            f"UPDATE instance_settings SET key = '{new}' WHERE key = '{old}' "
            f"AND NOT EXISTS (SELECT 1 FROM instance_settings WHERE key = '{new}')"
        )
        op.execute(f"DELETE FROM instance_settings WHERE key = '{old}'")


def upgrade() -> None:
    _rename(RENAMES)


def downgrade() -> None:
    _rename({new: old for old, new in RENAMES.items()})
