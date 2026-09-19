"""transactions.sequence — a real posting order for the transaction list

The same defect `events.sequence` had, in the same shape, for the same
reason: `transactions.created_at` is a `timestamptz` defaulting to
`CURRENT_TIMESTAMP`, which Postgres evaluates at *transaction start*, so
every transaction written inside one database transaction shares a single
value. Measured on the demo seed: 10 transactions, 1 distinct
`created_at`.

Both listing queries ordered by that column, so the order of tied rows was
whatever the planner returned. On `/transactions` that is worse than
cosmetic — `ORDER BY` with ties plus `OFFSET`/`LIMIT` has no stable
boundary, so a row can appear on two pages, or on none, across successive
requests for the same data.

Unlike migration b7855ff9a6aa, which had to drop a useless timestamp
column before adding the identity one, `transactions` has no `sequence`
column to replace — this is a plain ADD. Existing rows are assigned
identity values by the `ADD COLUMN` table rewrite: arbitrary relative to
each other, which is no worse than the tie they had, and correct for
everything inserted afterwards.

`ix_transactions_created_at` is deliberately left in place. Nothing orders
by `created_at` any more, but it still backs the `/transactions`
date-range filter.
"""

import sqlalchemy as sa

from alembic import op

revision = "fca143a4d6d9"
down_revision = "de4f1aec2fe6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
    )
    op.create_index("ix_transactions_sequence", "transactions", ["sequence"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_transactions_sequence", table_name="transactions")
    op.drop_column("transactions", "sequence")
