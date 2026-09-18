"""events.sequence as a real identity column, plus event-log indexes

`sequence` was a `timestamptz` defaulting to `CURRENT_TIMESTAMP` —
identical in type and default to `created_at`, and therefore useless for
ordering: Postgres evaluates `CURRENT_TIMESTAMP` at *transaction start*,
so every event appended inside one database transaction shared a single
value and their relative order was lost. This replaces it with a
`GENERATED ALWAYS AS IDENTITY` bigint, which the database assigns
monotonically per insert and the application cannot supply.

The old column is dropped rather than converted: its values carry no
ordering information worth preserving. Existing rows are assigned fresh
identity values by the `ADD COLUMN` table rewrite — arbitrary relative
to each other, which is no worse than what they had.

The indexes ride along in this migration because it is already rewriting
this table: `sequence` (the event-log ordering), `created_at DESC` (the
old ordering, still displayed) and `(aggregate_type, aggregate_id)` (the
linked-event lookup behind the transaction-detail page).
"""

import sqlalchemy as sa

from alembic import op

revision = "b7855ff9a6aa"
down_revision = "d8b553ce7776"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("events", "sequence")
    op.add_column(
        "events",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.create_index("ix_events_sequence", "events", ["sequence"], unique=True)
    op.create_index("ix_events_created_at", "events", [sa.text("created_at DESC")])
    op.create_index("ix_events_aggregate", "events", ["aggregate_type", "aggregate_id"])


def downgrade() -> None:
    op.drop_index("ix_events_aggregate", table_name="events")
    op.drop_index("ix_events_created_at", table_name="events")
    op.drop_index("ix_events_sequence", table_name="events")
    op.drop_column("events", "sequence")
    op.add_column(
        "events",
        sa.Column(
            "sequence",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
    )
