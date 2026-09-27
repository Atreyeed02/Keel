"""index idempotency_keys.created_at for retention cleanup

`app.domain.idempotency.prune_idempotency_keys` deletes keys older than
the retention window with `created_at < now() - interval`. Without an
index that is a scan of the whole table, and the table is the one that
grows with every posting.
"""

from alembic import op

revision = "9b1f6e2c8a47"
down_revision = "3c9e5a7b2d14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
