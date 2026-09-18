"""read-model indexes, plus a real CHECK on accounts.account_type

Two independent bits of hardening on the read model, in one migration
because both are cheap DDL on tables the app already has.

**Indexes.** The read-model tables had none, so every query through them
was a sequential scan. Each index here backs a query the app actually
runs:

- `ledger_entries.account_id` — the overview page outer-joins entries to
  accounts on this column to compute every per-account balance.
- `ledger_entries.transaction_id` — the transaction-detail page selects
  a transaction's entries by it.
- `transactions.created_at DESC` — the overview's "recent transactions"
  list orders by it descending; the index is declared in the same
  direction so the sort can be read straight off it.

**`ck_account_type_valid`.** `account_type` was a bare `String(32)` whose
valid values lived only in a code comment and in `validate_account`.
Anything writing to the database without going through the form — a
manual `INSERT`, a data fix, a future importer — could store a type the
overview page does not recognise, and since that page keys an account's
balance *sign* off this column, the result is a silently wrong balance
rather than a loud failure. This mirrors the `ck_entry_type_valid` and
`ck_amount_positive` constraints `ledger_entries` already carries.

The five values are spelled out literally below rather than imported
from `app.domain.accounts.ACCOUNT_TYPES`. A migration is a historical
record: it has to keep producing the same DDL it produced the day it was
written, so it must not read a live application constant that may later
change. `app/db/schema.py` *does* generate its copy from that tuple, so
the running schema and the validator cannot drift; if the tuple is ever
edited, this constraint needs a new migration to match, and that new
migration should spell out its own literal list too.

Note on existing data: `create_check_constraint` validates current rows,
so this migration fails loudly if any account already holds a type
outside the five. That is the intended behaviour — such a row is exactly
the bug the constraint exists to prevent, and it should be corrected
rather than grandfathered in.
"""

import sqlalchemy as sa

from alembic import op

revision = "de4f1aec2fe6"
down_revision = "b7855ff9a6aa"
branch_labels = None
depends_on = None

# Kept in sync by hand with app.domain.accounts.ACCOUNT_TYPES — see the
# docstring above for why this is deliberately a copy and not an import.
ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")


def upgrade() -> None:
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])
    op.create_index("ix_transactions_created_at", "transactions", [sa.text("created_at DESC")])
    op.create_check_constraint(
        "ck_account_type_valid",
        "accounts",
        "account_type IN ({})".format(", ".join(repr(t) for t in ACCOUNT_TYPES)),
    )


def downgrade() -> None:
    op.drop_constraint("ck_account_type_valid", "accounts", type_="check")
    op.drop_index("ix_transactions_created_at", table_name="transactions")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_account_id", table_name="ledger_entries")
