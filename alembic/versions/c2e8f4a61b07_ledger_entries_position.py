"""ledger_entries.position: each entry's place in the transaction as submitted

Entries were sorted by `(created_at, id)`. Every entry of a transaction
shares `created_at`, so a random UUID decided their order on the
transaction-detail page and in the API, and a rebuild, which mints new
entry ids, could change it.

`post_transaction` now stores each entry's zero-based submission index
here and in the `transaction.posted` payload, and reads sort by it.

The column is nullable, and existing rows are left NULL. Their order is not
recoverable from the read model: the arbitrary order they show today is all
it knows. Reads put NULL positions after numbered ones and then fall back to
`(created_at, id)`, so these rows keep exactly the order they have now. A
rebuild gives them their real submission order back, because the event log
has it: the payload's `entries` array has always been written in submission
order, and JSONB keeps array order.
"""

import sqlalchemy as sa

from alembic import op

revision = "c2e8f4a61b07"
down_revision = "a4c7e2d9b813"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ledger_entries", sa.Column("position", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("ledger_entries", "position")
