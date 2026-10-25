"""Strip HTML left in stored extractive summaries

Extractive summaries were cut from feed text without removing its markup, so
items from feeds such as hnrss.org showed "<p>Article URL: <a href=…" as their
summary. New summaries are plain text; this cleans the ones already stored.
LLM summaries never contained markup and are left alone.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_COLUMNS = ("summary_headline", "summary_brief", "summary_detailed")


# A tag, or one cut off at the end of the text: summaries were cut at 800 /
# 1600 characters before stripping, which can land inside a tag.
_TAG = "</?[a-zA-Z][^>]*(>|$)"


def upgrade() -> None:
    for column in _COLUMNS:
        # Tags out, the common entities decoded, whitespace collapsed.
        op.execute(
            f"""
            UPDATE content_items
            SET {column} = btrim(regexp_replace(
                replace(replace(replace(replace(replace(
                    regexp_replace({column}, '{_TAG}', ' ', 'g'),
                    '&quot;', '"'), '&#39;', ''''), '&lt;', '<'), '&gt;', '>'), '&amp;', '&'),
                '\\s+', ' ', 'g'))
            WHERE {column} ~ '{_TAG}'
              AND coalesce(summary_source, '') <> 'llm'
            """
        )


def downgrade() -> None:
    # The markup was never meant to be there; nothing to restore.
    pass
