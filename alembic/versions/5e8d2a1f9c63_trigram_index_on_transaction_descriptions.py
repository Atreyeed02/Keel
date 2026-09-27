"""trigram index on transactions.description for the /transactions search

The search filter is `description ILIKE '%term%' ESCAPE '\'`. The leading
wildcard means no btree index can help, so every search read the whole
table: correct at demo size, a sequential scan at any real volume. A GIN
index with pg_trgm's `gin_trgm_ops` answers substring matches, case-
insensitive ones and ones with an ESCAPE clause included.

pg_trgm ships with Postgres (it is in the official images) and is a
trusted extension since PostgreSQL 13, so the database owner can create
it without superuser rights.

Downgrade drops the index but leaves the extension installed: something
else in the database may have come to rely on it, and it costs nothing
unused.
"""

from alembic import op

revision = "5e8d2a1f9c63"
down_revision = "9b1f6e2c8a47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "ix_transactions_description_trgm",
        "transactions",
        ["description"],
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_description_trgm", table_name="transactions")
