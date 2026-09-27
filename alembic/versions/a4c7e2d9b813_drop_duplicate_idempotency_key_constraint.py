"""stop declaring a unique constraint on idempotency_keys.key, which is its primary key

The initial migration, and `schema.py`, declared both `PRIMARY KEY (key)`
and `CONSTRAINT uq_idempotency_key UNIQUE (key)`. Postgres never kept
both. When `CREATE TABLE` meets a unique constraint identical to the
primary key, it folds the two into one index-backed constraint: the
primary key, carrying the unique constraint's name. So every database
built so far has exactly one constraint on `key`, a primary key misleadingly
named `uq_idempotency_key`, while the model still describes a separate
unique constraint that does not exist. That mismatch is the difference
`alembic check` kept reporting.

`schema.py` now declares only the primary key, and this migration gives the
existing constraint the name Postgres gives an unnamed primary key,
`idempotency_keys_pkey`, which is what `metadata.create_all` produces.
Renaming the constraint renames its index with it. Nothing is rebuilt and
no row is touched.

If a database does have a separate unique constraint of that name, which
would mean Postgres did not fold them, it is dropped instead: the primary
key already enforces uniqueness.
"""

from alembic import op

revision = "a4c7e2d9b813"
down_revision = "5e8d2a1f9c63"
branch_labels = None
depends_on = None

UPGRADE = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conrelid = 'idempotency_keys'::regclass
                  AND conname = 'uq_idempotency_key' AND contype = 'u') THEN
        ALTER TABLE idempotency_keys DROP CONSTRAINT uq_idempotency_key;
    ELSIF EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'idempotency_keys'::regclass
                     AND conname = 'uq_idempotency_key' AND contype = 'p') THEN
        ALTER TABLE idempotency_keys
            RENAME CONSTRAINT uq_idempotency_key TO idempotency_keys_pkey;
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    # Back to the state every database was in before: one primary key, under
    # the old name.
    op.execute(
        "ALTER TABLE idempotency_keys RENAME CONSTRAINT idempotency_keys_pkey TO uq_idempotency_key"
    )
