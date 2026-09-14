"""
SQLAlchemy Core table definitions.

Two concerns live here, deliberately kept separate:

1. Event log (`events`) — the source of truth. Every state change is
   appended here first; nothing is ever updated or deleted.
2. Ledger read model (`accounts`, `transactions`, `ledger_entries`) —
   derived from the event log, structured for double-entry invariants
   and fast balance queries. Rebuildable from `events` if needed.

`idempotency_keys` backs the idempotency layer: a client-supplied key
is stored with a hash of the request body and the response that was
returned, so a retried request short-circuits instead of re-applying.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

# --- Event log ---------------------------------------------------------

events = Table(
    "events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("sequence", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("aggregate_type", String(64), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    # Append-only: no updated_at, no soft-delete flag. If it's wrong,
    # a compensating event gets appended, not a mutation.
)

# --- Ledger read model ---------------------------------------------------

accounts = Table(
    "accounts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("name", String(255), nullable=False),
    Column("account_type", String(32), nullable=False),  # asset/liability/equity/revenue/expense
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("description", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

ledger_entries = Table(
    "ledger_entries",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column(
        "transaction_id",
        UUID(as_uuid=True),
        ForeignKey("transactions.id"),
        nullable=False,
    ),
    Column("account_id", UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False),
    Column("entry_type", String(6), nullable=False),  # 'debit' | 'credit'
    Column("amount", Numeric(18, 2), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("entry_type IN ('debit', 'credit')", name="ck_entry_type_valid"),
    CheckConstraint("amount > 0", name="ck_amount_positive"),
    # Double-entry balance (sum(debits) == sum(credits) per transaction)
    # is enforced in app/domain/ledger.py at write time, not here — a
    # DB-level trigger is a reasonable v2 hardening step, noted in the
    # roadmap doc rather than built into the MVP.
)

# --- Idempotency layer ---------------------------------------------------

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("response_body", JSONB, nullable=True),
    Column("response_status", String(3), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("key", name="uq_idempotency_key"),
)
